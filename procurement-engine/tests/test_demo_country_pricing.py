from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from procure.demo import quote_service
from procure.demo.app import create_app
from procure.demo.company import get_company
from procure.demo.country_pricing import build_country_pricing, load_benchmarks

TODAY = date(2026, 10, 15)
FX = [{'base': 'EUR', 'quote': 'USD', 'rate': '1.25', 'observation_date': '2026-10-14', 'provider': 'ECB',
       'retrieved_at': '2026-10-15T08:00:00Z', 'informational': True},
      {'base': 'EUR', 'quote': 'INR', 'rate': '105', 'observation_date': '2026-10-14', 'provider': 'ECB',
       'retrieved_at': '2026-10-15T08:00:00Z', 'informational': True}]


@pytest.fixture(scope='module')
def snapshot():
    return load_benchmarks()


def saved_offer(fixture_id):
    extracted = quote_service.extract_fixture(fixture_id, mode='rules', as_of=date(2026, 10, 14))
    offer = quote_service.new_offer(extracted, offer_id=fixture_id, today=date(2026, 10, 14))
    for field, value in quote_service.fixture_review_values(fixture_id).items():
        offer = quote_service.correct_offer(offer, field=field, value=value, today=date(2026, 10, 14))
    return quote_service.save_offer(offer, today=date(2026, 10, 14))


def test_snapshot_values_are_unmodified_world_bank_figures_with_licence(snapshot):
    assert snapshot['source']['licence'] == 'Creative Commons Attribution 4.0 (CC BY 4.0)'
    assert snapshot['synthetic'] is False
    copper = snapshot['benchmarks']['copper']
    assert copper['series'] == 'Copper' and copper['observations'][-1] == {'month': '2026-09', 'value': '14474'}


def test_benchmark_is_converted_at_view_time_with_a_dated_cross_rate(snapshot):
    view = build_country_pricing(get_company('solara-cables'), [], FX, snapshot, today=TODAY)
    copper = next(row for row in view['benchmarks'] if row['material_id'] == 'copper')
    assert copper['status'] == 'benchmark'
    assert (copper['series'], copper['month'], copper['value'], copper['currency'], copper['per_unit']) == (
        'Copper', '2026-09', '14474', 'USD', 'tonne')
    assert copper['per_company_unit'] == {'value': '14.474', 'unit': 'kg', 'formula': '14474 USD per tonne / 1000 kg per tonne'}
    assert [c['currency'] for c in copper['conversions']] == ['USD', 'AED', 'EUR']
    eur = next(c for c in copper['conversions'] if c['currency'] == 'EUR')
    # USD->EUR = 1 / (EUR->USD 1.25) = 0.8; 14474 x 0.8 = 11579.2
    assert eur == {'currency': 'EUR', 'status': 'converted', 'rate': '0.8', 'amount': '11579.2', 'per_unit': 'tonne',
                   'observation_date': '2026-10-14', 'provider': 'ECB',
                   'formula': 'USD->EUR = 1 / EUR->USD 1.25 (ECB reference rate, 2026-10-14)'}
    aed = next(c for c in copper['conversions'] if c['currency'] == 'AED')
    assert aed['status'] == 'fx_unavailable' and aed['amount'] is None
    usd = next(c for c in copper['conversions'] if c['currency'] == 'USD')
    assert usd['status'] == 'same_currency' and usd['amount'] == '14474'
    assert copper['stale'] is False


def test_missing_fx_is_explicit_never_estimated(snapshot):
    view = build_country_pricing(get_company('solara-cables'), [], [], snapshot, today=TODAY)
    copper = next(row for row in view['benchmarks'] if row['material_id'] == 'copper')
    eur = next(c for c in copper['conversions'] if c['currency'] == 'EUR')
    assert eur == {'currency': 'EUR', 'status': 'fx_unavailable', 'rate': None, 'amount': None, 'per_unit': 'tonne',
                   'observation_date': None, 'provider': None, 'formula': None}
    assert 'No dated reference FX rate is loaded' in ' '.join(view['limitations'])


def test_materials_without_a_public_benchmark_say_so(snapshot):
    view = build_country_pricing(get_company('solara-cables'), [], FX, snapshot, today=TODAY)
    polymer = next(row for row in view['benchmarks'] if row['material_id'] == 'polymer')
    assert polymer == {'material_id': 'polymer', 'name': 'Cable insulation polymer', 'unit': 'kg',
                       'status': 'no_public_benchmark', 'message': 'no public benchmark'}


def test_old_benchmark_month_is_labelled_stale(snapshot):
    view = build_country_pricing(get_company('solara-cables'), [], FX, snapshot, today=date(2027, 3, 1))
    copper = next(row for row in view['benchmarks'] if row['material_id'] == 'copper')
    assert copper['stale'] is True
    assert copper['age_days'] == 152


def test_accepted_quotes_are_listed_by_origin_and_stay_synthetic(snapshot):
    saved = saved_offer('express-copper')
    draft = {**saved_offer('eu-copper'), 'status': 'needs_review'}
    view = build_country_pricing(get_company('solara-cables'), [saved, draft], FX, snapshot, today=TODAY)
    [quote] = view['quotes']
    assert {key: quote[key] for key in ('offer_id', 'supplier', 'origin_country_code', 'material_id', 'unit_price',
                                        'currency', 'per_unit', 'as_of', 'synthetic', 'document')} == {
        'offer_id': 'express-copper', 'supplier': 'Swift Copper Trading Ltd [SYNTHETIC]', 'origin_country_code': 'IN',
        'material_id': 'copper', 'unit_price': '10.50', 'currency': 'USD', 'per_unit': 'kg', 'as_of': '2026-10-14',
        'synthetic': True, 'document': 'SYN-express-copper.pdf'}
    assert view['synthetic_quotes'] is True and view['benchmarks_synthetic'] is False
    assert view['quote_note'] == 'Demo quotes are synthetic and not calibrated to market prices'


def test_quotes_get_one_consistent_unit_next_to_the_original(snapshot):
    tonne, kg = saved_offer('late-copper'), saved_offer('express-copper')
    view = build_country_pricing(get_company('solara-cables'), [tonne, kg], FX, snapshot, today=TODAY)
    atacama, swift = view['quotes']
    assert (atacama['unit_price'], atacama['per_unit']) == ('9412.50', 'tonne')
    assert atacama['comparable'] == {'amount': '9.4125', 'currency': 'USD', 'per_unit': 'kg'}
    assert swift['comparable'] == {'amount': '10.5', 'currency': 'USD', 'per_unit': 'kg'}


def test_difference_from_the_benchmark_is_a_labelled_synthetic_percentage(snapshot):
    view = build_country_pricing(get_company('solara-cables'), [saved_offer('late-copper'), saved_offer('express-copper')],
                                 FX, snapshot, today=TODAY)
    atacama, swift = view['quotes']
    # (10.5 - 14.474) / 14.474 x 100 = -27.456...; (9.4125 - 14.474) / 14.474 x 100 = -34.969...
    assert swift['vs_benchmark'] == {
        'status': 'compared', 'difference_percent': '-27.5', 'benchmark_per_unit': '14.474', 'currency': 'USD',
        'per_unit': 'kg', 'benchmark_month': '2026-09', 'series': 'Copper',
        'label': 'synthetic quote compared with a public benchmark',
        'formula': '(10.5 - 14.474) / 14.474 x 100, rounded to 0.1'}
    assert atacama['vs_benchmark']['difference_percent'] == '-35.0'


def test_quote_in_another_currency_uses_the_dated_reference_rate_or_says_unavailable(snapshot):
    euro = saved_offer('eu-copper')
    # benchmark 14.474 USD/kg x USD->EUR 0.8 = 11.5792 EUR/kg; (8 - 11.5792) / 11.5792 x 100 = -30.91...
    compared = build_country_pricing(get_company('solara-cables'), [euro], FX, snapshot, today=TODAY)['quotes'][0]
    assert compared['vs_benchmark']['difference_percent'] == '-30.9'
    assert compared['vs_benchmark']['benchmark_per_unit'] == '11.5792'
    assert 'ECB reference rate' in compared['vs_benchmark']['formula']
    missing = build_country_pricing(get_company('solara-cables'), [euro], [], snapshot, today=TODAY)['quotes'][0]
    assert missing['vs_benchmark'] == {'status': 'fx_unavailable', 'difference_percent': None,
                                       'label': 'synthetic quote compared with a public benchmark',
                                       'reason': 'No same-day ECB reference rate for USD to EUR is loaded.'}


def test_quote_for_a_material_without_benchmark_is_not_compared(snapshot):
    company = get_company('terrapack')
    view = build_country_pricing(company, [saved_offer('terrapack-pet-resin-selected')], FX, snapshot, today=TODAY)
    [quote] = view['quotes']
    assert quote['vs_benchmark']['status'] == 'no_public_benchmark'
    assert quote['comparable'] == {'amount': '130', 'currency': 'INR', 'per_unit': 'kg'}


def test_route_returns_view_with_fx_unavailable_when_offline():
    with TestClient(create_app(clock=lambda: datetime(2026, 10, 15, tzinfo=timezone.utc)),
                    base_url='https://demo.test') as client:
        client.get('/api/demo/session')
        response = client.get('/api/demo/country-pricing')
        assert response.status_code == 200, response.text
        body = response.json()
        assert body['company_id'] == 'solara-cables'
        assert body['source']['publisher'] == 'World Bank'
        copper = next(row for row in body['benchmarks'] if row['material_id'] == 'copper')
        assert {c['status'] for c in copper['conversions'] if c['currency'] != 'USD'} == {'fx_unavailable'}


def test_cross_rate_through_the_euro_reference():
    from procure.demo.country_pricing import usd_rate
    rate = usd_rate(FX, 'INR')
    assert rate == {'rate': '84', 'observation_date': '2026-10-14', 'provider': 'ECB',
                    'formula': 'USD->INR = EUR->INR 105 / EUR->USD 1.25 (ECB reference rates, 2026-10-14)'}
    assert usd_rate(FX, 'AED') is None
    mixed = [FX[0], {**FX[1], 'observation_date': '2026-10-13'}]
    assert usd_rate(mixed, 'INR') is None


def test_deploy_package_ships_the_benchmark_snapshot(tmp_path):
    import json
    import subprocess
    import sys
    from pathlib import Path
    repo = Path(__file__).resolve().parents[2]
    destination = tmp_path / 'package'
    prepared = subprocess.run([sys.executable, str(repo / 'deploy/judge-demo/prepare_package.py'), str(destination)],
                              capture_output=True, text=True)
    assert prepared.returncode == 0, prepared.stderr
    packaged = destination / 'procurement-engine/config/commodity_benchmarks_v1.json'
    assert json.loads(packaged.read_text())['snapshot_version'] == 'commodity-benchmarks-v1'
