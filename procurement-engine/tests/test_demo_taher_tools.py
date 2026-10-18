"""Read-only bot tools for the price outlook, carbon schemes and country pricing (text operator and voice)."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from procure.demo.company import get_company
from procure.demo.taher_tools import RANGES_ONLY, TAHER_TOOLS, taher_tool_read, taher_tool_request

NOW = datetime(2026, 10, 18, tzinfo=timezone.utc)


def session(company_id='solara-cables', context_version=1, schemes=None, offers=None):
    return SimpleNamespace(company_id=company_id, context_version=context_version, carbon_schemes=schemes,
                           offers=offers or {}, news=None)


def test_tool_names():
    assert TAHER_TOOLS == ('read_price_outlook', 'read_carbon_schemes', 'read_country_pricing')
    with pytest.raises(ValueError):
        taher_tool_read(session(), 'read_everything', {}, now=NOW)


def test_price_outlook_reads_the_committed_ranges_and_says_ranges_only():
    out = taher_tool_read(session(), 'read_price_outlook', {'material_id': 'copper'}, now=NOW)
    assert out['status'] == 'completed'
    series = out['workspace']['price_outlook']['series']
    copper = next(row for row in series if row['series_id'] == 'commodity:copper')
    assert copper['as_of'] == '2026-08' and copper['source'] == 'World Bank'
    one = copper['horizons'][0]
    assert (one['horizon'], one['verdict'], one['p10'], one['p90']) == (1, 'RANGE_ONLY', 13220.29766, 15561.8455)
    assert copper['horizons'][2]['verdict'] == 'NO_RELIABLE_FORECAST' and copper['horizons'][2]['p10'] is None
    assert RANGE_ONLY_TEXT in out['assistant_text']
    assert 'Copper' in out['assistant_text'] and '13,220 to 15,562 USD per mt' in out['assistant_text']
    assert 'prediction_results_v1:commodity:copper' in out['evidence_ids']


RANGE_ONLY_TEXT = 'No model beat the naive baseline in backtest, so these are ranges, not predictions.'


def test_price_outlook_covers_all_mapped_materials_and_currencies_without_a_material():
    assert RANGES_ONLY == RANGE_ONLY_TEXT
    out = taher_tool_read(session(), 'read_price_outlook', {}, now=NOW)
    ids = {row['series_id'] for row in out['workspace']['price_outlook']['series']}
    assert {'commodity:copper', 'commodity:aluminium', 'fx:AED', 'fx:EUR'} <= ids
    polymer = taher_tool_read(session(), 'read_price_outlook', {'material_id': 'polymer'}, now=NOW)
    assert polymer['status'] == 'completed' and 'No public monthly benchmark' in polymer['assistant_text']


def test_carbon_schemes_without_inputs_lists_catalogue_and_needed_inputs_but_no_figure():
    out = taher_tool_read(session(), 'read_carbon_schemes', {}, now=NOW)
    rows = out['workspace']['carbon_schemes']['schemes']
    assert {row['scheme_id'] for row in rows} >= {'in-sidbi-green-finance', 'in-msme-zed'}
    assert all(row['benefit'] == 'not stated' for row in rows)
    assert all(row['official_url'].startswith('https://') and row['checked_on'] for row in rows)
    assert 'loan_amount' in out['assistant_text'] and 'possibly eligible, check conditions' in out['assistant_text']
    assert out['workspace']['carbon_schemes']['basis'] == 'catalogue'


def stored_sidbi(company_id='solara-cables', context_version=1):
    from procure.carbon.schemes import find_schemes, load_schemes
    result = find_schemes(load_schemes(), get_company(company_id), {'in-sidbi-green-finance': {
        'loan_amount': '1000000', 'current_rate_percent': '10', 'offered_rate_percent': '9'}})
    result['context_version'] = context_version
    return {'company_id': company_id, 'context_version': context_version, 'result': result}


def test_carbon_schemes_use_the_stored_result_and_cite_every_figure():
    out = taher_tool_read(session(schemes=stored_sidbi()), 'read_carbon_schemes', {}, now=NOW)
    rows = {row['scheme_id']: row for row in out['workspace']['carbon_schemes']['schemes']}
    sidbi = rows['in-sidbi-green-finance']
    assert sidbi['benefit']['amount'] == '10000' and sidbi['benefit']['currency'] == 'INR'
    assert sidbi['benefit']['source'] == {'official_url': sidbi['official_url'], 'checked_on': sidbi['checked_on']}
    for row in rows.values():
        assert row['benefit'] == 'not stated' or (row['benefit']['source']['official_url'] and row['benefit']['source']['checked_on'])
    assert '10000 INR' in out['assistant_text'] and out['workspace']['carbon_schemes']['basis'] == 'your entered inputs'


def test_tools_ignore_another_company_or_an_older_context():
    for stored in (stored_sidbi('terrapack'), stored_sidbi(context_version=1)):
        out = taher_tool_read(session(context_version=3, schemes=stored), 'read_carbon_schemes', {}, now=NOW)
        assert out['workspace']['carbon_schemes']['basis'] == 'catalogue'
        assert '10000' not in out['assistant_text']
    harvest = taher_tool_read(session('harvest-foods', 2, schemes=stored_sidbi()), 'read_carbon_schemes', {}, now=NOW)
    assert all(row['country'] != 'India' for row in harvest['workspace']['carbon_schemes']['schemes'])
    assert harvest['workspace']['carbon_schemes']['company_id'] == 'harvest-foods'


def test_country_pricing_reads_benchmarks_and_labels_quotes_synthetic():
    offer = {'offer_id': 'o1', 'status': 'saved', 'items': [], 'supplier': {'name': 'Swift'}}
    out = taher_tool_read(session(offers={'o1': offer}), 'read_country_pricing', {}, now=NOW)
    view = out['workspace']['country_pricing']
    assert view['reporting_currency'] == 'USD' and view['company_id'] == 'solara-cables'
    copper = next(row for row in view['benchmarks'] if row['material_id'] == 'copper')
    assert (copper['month'], copper['value'], copper['stale']) == ('2026-09', '14474', False)
    assert 'no public benchmark' in out['assistant_text'].lower() and 'Cable insulation polymer' in out['assistant_text']
    assert 'Demo quotes are synthetic' in out['assistant_text']
    assert '14474 USD per tonne' in out['assistant_text']


@pytest.mark.parametrize('message,tool', [
    ('What is the price outlook for copper?', 'read_price_outlook'),
    ('Show me the copper price forecast and price trend', 'read_price_outlook'),
    ('Which carbon schemes could we use?', 'read_carbon_schemes'),
    ('Any green finance or CBAM exposure for us?', 'read_carbon_schemes'),
    ('What is the benchmark price for copper?', 'read_country_pricing'),
    ('Compare country prices for our materials', 'read_country_pricing'),
    ('Draft an RFQ for the selected supplier', None),
    ('What is our stock cover for copper?', None),
])
def test_text_router(message, tool):
    assert taher_tool_request(message) == tool
