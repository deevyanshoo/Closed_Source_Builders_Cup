"""Dated stock and receipt arithmetic over reviewed synthetic plans."""
from copy import deepcopy
from datetime import date
from decimal import Decimal

from procure.demo import company, planner, quote_service
from procure.demo.supply_coverage import assess_supply_coverage, with_supply_coverage

TODAY = date(2026, 10, 14)


def offers_for(profile):
    result = []
    for fixture_id in profile['quote_coverage']['scenarios'][0]['fixture_ids']:
        offer = quote_service.new_offer(quote_service.extract_fixture(fixture_id, mode='rules', as_of=TODAY),
                                        offer_id=fixture_id, today=TODAY)
        for field, value in quote_service.fixture_review_values(fixture_id).items():
            offer = quote_service.correct_offer(offer, field=field, value=value, today=TODAY)
        result.append(quote_service.save_offer(offer, today=TODAY))
    return result


def prepared(company_id='solara-cables', **changes):
    profile = company.get_company(company_id)
    offers = offers_for(profile)
    request = {**profile['buyer_defaults'], 'offer_ids': [o['offer_id'] for o in offers],
               'expected_versions': {o['offer_id']: o['version'] for o in offers}}
    if company_id == 'solara-cables':
        for old, current in (('quantity_kg', 'quantity'), ('current_stock_kg', 'current_stock')):
            if old in changes:
                changes[current] = changes[old]
    plan = planner.build_plan(offers, {**request, **changes}, today=TODAY)
    return profile, offers, plan


def test_solara_gap_and_no_mutation():
    profile, offers, plan = prepared()
    original = deepcopy(plan)
    enriched = with_supply_coverage(profile, plan, offers=offers)
    enriched['alternatives'][0]['reasons'].append('changed_copy')
    assert plan == original
    assessment = enriched['supply_coverage']
    assert plan == original and enriched is not plan
    assert assessment['schema'] == 1
    assert assessment['status'] == 'gap'
    assert assessment['order_deadline_met'] is True
    assert assessment['continuous_supply_covered'] is False
    assert assessment['inventory_as_of'] == '2026-10-14'
    assert assessment['consumption_as_of'] == '2026-10-14'
    assert assessment['selected_arrival_date'] == '2026-10-24'
    assert assessment['stock_depletion_date'] == '2026-10-20'
    assert Decimal(assessment['uncovered_quantity']) == Decimal('1333.30')
    assert Decimal(assessment['bridge_quantity']) == Decimal('1333.30')
    assert Decimal(assessment['uncovered_days']).quantize(Decimal('0.01')) == Decimal('4.00')
    assert assessment['bridge_needed_by'] == '2026-10-20'
    assert {a['offer_id'] for a in assessment['alternatives']} == {o['offer_id'] for o in offers}
    saved = deepcopy(plan)
    saved['selected_offer_id'] = 'express-copper'
    assert with_supply_coverage(profile, saved, offers=offers)['selected_offer_id'] == 'express-copper'
    assert saved['selected_offer_id'] == 'express-copper'


def test_boundary_arrival_zero_gap_and_insufficient_receipt_after_arrival():
    profile, offers, plan = prepared(current_stock_kg='3333.30', quantity_kg='4000')
    plan['selected_offer_id'] = 'eu-copper'
    plan['required_purchase'] = '666.70'
    plan['required_purchase_kg'] = '666.70'
    plan['need_by'] = '2026-10-24'
    boundary = assess_supply_coverage(profile, plan, offers=offers)
    assert boundary['status'] == 'covered' and boundary['uncovered_quantity'] == '0'
    plan['need_by'] = '2026-11-05'
    insufficient = assess_supply_coverage(profile, plan, offers=offers)
    assert insufficient['status'] == 'gap'
    assert Decimal(insufficient['uncovered_quantity']) == Decimal('3666.59')
    assert Decimal(insufficient['bridge_quantity']) == Decimal('3666.59')
    assert insufficient['bridge_needed_by'] == '2026-10-26'


def test_no_purchase_and_zero_consumption_do_not_invent_receipts():
    profile, offers, plan = prepared(current_stock_kg='10000')
    assert plan['selected_offer_id'] is None
    assessment = assess_supply_coverage(profile, plan, offers=offers)
    assert assessment['selected_arrival_date'] is None
    assert assessment['status'] == 'covered'
    profile = deepcopy(profile)
    profile['materials'][0]['consumption_per_day'] = '0'
    assert assess_supply_coverage(profile, plan)['status'] == 'covered'


def test_non_kg_and_reviewed_stock_override():
    profile, offers, plan = prepared('loomworks', current_stock='200')
    assessment = assess_supply_coverage(profile, plan, offers=offers)
    assert assessment['quantity_unit'] == 'bale'
    assert assessment['stock'] == '200'
    assert assessment['consumption_per_day'] == '32'
    assert assessment['status'] == 'gap'


def test_missing_stale_foreign_and_invalid_facts_are_unknown():
    profile, offers, plan = prepared()
    cases = []
    missing = deepcopy(profile)
    missing['materials'][0].pop('consumption_per_day')
    cases.append((missing, plan, offers, 'consumption_per_day'))
    invalid = deepcopy(profile)
    invalid['materials'][0]['consumption_per_day'] = 'NaN'
    cases.append((invalid, plan, offers, 'consumption_per_day'))
    stale = deepcopy(offers)
    stale[0]['version'] += 1
    cases.append((profile, plan, stale, 'offer_versions'))
    foreign = deepcopy(plan)
    foreign['company_id'] = 'terrapack'
    cases.append((profile, foreign, offers, 'company_id'))
    bad_unit = deepcopy(plan)
    bad_unit['quantity_unit'] = 'tonne'
    cases.append((profile, bad_unit, offers, 'quantity_unit'))
    bad_material = deepcopy(offers)
    chosen = next(offer for offer in bad_material if offer['offer_id'] == plan['selected_offer_id'])
    chosen['items'][0]['material']['value'] = 'polymer'
    cases.append((profile, plan, bad_material, 'selected_offer_material_or_arrival'))
    for owned, candidate, current_offers, missing_name in cases:
        result = assess_supply_coverage(owned, candidate, offers=current_offers)
        assert result['status'] == 'unknown'
        assert result['continuous_supply_covered'] is None
        assert missing_name in result['missing_inputs']


def test_date_overflow_and_all_late_offers_do_not_claim_supply():
    profile, offers, plan = prepared()
    plan['decision_date'] = '9999-12-31'
    plan['need_by'] = '9999-12-31'
    plan['alternatives'][0]['arrival_date'] = '9999-12-31'
    assert assess_supply_coverage(profile, plan)['status'] == 'unknown'
    profile, offers, plan = prepared(delay_days_by_offer={'eu-copper': 20, 'express-copper': 20})
    result = assess_supply_coverage(profile, plan, offers=offers)
    assert plan['selected_offer_id'] is None
    assert result['status'] == 'gap'
    assert result['order_deadline_met'] is False


def test_post_receipt_shortage_uses_actual_first_gap_date():
    profile, offers, plan = prepared(quantity_kg='3000', current_stock_kg='2000')
    profile = deepcopy(profile)
    profile['materials'][0]['consumption_per_day'] = '100'
    plan['decision_date'] = '2026-10-14'
    plan['need_by'] = '2026-11-22'  # Forty inclusive consumption days.
    plan['selected_offer_id'] = plan['alternatives'][0]['offer_id']
    chosen = next(row for row in plan['alternatives'] if row['offer_id'] == plan['selected_offer_id'])
    chosen['arrival_date'] = '2026-10-19'
    result = assess_supply_coverage(profile, plan)
    assert result['status'] == 'gap'
    assert result['stock_depletion_date'] == '2026-11-03'
    assert result['bridge_needed_by'] == '2026-11-13'
    assert result['uncovered_quantity'] == '1000'
    assert result['uncovered_days'] == '10'


def test_inclusive_last_day_shortage_and_same_day_receipt():
    profile, offers, plan = prepared(quantity_kg='4', current_stock_kg='2')
    profile = deepcopy(profile)
    profile['materials'][0]['consumption_per_day'] = '1'
    plan['need_by'] = '2026-10-16'
    plan['selected_offer_id'] = None
    gap = assess_supply_coverage(profile, plan)
    assert gap['horizon_end'] == '2026-10-16'
    assert gap['uncovered_quantity'] == '1'
    assert gap['bridge_needed_by'] == '2026-10-16'
    plan['selected_offer_id'] = plan['alternatives'][0]['offer_id']
    plan['alternatives'][0]['arrival_date'] = '2026-10-16'
    covered = assess_supply_coverage(profile, plan)
    assert covered['status'] == 'covered'
    assert covered['uncovered_quantity'] == '0'


def test_corrupted_plan_algebra_alias_and_selected_receipt_are_unknown():
    profile, offers, plan = prepared()
    cases = [
        ('quantity', '0', 'quantity'),
        ('quantity', 'NaN', 'quantity'),
        ('required_purchase', '7999', 'required_purchase'),
        ('quantity_kg', '10001', 'quantity_kg'),
        ('current_stock_kg', '2001', 'current_stock_kg'),
        ('required_purchase_kg', '8001', 'required_purchase_kg'),
    ]
    for key, value, missing in cases:
        bad = deepcopy(plan)
        bad[key] = value
        result = assess_supply_coverage(profile, bad, offers=offers)
        assert result['status'] == 'unknown'
        assert missing in result['missing_inputs']
    for key, value, missing in (
        ('quantity', '7999', 'selected_receipt_quantity'),
        ('quantity_kg', '7999', 'selected_receipt_quantity_kg'),
        ('quantity_unit', 'bale', 'selected_receipt_unit'),
        ('offer_version', -1, 'selected_receipt_version'),
    ):
        bad = deepcopy(plan)
        chosen = next(row for row in bad['alternatives'] if row['offer_id'] == bad['selected_offer_id'])
        chosen[key] = value
        result = assess_supply_coverage(profile, bad, offers=offers)
        assert result['status'] == 'unknown'
        assert missing in result['missing_inputs']


def test_no_purchase_zero_stock_and_malformed_inputs():
    profile, offers, plan = prepared(quantity_kg='2', current_stock_kg='2')
    profile = deepcopy(profile)
    profile['materials'][0]['consumption_per_day'] = '1'
    plan['need_by'] = '2026-10-16'
    result = assess_supply_coverage(profile, plan)
    assert result['status'] == 'gap'
    assert result['uncovered_quantity'] == '1'
    assert result['bridge_needed_by'] == '2026-10-16'
    plan['current_stock'] = '0'
    plan['current_stock_kg'] = '0'
    plan['required_purchase'] = '2'
    plan['required_purchase_kg'] = '2'
    zero_stock = assess_supply_coverage(profile, plan)
    assert zero_stock['status'] == 'gap'
    assert zero_stock['bridge_needed_by'] == '2026-10-14'
    assert zero_stock['uncovered_quantity'] == '3'
    for malformed in (None, [], {'alternatives': [None]}, {'company_id': []}):
        assert assess_supply_coverage(profile, malformed)['status'] == 'unknown'
