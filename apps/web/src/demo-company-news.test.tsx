import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { filterNewsItems, NewsWorkspace, OutboxWorkspace } from './demo-company-news';
import type { CompanyProfile, NewsView } from './demo-api';

const company: CompanyProfile = {
  company_id: 'solara-cables', name: 'Solara Cables', sector: 'Manufacturing', size: 'Mid-size', synthetic: true,
  as_of: '2026-10-14', description: 'Fictional company', locations: [{ country: 'India', city: 'Pune' }], reporting_currency: 'USD',
  materials: [{ material_id: 'copper', name: 'Copper', quantity_unit: 'kg', stock: '2000', consumption_per_day: '100', monthly_requirement: '3000', criticality: 'high', keywords: ['copper'] }],
  suppliers: [], orders: [], routes: [], currencies: ['USD'], news_keywords: ['copper'],
  quote_coverage: { materials: ['copper'], fixture_ids: [], note: 'No fixture' }, buyer_defaults: null,
};
const news: NewsView = {
  company_id: 'solara-cables', context_version: 2, checked_at: '2026-10-14T10:00:00Z', cache_state: 'stale',
  sources: [{ source_id: 'eia', name: 'EIA', state: 'unavailable', last_checked_at: '2026-10-14T09:00:00Z', reason: 'Timeout' }],
  items: [
    { item_id: 'a', title: 'Copper lane update', url: 'https://example.com/copper', publisher: 'Example', provider: 'GDELT', published_at: null, first_seen_at: '2026-10-14T08:00:00Z', retrieved_at: '2026-10-14T09:00:00Z', source_tier: 'secondary', source_reliability: 'limited', reliability_reason: 'Single report', category: 'trade', matched_materials: ['copper'], matched_routes: [], matched_currencies: [], relevance_reason: 'Mentions copper supply', implication: 'If the lane is affected, review lead times.', implication_confidence: 'low', next_steps: ['Check supplier'], synthetic: false },
    { item_id: 'b', title: 'Untrusted link', url: 'javascript:alert(1)', publisher: null, provider: 'GDELT', published_at: null, first_seen_at: null, retrieved_at: '2026-10-14T09:00:00Z', source_tier: 'secondary', source_reliability: 'limited', reliability_reason: 'Unverified', category: 'energy', matched_materials: [], matched_routes: [], matched_currencies: ['USD'], relevance_reason: 'Currency exposure', implication: 'If costs move, recheck assumptions.', implication_confidence: 'low', next_steps: [], synthetic: false },
    { item_id: 'c', title: 'Unmatched headline', url: 'https://example.com/unmatched', publisher: 'Example', provider: 'GDELT', published_at: null, first_seen_at: null, retrieved_at: '2026-10-14T09:00:00Z', source_tier: 'secondary', source_reliability: 'limited', reliability_reason: 'Unverified', category: 'general', matched_materials: [], matched_routes: [], matched_currencies: [], relevance_reason: 'No recorded match', implication: 'No company-specific implication established.', implication_confidence: 'none', next_steps: [], synthetic: false },
  ], fx: [], limitations: ['Coverage is incomplete'],
};

describe('company news workspace', () => {
  it('shows independent successful news, FX and attempted refresh times', () => {
    const scheduled = { ...news, collection: { mode: 'scheduled', status: 'partial',
      news_success_at: '2026-10-14T08:00:00Z', fx_success_at: '2026-10-14T09:00:00Z',
      attempted_at: '2026-10-14T10:00:00Z' } };
    const html = renderToStaticMarkup(<NewsWorkspace company={company} view={scheduled} loading={false} error="" onRefresh={() => {}} />);
    expect(html).toContain('Hourly background collection');
    expect(html).toContain('Last successful news collection');
    expect(html).toContain('Last successful FX collection');
    expect(html).toContain('Last recorded attempt');
    for (const stamp of [scheduled.collection.news_success_at, scheduled.collection.fx_success_at, scheduled.collection.attempted_at]) {
      expect(html).toContain(new Date(stamp).toLocaleString());
    }
    expect(html).toContain('partial');
    const manual = renderToStaticMarkup(<NewsWorkspace company={company} view={{ ...scheduled, collection: { ...scheduled.collection, mode: 'manual', status: 'manual' } }} loading={false} error="" onRefresh={() => {}} />);
    expect(manual).toContain('Manual source check');
    expect(manual).not.toContain('Hourly background collection');
  });
  it('shows animated source-check feedback only while fetching and keeps prior evidence dated', () => {
    const pending = renderToStaticMarkup(<NewsWorkspace company={company} view={news} loading={true} error="" onRefresh={() => {}} />);
    expect(pending).toContain('<canvas');
    expect(pending).toContain('data-activity="searching"');
    expect(pending).toContain('previous check');
    const settled = renderToStaticMarkup(<NewsWorkspace company={company} view={news} loading={false} error="Timeout" onRefresh={() => {}} />);
    expect(settled).not.toContain('<canvas');
    expect(settled).toContain('Source check unavailable');
  });
  it('filters observed items by both material and category without treating source reliability as relevance', () => {
    expect(filterNewsItems(news.items, 'copper', 'trade').map(item => item.item_id)).toEqual(['a']);
    expect(filterNewsItems(news.items, 'copper', 'energy')).toEqual([]);
    expect(filterNewsItems(news.items, 'all', 'energy').map(item => item.item_id)).toEqual(['b']);
    expect(filterNewsItems(news.items, 'all', 'all').map(item => item.item_id)).toEqual(['a', 'b']);
    expect(filterNewsItems(news.items, 'all', 'all', true).map(item => item.item_id)).toEqual(['a', 'b', 'c']);
  });

  it('shows source outages, timing and conditional exposure while refusing unsafe links', () => {
    const html = renderToStaticMarkup(<NewsWorkspace company={company} view={news} loading={false} error="" onRefresh={() => {}} />);
    expect(html).toContain('Coverage: stale');
    expect(html).toContain('EIA: unavailable');
    expect(html).toContain('Timeout');
    expect(html).toContain('Publication time unavailable');
    expect(html).toContain('Conditional implication (low)');
    expect(html).toContain('If the lane is affected');
    expect(html).toContain('Coverage is incomplete');
    expect(html).toContain('https://example.com/copper');
    expect(html).not.toContain('href="javascript:');
    expect(html).not.toContain('Unmatched headline');
    expect(html).toContain('Show unmatched headlines');
  });
  it('shows observed matched coverage and explains unmatched-only or stale refresh states honestly', () => {
    const unmatched = Array.from({ length: 23 }, (_, index) => ({ ...news.items[2], item_id: `unmatched-${index}` }));
    const matched = Array.from({ length: 8 }, (_, index) => ({ ...news.items[0], item_id: `matched-${index}` }));
    const loaded = { ...news, items: [...matched, ...unmatched] };
    const html = renderToStaticMarkup(<NewsWorkspace company={company} view={loaded} loading={false} error="" onRefresh={() => {}} />);
    expect(html).toContain('8 company-relevant of 31 observed items');
    expect(html.match(/Copper lane update/g)).toHaveLength(8);
    expect(html).not.toContain('Unmatched headline');
    const stale = renderToStaticMarkup(<NewsWorkspace company={company} view={{ ...loaded, items: unmatched }} loading={false} error="Session expired" onRefresh={() => {}} />);
    expect(stale).toContain('23 observed headlines have no recorded company match');
    expect(stale).toContain('Unmatched headline');
    expect(stale).toContain('Previously displayed results');
    const refreshing = renderToStaticMarkup(<NewsWorkspace company={company} view={loaded} loading={true} error="" onRefresh={() => {}} />);
    expect(refreshing).toContain('The items below are from the previous check');
    expect(refreshing).toContain('Checking sources…');
  });
  it('counts direct and indirect matches separately and explains heuristic metadata without treating it as probability', () => {
    const direct = { ...news.items[0], relevance_kind: 'direct_material' as const, relevance_score: 0.8, matched_operations: ['wire production'], summary: 'Copper line disruption reported.' };
    const indirect = { ...news.items[2], item_id: 'sector', relevance_kind: 'sector' as const, relevance_score: 0.4, matched_operations: ['wire production'], summary: 'Manufacturing activity reported.' };
    expect(filterNewsItems([direct, indirect], 'all', 'all').map(item => item.item_id)).toEqual(['a', 'sector']);
    const html = renderToStaticMarkup(<NewsWorkspace company={company} view={{ ...news, items: [direct, indirect] }} loading={false} error="" onRefresh={() => {}} />);
    expect(html).toContain('1 direct material match');
    expect(html).toContain('1 indirect match');
    expect(html).toContain('Wire production');
    expect(html).toContain('Uncalibrated relevance heuristic');
    expect(html).toContain('Copper line disruption reported.');
  });
  it('shows only this company’s server-confirmed simulated messages with no delivery claim', () => {
    const message = { message_id: 'm1', company_id: company.company_id, material_id: 'copper', recipient_label: 'Supplier desk', subject: 'Availability check', body: 'Please review.', status: 'simulated_sent' as const, simulated: true as const, external_delivery: false as const, created_at: '2026-10-14T12:00:00Z' };
    const html = renderToStaticMarkup(<OutboxWorkspace company={company} messages={[message, { ...message, message_id: 'm2', company_id: 'elsewhere' }, { ...message, message_id: 'm3', status: 'draft' as never, subject: 'Unconfirmed draft' }]} />);
    expect(html).toContain('No external delivery');
    expect(html).toContain('Availability check');
    expect(html.match(/Please review\./g)).toHaveLength(1);
    expect(html).not.toContain('Unconfirmed draft');
  });
});
