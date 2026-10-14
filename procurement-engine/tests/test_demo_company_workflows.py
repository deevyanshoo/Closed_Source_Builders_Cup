"""The fictional company scenarios use reviewed, same-unit document offers."""
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from procure.demo import company, planner, quote_service, recovery, rfq
from procure.demo.errors import DemoError
from procure.demo.app import create_app

TODAY = date(2026, 10, 14)


def accepted(profile):
    offers = []
    for fixture_id in profile['quote_coverage']['fixture_ids']:
        extracted = quote_service.extract_fixture(fixture_id, mode='rules', as_of=TODAY)
        offer = quote_service.new_offer(extracted, offer_id=fixture_id, today=TODAY)
        for field, value in quote_service.fixture_review_values(fixture_id).items():
            offer = quote_service.correct_offer(offer, field=field, value=value, today=TODAY)
        offers.append(quote_service.save_offer(offer, today=TODAY))
    return offers


def request(profile, offers):
    return {**profile['buyer_defaults'], 'offer_ids': [o['offer_id'] for o in offers],
            'expected_versions': {o['offer_id']: o['version'] for o in offers}}


@pytest.mark.parametrize('profile', company.list_companies(), ids=lambda p: p['company_id'])
def test_each_company_has_a_complete_reviewed_three_offer_plan_and_recovery(profile):
    coverage = profile['quote_coverage']
    assert len(coverage['fixture_ids']) == 3
    assert profile['buyer_defaults']['material'] in coverage['materials']
    offers = accepted(profile)
    for offer in offers:
        assert offer['document']['document_id']
        pricing = offer['items'][0]['pricing']
        original = pricing.get('original', pricing)
        assert original['status'] in {'extracted', 'missing', 'unverified'}
        if original['status'] == 'extracted':
            assert original['evidence']
    baseline = planner.build_plan(offers, request(profile, offers), today=TODAY)
    unit = profile['buyer_defaults']['quantity_unit']
    assert baseline['quantity_unit'] == unit
    assert Decimal(baseline['required_purchase']) > 0
    assert all(alt['unit_price_per_unit'] == alt['unit_price'] for alt in baseline['alternatives'])
    selected_index = 2 if profile['company_id'] == 'solara-cables' else 1
    alternate_index = 1 if profile['company_id'] == 'solara-cables' else 2
    assert baseline['selected_offer_id'] == coverage['fixture_ids'][selected_index]
    assert baseline['required_purchase_kg'] == baseline['required_purchase'] if unit == 'kg' else 'required_purchase_kg' not in baseline
    delayed = recovery.compare(baseline, request(profile, offers), offers,
                               {baseline['selected_offer_id']: 20}, today=TODAY)
    assert delayed['scenario']['selected_offer_id'] == coverage['fixture_ids'][alternate_index]
    draft = rfq.build_draft(baseline, offers[selected_index], all_offers=offers,
                            created_at=datetime(2026, 10, 14, tzinfo=timezone.utc))
    assert draft['quantity'] == baseline['required_purchase']
    assert draft['quantity_unit'] == unit
    assert f"Requested quantity: {draft['quantity']} {unit}" in draft['text']
    assert draft['material'] == profile['buyer_defaults']['material']
    assert baseline['company_id'] == profile['company_id']
    assert draft['company_id'] == profile['company_id']
    assert offers[selected_index]['document']['document_id'] in draft['text']
    if unit in {'bale', 'unit', 'barrel'}:
        assert 'Original quoted price: unavailable (unverified document field)' in draft['text']
        assert 'r7:c2' in draft['text']
    scenario_order = next(order for order in profile['orders'] if order['order_id'].endswith('scenario-order')) if profile['company_id'] != 'solara-cables' else None
    if scenario_order:
        assert scenario_order['quantity'] == profile['buyer_defaults']['quantity']
        assert scenario_order['need_by'] == profile['buyer_defaults']['need_by']
        for offer, role in zip(offers, ('late', 'selected', 'alternate')):
            supplier = next(s for s in profile['suppliers'] if s['supplier_id'].endswith(f'scenario-{role}'))
            assert offer['fields']['supplier_name']['value'] == supplier['name']
            assert offer['fields']['lead_time_days']['value'] == supplier['lead_time_days']


def test_generic_unit_mismatch_and_stale_offer_version_fail_closed():
    profile = company.get_company('loomworks')
    offers = accepted(profile)
    data = request(profile, offers)
    data['quantity_unit'] = 'kg'
    with pytest.raises(DemoError) as err:
        planner.build_plan(offers, data, today=TODAY)
    assert err.value.code == 'unit_mismatch'
    data = request(profile, offers)
    data['expected_versions'][offers[0]['offer_id']] -= 1
    with pytest.raises(DemoError) as err:
        planner.build_plan(offers, data, today=TODAY)
    assert err.value.code == 'stale_version'


def test_generic_request_rejects_kg_aliases_for_non_kg_material():
    profile = company.get_company('loomworks')
    offers = accepted(profile)
    data = request(profile, offers)
    data['quantity_kg'] = data['quantity']
    with pytest.raises(DemoError) as err:
        planner.build_plan(offers, data, today=TODAY)
    assert err.value.code == 'unit_mismatch'


def test_fixture_hash_and_company_mismatch_are_rejected():
    profile = company.get_company('loomworks')
    fixture_id = profile['quote_coverage']['fixture_ids'][0]
    filename, content = quote_service.fixture_bytes(fixture_id)
    with pytest.raises(DemoError) as err:
        quote_service.match_fixture(filename, content + b'altered')
    assert err.value.code == 'fixture_not_allowed'
    offers = accepted(profile)
    data = request(profile, offers)
    data['company_id'] = 'terrapack'
    with pytest.raises(DemoError) as err:
        planner.build_plan(offers, data, today=TODAY)
    assert err.value.code == 'company_mismatch'


def test_company_alternate_uses_dated_synthetic_fx_and_abstains_on_other_day():
    profile = company.get_company('loomworks')
    offers = accepted(profile)
    data = request(profile, offers)
    result = planner.build_plan(offers, data, today=TODAY)
    alternate = result['alternatives'][2]
    assert alternate['unit_price'] == '60000'
    assert result['assumptions']['fx_rates'] == [{
        'base': 'USD', 'quote': 'BDT', 'rate': '100',
        'rate_date': '2026-10-14', 'source': 'synthetic_scenario'}]
    data['decision_date'] = '2026-10-15'
    result = planner.build_plan(offers, data, today=TODAY)
    assert 'fx_unavailable' in result['alternatives'][2]['reasons']


def test_unsent_rfq_rejects_foreign_or_stale_reviewed_offer():
    profile = company.get_company('loomworks')
    offers = accepted(profile)
    plan = planner.build_plan(offers, request(profile, offers), today=TODAY)
    created = datetime(2026, 10, 14, tzinfo=timezone.utc)
    with pytest.raises(DemoError) as err:
        rfq.build_draft(plan, offers[2], all_offers=offers, created_at=created)
    assert err.value.code == 'stale_plan'
    stale = {**offers[1], 'version': offers[1]['version'] + 1}
    with pytest.raises(DemoError) as err:
        rfq.build_draft(plan, stale, all_offers=offers, created_at=created)
    assert err.value.code == 'stale_version'


def test_loomworks_document_suggestions_rescue_unverified_critical_fields_only_after_review():
    profile = company.get_company('loomworks')
    critical = ('supplier_name', 'origin_country', 'incoterm', 'settlement_currency',
                'valid_until', 'lead_time_days', 'payment_terms')
    offers = []
    for fixture_id in profile['quote_coverage']['fixture_ids']:
        payload = quote_service.extract_fixture(fixture_id, mode='rules', as_of=TODAY)
        for name in critical:
            field = payload['fields'][name]
            field.update(status='unverified', value=None, evidence=[], reasons=['simulated evidence mismatch'])
        offer = quote_service.new_offer(payload, offer_id=fixture_id, today=TODAY)
        with pytest.raises(DemoError) as blocked:
            quote_service.save_offer(offer, today=TODAY)
        assert blocked.value.code == 'save_blocked'
        suggested = next(row['review_values'] for row in quote_service.list_fixtures()
                         if row['id'] == fixture_id)
        assert set(critical) <= set(suggested)
        assert suggested['supplier_name'] == next(
            supplier['name'] for supplier in profile['suppliers']
            if supplier['supplier_id'].endswith(f"scenario-{fixture_id.rsplit('-', 1)[-1]}"))
        assert suggested['incoterm'] == {'code': 'DAP', 'place': 'Dhaka Bangladesh'}
        assert suggested['settlement_currency'] in {'BDT', 'USD'}
        assert suggested['valid_until'] == '2026-12-31'
        for name, corrected in suggested.items():
            offer = quote_service.correct_offer(offer, field=name, value=corrected, today=TODAY)
        for name in critical:
            field = offer['fields'][name]
            assert field['status'] == 'corrected' and field['source'] == 'user'
            assert field['original']['status'] == 'unverified'
            assert field['original']['value'] is None
        offers.append(quote_service.save_offer(offer, today=TODAY))
    plan = planner.build_plan(offers, request(profile, offers), today=TODAY)
    assert plan['selected_offer_id'] == profile['quote_coverage']['fixture_ids'][1]


@pytest.mark.parametrize('profile', company.list_companies(), ids=lambda p: p['company_id'])
def test_all_ten_company_workflows_over_http(profile):
    with TestClient(create_app(clock=lambda: datetime(2026, 10, 14, tzinfo=timezone.utc)),
                    base_url='https://demo.test') as client:
        csrf = client.get('/api/demo/session').json()['csrf_token']
        headers = {'Origin': 'https://demo.test', 'X-CSRF-Token': csrf}
        context = 1
        if profile['company_id'] != 'solara-cables':
            selected = client.post('/api/demo/company', headers=headers, json={
                'company_id': profile['company_id'], 'expected_context_version': 1,
                'idempotency_key': 'choose-company'})
            assert selected.status_code == 200, selected.text
            context = 2
        offers = []
        for index, fixture_id in enumerate(profile['quote_coverage']['fixture_ids']):
            response = client.post('/api/demo/extract', headers=headers, json={
                'fixture_id': fixture_id, 'mode': 'rules',
                'expected_context_version': context, 'idempotency_key': f'extract-{index}'})
            assert response.status_code == 200, response.text
            offer = response.json()['offer']
            for correction_index, (field, corrected) in enumerate(quote_service.fixture_review_values(fixture_id).items()):
                response = client.post(f"/api/demo/offers/{offer['offer_id']}/correct", headers=headers, json={
                    'field': field, 'value': corrected, 'expected_version': offer['version'],
                    'expected_context_version': context, 'idempotency_key': f'correct-{index}-{correction_index}'})
                assert response.status_code == 200, response.text
                offer = response.json()['offer']
            response = client.post(f"/api/demo/offers/{offer['offer_id']}/save", headers=headers, json={
                'expected_version': offer['version'], 'expected_context_version': context,
                'idempotency_key': f'save-{index}'})
            assert response.status_code == 200, response.text
            offers.append(response.json()['offer'])
        payload = {**request(profile, offers), 'expected_context_version': context}
        response = client.post('/api/demo/plan', headers=headers, json=payload)
        assert response.status_code == 200, response.text
        plan = response.json()
        assert plan['company_id'] == profile['company_id'] and plan['selected_offer_id']
        response = client.post('/api/demo/recovery', headers=headers, json={
            'baseline_plan_id': plan['plan_id'], 'expected_versions': plan['offer_versions'],
            'delay_days_by_offer': {plan['selected_offer_id']: 20},
            'expected_context_version': context, 'idempotency_key': 'recover'})
        assert response.status_code == 200, response.text
        scenario = response.json()['scenario']
        assert scenario['selected_offer_id'] != plan['selected_offer_id']
        response = client.post('/api/demo/rfq', headers=headers, json={
            'plan_id': scenario['plan_id'], 'expected_versions': scenario['offer_versions'],
            'reviewed': True, 'expected_context_version': context, 'idempotency_key': 'draft'})
        assert response.status_code == 200, response.text
        assert response.json()['status'] == 'draft_unsent'
