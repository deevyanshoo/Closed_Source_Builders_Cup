import { useState } from 'react';
import type { NewsItem, NewsView } from './demo-api';
import { industryPhotos } from './news-industry-photos';

const displayTime = (value: string | null | undefined) => {
  if (!value || !Number.isFinite(Date.parse(value))) return null;
  return new Date(value).toLocaleString();
};
const unhealthy = (state: string) => state === 'stale' || state === 'unavailable' || state === 'offline';

export function newsPresentationStatus(view: NewsView | null, now = Date.now()): { kind: 'updated' | 'saved' | 'collecting'; label: string; detail: string } {
  if (!view) return { kind: 'collecting', label: 'Collecting relevant stories', detail: '' };
  const newsStamp = view.collection?.news_success_at ? Date.parse(view.collection.news_success_at) : NaN;
  const checkedStamp = Date.parse(view.checked_at);
  const newsSources = view.sources.filter(source => source.source_id !== 'ecb_fx');
  const sourceStamp = (states: string[]) => Math.max(...newsSources
    .filter(source => states.includes(source.state))
    .map(source => source.last_checked_at ? Date.parse(source.last_checked_at) : NaN)
    .filter(stamp => Number.isFinite(stamp) && stamp <= now));
  const healthyStamp = sourceStamp(['fresh', 'cached', 'empty']);
  const trustedStamp = sourceStamp(['fresh', 'cached', 'empty', 'stale']);
  const newsTime = displayTime(view.collection?.news_success_at) ??
    (!view.collection && Number.isFinite(trustedStamp) ? new Date(trustedStamp).toLocaleString() : null);
  const fxSource = view.sources.find(source => source.source_id === 'ecb_fx');
  const fxIssue = !!fxSource && unhealthy(fxSource.state);
  const newsIssue = newsSources.some(source => unhealthy(source.state));
  const healthyNewsSource = newsSources.some(source => ['fresh', 'cached', 'empty'].includes(source.state));
  const recentNewsSuccess = Number.isFinite(newsStamp) && now - newsStamp >= 0 && now - newsStamp <= 2 * 60 * 60 * 1000;
  const recentLegacyCheck = !view.collection && (
    (Number.isFinite(healthyStamp) && now - healthyStamp <= 2 * 60 * 60 * 1000) ||
    (newsSources.length === 0 && ['fresh', 'empty'].includes(view.cache_state) &&
      Number.isFinite(checkedStamp) && now - checkedStamp >= 0 && now - checkedStamp <= 2 * 60 * 60 * 1000));
  const currentNews = !['unavailable', 'offline'].includes(view.cache_state) &&
    ((recentNewsSuccess && healthyNewsSource) || recentLegacyCheck);
  const detail = fxIssue ? 'FX reference needs a separate source check. See Collection details.' : 'See Collection details for source coverage and times.';
  if (!currentNews) return view.items.length
    ? { kind: 'saved', label: newsTime ? `Showing saved stories from ${newsTime}` : 'Showing saved stories', detail }
    : { kind: 'collecting', label: 'Collecting relevant stories', detail };
  const updatedTime = displayTime(view.collection?.news_success_at) ??
    (Number.isFinite(healthyStamp) ? new Date(healthyStamp).toLocaleString() : displayTime(view.checked_at));
  return updatedTime
    ? { kind: 'updated', label: `Updated ${updatedTime}`, detail: fxIssue || newsIssue ? detail : '' }
    : { kind: 'collecting', label: 'Collecting relevant stories', detail };
}

export function safeNewsImageUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    const host = url.hostname.toLowerCase();
    if (url.protocol !== 'https:' || url.username || url.password || url.port ||
      (host !== 'www.eia.gov' && host !== 'www.ecb.europa.eu')) return null;
    return url.href;
  } catch { return null; }
}

type StoryVisual = Pick<NewsItem, 'category' | 'matched_materials'> & { image_url?: string | null; image_source?: string | null };
export function StoryImage({ item, companyId, large = false }: { item: StoryVisual; companyId?: string; large?: boolean }) {
  const source = item.image_source === 'publisher_page' ? 'publisher page' : item.image_source === 'publisher_feed' ? 'publisher feed' : item.image_source === 'discovery_metadata' ? 'discovery metadata' : null;
  const sourceUrl = source ? safeNewsImageUrl(item.image_url) : null;
  const photo = companyId ? industryPhotos[companyId] : null;
  const [failedUrls, setFailedUrls] = useState<string[]>([]);
  const displayedUrl = sourceUrl && !failedUrls.includes(sourceUrl) ? sourceUrl :
    photo && !failedUrls.includes(photo.src) ? photo.src : null;
  const showingContext = displayedUrl === photo?.src;
  const label = item.matched_materials[0] ?? item.category ?? 'Industry';
  return <span className={`demo-news-visual${large ? ' demo-news-visual-large' : ''}`}>
    {displayedUrl ? <img src={displayedUrl} alt="" loading="lazy" decoding="async" referrerPolicy="no-referrer" onError={() => setFailedUrls(previous => [...previous, displayedUrl])} /> :
      <span className="demo-news-visual-context" role="img" aria-label={`Industry context: ${label}`}><span className="demo-news-visual-lines" aria-hidden="true" /><span className="demo-news-visual-label">Industry context</span><strong>{label}</strong></span>}
    {showingContext && photo ? <span className="demo-news-visual-credit">Industry context{large && <> · {photo.caption}<br />{photo.display_note}<br />Photo: {photo.author} · <a href={photo.source_page} target="_blank" rel="noopener noreferrer">Source</a> · <a href={photo.license_url} target="_blank" rel="noopener noreferrer">{photo.license} license</a></>}</span> :
      displayedUrl && source && <span className="demo-news-visual-credit">Image: {source}</span>}
  </span>;
}
