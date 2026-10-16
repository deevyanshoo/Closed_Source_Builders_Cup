import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { NewsItem, NewsView } from './demo-api';
import { newsPresentationStatus, safeNewsImageUrl, StoryImage } from './news-presentation';

const base: NewsView = {
  company_id: 'solara-cables', context_version: 1, checked_at: '2026-10-16T11:45:00Z', cache_state: 'stale',
  items: [], fx: [], limitations: [], sources: [
    { source_id: 'eia', name: 'EIA', state: 'fresh', last_checked_at: '2026-10-16T11:00:00Z', reason: null },
    { source_id: 'ecb_fx', name: 'ECB FX', state: 'stale', last_checked_at: '2026-10-15T11:00:00Z', reason: 'Timeout' },
  ],
  collection: { status: 'partial', attempted_at: '2026-10-16T11:45:00Z', news_success_at: '2026-10-16T11:00:00Z', fx_success_at: '2026-10-15T11:00:00Z' },
};
const now = Date.parse('2026-10-16T12:00:00Z');

describe('news presentation', () => {
  it('uses the successful news clock when only FX is stale', () => {
    const status = newsPresentationStatus(base, now);
    expect(status.kind).toBe('updated');
    expect(status.label).toContain('Updated');
    expect(status.label).toContain(new Date(base.collection!.news_success_at!).toLocaleString());
    expect(status.detail).toContain('FX reference');
  });
  it('uses Updated for a recent successful news set when one provider times out', () => {
    const view = { ...base, items: [{} as NewsItem], sources: [
      { ...base.sources[0], source_id: 'rss', state: 'fresh' },
      { ...base.sources[0], source_id: 'gdelt', state: 'unavailable', reason: 'Timeout' },
    ] };
    const status = newsPresentationStatus(view, now);
    expect(status.kind).toBe('updated');
    expect(status.label).toContain(new Date(base.collection!.news_success_at!).toLocaleString());
    expect(status.detail).toContain('Collection details');
  });
  it('keeps a dated saved status when every news provider failed', () => {
    const view = { ...base, items: [{} as NewsItem], sources: [
      { ...base.sources[0], source_id: 'rss', state: 'unavailable' },
      { ...base.sources[0], source_id: 'gdelt', state: 'stale' },
    ] };
    const status = newsPresentationStatus(view, now);
    expect(status.kind).toBe('saved');
    expect(status.label).toContain(new Date(base.collection!.news_success_at!).toLocaleString());
  });
  it('dates saved stories from news success, never from the newer attempt or FX clock', () => {
    const view = { ...base, items: [{} as NewsItem], sources: [{ ...base.sources[0], state: 'stale' }], collection: { ...base.collection!, news_success_at: '2026-10-15T08:00:00Z' } };
    const status = newsPresentationStatus(view, now);
    expect(status.kind).toBe('saved');
    expect(status.label).toContain(new Date(view.collection.news_success_at).toLocaleString());
    expect(status.label).not.toContain(new Date(view.checked_at).toLocaleString());
  });
  it('calls an empty failed collection collecting, without inventing a successful time', () => {
    const status = newsPresentationStatus({ ...base, items: [], collection: { ...base.collection!, news_success_at: null } }, now);
    expect(status).toMatchObject({ kind: 'collecting', label: 'Collecting relevant stories' });
  });
  it('does not call unavailable news updated just because FX alone has a known source failure', () => {
    const status = newsPresentationStatus({ ...base, cache_state: 'unavailable', items: [{} as NewsItem] }, now);
    expect(status.kind).toBe('saved');
  });
  it('does not label an old snapshot read updated when no success clock exists', () => {
    const status = newsPresentationStatus({ ...base, cache_state: 'fresh', collection: undefined, checked_at: '2026-10-15T08:00:00Z', items: [{} as NewsItem], sources: [] }, now);
    expect(status.kind).toBe('saved');
    expect(status.label).not.toContain('Updated');
  });
  it('dates a legacy partial refresh from a healthy RSS source, not the failed GDELT attempt', () => {
    const view = { ...base, collection: undefined, cache_state: 'fresh', items: [{} as NewsItem], sources: [
      { ...base.sources[0], source_id: 'rss', state: 'fresh', last_checked_at: '2026-10-16T11:00:00Z' },
      { ...base.sources[0], source_id: 'gdelt', state: 'unavailable', last_checked_at: '2026-10-16T11:45:00Z', reason: 'Timeout' },
    ] };
    const status = newsPresentationStatus(view, now);
    expect(status.kind).toBe('updated');
    expect(status.label).toContain(new Date('2026-10-16T11:00:00Z').toLocaleString());
    expect(status.label).not.toContain(new Date(view.checked_at).toLocaleString());
  });
  it('dates legacy saved stories from a trusted old source check, not a newer failed attempt', () => {
    const view = { ...base, collection: undefined, cache_state: 'fresh', items: [{} as NewsItem], sources: [
      { ...base.sources[0], source_id: 'rss', state: 'stale', last_checked_at: '2026-10-15T08:00:00Z' },
      { ...base.sources[0], source_id: 'gdelt', state: 'unavailable', last_checked_at: '2026-10-16T11:45:00Z', reason: 'Timeout' },
    ] };
    const status = newsPresentationStatus(view, now);
    expect(status.kind).toBe('saved');
    expect(status.label).toContain(new Date('2026-10-15T08:00:00Z').toLocaleString());
    expect(status.label).not.toContain(new Date(view.checked_at).toLocaleString());
  });
  it('accepts only public HTTPS image URLs and keeps attribution separate', () => {
    expect(safeNewsImageUrl('https://www.eia.gov/story.jpg')).toBe('https://www.eia.gov/story.jpg');
    expect(safeNewsImageUrl('https://www.ecb.europa.eu/story.jpg')).toBe('https://www.ecb.europa.eu/story.jpg');
    expect(safeNewsImageUrl('https://images.publisher.com/story.jpg')).toBeNull();
    for (const url of ['javascript:alert(1)', 'data:image/svg+xml,hello', 'http://example.com/x.png', 'https://user:pass@example.com/x', 'https://localhost/a', 'https://127.0.0.1/x', 'https://10.0.0.1/x', 'https://[::1]/x', 'https://assets.internal/x']) {
      expect(safeNewsImageUrl(url)).toBeNull();
    }
    const image = renderToStaticMarkup(<StoryImage item={{ image_url: 'https://www.eia.gov/story.jpg', image_source: 'publisher_page', category: 'trade', matched_materials: ['copper'] }} />);
    expect(image).toContain('loading="lazy"');
    expect(image).toContain('referrerPolicy="no-referrer"');
    expect(image).toContain('Image: publisher page');
    expect(image).toContain('https://www.eia.gov/story.jpg');
    const discovered = renderToStaticMarkup(<StoryImage item={{ image_url: 'https://images.publisher.com/story.jpg', image_source: 'discovery_metadata', category: 'trade', matched_materials: ['copper'] }} companyId="forgeline-components" />);
    expect(discovered).toContain('src="/assets/news/forgeline-components.jpg"');
    expect(discovered).not.toContain('https://images.publisher.com/story.jpg');
    const fallback = renderToStaticMarkup(<StoryImage item={{ image_url: 'https://127.0.0.1/x', category: 'trade', matched_materials: ['copper'] }} />);
    expect(fallback).toContain('Industry context');
    expect(fallback).not.toContain('<img');
    const noSource = renderToStaticMarkup(<StoryImage item={{ image_url: 'https://images.publisher.com/x', category: 'trade', matched_materials: ['copper'] }} />);
    expect(noSource).not.toContain('<img');
  });
  it('credits a bundled industry photo in detail without nesting links in story buttons', () => {
    const item = { category: 'trade', matched_materials: ['copper'] };
    const row = renderToStaticMarkup(<StoryImage item={item} companyId="forgeline-components" />);
    expect(row).toContain('Industry context');
    expect(row).toContain('src="/assets/news/forgeline-components.jpg"');
    expect(row).not.toContain('<a ');
    const detail = renderToStaticMarkup(<StoryImage item={item} companyId="forgeline-components" large />);
    expect(detail).toContain('src="/assets/news/forgeline-components.jpg"');
    expect(detail).toContain('Industry context');
    expect(detail).toContain('https://commons.wikimedia.org/');
    expect(detail).toContain('license');
  });
});
