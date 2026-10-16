"""Programme discovery with gated benefit calculations (schemes review, 16 Oct)."""
from copy import deepcopy

import pytest

from procure.carbon.schemes import SchemeInputError, find_schemes, load_schemes
from procure.demo.company import get_company, list_companies


@pytest.fixture(scope='module')
def registry():
    return load_schemes()


def by_id(result):
    return {row['scheme_id']: row for row in result['matches']}


def test_every_scheme_has_an_official_url_date_and_benefit_status(registry):
    assert registry['label'] == 'indicative, not financial or legal advice'
    for scheme in registry['schemes']:
        assert scheme['official_url'].startswith('https://'), scheme['scheme_id']
        assert scheme['checked_on'] in {'2026-10-15', '2026-10-16'}
        assert scheme['benefit']['status'] in {'available', 'unavailable'}
        if scheme['benefit']['status'] == 'unavailable':
            assert scheme['benefit']['reason'], scheme['scheme_id']
        assert scheme['who'] and scheme['offers_text'] and scheme['numbers_used']


def test_each_company_gets_the_schemes_for_its_countries(registry):
    expected = {
        'solara-cables': {'in-ccts-offset', 'in-msme-zed', 'in-sidbi-green-finance'},
        'terrapack': {'in-ccts-offset', 'in-msme-zed', 'in-sidbi-green-finance'},
    }
    for summary in list_companies():
        result = find_schemes(registry, get_company(summary['company_id']), {})
        assert set(by_id(result)) == expected.get(summary['company_id'], set()), summary['company_id']
        if not result['matches']:
            assert 'not evidence that no scheme exists' in result['no_match_reason']


def test_unsupported_numbers_are_disabled_in_the_backend(registry):
    zed = by_id(find_schemes(registry, get_company('terrapack'), {}))['in-msme-zed']
    assert zed['benefit_status'] == 'unavailable' and zed['benefit'] is None
    assert 'fee schedule' in zed['benefit_reason']
    with pytest.raises(SchemeInputError, match='unavailable'):
        find_schemes(registry, get_company('terrapack'), {'in-msme-zed': {'certification_cost': '250000'}})


def test_zed_women_owned_rule_follows_the_free_certification_notice(registry):
    zed = next(s for s in registry['schemes'] if s['scheme_id'] == 'in-msme-zed')
    text = ' '.join(zed['numbers_used'])
    assert 'Women-owned MSMEs: free ZED certification' in text
    assert 'additional 10%' in text and 'SC/ST' in text and 'women' not in text.split('additional 10%')[1].split('.')[0].lower()
    assert 'joining reward of Rs 10,000' in text


def test_credit_value_needs_an_issued_credit_count_and_is_hypothetical_gross(registry):
    rows = by_id(find_schemes(registry, get_company('terrapack'), {}))
    offset = rows['in-ccts-offset']
    assert offset['benefit_status'] == 'available' and offset['missing_inputs'] == ['issued_certificates', 'price_per_certificate']
    with pytest.raises(SchemeInputError):
        find_schemes(registry, get_company('terrapack'), {'in-ccts-offset': {'tonnes_reduced': '40'}})
    value = by_id(find_schemes(registry, get_company('terrapack'), {'in-ccts-offset': {
        'issued_certificates': '40', 'price_per_certificate': '1500'}}))['in-ccts-offset']['benefit']
    assert value['amount'] == '60000' and value['currency'] == 'INR'
    assert value['label'] == 'hypothetical gross certificate value, not issued credit or a sale'


def test_sidbi_interest_difference_only_from_an_entered_offered_rate(registry):
    rows = by_id(find_schemes(registry, get_company('terrapack'), {}))
    sidbi = rows['in-sidbi-green-finance']
    assert sidbi['offers'] == 'green_financing'
    assert sidbi['offers_text'].startswith('Green project financing may be available')
    assert sidbi['missing_inputs'] == ['loan_amount', 'current_rate_percent', 'offered_rate_percent']

    def diff(current, offered, amount='20000000'):
        return by_id(find_schemes(registry, get_company('terrapack'), {'in-sidbi-green-finance': {
            'loan_amount': amount, 'current_rate_percent': current, 'offered_rate_percent': offered}}))['in-sidbi-green-finance']['benefit']
    lower = diff('10.5', '9.25')
    assert (lower['amount'], lower['direction']) == ('250000', 'interest_reduction')
    assert lower['label'] == 'first-year simple interest difference, not an offer'
    assert diff('9', '10')['direction'] == 'extra_cost' and diff('9', '10')['amount'] == '-200000'
    assert diff('9', '9')['direction'] == 'no_difference' and diff('9', '9')['amount'] == '0'
    with pytest.raises(SchemeInputError, match='50 crore'):
        diff('20', '1', amount='600000000')
    with pytest.raises(SchemeInputError):
        diff('101', '1')


def test_cbam_is_compliance_exposure_with_a_quarter_bound_gross_sensitivity(registry):
    company = deepcopy(get_company('forgeline-components'))
    company['routes'].append({'route_id': 'r-eu', 'origin': 'Monterrey, Mexico', 'destination': 'Hamburg, Germany',
                              'via': [], 'materials': ['steel'], 'direction': 'export', 'currency': 'EUR'})
    cbam = by_id(find_schemes(registry, company, {}))['eu-cbam']
    assert cbam['offers'] == 'compliance_exposure'
    assert cbam['missing_inputs'] == ['import_quarter', 'embedded_tonnes']
    value = by_id(find_schemes(registry, company, {'eu-cbam': {'import_quarter': '2026-Q3', 'embedded_tonnes': '120'}}))['eu-cbam']['benefit']
    # 120 t x EUR 82.32 (Q3 2026 price, published 5 October 2026) = EUR 9878.4
    assert (value['amount'], value['currency']) == ('9878.4', 'EUR')
    assert value['label'].startswith('gross sensitivity for imports in 2026-Q3')
    assert 'not a buyer saving' in value['label']
    with pytest.raises(SchemeInputError, match='published'):
        find_schemes(registry, company, {'eu-cbam': {'import_quarter': '2026-Q4', 'embedded_tonnes': '1'}})
    non_covered = deepcopy(company)
    non_covered['routes'][-1]['materials'] = ['bearings']
    assert 'eu-cbam' not in by_id(find_schemes(registry, non_covered, {}))


def test_no_output_says_saving_eligible_or_tradeable(registry):
    for summary in list_companies():
        text = repr(find_schemes(registry, get_company(summary['company_id']), {})).lower()
        assert 'saving' not in text.replace('not a saving', '').replace('not a buyer saving', '')
        assert 'is eligible' not in text and 'tradeable credit' not in text and 'cheaper loan' not in text
