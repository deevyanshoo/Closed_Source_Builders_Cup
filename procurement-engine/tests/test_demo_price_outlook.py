"""Price and FX outlook API: read-only, company-scoped, sourced, labelled and stale-aware."""
from datetime import date
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from procure.demo.price_outlook import (LABEL, STALE_AFTER_DAYS, get_fx_outlook, get_price_outlook,
                                        outlook_export, price_outlook_router)

TODAY = date(2026, 10, 16)


def test_price_outlook_shape_source_and_labels():
    view = get_price_outlook('solara-cables', 'copper', now=TODAY, context_version=4)
    assert (view['company_id'], view['context_version'], view['material_id']) == ('solara-cables', 4, 'copper')
    assert view['status'] == 'available' and view['match'] == 'exact'
    assert view['label'] == LABEL == 'Projection with uncertainty, not a recommendation'
    series = view['series']
    assert series['series_id'] == 'commodity:copper' and series['data_date'] == '2026-08'
    assert series['issue_month'] == '2026-09' and series['freshness_state'] == 'HISTORICAL_NOT_CURRENT'
    assert series['source']['publisher'] == 'World Bank' and 'CC BY 4.0' in series['source']['licence']
    assert [h['horizon'] for h in series['horizons']] == [1, 3, 6]
    for h in series['horizons']:
        assert h['status'] in {'PREDICTION', 'RANGE_ONLY', 'NO_RELIABLE_FORECAST'}
        assert set(h['backtest']) >= {'mape', 'naive_mape', 'coverage', 'naive_coverage', 'skill', 'n_origins'}
    text = repr(view).lower()
    assert 'buy' not in text and 'wait' not in text


def test_stale_label_after_45_days():
    assert STALE_AFTER_DAYS == 45
    fresh = get_price_outlook('solara-cables', 'copper', now=date(2026, 10, 15))['series']
    stale = get_price_outlook('solara-cables', 'copper', now=date(2026, 10, 16))['series']
    assert (fresh['age_days'], fresh['stale']) == (45, False)  # data month ends 31 August
    assert (stale['age_days'], stale['stale']) == (46, True)
    assert stale['stale_label'] == 'Data is older than 45 days: latest month 2026-08.'
    assert fresh['stale_label'] is None


def test_company_isolation_and_missing_series():
    assert get_price_outlook('terrapack', 'copper', now=TODAY)['status'] == 'unknown_material'
    assert get_price_outlook('nobody', 'copper', now=TODAY)['status'] == 'unknown_company'
    polymer = get_price_outlook('solara-cables', 'polymer', now=TODAY)
    assert polymer['status'] == 'no_series'
    assert polymer['message'] == 'No public monthly benchmark series is mapped to this material.'
    rod = get_price_outlook('forgeline-components', 'copper', now=TODAY)
    assert rod['match'] == 'proxy' and 'rod premium' in rod['note']


def test_fx_outlook_covers_every_company_currency_and_says_when_history_is_missing():
    solara = get_fx_outlook('solara-cables', now=TODAY, context_version=2)
    assert solara['company_id'] == 'solara-cables' and solara['reporting_currency'] == 'USD'
    assert [c['currency'] for c in solara['currencies']] == ['AED', 'EUR']
    assert all(c['status'] == 'available' and c['series']['unit'] == f"{c['currency']} per USD" for c in solara['currencies'])
    harvest = {c['currency']: c for c in get_fx_outlook('harvest-foods', now=TODAY)['currencies']}
    assert harvest['TZS']['status'] == 'no history available' and 'not interpolated' in harvest['TZS']['reason']
    assert harvest['KES']['status'] == 'available'
    assert get_fx_outlook('nobody', now=TODAY)['status'] == 'unknown_company'


def test_export_helper_keeps_scope_labels_and_sources():
    price = get_price_outlook('solara-cables', 'copper', now=TODAY, context_version=4)
    fx = get_fx_outlook('solara-cables', now=TODAY, context_version=4)
    exported = outlook_export(price, fx)
    assert exported['schema'] == 'price-outlook-export.v1'
    assert exported['company_id'] == 'solara-cables' and exported['context_version'] == 4
    assert exported['label'] == LABEL and exported['price']['series']['source']['publisher'] == 'World Bank'
    assert exported['fx']['currencies'][0]['currency'] == 'AED'
    try:
        outlook_export(price, get_fx_outlook('terrapack', now=TODAY, context_version=4))
    except ValueError as error:
        assert 'scope' in str(error)
    else:
        raise AssertionError('mixed-company export accepted')


def test_router_answers_for_the_current_session_company_a_to_b_to_a():
    state = SimpleNamespace(company_id='solara-cables', context_version=1)
    app = FastAPI()

    def session(request: Request):
        return state
    app.include_router(price_outlook_router(session=session, now=lambda: TODAY))
    with TestClient(app) as client:
        first = client.get('/api/demo/price-outlook', params={'material_id': 'copper'}).json()
        state.company_id, state.context_version = 'harvest-foods', 2
        second = client.get('/api/demo/price-outlook', params={'material_id': 'copper'}).json()
        state.company_id, state.context_version = 'solara-cables', 3
        third = client.get('/api/demo/price-outlook', params={'material_id': 'copper'}).json()
        fx = client.get('/api/demo/fx-outlook').json()
    assert (first['company_id'], first['context_version'], first['status']) == ('solara-cables', 1, 'available')
    assert (second['company_id'], second['context_version'], second['status']) == ('harvest-foods', 2, 'unknown_material')
    assert (third['company_id'], third['context_version']) == ('solara-cables', 3)
    assert (fx['company_id'], fx['context_version']) == ('solara-cables', 3)


def test_deploy_package_ships_the_outlook_results(tmp_path):
    import json
    import subprocess
    import sys
    from pathlib import Path
    repo = Path(__file__).resolve().parents[2]
    destination = tmp_path / 'package'
    prepared = subprocess.run([sys.executable, str(repo / 'deploy/judge-demo/prepare_package.py'), str(destination)],
                              capture_output=True, text=True)
    assert prepared.returncode == 0, prepared.stderr
    packaged = destination / 'procurement-engine/config/prediction_results_v1.json'
    assert json.loads(packaged.read_text())['results_version'] == 'prediction-results-v1'
    requirements = (repo / 'deploy/judge-demo/requirements.txt').read_text()
    assert 'statsmodels' not in requirements and 'scikit-learn' not in requirements  # runtime reads JSON only
