"""Presentation preserves facts and canonical identities."""
from procure.demo.agent_tools import inventory_analytics
from procure.demo.company import get_company, list_companies
from procure.demo.operator import run_turn
from procure.demo.presentation import present_turn, supplier_display_name
from procure.demo.live_voice import INSTRUCTION


def snapshot():
    company = get_company('solara-cables')
    return {'company': company, 'company_id': company['company_id'], 'context_version': 1,
            'news': {'items': []}, 'offers': [], 'plan': None, 'baseline_plan': None,
            'buyer_draft': None, 'selected_material_id': None, 'rfq': None, 'outbox': []}


class Provider:
    def __init__(self, intent='answer', answer=None, parameters=None, material='copper'):
        self.intent, self.answer, self.parameters, self.material = intent, answer, parameters, material

    def __call__(self, role, prompt):
        if role == 'coordinator':
            result = {'intent': self.intent, 'material_id': self.material, 'offer_id': None,
                      'delay_days': None, 'evidence_ids': []}
            if self.parameters is not None:
                result['parameters'] = self.parameters
            return result
        if role == 'evidence':
            result = {'material_id': self.material, 'evidence_ids': [], 'assessment': 'supported',
                      'implication': 'Check the supplier schedule.',
                      'confidence_reason': 'Catalogue facts only.', 'next_steps': []}
            if self.answer is not None:
                result['answer'] = self.answer
            return result
        return {'action': self.intent if self.intent == 'simulate_email' else 'none',
                'offer_id': None, 'delay_days': None, 'evidence_ids': []}


def turn(value, question, provider):
    return run_turn(value, question, provider, model='test', provider_call='mocked')


def test_supplier_display_name_only_removes_trailing_annotation():
    assert supplier_display_name('Europa Copper SA [SYNTHETIC]') == 'Europa Copper SA'
    assert supplier_display_name('Europa [SYNTHETIC] extra') == 'Europa [SYNTHETIC] extra'
    assert supplier_display_name('Europa Copper SA') == 'Europa Copper SA'


def test_stock_and_lead_answers_keep_facts_without_repeated_fixture_qualifiers():
    value = snapshot()
    stock = turn(value, 'How much copper stock do we have?', Provider(answer='Stock is {{material.copper.stock}} {{material.copper.unit}}.'))
    lead = turn(value, 'What is Europa Copper SA lead time?', Provider(answer='Lead is {{supplier.solara-cables-supplier-3.lead_days}} days.'))
    assert '2000 kg' in stock['assistant_text']
    assert '10 days' in lead['assistant_text']
    assert 'Europa Copper SA' in lead['assistant_text']
    assert '[SYNTHETIC]' not in lead['assistant_text']
    assert 'Source: fictional company profile' not in stock['assistant_text'] + lead['assistant_text']
    assert value['company']['suppliers'][2]['name'] == 'Europa Copper SA [SYNTHETIC]'


def test_shortest_lead_and_stock_cover_ignore_model_stock_token_for_each_company():
    for company in list_companies():
        material = company['materials'][0]
        material_id = material['material_id']
        value = snapshot()
        value['company'], value['company_id'] = company, company['company_id']
        analytics = inventory_analytics(value)['materials'][0]
        fastest = [supplier for supplier in company['suppliers']
                   if material_id in supplier.get('materials', [])
                   and supplier.get('lead_time_days') == analytics['shortest_supplier_lead_days']]
        result = turn(value,
                      f'Which supplier has the shortest {material_id} lead time, and how many days of stock cover do we have?',
                      Provider(answer=f'Lead is {{{{material.{material_id}.shortest_supplier_lead_days}}}} days; '
                                      f'stock cover is {{{{material.{material_id}.stock}}}} days.',
                               material=material_id))
        answer = result['assistant_text']
        assert f"{analytics['shortest_supplier_lead_days']} days" in answer
        assert f"{analytics['stock_cover_days']} days" in answer
        assert f"{material['stock']} stock cover days" not in answer
        for supplier in fastest:
            assert supplier_display_name(supplier['name']) in answer


def test_tied_shortest_supplier_leads_name_both_suppliers():
    value = snapshot()
    company = value['company']
    copper_suppliers = [supplier for supplier in company['suppliers']
                        if 'copper' in supplier.get('materials', [])]
    minimum = min(supplier['lead_time_days'] for supplier in copper_suppliers)
    tied = [supplier for supplier in copper_suppliers if supplier['lead_time_days'] == minimum]
    if len(tied) < 2:
        other = next(supplier for supplier in copper_suppliers if supplier['lead_time_days'] != minimum)
        other['lead_time_days'] = minimum
        tied.append(other)
    result = turn(value, 'Which copper supplier has the fastest listed lead time?',
                  Provider(answer='It is {{material.copper.stock}} days.'))
    answer = result['assistant_text']
    assert f'{minimum} days' in answer
    for supplier in tied:
        assert supplier_display_name(supplier['name']) in answer


def test_days_of_cover_uses_analytics_and_handles_missing_daily_use():
    value = snapshot()
    result = turn(value, 'How many days of cover do we have for copper?',
                  Provider(answer='{{material.copper.stock}} days of cover.'))
    assert '6.0000600006000060000600006 days' in result['assistant_text']
    assert '2000 days' not in result['assistant_text']

    value['company']['materials'][0]['consumption_per_day'] = None
    result = turn(value, 'What is copper stock coverage?',
                  Provider(answer='{{material.copper.stock}} days.'))
    assert 'unavailable' in result['assistant_text']
    assert '2000 days' not in result['assistant_text']


def test_provenance_question_discloses_sample_records_and_public_news():
    result = turn(snapshot(), 'Where did company inventory and news data come from?',
                  Provider(answer='Copper stock is {{material.copper.stock}} {{material.copper.unit}}.'))
    assert 'sample company records' in result['assistant_text'].lower()
    assert 'public sources' in result['assistant_text'].lower()
    assert '2000 kg' in result['assistant_text']


def test_direct_sample_origin_questions_are_answered_truthfully():
    from procure.demo.presentation import present_answer
    for question in ('Are these company records simulated?', 'Is the supplier data fictional?',
                     'Is the inventory data from a connected company system?'):
        answer = present_answer('We track 2000 kg.', question, [])
        assert 'sample company records' in answer
        assert '2000 kg' in answer
    assert present_answer('Review this unsent draft.',
                          'Draft a simulated email to our copper supplier.', []) == 'Review this unsent draft.'
    assert present_answer('Lead time is 10 days.',
        'What is Europa Copper SA [SYNTHETIC] supplier lead time?', []) == 'Lead time is 10 days.'


def test_display_name_request_keeps_canonical_recipient_and_unsent_status():
    value = snapshot()
    raw = value['company']['suppliers'][2]['name']
    provider = Provider('simulate_email', parameters={
        'recipient_label': raw, 'subject': 'Copper availability',
        'body': 'Please confirm available capacity.'})
    result = turn(value, 'Draft a simulated email to Europa Copper SA about copper', provider)
    assert result['candidate']['parameters']['recipient_label'] == raw
    assert 'Europa Copper SA' in result['assistant_text']
    assert '[SYNTHETIC]' not in result['assistant_text']
    assert 'unsent' in result['assistant_text'].lower()


def test_default_message_body_reads_like_business_draft_while_review_stays_simulated():
    raw = snapshot()['company']['suppliers'][2]['name']
    result = turn(snapshot(), 'Draft a simulated email to Europa Copper SA about copper',
                  Provider('simulate_email'))
    parameters = result['candidate']['parameters']
    assert parameters['recipient_label'] == raw
    assert 'synthetic' not in parameters['subject'].lower() + parameters['body'].lower()
    assert 'unsent' in result['assistant_text'].lower()


def test_live_voice_instruction_separates_natural_speech_from_canonical_tools():
    assert 'omit repeated fixture qualifiers' in INSTRUCTION.lower()
    assert 'exact canonical recipient' in INSTRUCTION.lower()
    assert 'source' in INSTRUCTION.lower()
    assert 'no external delivery' in INSTRUCTION.lower()


def test_live_tool_presentation_changes_answer_only_and_preserves_proposal_arguments():
    raw = 'Europa Copper SA [SYNTHETIC]'
    original = {'assistant_text': f'Review a simulated unsent message to {raw}.',
                'proposal': {'parameters': {'recipient_label': raw}}, 'evidence_ids': ['e1'],
                'analytics': {'stock': '2000', 'unit': 'kg'}}
    shown = present_turn(original, [raw])
    assert shown['assistant_text'] == 'Review a simulated unsent message to Europa Copper SA.'
    assert shown['proposal']['parameters']['recipient_label'] == raw
    assert shown['evidence_ids'] == ['e1'] and shown['analytics']['stock'] == '2000'
    assert original['assistant_text'].endswith('[SYNTHETIC].')
