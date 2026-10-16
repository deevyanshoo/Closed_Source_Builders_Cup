import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { NewsItem, NewsView } from './demo-api';
import { ZoorqOverviewSignal } from './zoorq-overview-signal';

const story: NewsItem = {
  item_id: 'copper', title: 'Copper shipping update', url: 'https://example.com/copper',
  publisher: 'Port Journal', provider: 'public-feed', published_at: '2026-10-15T08:30:00Z',
  first_seen_at: '2026-10-15T09:00:00Z', retrieved_at: '2026-10-15T10:00:00Z',
  source_tier: 'secondary', source_reliability: 'limited', reliability_reason: 'Single report',
  category: 'trade', matched_materials: ['copper'], matched_routes: [], matched_currencies: [],
  relevance_strength: 'strong', relevance_reason: 'Copper exposure', implication: 'Review supply',
  implication_confidence: 'conditional', next_steps: [], synthetic: false,
};
const news: NewsView = {
  company_id: 'solara-cables', context_version: 2, checked_at: '2026-10-16T10:00:00Z',
  cache_state: 'fresh', sources: [], items: [story], fx: [], limitations: [],
};
const render = (view: NewsView | null, loading = false, error = '') =>
  renderToStaticMarkup(<ZoorqOverviewSignal news={view} loading={loading} error={error} onOpenNews={() => {}} />);

describe('Overview news signal', () => {
  beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date('2026-10-16T12:00:00Z')); });
  afterEach(() => vi.useRealTimers());

  it('uses calm dated status for partial FX-only coverage without a prominent warning', () => {
    const html = render({ ...news, cache_state: 'stale', sources: [
      { source_id: 'eia', name: 'EIA', state: 'fresh', last_checked_at: '2026-10-16T11:00:00Z', reason: null },
      { source_id: 'ecb_fx', name: 'ECB FX', state: 'stale', last_checked_at: '2026-10-15T11:00:00Z', reason: 'Timeout' },
    ], collection: { status: 'partial', attempted_at: '2026-10-16T11:00:00Z', news_success_at: '2026-10-16T11:00:00Z', fx_success_at: '2026-10-15T11:00:00Z' } });
    expect(html).toContain(`Updated ${new Date('2026-10-16T11:00:00Z').toLocaleString()}`);
    expect(html).toContain('FX reference needs a separate source check');
    expect(html).not.toContain('Showing saved stories');
    expect(html).not.toContain('class="demo-news-warning');
  });

  it('keeps a current published story visible with a saved collection time', () => {
    const html = render({ ...news, cache_state: 'partial', collection: { status: 'partial', attempted_at: '2026-10-16T10:00:00Z', news_success_at: '2026-10-16T09:00:00Z', fx_success_at: '2026-10-16T09:00:00Z' } });
    expect(html).toContain('Copper shipping update');
    expect(html).toContain('Port Journal');
    expect(html).toContain('Published ');
    expect(html).toMatch(/Showing saved stories/);
    expect(html).toContain('View all signals');
    expect(html).not.toContain('Published 2026-10-15T08:30:00Z');
  });

  it('keeps a current published story visible when its source is stale', () => {
    const html = render({ ...news, sources: [{ source_id: 'port', name: 'Port source', state: 'stale', last_checked_at: '2026-10-15T10:00:00Z', reason: 'Refresh failed' }] });
    expect(html).toContain('Copper shipping update');
    expect(html).toContain('Showing saved stories');
  });

  it('dates current news separately when an FX source needs review', () => {
    const html = render({ ...news, cache_state: 'stale', sources: [
      { source_id: 'eia', name: 'EIA', state: 'fresh', last_checked_at: '2026-10-16T11:00:00Z', reason: null },
      { source_id: 'ecb_fx', name: 'ECB FX', state: 'stale', last_checked_at: '2026-10-15T11:00:00Z', reason: 'Refresh failed' },
    ], collection: { status: 'partial', attempted_at: '2026-10-16T11:00:00Z', news_success_at: '2026-10-16T11:00:00Z', fx_success_at: '2026-10-15T11:00:00Z' } });
    expect(html).toContain('Copper shipping update');
    expect(html).toContain('FX reference needs a separate source check');
    expect(html).not.toContain('Showing saved stories');
  });

  it('does not infer an FX-only failure from partial status without source evidence', () => {
    const html = render({ ...news, collection: { status: 'partial', attempted_at: '2026-10-16T11:00:00Z', news_success_at: '2026-10-16T11:00:00Z', fx_success_at: '2026-10-15T11:00:00Z' } });
    expect(html).toContain('Showing saved stories');
    expect(html).not.toContain('FX reference needs a separate source check');
  });

  it('dates saved news when both news and FX sources need review', () => {
    const html = render({ ...news, sources: [
      { source_id: 'eia', name: 'EIA', state: 'unavailable', last_checked_at: '2026-10-16T11:00:00Z', reason: 'Timeout' },
      { source_id: 'ecb_fx', name: 'ECB FX', state: 'unavailable', last_checked_at: '2026-10-16T11:00:00Z', reason: 'Timeout' },
    ], collection: { status: 'partial', attempted_at: '2026-10-16T11:00:00Z', news_success_at: '2026-10-16T11:00:00Z', fx_success_at: null } });
    expect(html).toContain('Copper shipping update');
    expect(html).toContain('Showing saved stories');
  });

  it.each([
    ['unavailable', 'unavailable'],
    ['offline', 'offline'],
  ])('does not claim a healthy empty result for %s cache state', (cacheState, expected) => {
    const html = render({ ...news, items: [], cache_state: cacheState });
    expect(html).toContain(`News coverage ${expected}`);
    expect(html).not.toContain('No company story in the past 7 days');
  });

  it('reports an offline news source rather than a healthy empty result', () => {
    const html = render({ ...news, items: [], cache_state: 'empty', sources: [
      { source_id: 'eia', name: 'EIA', state: 'offline', last_checked_at: null, reason: 'Disabled' },
    ] });
    expect(html).toContain('News coverage offline');
    expect(html).not.toContain('No company story in the past 7 days');
  });

  it('does not claim healthy empty coverage when no source status is returned', () => {
    const html = render({ ...news, items: [], cache_state: 'empty', sources: [] });
    expect(html).toContain('News source status unavailable');
    expect(html).not.toContain('No company story in the past 7 days');
  });

  it('dates saved news when the last successful collection is over two hours old', () => {
    const html = render({ ...news, collection: { status: 'fresh', attempted_at: '2026-10-16T11:50:00Z', news_success_at: '2026-10-16T09:30:00Z', fx_success_at: '2026-10-16T11:50:00Z' } });
    expect(html).toContain('Copper shipping update');
    expect(html).toContain('Showing saved stories');
  });

  it('shows saved status and no invented headline when no eligible story exists', () => {
    const html = render({ ...news, cache_state: 'stale', items: [
      { ...story, title: 'Old story', published_at: '2026-10-01T08:30:00Z' },
      { ...story, item_id: 'undated', title: 'Undated story', published_at: null },
      { ...story, item_id: 'background', title: 'Background story', relevance_strength: 'background' },
      { ...story, item_id: 'unmatched', title: 'Unmatched story', relevance_strength: 'unmatched' },
    ] });
    expect(html).toContain('No company story in the past 7 days');
    expect(html).toContain('Showing saved stories');
    expect(html).not.toContain('Old story');
    expect(html).not.toContain('Undated story');
    expect(html).not.toContain('Background story');
    expect(html).not.toContain('Unmatched story');
  });

  it('distinguishes first load, missing coverage and fetch error', () => {
    expect(render(null, true)).toContain('Checking public news sources');
    expect(render(null)).toContain('News coverage unavailable');
    expect(render(null, false, 'Request failed')).toContain('Source check unavailable');
  });

  it('keeps the last eligible story and warns when a refresh fails', () => {
    const html = render(news, false, 'Request failed');
    expect(html).toContain('Copper shipping update');
    expect(html).toContain('Source check unavailable');
  });

  it('treats checked_at as a snapshot read, not a story publication date', () => {
    const html = render({ ...news, checked_at: '2026-10-16T11:55:00Z', items: [{ ...story, published_at: null }] });
    expect(html).toContain('No company story in the past 7 days');
    expect(html).not.toContain('Copper shipping update');
    expect(html).not.toContain('Published 2026-10-16');
  });
});
