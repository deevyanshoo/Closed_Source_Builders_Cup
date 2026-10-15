from copy import deepcopy

import pytest

from procure.carbon.schemes import SchemeInputError, find_schemes, load_schemes
from procure.demo.company import get_company


@pytest.fixture(scope='module')
def registry():
    return load_schemes()


def test_every_scheme_is_dated_with_an_official_url_and_plain_words(registry):
    assert registry['label'] == 'indicative, not financial or legal advice'
    for scheme in registry['schemes']:
        assert scheme['official_url'].startswith('https://')
        assert scheme['checked_on'] == '2026-10-15'
        assert scheme['offers'] in {'credit', 'subsidy', 'cheaper_loan', 'avoided_cost'}
        assert scheme['who'] and scheme['offers_text'] and scheme['numbers_used']


def test_indian_company_lists_possible_schemes_with_cautious_wording(registry):
    result = find_schemes(registry, get_company('solara-cables'), {})
    ids = [row['scheme_id'] for row in result['matches']]
    assert ids == ['in-ccts-offset', 'in-msme-zed', 'in-sidbi-green-finance']
    assert result['label'] == 'indicative, not financial or legal advice'
    for row in result['matches']:
        assert row['eligibility'] == 'possibly eligible, check conditions'
        assert row['saving']['status'] == 'missing_inputs'
        assert row['saving']['amount'] is None
        assert [field['name'] for field in row['input_fields']] == [
            item['name'] for item in next(x for x in registry['schemes'] if x['scheme_id'] == row['scheme_id'])['saving']['inputs']]
    text = repr(result).lower()
    assert 'is eligible' not in text and 'will save' not in text


def test_no_matching_scheme_is_explicit(registry):
    result = find_schemes(registry, get_company('forgeline-components'), {})
    assert result['matches'] == []
    assert result['no_match_reason'] == ('No scheme in registry carbon-schemes-v1 matches Mexico or this company\'s '
                                         'export routes. Absence here is not evidence that no scheme exists.')


def test_cbam_matches_only_covered_goods_exported_to_the_eu(registry):
    company = deepcopy(get_company('forgeline-components'))
    company['routes'].append({'route_id': 'r-eu', 'origin': 'Monterrey, Mexico', 'destination': 'Hamburg, Germany',
                              'via': [], 'materials': ['steel'], 'direction': 'export', 'currency': 'EUR'})
    result = find_schemes(registry, company, {'eu-cbam': {'tonnes_reduced': '120'}})
    [cbam] = result['matches']
    assert cbam['scheme_id'] == 'eu-cbam'
    assert cbam['matched_on'] == ['export route Monterrey, Mexico to Hamburg, Germany carries steel']
    # 120 t x EUR 82.32 = EUR 9878.4, using the dated official default price
    assert cbam['saving'] == {
        'status': 'calculated', 'amount': '9878.4', 'currency': 'EUR',
        'wording': 'approximate, based on these assumptions',
        'formula': 'embedded tonnes avoided x CBAM certificate price',
        'arithmetic': '120 tCO2e x 82.32 EUR per tCO2e = 9878.4 EUR',
        'inputs': [
            {'name': 'tonnes_reduced', 'value': '120', 'unit': 'tCO2e', 'source': 'entered by you'},
            {'name': 'price_per_tonne', 'value': '82.32', 'unit': 'EUR per tCO2e',
             'source': 'European Commission, Price of CBAM certificates, Q3 2026, published 2026-10-05'}]}
    non_covered = deepcopy(company)
    non_covered['routes'][-1]['materials'] = ['bearings']
    assert find_schemes(registry, non_covered, {})['matches'] == []


def test_formula_arithmetic_for_subsidy_loan_and_credits(registry):
    inputs = {'in-msme-zed': {'enterprise_category': 'small', 'certification_cost': '250000'},
              'in-sidbi-green-finance': {'loan_amount': '20000000', 'current_rate_percent': '10.5', 'green_rate_percent': '9.25'},
              'in-ccts-offset': {'tonnes_reduced': '40', 'price_per_tonne': '1500'}}
    rows = {row['scheme_id']: row['saving'] for row in find_schemes(registry, get_company('terrapack'), inputs)['matches']}
    assert rows['in-msme-zed']['amount'] == '150000'
    assert rows['in-msme-zed']['arithmetic'] == '250000 INR x 60% (small) = 150000 INR'
    assert rows['in-sidbi-green-finance']['amount'] == '250000'
    assert rows['in-sidbi-green-finance']['arithmetic'] == '20000000 INR x (10.5% - 9.25%) = 250000 INR per year'
    assert rows['in-ccts-offset']['amount'] == '60000'


def test_missing_and_invalid_inputs(registry):
    partial = find_schemes(registry, get_company('terrapack'), {'in-msme-zed': {'certification_cost': '1000'}})
    zed = next(row for row in partial['matches'] if row['scheme_id'] == 'in-msme-zed')
    assert zed['saving']['status'] == 'missing_inputs'
    assert zed['saving']['missing'] == ['enterprise_category']
    for bad in ({'in-msme-zed': {'enterprise_category': 'large', 'certification_cost': '1'}},
                {'in-msme-zed': {'certification_cost': '-1'}},
                {'in-msme-zed': {'certification_cost': 5}},
                {'in-msme-zed': {'unknown': '1'}},
                {'not-a-scheme': {}}):
        with pytest.raises(SchemeInputError):
            find_schemes(registry, get_company('terrapack'), bad)


def test_a_loan_rate_higher_than_today_is_shown_as_extra_cost_not_saving(registry):
    rows = find_schemes(registry, get_company('terrapack'), {'in-sidbi-green-finance': {
        'loan_amount': '1000000', 'current_rate_percent': '9', 'green_rate_percent': '10'}})['matches']
    loan = next(row for row in rows if row['scheme_id'] == 'in-sidbi-green-finance')['saving']
    assert loan['amount'] == '-10000'
    assert loan['status'] == 'calculated'
