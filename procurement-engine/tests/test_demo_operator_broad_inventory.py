"""Broad inventory and sourcing reads are grounded in the selected workspace."""

from datetime import date

import pytest

from procure.demo.agent_tools import inventory_analytics
from procure.demo.company import get_company, list_companies
from procure.demo.operator import OperatorValidationError, _broad_inventory_scope, run_turn
from procure.demo.quote_service import (correct_offer, extract_fixture, fixture_review_values,
                                       new_offer, save_offer)


def snapshot(company):
    return {'company_id': company['company_id'], 'context_version': 2,
            'company': company, 'news': {'items': []}, 'offers': [], 'plan': None,
            'baseline_plan': None, 'buyer_draft': None, 'selected_material_id': None,
            'rfq': None, 'outbox': []}


def broad_turn(value, material_id, *, answer=None, message=None):
    material = next(row for row in value['company']['materials']
                    if row['material_id'] == material_id)

    def provider(role, prompt):
        if role == 'coordinator':
            return {'intent': 'explain', 'material_id': material_id, 'offer_id': None,
                    'delay_days': None, 'evidence_ids': []}
        if role == 'evidence':
            return {'material_id': material_id, 'evidence_ids': [],
                    'assessment': 'supported', 'implication': 'Check the current supplier terms.',
                    'confidence_reason': 'Fictional company profile data only.', 'next_steps': [],
                    'answer': answer or (f'Current stock is {{{{material.{material_id}.stock}}}} '
                                         'and lead is 999999 days.')}
        return {'action': 'none', 'offer_id': None, 'delay_days': None,
                'evidence_ids': []}

    message = message or (f'Explain the inventory position and sourcing options for '
                          f'{material["name"]} at {value["company"]["name"]}.')
    return run_turn(value, message, provider, model='test', provider_call='mocked')


@pytest.mark.parametrize('company', list_companies(), ids=lambda row: row['company_id'])
def test_broad_material_read_uses_each_company_profile_without_model_numbers(company):
    value = snapshot(company)
    material = company['materials'][0]
    analytics = next(row for row in inventory_analytics(value)['materials']
                     if row['material_id'] == material['material_id'])
    response = broad_turn(value, material['material_id'])
    text = response['assistant_text']
    assert response['status'] == 'completed'
    assert company['name'] in text and material['name'] in text
    assert f"{analytics['stock']} {analytics['unit']}" in text
    assert f"{analytics['shortest_supplier_lead_days']} days" in text
    assert any(supplier['name'].split(' [')[0] in text for supplier in analytics['suppliers'])
    assert 'supplier profile' in text.lower() and 'saved reviewed quote' in text.lower()
    assert '999999' not in text
    assert response['candidate'] is None and response['utility'] is None


def test_broad_read_distinguishes_saved_owned_reviewed_quote_from_supplier_profile():
    value = snapshot(get_company('solara-cables'))
    value['offers'].append(saved_offer('eu-copper'))
    text = broad_turn(value, 'copper')['assistant_text']
    assert 'saved reviewed quote' in text.lower()
    assert 'compare' in text.lower()
    assert 'No saved reviewed quote' not in text
    assert 'supplier profile' in text.lower()


def saved_offer(fixture_id):
    offer = new_offer(extract_fixture(fixture_id, mode='rules', as_of=date(2026, 10, 14)),
                      offer_id='reviewed-' + fixture_id, today=date(2026, 10, 14))
    offer['fixture_id'] = fixture_id
    for field, reviewed in fixture_review_values(fixture_id).items():
        offer = correct_offer(offer, field=field, value=reviewed,
                              today=date(2026, 10, 14))
    return save_offer(offer, today=date(2026, 10, 14))


def test_draft_foreign_and_other_material_offers_do_not_count_as_owned_saved_quotes():
    value = snapshot(get_company('solara-cables'))
    draft = new_offer(extract_fixture('eu-copper', mode='rules', as_of=date(2026, 10, 14)),
                      offer_id='draft-eu-copper', today=date(2026, 10, 14))
    value['offers'] = [draft, saved_offer('solara-cables-polymer-selected'),
                       saved_offer('forgeline-components-copper-selected')]
    text = broad_turn(value, 'copper')['assistant_text']
    assert 'No saved reviewed quote' in text
    assert 'supplier profile' in text.lower()


def test_saved_label_does_not_make_an_incomplete_offer_reviewed():
    value = snapshot(get_company('solara-cables'))
    partial = saved_offer('eu-copper')
    partial['items'][0]['pricing']['status'] = 'missing'
    partial['items'][0]['pricing']['value'] = None
    value['offers'] = [partial]
    assert 'No saved reviewed quote' in broad_turn(value, 'copper')['assistant_text']


def test_named_nonprimary_material_uses_its_own_unit_and_supplier():
    value = snapshot(get_company('loomworks'))
    material = next(row for row in value['company']['materials'] if row['material_id'] == 'dyes')
    text = broad_turn(value, 'dyes')['assistant_text']
    assert material['name'] in text
    assert f"{material['stock']} {material['quantity_unit']}" in text
    assert 'Chromex' in text
    assert 'Cotton fibre' not in text


@pytest.mark.parametrize('message', [
    'Give me an inventory and sourcing overview.',
    'Where are we short and what sourcing options do we have?',
])
def test_company_wide_overview_shows_bounded_material_specific_risks(message):
    value = snapshot(get_company('gridspring-batteries'))
    result = broad_turn(value, 'lithium', message=message)
    text = result['assistant_text']
    assert result['status'] == 'completed'
    assert 'GridSpring Batteries' in text
    assert 'company-wide' in text.lower()
    assert 'stock' in text.lower() and 'supplier profile' in text.lower()
    assert 'tonne' in text or 'roll' in text
    assert 'named material' in text
    assert text.count('\n- ') == 3
    assert result['candidate'] is None and result['utility'] is None


def test_missing_use_and_supplier_lead_are_explained_without_inventing_cover():
    value = snapshot(get_company('solara-cables'))
    value['company']['materials'][0]['consumption_per_day'] = None
    for supplier in value['company']['suppliers']:
        if 'copper' in supplier['materials']:
            supplier['lead_time_days'] = None
    text = broad_turn(value, 'copper')['assistant_text']
    assert 'daily use is unavailable' in text.lower()
    assert 'stock cover is unavailable' in text.lower()
    assert 'no listed supplier lead time' in text.lower()
    assert 'None days' not in text


def test_mixed_action_request_stays_on_existing_operator_path():
    value = snapshot(get_company('solara-cables'))
    message = 'Explain copper inventory and sourcing options and send an RFQ.'
    result = broad_turn(value, 'copper', message=message,
                        answer='Stock is {{material.copper.stock}} {{material.copper.unit}}.')
    assert result['agent_steps'][0]['agent'] == 'coordinator'
    assert 'company-wide' not in result['assistant_text'].lower()


def test_foreign_company_name_does_not_use_current_workspace_canonical_answer():
    value = snapshot(get_company('solara-cables'))
    result = broad_turn(value, 'copper', message=(
        'Explain the inventory position and sourcing options for Copper cathode '
        'at ForgeLine Components.'))
    assert result['status'] == 'needs_clarification'
    assert result['candidate'] is None
    assert 'active company' in result['assistant_text']
    assert '2000' not in result['assistant_text']


@pytest.mark.parametrize('reference', ['nickel', 'unobtainium'])
def test_unknown_explicit_material_never_becomes_company_wide(reference):
    value = snapshot(get_company('solara-cables'))
    message = f'Explain inventory position and sourcing options for {reference} at Solara Cables.'
    assert _broad_inventory_scope(value, message) is None


def test_current_material_requires_agreement_between_selected_and_buyer_draft():
    value = snapshot(get_company('solara-cables'))
    value['selected_material_id'] = 'polymer'
    value['buyer_draft'] = {'material': 'polymer'}
    message = 'Explain the inventory and sourcing position for the current material.'
    assert _broad_inventory_scope(value, message) == 'polymer'
    assert 'Cable insulation polymer' in broad_turn(value, 'polymer', message=message)['assistant_text']
    value['buyer_draft']['material'] = 'copper'
    assert _broad_inventory_scope(value, message) is None
    value['selected_material_id'] = None
    assert _broad_inventory_scope(value, message) is None


@pytest.mark.parametrize('ending', ['Do not change anything.', 'Without changing anything.'])
def test_negated_change_clause_preserves_read_only_inventory_overview(ending):
    value = snapshot(get_company('solara-cables'))
    message = f'Give me an inventory and sourcing overview. {ending}'
    assert _broad_inventory_scope(value, message) == 'company-wide'
    assert broad_turn(value, 'copper', message=message)['status'] == 'completed'
    assert _broad_inventory_scope(
        value, message + ' Send an RFQ to the supplier.') is None


def test_explicit_provenance_question_preserves_sample_not_connected_disclosure():
    value = snapshot(get_company('solara-cables'))
    message = ('Give me a real inventory and sourcing overview. '
               'Are these inventory and supplier records connected?')
    text = broad_turn(value, 'copper', message=message)['assistant_text']
    assert 'sample company records' in text
    assert 'not a connected company system' in text


@pytest.mark.parametrize('reference', [
    'the current company', 'current company', 'the selected workspace', 'selected company',
])
def test_company_wide_voice_reference_does_not_require_a_material_selection(reference):
    value = snapshot(get_company('solara-cables'))
    message = (f'Give me a brief inventory and sourcing overview for {reference}. '
               'Which material needs attention first, and which listed supplier has the shortest lead time?')
    assert _broad_inventory_scope(value, message) == 'company-wide'
    text = broad_turn(value, 'copper', message=message)['assistant_text']
    assert 'Company-wide inventory and sourcing overview for Solara Cables' in text
    assert 'Copper cathode' in text and 'Europa Copper SA' in text


def test_broad_read_keeps_unknown_fact_guard_for_unrecognized_questions():
    value = snapshot(get_company('solara-cables'))
    material_id = 'copper'

    def provider(role, prompt):
        if role == 'coordinator':
            return {'intent': 'answer', 'material_id': material_id, 'offer_id': None,
                    'delay_days': None, 'evidence_ids': []}
        if role == 'evidence':
            return {'material_id': material_id, 'evidence_ids': [],
                    'assessment': 'supported', 'implication': 'Check the profile.',
                    'confidence_reason': 'Fictional facts only.', 'next_steps': [],
                    'answer': 'The price is {{material.copper.secret_price}}.'}
        return {'action': 'none', 'offer_id': None, 'delay_days': None,
                'evidence_ids': []}

    with pytest.raises(OperatorValidationError):
        run_turn(value, 'What is the secret copper price?', provider,
                 model='test', provider_call='mocked')
