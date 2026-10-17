"""Export market ranking from observed trade: missing is not zero, unit values suppressed when unsafe (#131)."""
from decimal import Decimal

import pytest

from procure.exports.ranking import LABEL, SORTS, rank_markets

HOME = 699


def row(reporter, partner, year, value, flow='M', wgt=None, est=False, unit=8, cif=True, name=None, pname=None):
    return {'period': year, 'reporter_code': reporter, 'reporter_name': name or f'R{reporter}', 'flow': flow,
            'partner_code': partner, 'partner_name': pname or ('World' if partner == 0 else f'P{partner}'),
            'cmd_code': '8544', 'classification_code': 'H6', 'is_original_classification': True,
            'primary_value': value, 'cif_value': value if cif else None, 'fob_value': None if cif else value,
            'net_wgt': wgt, 'is_net_wgt_estimated': est, 'qty': wgt, 'qty_unit_code': unit, 'qty_unit_abbr': 'kg',
            'is_qty_estimated': est, 'is_reported': True, 'is_aggregate': True, 'legacy_estimation_flag': 0}


def series(role, year, rows, flow='M'):
    return {'role': role, 'period': year, 'flow': flow, 'rows': rows}


def company(market_rows_by_year, export_rows_by_year=None, candidates=(100, 200, 300)):
    return {'company_id': 'solara-cables', 'home_reporter_code': HOME, 'hs_heading': '8544',
            'candidate_codes': list(candidates), 'existing_export_codes': [300], 'candidate_discovery': 'home_exports',
            'home_exports': [series('home_exports', y, rows, 'X') for y, rows in (export_rows_by_year or {}).items()],
            'mirror_home_exports': [],
            'market_imports': [series('market_imports', y, rows) for y, rows in market_rows_by_year.items()]}


PRODUCT = {'company_id': 'solara-cables', 'product': 'Cable', 'hs_heading': '8544', 'hs_description': 'Insulated wire',
           'home_country': 'India', 'note': 'illustrative mapping for a fictional company'}

# Market 100 "Alpha": reports 2020, 2022, 2024 (2021 and 2023 not reported, never zero).
# Market 200 "Beta": reports only 2023 and 2024, with an estimated weight.
# Market 300 "Gamma": never reports imports; India's exports to it give a mirror value.
MARKETS = {
    2020: [row(100, 0, 2020, 1000.0, wgt=100.0, name='Alpha'), row(100, HOME, 2020, 100.0, name='Alpha')],
    2022: [row(100, 0, 2022, 1440.0, wgt=150.0, name='Alpha'), row(100, HOME, 2022, 200.0, name='Alpha')],
    2023: [row(200, 0, 2023, 5000.0, wgt=400.0, est=True, name='Beta')],
    2024: [row(100, 0, 2024, 2000.0, wgt=250.0, name='Alpha'), row(100, HOME, 2024, 500.0, name='Alpha'),
           row(200, 0, 2024, 3000.0, wgt=300.0, est=True, name='Beta')],
}
EXPORTS = {
    2024: [row(HOME, 0, 2024, 9000.0, flow='X', name='India'), row(HOME, 300, 2024, 700.0, flow='X', name='India', pname='Gamma'),
           row(HOME, 200, 2024, 640.0, flow='X', name='India', pname='Beta'), row(HOME, 100, 2024, 450.0, flow='X', name='India', pname='Alpha')],
}


def by_code(result):
    return {m['code']: m for m in result['markets']}


def test_label_and_default_sort_by_market_size():
    result = rank_markets(company(MARKETS, EXPORTS), PRODUCT)
    assert result['label'] == LABEL == 'observed historical trade, not buyer demand, a confirmed buyer or a price quote'
    assert result['sort'] == 'market_size' and SORTS == ('market_size', 'growth', 'home_share', 'margin')
    assert [m['name'] for m in result['markets']] == ['Beta', 'Alpha', 'Gamma']
    assert result['product']['note'] == 'illustrative mapping for a fictional company'


def test_missing_is_not_zero_and_mirror_is_labelled():
    markets = by_code(rank_markets(company(MARKETS, EXPORTS), PRODUCT))
    alpha, gamma = markets[100], markets[300]
    assert alpha['years_reported'] == [2020, 2022, 2024]
    assert alpha['years_not_reported'] == [2021, 2023]
    assert gamma['import_value'] == {'status': 'not reported', 'reason': 'Gamma did not report imports of this heading in 2020 to 2024.'}
    assert gamma['mirror'] == {'value_usd': '700.0', 'year': 2024, 'label': 'mirror (reported by exporter)',
                               'meaning': 'India reported exports to Gamma'}
    assert '0' not in (gamma['import_value'].get('value_usd'),)
    assert gamma['existing_export_market'] is True and alpha['existing_export_market'] is False


def test_growth_needs_three_reported_years_and_uses_the_hand_value():
    markets = by_code(rank_markets(company(MARKETS, EXPORTS), PRODUCT))
    # Alpha: 1000 (2020) to 2000 (2024): (2000 / 1000) ** (1 / 4) - 1 = 0.189207115...
    growth = markets[100]['growth']
    assert growth['status'] == 'available' and growth['from_year'] == 2020 and growth['to_year'] == 2024
    assert Decimal(growth['cagr']).quantize(Decimal('0.000001')) == Decimal('0.189207')
    assert markets[200]['growth'] == {'status': 'growth unavailable', 'reason': 'needs at least 3 reported years; 2 reported'}


def test_home_share_from_importer_data_and_mirror_fallback():
    markets = by_code(rank_markets(company(MARKETS, EXPORTS), PRODUCT))
    assert markets[100]['home_share'] == {'status': 'available', 'share': '0.25', 'year': 2024,
                                          'basis': 'reported by importer'}
    beta = markets[200]['home_share']  # Beta reports no imports from India: India's exports to it are a mirror
    assert beta['status'] == 'available' and beta['basis'] == 'mirror (reported by exporter)'
    assert Decimal(beta['share']) == Decimal('640') / Decimal('3000')
    assert markets[300]['home_share']['status'] == 'unavailable'


def test_unit_value_only_from_reported_single_unit_weight():
    markets = by_code(rank_markets(company(MARKETS, EXPORTS), PRODUCT))
    assert markets[100]['unit_value'] == {'status': 'available', 'usd_per_kg': '8', 'year': 2024, 'basis': 'CIF',
                                          'net_weight_kg': '250.0', 'label': 'historical customs unit value'}
    assert markets[200]['unit_value']['status'] == 'unit value suppressed'
    assert 'estimated' in markets[200]['unit_value']['reason']
    no_weight = {2024: [row(100, 0, 2024, 2000.0, wgt=None, name='Alpha')]}
    assert 'not reported' in by_code(rank_markets(company(no_weight), PRODUCT))[100]['unit_value']['reason']
    mixed = {2024: [row(100, 0, 2024, 1000.0, wgt=100.0, unit=8, name='Alpha'), row(100, 0, 2024, 1000.0, wgt=50.0, unit=5, name='Alpha')]}
    assert 'mixed' in by_code(rank_markets(company(mixed), PRODUCT))[100]['unit_value']['reason']
    fob = {2024: [row(100, 0, 2024, 900.0, wgt=100.0, cif=False, name='Alpha')]}
    assert by_code(rank_markets(company(fob), PRODUCT))[100]['unit_value']['basis'] == 'FOB'


def test_each_sort_is_deterministic_with_name_tie_break():
    tie = {2024: [row(100, 0, 2024, 500.0, wgt=10.0, name='Zeta'), row(200, 0, 2024, 500.0, wgt=10.0, name='Eta')]}
    assert [m['name'] for m in rank_markets(company(tie, candidates=(100, 200)), PRODUCT)['markets']] == ['Eta', 'Zeta']
    data = company(MARKETS, EXPORTS)
    assert [m['name'] for m in rank_markets(data, PRODUCT, sort='growth')['markets']] == ['Alpha', 'Beta', 'Gamma']
    assert [m['name'] for m in rank_markets(data, PRODUCT, sort='home_share')['markets']] == ['Alpha', 'Beta', 'Gamma']
    margins = {100: {'status': 'available', 'contribution': '10'}, 200: {'status': 'available', 'contribution': '50'}}
    assert [m['name'] for m in rank_markets(data, PRODUCT, sort='margin', margins=margins)['markets']] == ['Beta', 'Alpha', 'Gamma']
    with pytest.raises(ValueError):
        rank_markets(data, PRODUCT, sort='score')
