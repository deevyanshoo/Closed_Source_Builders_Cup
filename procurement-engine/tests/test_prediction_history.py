"""Price prediction history snapshot: unmodified public monthly series with provenance and hashes."""
import hashlib
import json
from pathlib import Path

import pytest

from procure.prediction.history import load_history, series_values_sha256

SNAPSHOT = Path(__file__).resolve().parents[1] / 'config' / 'prediction_history_v1.json'
FX_AVAILABLE = {'AED', 'BDT', 'BWP', 'CHF', 'CNY', 'EUR', 'INR', 'JPY', 'KES', 'KRW', 'MXN', 'MYR', 'NAD', 'OMR', 'SGD',
                'ZAR'}
FX_UNAVAILABLE = {'TZS'}


@pytest.fixture(scope='module')
def history():
    return load_history(SNAPSHOT)


def _months(series):
    return [row[0] for row in series['observations']]


def test_every_series_has_source_licence_retrieval_and_hashes(history):
    for series_id, series in history['series'].items():
        if series['status'] != 'available':
            continue
        source = history['sources'][series['source_id']]
        assert source['url'].startswith('https://'), series_id
        assert source['publisher'] and source['licence'] and source['retrieved_at'], series_id
        assert len(source['file_sha256']) == 64, series_id
        assert series['source_series'] and series['unit'], series_id
        assert series['values_sha256'] == series_values_sha256(series['observations']), series_id


def test_available_series_are_contiguous_complete_months_without_gaps(history):
    for series_id, series in history['series'].items():
        if series['status'] != 'available':
            continue
        months = _months(series)
        assert len(months) == 120, series_id  # the catalog's fixed window
        for previous, current in zip(months, months[1:]):
            y, m = map(int, previous.split('-'))
            assert current == f'{y + (m == 12):04d}-{m % 12 + 1:02d}', (series_id, previous, current)
        assert all(float(value) > 0 for _, value in series['observations']), series_id
        # month m is usable only from the end of m+1, so on 16 October the last usable month is August
        assert months[0] == '2016-09' and months[-1] == '2026-08', series_id


def test_fx_covers_every_company_currency_and_never_fills_missing_history(history):
    fx = {sid.split(':', 1)[1]: s for sid, s in history['series'].items() if sid.startswith('fx:')}
    assert set(fx) == FX_AVAILABLE | FX_UNAVAILABLE
    for code in FX_AVAILABLE:
        assert fx[code]['status'] == 'available', code
    for code in FX_UNAVAILABLE:
        assert fx[code] == {'status': 'no history available', 'currency': code, 'quote': f'{code} per USD',
                            'reason': fx[code]['reason']}
        assert 'not interpolated' in fx[code]['reason'] and 'incomplete_or_invalid_required_window' in fx[code]['reason']
    # BIS publishes national currency per US dollar; the euro uses the catalog's DE representative area
    assert fx['EUR']['quote'] == 'EUR per USD' and fx['EUR']['source_series'] == 'WS_XRU M.DE.EUR.A'
    assert fx['INR']['source_id'] == 'bis-ws-xru-monthly-average'


def test_commodity_series_and_material_map_label_proxies(history):
    commodities = {sid: s for sid, s in history['series'].items() if sid.startswith('commodity:')}
    assert set(commodities) == {'commodity:copper', 'commodity:aluminium', 'commodity:wheat', 'commodity:sugar',
                                'commodity:cotton', 'commodity:sunflower_oil', 'commodity:nickel'}
    assert commodities['commodity:copper']['source_series'] == 'Copper'
    assert history['sources']['world-bank-pink-sheet']['licence'] == 'Creative Commons Attribution 4.0 (CC BY 4.0)'
    assert history['parser'].startswith('procure/evaluation/series_catalog.py')
    assert history['availability_policy'].startswith('month_m_available_at_end_of_m_plus_1')
    matches = {(row['company_id'], row['material_id']): row for row in history['material_map']}
    assert matches[('solara-cables', 'copper')]['match'] == 'exact'
    assert matches[('forgeline-components', 'copper')]['match'] == 'proxy'
    assert matches[('gridspring-batteries', 'nickel')]['match'] == 'proxy'
    assert all(row['note'] for row in history['material_map'] if row['match'] == 'proxy')


def test_snapshot_file_is_what_the_loader_returns():
    raw = json.loads(SNAPSHOT.read_text(encoding='utf-8'))
    assert raw == load_history(SNAPSHOT)
    assert hashlib.sha256(SNAPSHOT.read_bytes()).hexdigest() == load_history(SNAPSHOT, with_digest=True)['_sha256']
