"""Model evidence prose must not leak unresolved answer placeholders."""

from procure.demo.company import get_company
from procure.demo.agent_tools import inventory_analytics
from procure.demo.operator import run_turn


def test_cited_news_explanation_renders_known_evidence_tokens_and_omits_unknown_ones():
    company = get_company('solara-cables')
    snapshot = {'company_id': 'solara-cables', 'context_version': 2, 'company': company,
                'news': {'items': [{'item_id': 'news-copper', 'title': 'Copper supply update',
                                    'matched_materials': ['copper'], 'publisher': 'Example'}]},
                'offers': [], 'plan': None, 'baseline_plan': None, 'buyer_draft': None,
                'selected_material_id': None, 'rfq': None, 'outbox': []}

    def provider(role, prompt):
        if role == 'coordinator':
            return {'intent': 'explain', 'material_id': 'copper', 'offer_id': None,
                    'delay_days': None, 'evidence_ids': []}
        if role == 'evidence':
            return {'material_id': 'copper', 'evidence_ids': ['news-copper'],
                    'assessment': 'conditional',
                    'answer': 'Stock is {{material.copper.stock}} {{material.copper.unit}}.',
                    'implication': ('Current stock cover stands at '
                                    '{{material.copper.stock_cover_days}} days against a lead time of '
                                    '{{material.copper.shortest_supplier_lead_days}} days.'),
                    'confidence_reason': 'Unsupported {{material.copper.secret_margin}}.',
                    'next_steps': ['Check {{material.copper.unavailable}} before changing the plan.']}
        return {'action': 'none', 'offer_id': None, 'delay_days': None, 'evidence_ids': []}

    result = run_turn(snapshot, 'Explain the copper news and stock cover.', provider,
                      model='test', provider_call='mocked')
    answer = result['assistant_text']
    copper = next(row for row in inventory_analytics(snapshot)['materials']
                  if row['material_id'] == 'copper')
    assert result['status'] == 'completed'
    assert (f"Current stock cover stands at {copper['stock_cover_days']} days "
            f"against a lead time of {copper['shortest_supplier_lead_days']} days.") in answer
    assert 'Copper cathode stock cover' in answer
    assert '{{' not in answer and '}}' not in answer
    assert 'secret_margin' not in answer and 'unavailable' not in answer
