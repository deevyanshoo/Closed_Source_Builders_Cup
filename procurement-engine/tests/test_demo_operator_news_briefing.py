"""Company news questions stay within dated, citable workspace evidence."""
import pytest

from procure.demo.company import get_company
from procure.demo.operator import OperatorValidationError, run_turn


def _snapshot():
    company = get_company('solara-cables')
    return {
        'company_id': 'solara-cables', 'context_version': 9, 'company': company,
        'news': {
            'checked_at': '2026-10-15T08:00:00+00:00', 'cache_state': 'fresh',
            'sources': [{'source_id': 'google_news_1', 'name': 'Google News RSS discovery',
                         'state': 'fresh', 'last_checked_at': '2026-10-15T08:00:00+00:00'}],
            'items': [{
                'item_id': 'news-copper', 'title': 'Copper supply concern for cable makers',
                'url': 'https://example.com/copper', 'publisher': 'Example News',
                'source_id': 'google_news_1', 'provider': 'Google News RSS',
                'synthetic': False,
                'published_at': '2026-10-14T06:00:00+00:00',
                'retrieved_at': '2026-10-15T08:00:00+00:00',
                'matched_materials': ['copper'], 'relevance_kind': 'direct_material',
                'relevance_reason': 'Headline names copper supply for cable makers.',
                'article': {'excerpt': '<p>Untrusted article content</p>'},
            }],
            'global_watch': {
                'status': 'fresh', 'attempted_at': '2026-10-15T08:00:00+00:00',
                'success_at': '2026-10-15T08:00:00+00:00', 'sources': [],
                'events': [{
                    'item_id': 'watch-port', 'title': 'Port congestion may affect shipping',
                    'url': 'https://example.com/port', 'publisher': 'Example Wire',
                    'source_id': 'global_shipping', 'provider': 'Google News RSS',
                    'published_at': '2026-10-14T07:00:00+00:00',
                    'retrieved_at': '2026-10-15T08:00:00+00:00',
                    'exposure': {'kind': 'background', 'matched_materials': [],
                                 'matched_routes': [],
                                 'reason': 'No recorded company exposure.',
                                 'next_action': 'Read the original source.'},
                }],
            },
        },
        'offers': [], 'plan': None, 'baseline_plan': None, 'buyer_draft': None,
        'selected_material_id': None, 'rfq': None, 'outbox': [],
    }


class NewsProvider:
    def __init__(self, *, answer=None, cited=True, cited_ids=None, material='copper',
                 intent='answer', implication='Check the supplier and current price evidence.',
                 confidence_reason='Headline metadata does not verify an actual delay.',
                 next_steps=None):
        self.prompts = []
        self.answer = answer
        self.cited = cited
        self.cited_ids = cited_ids
        self.material = material
        self.intent = intent
        self.implication = implication
        self.confidence_reason = confidence_reason
        self.next_steps = ['Check current suppliers.'] if next_steps is None else next_steps

    def __call__(self, role, prompt):
        self.prompts.append((role, prompt))
        if role == 'coordinator':
            return {'intent': self.intent, 'material_id': self.material, 'offer_id': None,
                    'delay_days': None, 'evidence_ids': []}
        if role == 'evidence':
            return {'material_id': self.material,
                    'evidence_ids': (self.cited_ids if self.cited_ids is not None else
                                     ['news-copper'] if self.cited else []),
                    'assessment': 'conditional',
                    'implication': self.implication,
                    'confidence_reason': self.confidence_reason,
                    'next_steps': self.next_steps,
                    'answer': self.answer or (
                        'For {{company.name}}, {{news.news-copper.title}} may affect '
                        '{{material.copper.name}} procurement. Published '
                        '{{news.news-copper.published_date}} by '
                        '{{news.news-copper.publisher}}.')}
        return {'action': 'none', 'offer_id': None, 'delay_days': None,
                'evidence_ids': []}


def test_world_procurement_question_uses_dated_company_news_and_cited_answer():
    provider = NewsProvider()
    result = run_turn(_snapshot(), 'What current world news can affect my business procurement?',
                      provider, model='test', provider_call='mocked')

    assert result['status'] == 'completed'
    assert result['proposal_kind'] is None
    assert result['evidence_ids'] == ['news-copper']
    assert 'Copper supply concern for cable makers' in result['assistant_text']
    assert '2026-10-14' in result['assistant_text']
    assert 'T06:00:00' not in result['assistant_text']
    assert 'news-copper' not in result['assistant_text']
    assert [role for role, _ in provider.prompts] == ['coordinator', 'evidence', 'procurement']
    prompt = provider.prompts[1][1]
    assert 'google_news_1' in prompt
    assert 'published_at' in prompt
    assert 'https://example.com/copper' in prompt
    assert 'watch-port' in prompt
    assert 'No recorded company exposure.' in prompt
    assert 'news.<allowedID>.title' in prompt
    assert 'news.<allowedID>.published_at' in prompt
    assert 'Keep opaque evidence IDs in evidence_ids only' in prompt
    assert '<p>Untrusted article content</p>' not in prompt


def test_cited_news_question_cannot_collapse_to_inventory_only_answer():
    provider = NewsProvider(answer='{{company.name}} has {{material.copper.stock}} {{material.copper.unit}} in stock.')
    result = run_turn(_snapshot(), 'What current world news can affect my business procurement?',
                      provider, model='test', provider_call='mocked')

    assert result['status'] == 'completed'
    assert 'Copper supply concern for cable makers' in result['assistant_text']
    assert result['evidence_ids'] == ['news-copper']
    assert 'news-copper' not in result['assistant_text']
    assert '2000 kg' not in result['assistant_text']


def test_news_question_rejects_generic_answer_without_current_citation():
    provider = NewsProvider(answer='{{company.name}} has {{material.copper.stock}} {{material.copper.unit}} in stock.',
                            cited=False)
    with pytest.raises(OperatorValidationError, match='headline'):
        run_turn(_snapshot(), 'What current world news can affect my business procurement?',
                 provider, model='test', provider_call='mocked')


def test_broad_company_news_question_can_cite_owned_material_without_selected_material():
    result = run_turn(_snapshot(), 'What current world news can affect my business procurement?',
                      NewsProvider(material=None), model='test', provider_call='mocked')
    assert result['evidence_ids'] == ['news-copper']
    assert 'Copper supply concern for cable makers' in result['assistant_text']


def test_news_question_without_company_match_reports_evidence_gap():
    value = _snapshot()
    value['news']['items'] = []
    provider = NewsProvider(answer='{{company.name}} has {{material.copper.stock}} {{material.copper.unit}} in stock.',
                            cited=False)
    result = run_turn(value, 'What current world news can affect my business procurement?',
                      provider, model='test', provider_call='mocked')

    assert result['status'] == 'completed'
    assert result['evidence_ids'] == []
    assert 'No company-matched public headline' in result['assistant_text']
    assert '2000 kg' not in result['assistant_text']
    assert 'watch-port' not in result['evidence_ids']


def test_relevant_company_headline_survives_large_unmatched_news_view():
    value = _snapshot()
    relevant = value['news']['items'][0]
    value['news']['items'] = [
        {'item_id': f'unrelated-{index}', 'title': f'Unrelated sports news {index}',
         'relevance_kind': 'unmatched', 'matched_materials': []}
        for index in range(30)
    ] + [relevant]

    result = run_turn(value, 'What current world news can affect my business procurement?',
                      NewsProvider(), model='test', provider_call='mocked')
    assert result['evidence_ids'] == ['news-copper']
    assert 'Copper supply concern for cable makers' in result['assistant_text']


def test_read_only_news_question_recovers_from_inspect_classification():
    result = run_turn(_snapshot(), 'What current world news can affect my business procurement?',
                      NewsProvider(intent='inspect'), model='test', provider_call='mocked')
    assert result['evidence_ids'] == ['news-copper']
    assert 'Copper supply concern for cable makers' in result['assistant_text']


def test_broad_company_news_answer_is_not_limited_to_one_selected_material():
    result = run_turn(_snapshot(), 'What current world news can affect my business procurement?',
                      NewsProvider(material='polymer'), model='test', provider_call='mocked')
    assert result['evidence_ids'] == ['news-copper']
    assert 'Copper supply concern for cable makers' in result['assistant_text']


def test_observed_headline_remains_citable_after_synthetic_candidates():
    value = _snapshot()
    observed = value['news']['items'][0]
    value['news']['items'] = [
        {'item_id': f'synthetic-{index}', 'title': 'Synthetic copper headline',
         'matched_materials': ['copper'], 'relevance_kind': 'direct_material',
         'synthetic': True}
        for index in range(3)
    ] + [observed]

    result = run_turn(value, 'What current world news can affect my business procurement?',
                      NewsProvider(), model='test', provider_call='mocked')
    assert result['evidence_ids'] == ['news-copper']


def test_fallback_keeps_two_dated_sources_and_evidence_agents_assessment():
    value = _snapshot()
    second = dict(value['news']['items'][0],
                  item_id='news-aluminium', title='Aluminium shipments face scrutiny',
                  url='https://example.com/aluminium', publisher='Trade Wire',
                  published_at='2026-10-13T09:30:00+00:00',
                  matched_materials=['aluminium'])
    value['news']['items'].append(second)
    provider = NewsProvider(
        answer='{{company.name}} has {{material.copper.stock}} {{material.copper.unit}} in stock.',
        cited_ids=['news-copper', 'news-aluminium'],
        implication='Supply timing could tighten if the reported conditions persist.',
        confidence_reason='Headlines alone do not verify supplier impact.',
        next_steps=['Confirm current supplier lead times.'])

    result = run_turn(value, 'What current world news can affect my business procurement?',
                      provider, model='test', provider_call='mocked')
    answer = result['assistant_text']
    assert result['evidence_ids'] == ['news-copper', 'news-aluminium']
    for text in ('Copper supply concern for cable makers', 'Example News', '2026-10-14',
                 'Aluminium shipments face scrutiny', 'Trade Wire', '2026-10-13',
                 'Potential impact: Supply timing could tighten if the reported conditions persist.',
                 'Confidence: Headlines alone do not verify supplier impact.',
                 'Next check: Confirm current supplier lead times.'):
        assert text in answer
    assert 'Sources:' not in answer
    assert 'news-copper' not in answer and 'news-aluminium' not in answer
    assert 'T09:30:00' not in answer and '2000 kg' not in answer


def test_indirect_news_answer_keeps_id_in_metadata_only():
    value = _snapshot()
    value['news']['items'] = [dict(
        value['news']['items'][0], item_id='news-sector',
        title='Cable manufacturing operations face uncertainty',
        matched_materials=[], relevance_kind='sector',
        matched_operations=['production'],
        relevance_reason='Headline connects the company sector to operations: production')]
    provider = NewsProvider(
        answer='{{company.name}} has {{material.copper.stock}} {{material.copper.unit}} in stock.',
        cited_ids=['news-sector'])

    result = run_turn(value, 'What current world news can affect my business procurement?',
                      provider, model='test', provider_call='mocked')
    assert result['evidence_ids'] == ['news-sector']
    assert 'indirect company relevance' in result['assistant_text'].lower()
    assert 'news-sector' not in result['assistant_text']
