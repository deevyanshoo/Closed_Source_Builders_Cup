import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { feedPublicationLabel, filterNewsItems, rankNewsItems, selectNewsItem, NewsWorkspace, OutboxWorkspace } from './demo-company-news';
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
  beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date('2026-10-15T10:00:00Z')); });
  afterEach(() => vi.useRealTimers());
  it('ranks operational fit ahead of incidental material mentions, with stable newest sorting', () => {
    const background = { ...news.items[0], item_id: 'background', relevance_strength: 'background' as const, published_at: '2026-10-15T09:00:00Z' };
    const strong = { ...news.items[0], item_id: 'strong', relevance_strength: 'strong' as const, published_at: '2026-10-14T09:00:00Z' };
    const related = { ...news.items[0], item_id: 'related', relevance_strength: 'related' as const, published_at: '2026-10-15T08:00:00Z' };
    expect(rankNewsItems([background, related, strong], 'relevant').map(item => item.item_id)).toEqual(['strong', 'related', 'background']);
    expect(rankNewsItems([background, related, strong], 'newest').map(item => item.item_id)).toEqual(['background', 'related', 'strong']);
    expect(rankNewsItems([strong, { ...strong, item_id: 'a-strong' }], 'relevant').map(item => item.item_id)).toEqual(['a-strong', 'strong']);
  });
  it('defaults to seven days and excludes unknown, future and background dates unless disclosed', () => {
    const dated = { ...news.items[0], published_at: '2026-10-14T09:00:00Z' };
    const old = { ...dated, item_id: 'old', published_at: '2026-10-01T09:00:00Z' };
    const unknown = { ...dated, item_id: 'unknown', published_at: null, first_seen_at: '2026-10-15T09:00:00Z' };
    const future = { ...dated, item_id: 'future', published_at: '2026-10-16T09:00:00Z' };
    const background = { ...dated, item_id: 'background', relevance_strength: 'background' as const };
    expect(filterNewsItems([dated, old, unknown, future, background], 'all', 'all', false, { now: Date.parse('2026-10-15T10:00:00Z') }).map(item => item.item_id)).toEqual(['a']);
    expect(filterNewsItems([dated, old, unknown, future, background], 'all', 'all', false, { now: Date.parse('2026-10-15T10:00:00Z'), age: '30d', includeUnknown: true, includeBackground: true }).map(item => item.item_id)).toEqual(['a', 'old', 'unknown', 'background']);
    expect(selectNewsItem('from-previous-company', [dated])?.item_id).toBe('a');
  });
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
    expect(html).toContain('Include unknown publication dates');
    expect(html).toContain('No stories match these controls');
    expect(html).toContain('Coverage is incomplete');
    const dated = renderToStaticMarkup(<NewsWorkspace company={company} view={{ ...news, items: [{ ...news.items[0], published_at: '2026-10-14T09:00:00Z' }] }} loading={false} error="" onRefresh={() => {}} />);
    expect(dated).toContain('https://example.com/copper');
    const unsafe = renderToStaticMarkup(<NewsWorkspace company={company} view={{ ...news, items: [{ ...news.items[1], published_at: '2026-10-14T09:00:00Z', relevance_strength: 'strong' }] }} loading={false} error="" onRefresh={() => {}} />);
    expect(unsafe).not.toContain('href="javascript:');
    expect(html).not.toContain('Unmatched headline');
    expect(html).toContain('Show unmatched headlines');
  });
  it('shows observed matched coverage and explains unmatched-only or stale refresh states honestly', () => {
    const unmatched = Array.from({ length: 23 }, (_, index) => ({ ...news.items[2], item_id: `unmatched-${index}` }));
    const matched = Array.from({ length: 8 }, (_, index) => ({ ...news.items[0], item_id: `matched-${index}` }));
    const loaded = { ...news, items: [...matched.map(item => ({ ...item, published_at: '2026-10-14T09:00:00Z' })), ...unmatched] };
    const html = renderToStaticMarkup(<NewsWorkspace company={company} view={loaded} loading={false} error="" onRefresh={() => {}} />);
    expect(html).toContain('8 company-relevant of 31 observed items');
    expect(html.match(/Copper lane update/g)).toHaveLength(9);
    expect(html).not.toContain('Unmatched headline');
    const stale = renderToStaticMarkup(<NewsWorkspace company={company} view={{ ...loaded, items: unmatched }} loading={false} error="Session expired" onRefresh={() => {}} />);
    expect(stale).toContain('23 observed headlines have no recorded company match');
    expect(stale).toContain('Show unmatched headlines');
    expect(stale).not.toContain('Unmatched headline</strong>');
    expect(stale).toContain('Previously displayed results');
    const refreshing = renderToStaticMarkup(<NewsWorkspace company={company} view={loaded} loading={true} error="" onRefresh={() => {}} />);
    expect(refreshing).toContain('The items below are from the previous check');
    expect(refreshing).toContain('Checking sources…');
  });
  it('counts direct and indirect matches separately and explains heuristic metadata without treating it as probability', () => {
    const direct = { ...news.items[0], published_at: '2026-10-14T09:00:00Z', relevance_kind: 'direct_material' as const, relevance_score: 0.8, matched_operations: ['wire production'], summary: 'Copper line disruption reported.' };
    const indirect = { ...news.items[2], published_at: '2026-10-14T09:00:00Z', item_id: 'sector', relevance_kind: 'sector' as const, relevance_score: 0.4, matched_operations: ['wire production'], summary: 'Manufacturing activity reported.' };
    expect(filterNewsItems([direct, indirect], 'all', 'all').map(item => item.item_id)).toEqual(['a', 'sector']);
    const html = renderToStaticMarkup(<NewsWorkspace company={company} view={{ ...news, items: [direct, indirect] }} loading={false} error="" onRefresh={() => {}} />);
    expect(html).toContain('1 direct material match');
    expect(html).toContain('1 indirect match');
    expect(html).toContain('wire production');
    expect(html).toContain('Uncalibrated relevance heuristic');
    expect(html).toContain('Copper line disruption reported.');
  });
  it('shows selected-story actions and bounded enrichment, while hiding material actions for background fit', () => {
    const selected = { ...news.items[0], published_at: '2026-10-14T09:00:00Z', relevance_strength: 'strong' as const, ranking_reason: 'Supply operation and tracked input', article: { status: 'enriched' as const, method: 'scrapling-static' as const, fetched_at: '2026-10-15T09:00:00Z', title: null, published_at: null, excerpt: 'A bounded excerpt from an official source.', content_chars: 231, reason: null } };
    const html = renderToStaticMarkup(<NewsWorkspace company={company} view={{ ...news, items: [selected] }} loading={false} error="" onRefresh={() => {}} onAction={() => {}} />);
    expect(html).toContain('Ask operator');
    expect(html).toContain('Check inventory');
    expect(html).toContain('Prepare unsent draft');
    expect(html).toContain('A bounded excerpt from an official source.');
    expect(html).toContain('Ranking reason:');
    const background = renderToStaticMarkup(<NewsWorkspace company={company} view={{ ...news, items: [{ ...selected, relevance_strength: 'background' }] }} loading={false} error="" onRefresh={() => {}} onAction={() => {}} />);
    expect(background).not.toContain('Check inventory');
    expect(background).not.toContain('Prepare unsent draft');
  });
  it('labels the article page date separately without using it for feed-age ranking', () => {
    const articleDate = '2026-10-13T12:00:00Z';
    const item = { ...news.items[0], published_at: '2026-10-14T09:00:00Z', article: { status: 'enriched' as const, method: 'scrapling-static' as const, fetched_at: '2026-10-15T09:00:00Z', title: null, published_at: articleDate, excerpt: 'Official article excerpt.', content_chars: 80, reason: null } };
    const html = renderToStaticMarkup(<NewsWorkspace company={company} view={{ ...news, items: [item] }} loading={false} error="" onRefresh={() => {}} />);
    expect(html).toContain(`Article page date: ${new Date(articleDate).toLocaleString()}`);
    expect(html).toContain(`Article retrieved: ${new Date(item.article.fetched_at).toLocaleString()}`);
    expect(html).toContain(`Feed published: ${new Date(item.published_at).toLocaleString()}`);
    expect(rankNewsItems([{ ...item, item_id: 'undated', published_at: null, article: { ...item.article, published_at: '2026-10-15T09:00:00Z' } }, item], 'newest').map(value => value.item_id)).toEqual(['a', 'undated']);
  });
  it('labels invalid feed publication as unavailable and never exposes raw invalid time strings', () => {
    expect(feedPublicationLabel(null)).toBe('Publication time unavailable');
    expect(feedPublicationLabel('not-a-date')).toBe('Publication time unavailable');
    expect(filterNewsItems([{ ...news.items[0], published_at: 'not-a-date' }], 'all', 'all', false, { now: Date.parse('2026-10-15T10:00:00Z') })).toEqual([]);
    const html = renderToStaticMarkup(<NewsWorkspace company={company} view={{ ...news, checked_at: 'not-a-date', items: [{ ...news.items[0], published_at: '2026-10-14T09:00:00Z' }] }} loading={false} error="" onRefresh={() => {}} />);
    expect(html).toContain('Checked Time unavailable');
    expect(html).not.toContain('not-a-date');
  });
  it('keeps Global Watch visible independently of the company inbox and labels exposure and source clocks', () => {
    const watch = { attempted_at: '2026-10-15T10:00:00Z', success_at: '2026-10-14T09:00:00Z',
      status: 'stale' as const, sources: [{ source_id: 'global_conflict', name: 'Conflict discovery',
        state: 'stale', last_checked_at: '2026-10-14T09:00:00Z', reason: 'Source unavailable' }],
      events: [{ item_id: 'global-one', title: 'Port congestion reported', url: 'https://example.org/port',
        publisher: 'Example', provider: 'Google News RSS', published_at: null,
        first_seen_at: '2026-10-14T09:00:00Z', retrieved_at: '2026-10-14T09:00:00Z',
        source_id: 'global_conflict', topic: 'chokepoint' as const,
        exposure: { kind: 'background' as const, reason: 'Public context only.',
          matched_materials: [], matched_routes: [], next_action: 'Read original source.' } }] };
    const html = renderToStaticMarkup(<NewsWorkspace company={company}
      view={{ ...news, items: [], global_watch: watch }} loading={false} error="" onRefresh={() => {}} />);
    expect(html).toContain('Global Watch');
    expect(html).toContain('Port congestion reported');
    expect(html).toContain('background context');
    expect(html).toContain('Publication time unavailable');
    expect(html).toContain('Last successful global collection');
    expect(html).toContain('Source unavailable');
    expect(html).toContain('Read original source.');
    expect(html).toContain('No observed headlines were returned');
    const empty = renderToStaticMarkup(<NewsWorkspace company={company} view={null}
      loading={false} error="" onRefresh={() => {}} />);
    expect(empty).toContain('Global Watch');
    expect(empty).toContain('Global coverage unavailable');
  });
  it('labels a Google News destination as a discovery link and separates listed publisher from provider', () => {
    const watch = { attempted_at: '2026-10-15T10:00:00Z', success_at: '2026-10-15T10:00:00Z',
      status: 'fresh' as const, sources: [], events: [{ item_id: 'google-link',
        title: 'Port update', url: 'https://news.google.com/rss/articles/example',
        publisher: 'Example Publisher', provider: 'Google News RSS', published_at: null,
        first_seen_at: '2026-10-15T10:00:00Z', retrieved_at: '2026-10-15T10:00:00Z',
        source_id: 'global_port', topic: 'port' as const,
        exposure: { kind: 'background' as const, reason: 'Context only.',
          matched_materials: [], matched_routes: [], next_action: 'Verify publisher report.' } }] };
    const html = renderToStaticMarkup(<NewsWorkspace company={company}
      view={{ ...news, items: [], global_watch: watch }} loading={false} error="" onRefresh={() => {}} />);
    expect(html).toContain('Publisher listed: Example Publisher');
    expect(html).toContain('Discovered via: Google News RSS');
    expect(html).toContain('Open news link ↗');
    expect(html).not.toContain('>Read original source ↗</a>');
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
