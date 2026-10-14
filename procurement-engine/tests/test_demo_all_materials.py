"""Every fictional catalogue material has a reviewed same-unit sourcing scenario."""
import hashlib
from datetime import date, datetime, timezone
from decimal import Decimal

from procure.demo import company, planner, quote_service, recovery, rfq

TODAY = date(2026, 10, 14)


def test_all_material_scenario_contract_and_complete_reviewed_matrix():
    profiles = company.list_companies()
    assert len(profiles) == 10
    assert sum(len(profile['materials']) for profile in profiles) == 52
    assert len(quote_service.list_fixtures()) == 156
    for profile in profiles:
        materials = {material['material_id']: material for material in profile['materials']}
        coverage = profile['quote_coverage']
        scenarios = coverage['scenarios']
        assert {scenario['material_id'] for scenario in scenarios} == set(materials)
        assert set(coverage['materials']) == set(materials)
        assert len(coverage['fixture_ids']) == 3 * len(materials)
        assert set(coverage['fixture_ids']) == {
            fixture_id for scenario in scenarios for fixture_id in scenario['fixture_ids']}
        assert scenarios[0]['fixture_ids'] == coverage['fixture_ids'][:3]
        assert scenarios[0]['buyer_defaults'] == profile['buyer_defaults']
        for scenario in scenarios:
            material = materials[scenario['material_id']]
            defaults = scenario['buyer_defaults']
            unit = material['quantity_unit']
            assert defaults['material'] == material['material_id']
            assert defaults['quantity_unit'] == unit
            assert Decimal(defaults['quantity']) > Decimal(defaults['current_stock'])
            assert len(scenario['fixture_ids']) == 3
            if not (profile['company_id'] == 'solara-cables' and scenario['material_id'] == 'copper'):
                order = next(order for order in profile['orders']
                             if order['order_id'] == f"{profile['company_id']}-{material['material_id']}-scenario-order"
                             or (material['material_id'] == profile['materials'][0]['material_id']
                                 and order['order_id'] == f"{profile['company_id']}-scenario-order"))
                assert order['quantity'] == defaults['quantity'] and order['unit'] == unit
                assert order['need_by'] == defaults['need_by']
            offers = []
            for role, fixture_id in zip(('late', 'selected', 'alternate'), scenario['fixture_ids']):
                filename, raw = quote_service.fixture_bytes(fixture_id)
                assert quote_service.match_fixture(filename, raw) == fixture_id
                payload = quote_service.extract_fixture(fixture_id, mode='rules', as_of=TODAY)
                assert payload['extractor'] == 'rules'
                assert payload['document']['document_id'] == 'sha256:' + hashlib.sha256(raw).hexdigest()
                if unit not in {'kg', 'tonne'}:
                    assert payload['items'][0]['quantity']['status'] != 'extracted'
                offer = quote_service.new_offer(payload, offer_id=fixture_id, today=TODAY)
                for field, value in quote_service.fixture_review_values(fixture_id).items():
                    offer = quote_service.correct_offer(offer, field=field, value=value, today=TODAY)
                offer = quote_service.save_offer(offer, today=TODAY)
                offers.append(offer)
                if not (profile['company_id'] == 'solara-cables' and scenario['material_id'] == 'copper'):
                    suppliers = [supplier for supplier in profile['suppliers']
                                 if supplier['name'] == offer['fields']['supplier_name']['value']]
                    assert len(suppliers) == 1
                    supplier = suppliers[0]
                    assert supplier['lead_time_days'] == offer['fields']['lead_time_days']['value']
                    assert supplier['settlement_currency'] == offer['fields']['settlement_currency']['value']
                    assert material['material_id'] in supplier['materials']
                    assert any(material['material_id'] in route['materials']
                               and route['origin'] == supplier['country']
                               and route['currency'] == supplier['settlement_currency']
                               for route in profile['routes'])
            request = {**defaults, 'offer_ids': [offer['offer_id'] for offer in offers],
                       'expected_versions': {offer['offer_id']: offer['version'] for offer in offers}}
            plan = planner.build_plan(offers, request, today=TODAY)
            selected_index = 2 if profile['company_id'] == 'solara-cables' and scenario['material_id'] == 'copper' else 1
            alternate_index = 1 if selected_index == 2 else 2
            assert plan['selected_offer_id'] == scenario['fixture_ids'][selected_index]
            assert plan['quantity_unit'] == unit and Decimal(plan['required_purchase']) > 0
            delayed = recovery.compare(plan, request, offers,
                                       {plan['selected_offer_id']: 20}, today=TODAY)
            assert delayed['scenario']['selected_offer_id'] == scenario['fixture_ids'][alternate_index]
            draft = rfq.build_draft(plan, offers[selected_index], all_offers=offers,
                                    created_at=datetime(2026, 10, 14, tzinfo=timezone.utc))
            assert draft['status'] == 'draft_unsent'
            assert draft['quantity_unit'] == unit and draft['material'] == material['material_id']
            assert f"Requested quantity: {draft['quantity']} {unit}" in draft['text']


def test_all_material_scenarios_remain_reviewable_on_october_17_with_dated_synthetic_fx():
    today = date(2026, 10, 17)
    for profile in company.list_companies():
        for scenario in profile['quote_coverage']['scenarios']:
            offers = []
            for fixture_id in scenario['fixture_ids']:
                payload = quote_service.extract_fixture(fixture_id, mode='rules', as_of=today)
                offer = quote_service.new_offer(payload, offer_id=fixture_id, today=today)
                for field, value in quote_service.fixture_review_values(fixture_id).items():
                    offer = quote_service.correct_offer(offer, field=field, value=value, today=today)
                offer = quote_service.save_offer(offer, today=today)
                assert offer['normalized']['as_of'] == '2026-10-17'
                assert offer['normalized']['expiry'] == 'valid'
                fx = offer['normalized']['items'][0]['pricing']['reporting']['fx']
                if fx is not None:
                    assert fx['rate_date'] == '2026-10-14'
                    assert fx['source'] == 'synthetic_scenario'
                offers.append(offer)
            defaults = scenario['buyer_defaults']
            request = {**defaults, 'offer_ids': [offer['offer_id'] for offer in offers],
                       'expected_versions': {offer['offer_id']: offer['version'] for offer in offers}}
            plan = planner.build_plan(offers, request, today=today)
            assert plan['selected_offer_id']
            delayed = recovery.compare(plan, request, offers,
                                       {plan['selected_offer_id']: 20}, today=today)
            assert delayed['scenario']['selected_offer_id'] != plan['selected_offer_id']
            assert all(rate['rate_date'] == '2026-10-14'
                       for rate in plan['assumptions']['fx_rates'])
    # Legacy Europa normalization still has no invented October 17 EUR/USD rate.
    legacy = quote_service.new_offer(
        quote_service.extract_fixture('eu-copper', mode='rules', as_of=today),
        offer_id='eu-copper', today=today)
    assert legacy['normalized']['items'][0]['pricing']['reporting']['status'] == 'unavailable'
