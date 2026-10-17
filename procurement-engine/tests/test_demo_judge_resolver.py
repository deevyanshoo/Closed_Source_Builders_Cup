from copy import deepcopy
from datetime import datetime, timezone

import pytest

from procure.demo.agent_tools import inventory_analytics
from procure.demo.company import get_company
from procure.demo.judge_resolver import resolve_judge_turn


NOW = datetime(2026, 10, 17, tzinfo=timezone.utc)


def snapshot(company_id='solara-cables'):
    company = get_company(company_id)
    material = company['materials'][0]
    return {'company_id': company_id, 'context_version': 4, 'company': company,
            'selected_material_id': material['material_id'], 'offers': [], 'news': None,
            'plan': None, 'shipments': {'shipments': []}, 'carbon_assessment': None}


def bridge_snapshot():
    state = snapshot()
    state['plan'] = {'plan_id': 'reviewed-plan', 'company_id': state['company_id'],
                     'material': state['selected_material_id'], 'selected_offer_id': 'offer-1',
                     'supply_coverage': {'status': 'gap', 'bridge_needed_by': '2026-10-20',
                                         'bridge_quantity': '1333.30', 'evidence_ids': []}}
    return state


@pytest.mark.parametrize('message,missing', (
    ('Draft a bridge RFQ for polymer earlier delivery.', 'material'),
    ('Draft a bridge RFQ for Cable insulation polymer earlier delivery.', 'material'),
    ('Draft a bridge RFQ for copper and polymer earlier delivery.', 'material'),
    ('Draft a bridge RFQ for copper and polymer using the current material.', 'material'),
    ('Draft a bridge RFQ for all materials for earlier delivery.', 'material'),
    ('Draft a bridge RFQ for both materials for earlier delivery.', 'material'),
    ('Prepare an unsent earlier-delivery or bridge RFQ for the production supply gap in current reviewed plan plan-0123456789abcdef. Ask the selected supplier for new capacity, price and terms. Save the draft; do not send it.', 'current_selected_plan'),
    ('Draft a bridge RFQ for earlier delivery using plan ID stale.', 'current_selected_plan'),
    ('Draft a bridge RFQ for earlier delivery using plan "old-plan".', 'current_selected_plan'),
    ('Draft a bridge RFQ for earlier delivery using plan reviewed-plan and plan old-plan.', 'current_selected_plan'),
))
def test_bridge_binding_rejects_explicit_mismatch_without_mutation(message, missing):
    state = bridge_snapshot()
    before = deepcopy(state)
    result = resolve_judge_turn(state, message, now=NOW)
    assert result['kind'] == 'needs_input'
    assert result['missing_inputs'] == [missing]
    assert not result.get('follow_up_research')
    assert state == before


@pytest.mark.parametrize('message', (
    'Draft a bridge RFQ for Copper cathode earlier delivery using plan reviewed-plan.',
    'Draft a bridge RFQ for earlier delivery using plan ID reviewed-plan.',
    'Draft a bridge RFQ for earlier delivery using the current plan.',
    'Draft a bridge RFQ for earlier delivery using the current plan to cover the gap.',
    'Draft a bridge RFQ for earlier delivery using the current plan for production.',
    'Draft a bridge RFQ for earlier delivery using the current plan and save it unsent.',
    'Draft a bridge RFQ for earlier delivery using the current plan without sending it.',
))
def test_bridge_binding_accepts_current_plan_and_ordinary_plan_prose(message):
    state = bridge_snapshot()
    before = deepcopy(state)
    assert resolve_judge_turn(state, message, now=NOW)['kind'] == 'bridge_rfq_request'
    assert state == before


def test_bridge_binding_checks_all_letter_reviewed_plan_nonce():
    old_id = 'ABCDEFGHIJKLMNOPQRSTUVWX'
    current_id = 'ZYXWVUTSRQPONMLKJIHGFEDC'
    state = bridge_snapshot()
    state['plan']['plan_id'] = current_id
    before = deepcopy(state)
    cta = ('Prepare an unsent earlier-delivery or bridge RFQ for the production supply gap '
           'in current reviewed plan {plan_id}. Ask the selected supplier for new capacity, '
           'price and terms. Save the draft; do not send it.')
    stale = resolve_judge_turn(state, cta.format(plan_id=old_id), now=NOW)
    assert stale['kind'] == 'needs_input'
    assert stale['missing_inputs'] == ['current_selected_plan']
    assert resolve_judge_turn(state, cta.format(plan_id=current_id), now=NOW)['kind'] == 'bridge_rfq_request'
    assert state == before


@pytest.mark.parametrize('message', (
    'Prepare an unsent earlier-delivery or bridge RFQ for the production supply gap in current reviewed plan reviewed-plan. Ask the selected supplier for new capacity, price and terms. Save the draft; do not send it.',
    'Prepare an earlier delivery RFQ for the supply gap using our current plan. Save the draft without sending it.',
    'Do not send a bridge RFQ; prepare an unsent bridge RFQ draft for the current supply gap.',
    'Do not send the draft; prepare an unsent bridge RFQ for the current supply gap.',
    'Do not send the bridge RFQ, prepare an unsent bridge RFQ draft for the current supply gap.',
    'Could you please prepare an unsent bridge RFQ for the current supply gap?',
))
def test_bridge_draft_command_remains_actionable_when_only_sending_is_prohibited(message):
    assert resolve_judge_turn(bridge_snapshot(), message, now=NOW)['kind'] == 'bridge_rfq_request'


@pytest.mark.parametrize('message', (
    'Do not draft a bridge RFQ for earlier delivery.',
    'Do not prepare an earlier delivery RFQ; do not send it.',
    'Prepare a bridge RFQ for earlier delivery, but do not create the draft.',
    'Prepare a bridge RFQ for earlier delivery without creating a bridge RFQ.',
    'Do not send or draft a bridge RFQ for earlier delivery.',
    'Explain how to prepare a bridge RFQ for earlier delivery.',
    'How do I prepare a bridge RFQ for earlier delivery?',
    'Can you explain how to prepare a bridge RFQ for earlier delivery?',
    'Could we prepare a bridge RFQ for earlier delivery?',
    'What if we prepare a bridge RFQ for earlier delivery?',
))
def test_negated_or_discussed_bridge_draft_does_not_request_an_action(message):
    result = resolve_judge_turn(bridge_snapshot(), message, now=NOW)
    assert result is None or result['kind'] != 'bridge_rfq_request'


def test_internal_stock_survives_public_news_gap_and_offline_provider():
    state = snapshot()
    result = resolve_judge_turn(state, 'What is our stock and the latest Suez news?', now=NOW)
    assert result['kind'] == 'internal_answer'
    assert result['company_id'] == state['company_id']
    assert 'stock' in result['answer'].lower()
    assert result['follow_up_research']['query'].lower().startswith('suez canal')
    assert 'stock' not in result['follow_up_research']['query'].lower()
    assert 'solara' not in result['follow_up_research']['query'].lower()


def test_unknown_question_defers_to_grounded_model():
    assert resolve_judge_turn(snapshot(), 'Can you explain the tradeoffs?', now=NOW) is None


def test_selected_arrival_question_uses_active_coverage_before_profile_stock():
    state = snapshot()
    state['plan'] = {'plan_id': 'p1', 'company_id': state['company_id'],
                     'material': state['selected_material_id'], 'offer_versions': {'o1': 2},
                     'supply_coverage': {'status': 'gap', 'order_deadline_met': True,
                         'continuous_supply_covered': False, 'stock': '2000',
                         'quantity_unit': 'kg', 'inventory_as_of': '2026-10-14',
                         'consumption_per_day': '333.333', 'consumption_as_of': '2026-10-14',
                         'stock_depletion_date': '2026-10-20',
                         'selected_arrival_date': '2026-10-24', 'uncovered_days': '4',
                         'uncovered_quantity': '1333.332', 'bridge_quantity': '1333.332',
                         'bridge_needed_by': '2026-10-20', 'missing_inputs': [],
                         'evidence_ids': ['document:o1']}}
    result = resolve_judge_turn(state, 'Will our stock run out before the selected arrival?', now=NOW)
    assert result['kind'] == 'internal_answer'
    assert '2026-10-20' in result['answer'] and '2026-10-24' in result['answer']
    assert 'about 1,333.33 kg' in result['answer']
    assert 'profile-only' not in result['answer'].lower()
    assert result.get('follow_up_research') is None


def test_coverage_voice_display_rounds_repeating_and_tiny_positive_values():
    state = snapshot()
    state['plan'] = {'plan_id': 'p1', 'company_id': state['company_id'],
                     'material': 'copper', 'selected_offer_id': 'o1',
                     'supply_coverage': {'status': 'gap', 'order_deadline_met': True,
                         'quantity_unit': 'kg', 'stock': '2000', 'inventory_as_of': '2026-10-14',
                         'consumption_per_day': '333.333333333333333333', 'consumption_as_of': '2026-10-14',
                         'stock_depletion_date': '2026-10-20', 'selected_arrival_date': '2026-10-24',
                         'uncovered_days': '4.000000000000000001',
                         'uncovered_quantity': '1333.333333333333333333',
                         'bridge_quantity': '1333.333333333333333333',
                         'bridge_needed_by': '2026-10-20', 'evidence_ids': []}}
    answer = resolve_judge_turn(state, 'Will stock run out before arrival?', now=NOW)['answer']
    assert 'about 4 days' in answer
    assert 'about 1,333.33 kg' in answer
    assert '333.33 kg' in answer
    assert '333333333' not in answer
    state['plan']['supply_coverage']['uncovered_quantity'] = '0.00000001'
    state['plan']['supply_coverage']['bridge_quantity'] = '0.00000001'
    tiny = resolve_judge_turn(state, 'Will stock run out before arrival?', now=NOW)['answer']
    assert '<0.01 kg' in tiny
    assert 'gap is about 4 days / 0 kg' not in tiny


def test_unselected_plan_reports_gap_without_supplier_bridge_action():
    state = snapshot()
    state['plan'] = {'plan_id': 'p1', 'company_id': state['company_id'],
                     'material': state['selected_material_id'], 'selected_offer_id': None,
                     'supply_coverage': {'status': 'gap', 'order_deadline_met': False,
                         'stock': '2000', 'quantity_unit': 'kg', 'inventory_as_of': '2026-10-14',
                         'consumption_per_day': '333.333', 'consumption_as_of': '2026-10-14',
                         'stock_depletion_date': '2026-10-20', 'selected_arrival_date': None,
                         'uncovered_days': '4', 'uncovered_quantity': '1333.332',
                         'bridge_quantity': '1333.332', 'bridge_needed_by': '2026-10-20'}}
    answer = resolve_judge_turn(state, 'Will our stock run out before an arrival?', now=NOW)
    assert answer['kind'] == 'internal_answer'
    assert 'projected supply gap' in answer['answer'].lower()
    assert 'bridge rfq' not in answer['answer'].lower()
    action = resolve_judge_turn(state, 'Draft a bridge RFQ for earlier delivery.', now=NOW)
    assert action['kind'] == 'needs_input'


def test_generic_port_preview_requires_material_location_and_duration():
    result = resolve_judge_turn(snapshot(), 'What if shipping is disrupted?', now=NOW)
    assert result['kind'] == 'needs_input'
    assert {'material', 'location_or_route', 'delay_days'} <= set(result['missing_inputs'])


def test_supplier_delay_binds_owned_current_shipment_without_mutation():
    state = snapshot()
    material = state['selected_material_id']
    state['plan'] = {'plan_id': 'p1', 'company_id': state['company_id'], 'material': material,
                     'selected_offer_id': 'o1', 'offer_versions': {'o1': 2}}
    state['shipments'] = {'shipments': [{'shipment_id': 's1', 'company_id': state['company_id'],
                                       'plan_id': 'p1', 'material_id': material, 'offer_id': 'o1',
                                       'offer_version': 2, 'recovery_available': True,
                                       'route': None}]}
    result = resolve_judge_turn(state, 'What if current shipment is delayed by 7 days?', now=NOW)
    assert result['kind'] == 'read_only_preview'
    assert result['preview_request']['shipment_id'] == 's1'
    assert result['preview_request']['delay_days_by_offer'] == {'o1': 7}
    assert result['preview_request']['scenario_plan_id'] is None
    assert state['plan']['plan_id'] == 'p1'


def test_selected_material_supplier_alias_requires_one_current_owned_baseline_shipment():
    state = snapshot()
    material = state['selected_material_id']
    state['plan'] = {'plan_id': 'p1', 'company_id': state['company_id'], 'material': material,
                     'selected_offer_id': 'o1', 'offer_versions': {'o1': 2}}
    row = {'shipment_id': 's1', 'company_id': state['company_id'], 'plan_id': 'p1',
           'material_id': material, 'offer_id': 'o1', 'offer_version': 2,
           'recovery_available': True, 'route': None}
    state['shipments'] = {'shipments': [row]}
    question = 'What if our current copper cathode supplier is delayed by 7 days?'
    resolved = resolve_judge_turn(state, question, now=NOW)
    assert resolved['kind'] == 'read_only_preview'
    assert resolved['preview_request']['shipment_id'] == 's1'
    assert resolved['preview_request']['delay_days_by_offer'] == {'o1': 7}
    state['shipments']['shipments'].append({**row, 'shipment_id': 's2'})
    assert resolve_judge_turn(state, question, now=NOW)['kind'] == 'needs_input'
    state['shipments']['shipments'] = [{**row, 'offer_version': 3}]
    assert resolve_judge_turn(state, question, now=NOW)['kind'] == 'needs_input'
    state['shipments']['shipments'] = [row]
    state['selected_material_id'] = 'polymer'
    assert resolve_judge_turn(state, question, now=NOW)['kind'] == 'needs_input'
    state['selected_material_id'] = material
    foreign = resolve_judge_turn(state,
        'What if LoomWorks current copper cathode supplier is delayed by 7 days?', now=NOW)
    assert foreign['kind'] == 'needs_input' and foreign['missing_inputs'] == ['company']


def test_suez_does_not_infer_route_from_supplier_or_country():
    state = snapshot()
    result = resolve_judge_turn(state, 'What if Suez delays shipping by 14 days?', now=NOW)
    assert result['kind'] == 'needs_input'
    assert 'location_or_route' in result['missing_inputs']


def test_relevant_history_is_owned_and_fresh_request_skips_it():
    state = snapshot()
    state['completed_research'] = [{'company_id': state['company_id'], 'context_version': 4,
                                    'status': 'completed', 'query': 'Suez Canal shipping status',
                                    'answer': 'Previously observed blockage.', 'evidence_refs': ['source-1'],
                                    'checked_at': '2026-10-16T10:00:00Z'}]
    old = resolve_judge_turn(state, 'What is the Suez Canal shipping status?', now=NOW)
    assert old['kind'] == 'internal_answer'
    assert 'source-1' in old['evidence_refs']
    assert '2026-10-16' in old['answer']
    fresh = resolve_judge_turn(state, 'What is the Suez Canal shipping status?', now=NOW,
                               fresh_requested=True)
    assert fresh['kind'] == 'research_proposal'
    state['company_id'] = 'loomworks'
    state['company'] = get_company('loomworks')
    other = resolve_judge_turn(state, 'What is the Suez Canal shipping status?', now=NOW)
    assert other['kind'] != 'internal_answer'


def test_local_quotes_carbon_and_shipments_are_answered_offline():
    state = snapshot()
    state['offers'] = [{'offer_id': 'foreign', 'status': 'saved',
                        'company_id': 'loomworks'}]
    assert resolve_judge_turn(state, 'How many of our saved quotes?', now=NOW)['answer'].startswith('0 saved')
    assert resolve_judge_turn(state, 'How many of our saved quotes?', now=NOW)['kind'] == 'internal_answer'
    assert resolve_judge_turn(state, 'What is our current carbon assessment?', now=NOW)['kind'] == 'internal_answer'
    assert resolve_judge_turn(state, 'What are our current shipments?', now=NOW)['kind'] == 'internal_answer'


def test_mixed_quote_and_latest_public_news_preserves_local_answer():
    state = snapshot()
    result = resolve_judge_turn(state, 'What are our saved quotes and latest Suez news?', now=NOW)
    assert result['kind'] == 'internal_answer'
    assert 'quote' in result['answer'].lower()
    assert result['follow_up_research']['destination']


def test_unrelated_cached_headline_does_not_answer_suez_question():
    state = snapshot()
    state['news'] = {'company_id': state['company_id'], 'cache_state': 'fresh',
                     'items': [{'item_id': 'copper-1', 'synthetic': False,
                                'relevance_kind': 'direct_material', 'relevance_strength': 'strong',
                                'matched_materials': [state['selected_material_id']],
                                'title': 'Copper cathode production update',
                                'url': 'https://example.org/copper',
                                'published_at': '2026-10-16T00:00:00Z',
                                'retrieved_at': '2026-10-17T00:00:00Z'}]}
    result = resolve_judge_turn(state, 'What is the Suez Canal shipping status?', now=NOW)
    assert result['kind'] == 'research_proposal'


def test_factual_shipment_delay_uses_status_reader_not_preview():
    state = snapshot()
    result = resolve_judge_turn(state, 'Is our current shipment delayed by 7 days?', now=NOW)
    assert result['kind'] == 'internal_answer'
    assert 'preview_request' not in result


def test_foreign_company_name_blocks_preview():
    state = snapshot()
    result = resolve_judge_turn(state, 'What if LoomWorks shipment is delayed by 7 days?', now=NOW)
    assert result['kind'] == 'needs_input'
    assert 'company' in result['missing_inputs']
    assert 'preview_request' not in result


def test_foreign_company_name_does_not_return_current_company_stock():
    result = resolve_judge_turn(snapshot(), 'What is our LoomWorks stock?', now=NOW)
    assert result['kind'] == 'needs_input'
    assert 'company' in result['missing_inputs']


def test_profile_supplier_delay_uses_canonical_cover_without_plan():
    state = snapshot()
    material = state['company']['materials'][0]
    result = resolve_judge_turn(state, f'What if {material["name"]} supplier is delayed by two weeks?', now=NOW)
    assert result['kind'] == 'read_only_preview'
    assert result['preview_request']['mode'] == 'profile_stock_cover'
    assert result['preview_request']['delay_days'] == 14
    assert 'stock cover' in result['answer'].lower()
    assert 'not a costed plan recovery' in result['answer'].lower()
    assert result['baseline_plan_id'] is None


def test_profile_material_delay_without_supplier_word_is_read_only():
    state = snapshot()
    material = state['company']['materials'][0]
    result = resolve_judge_turn(state, f'What if {material["name"]} is delayed for a week?', now=NOW)
    assert result['kind'] == 'read_only_preview'
    assert result['preview_request']['delay_days'] == 7


def test_numeric_week_duration_and_order_id_digits_do_not_become_days():
    state = snapshot()
    material = state['company']['materials'][0]
    result = resolve_judge_turn(state, f'What if {material["name"]} supplier is delayed for 2 weeks?', now=NOW)
    assert result['preview_request']['delay_days'] == 14
    result = resolve_judge_turn(state, f'What if order-14 for {material["name"]} supplier is delayed?', now=NOW)
    assert result['kind'] == 'needs_input'
    assert 'delay_days' in result['missing_inputs']
    result = resolve_judge_turn(state, f'What if order-14-day for {material["name"]} supplier is delayed?', now=NOW)
    assert result['kind'] == 'needs_input'
    assert 'delay_days' in result['missing_inputs']


def test_cached_suez_news_is_read_before_fresh_follow_up_for_mixed_question():
    state = snapshot()
    state['news'] = {'company_id': state['company_id'], 'cache_state': 'fresh',
                     'items': [{'item_id': 'suez-1', 'synthetic': False,
                                'relevance_kind': 'sector', 'relevance_strength': 'strong',
                                'relevance_reason': 'Public shipping event',
                                'title': 'Suez Canal shipping update',
                                'url': 'https://example.org/suez',
                                'published_at': '2026-10-16T00:00:00Z',
                                'retrieved_at': '2026-10-17T00:00:00Z'}]}
    result = resolve_judge_turn(state, 'What is our stock and latest Suez news?', now=NOW)
    assert result['kind'] == 'internal_answer'
    assert 'stock' in result['answer'].lower() and 'Suez Canal shipping update' in result['answer']
    assert result['follow_up_research']['query'].startswith('Suez Canal')


def test_undated_cached_item_does_not_claim_current_suez_evidence():
    state = snapshot()
    state['news'] = {'company_id': state['company_id'], 'cache_state': 'fresh',
                     'items': [{'item_id': 'suez-undated', 'synthetic': False,
                                'relevance_kind': 'sector', 'relevance_strength': 'strong',
                                'relevance_reason': 'Public shipping event',
                                'title': 'Suez Canal shipping update',
                                'url': 'https://example.org/suez',
                                'retrieved_at': '2026-10-17T00:00:00Z'}]}
    assert resolve_judge_turn(state, 'What is the Suez shipping status?', now=NOW)['kind'] == 'research_proposal'


def test_mixed_carbon_and_shipment_public_question_reads_local():
    state = snapshot()
    for message in ('What is our current carbon assessment and latest Suez news?',
                    'What are our current shipments and latest Suez news?'):
        result = resolve_judge_turn(state, message, now=NOW)
        assert result['kind'] == 'internal_answer'
        assert 'follow_up_research' in result


def test_unrelated_or_undated_completed_research_cannot_answer():
    state = snapshot()
    state['completed_research'] = [{'company_id': state['company_id'], 'context_version': 4,
                                    'status': 'completed', 'query': 'Port labor news',
                                    'answer': 'Some unrelated port labor answer.',
                                    'evidence_refs': ['source-2'],
                                    'checked_at': '2026-10-16T10:00:00Z'}]
    assert resolve_judge_turn(state, 'What is the Suez shipping status?', now=NOW)['kind'] == 'research_proposal'
    state['completed_research'][0]['query'] = 'Suez Canal shipping status'
    state['completed_research'][0].pop('checked_at')
    assert resolve_judge_turn(state, 'What is the Suez shipping status?', now=NOW)['kind'] == 'research_proposal'


def test_generic_news_word_does_not_match_unrelated_history():
    state = snapshot()
    state['completed_research'] = [{'company_id': state['company_id'], 'context_version': 4,
                                    'status': 'completed', 'query': 'Port labor news',
                                    'answer': 'Unrelated labor record.', 'evidence_refs': ['source-2'],
                                    'checked_at': '2026-10-16T10:00:00Z'}]
    result = resolve_judge_turn(state, 'What is shipping news?', now=NOW)
    assert result['kind'] == 'research_proposal'


def test_reviewed_price_question_does_not_get_quote_count():
    result = resolve_judge_turn(snapshot(), 'What is our reviewed copper cathode price?', now=NOW)
    assert result is None or 'saved reviewed quote records' not in result.get('answer', '')


def _public_news_state(title):
    state = snapshot()
    state['news'] = {'company_id': state['company_id'], 'cache_state': 'fresh',
                     'items': [{'item_id': 'public-1', 'synthetic': False,
                                'relevance_kind': 'sector', 'relevance_strength': 'strong',
                                'relevance_reason': 'Company sector exposure',
                                'title': title, 'url': 'https://example.org/public-1',
                                'published_at': '2026-10-16T00:00:00Z',
                                'retrieved_at': '2026-10-17T00:00:00Z'}]}
    return state


def test_specific_port_policy_does_not_use_unrelated_copper_headline():
    state = _public_news_state('Copper cathode production update')
    result = resolve_judge_turn(state, 'What is the port policy?', now=NOW)
    assert result['kind'] == 'research_proposal'
    assert 'port policy' in result['research_parameters']['query'].lower()
    assert 'copper' not in result['research_parameters']['query'].lower()


def test_specific_mixed_port_insurance_keeps_stock_and_exact_public_follow_up():
    state = _public_news_state('Copper cathode production update')
    result = resolve_judge_turn(state, 'What is our stock and latest port insurance rates?', now=NOW)
    assert result['kind'] == 'internal_answer'
    assert 'stock' in result['answer'].lower()
    assert 'Copper cathode production update' not in result['answer']
    query = result['follow_up_research']['query'].lower()
    assert 'port' in query and 'insurance' in query and 'rate' in query
    assert 'stock' not in query


def test_matching_port_policy_and_broad_world_or_headline_briefs_use_cache():
    policy = _public_news_state('Port policy update for shipping')
    assert 'Port policy update' in resolve_judge_turn(policy, 'What is the port policy?', now=NOW)['answer']
    copper = _public_news_state('Copper cathode production update')
    for message in ('What is happening in the world that can affect procurement and my business?',
                    'What are the news headlines for our business?'):
        result = resolve_judge_turn(copper, message, now=NOW)
        assert result['kind'] == 'internal_answer'
        assert 'Copper cathode production update' in result['answer']


def test_suez_history_requires_insurance_rate_topic_but_accepts_status_evidence():
    state = snapshot()
    state['completed_research'] = [{'company_id': state['company_id'], 'context_version': 4,
                                    'status': 'completed', 'query': 'Suez Canal blockage update',
                                    'answer': 'Previously reported blockage.',
                                    'evidence_refs': ['maersk-suez'],
                                    'checked_at': '2026-10-16T10:00:00Z'}]
    mismatch = resolve_judge_turn(state, 'What are Suez insurance rates?', now=NOW)
    assert mismatch['kind'] == 'research_proposal'
    assert 'insurance' in mismatch['research_parameters']['query'].lower()
    matched = resolve_judge_turn(state, 'What is Suez shipping status?', now=NOW)
    assert matched['kind'] == 'internal_answer'
    assert '2026-10-16' in matched['answer']


@pytest.mark.parametrize('company_id,message', [
    ('atlas-petrochem', 'Which of our inventory materials need attention and what sourcing actions should we take?'),
    ('gridspring-batteries', 'Analyze our inventory and sourcing across all materials'),
])
def test_broad_inventory_read_ranks_profile_and_exposes_all_material_analytics(company_id, message):
    state = snapshot(company_id)
    expected = inventory_analytics(state)
    result = resolve_judge_turn(state, message, now=NOW)
    assert result['kind'] == 'internal_answer'
    assert result['analytics'] == expected
    assert len(result['analytics']['materials']) == len(state['company']['materials'])
    assert f"{len(expected['materials'])} profile materials" in result['answer']
    assert 'stock cover' in result['answer'].lower() or 'days cover' in result['answer'].lower()
    assert 'shortest listed lead' in result['answer'].lower()
    assert 'saved reviewed quote' in result['answer'].lower()
    assert 'supplier profiles are not live quotes' in result['answer'].lower()
    assert 'compare quote validity' in result['answer'].lower()
    assert len(result['evidence_refs']) == len(expected['materials'])
    if company_id == 'gridspring-batteries':
        assert result['answer'].split('\n- ')[1].startswith('Lithium carbonate:')


def test_broad_inventory_plus_latest_public_keeps_local_analytics_and_separate_public_query():
    state = snapshot('gridspring-batteries')
    result = resolve_judge_turn(
        state, 'Analyze our inventory and sourcing across all materials and latest Suez news', now=NOW)
    assert result['kind'] == 'internal_answer'
    assert len(result['analytics']['materials']) == len(state['company']['materials'])
    assert result['follow_up_research']['query'].startswith('Suez Canal')
    assert 'inventory' not in result['follow_up_research']['query'].lower()


def test_direct_selected_stock_stays_simple_and_action_request_does_not_become_read():
    state = snapshot('gridspring-batteries')
    direct = resolve_judge_turn(state, 'How much is our current selected material stock?', now=NOW)
    assert direct['kind'] == 'internal_answer'
    assert 'analytics' not in direct
    assert state['company']['materials'][0]['name'] in direct['answer']
    assert resolve_judge_turn(
        state, 'Analyze our inventory and sourcing across all materials and send an RFQ.', now=NOW
    ) is None
