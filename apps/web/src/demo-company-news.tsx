import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import type { CompanyProfile, GlobalWatch, NewsItem, NewsView, SimulatedOutboxMessage } from './demo-api';
import { ActivityIndicator } from './demo-activity';
import { supplierDisplayName } from './demo-display';
import { newsPresentationStatus, StoryImage } from './news-presentation';
import './demo-company-news.css';

const dateLabel = (value: string | null | undefined) => {
  if (!value) return 'Time unavailable';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'Time unavailable' : date.toLocaleString();
};
const sourceHref = (url: string) => {
  try { const parsed = new URL(url); return parsed.protocol === 'https:' && !parsed.username && !parsed.password ? parsed.href : null; }
  catch { return null; }
};
type NewsFit = 'strong' | 'related' | 'background' | 'unmatched';
type EnrichedNewsItem = NewsItem & { relevance_strength?: NewsFit; ranking_reason?: string; article?: { status: 'enriched' | 'unavailable' | 'unsupported'; method: string; fetched_at: string; title: string | null; published_at: string | null; excerpt: string | null; content_chars: number; reason: string | null } };
type NewsSort = 'relevant' | 'newest';
type NewsAge = '24h' | '7d' | '30d';
export const activeAdditionalFilters = (sort: NewsSort, category: string, includeUnknown: boolean, includeBackground: boolean, showUnmatched: boolean) =>
  Number(sort !== 'relevant') + Number(category !== 'all') + Number(includeUnknown) + Number(includeBackground) + Number(showUnmatched);
type NewsFilterOptions = { age?: NewsAge; now?: number; includeUnknown?: boolean; includeBackground?: boolean };
const ageMs: Record<NewsAge, number> = { '24h': 86_400_000, '7d': 604_800_000, '30d': 2_592_000_000 };
const publishedTime = (item: NewsItem) => {
  const time = item.published_at ? Date.parse(item.published_at) : NaN;
  return Number.isFinite(time) ? time : null;
};
export const feedPublicationLabel = (value: string | null | undefined) =>
  value && Number.isFinite(Date.parse(value)) ? dateLabel(value) : 'Publication time unavailable';
const normalizedStoryCopy = (value: string | null | undefined) => (value ?? '').replace(/\s+/g, ' ').trim().toLowerCase();
const relevanceKind = (item: NewsItem) => item.relevance_kind ?? (item.matched_materials.length ? 'direct_material' : item.matched_routes.length ? 'route' : item.matched_currencies.length ? 'currency' : item.matched_operations?.length ? 'sector' : 'unmatched');
const newsFit = (item: NewsItem): NewsFit => {
  const explicit = (item as EnrichedNewsItem).relevance_strength;
  if (explicit === 'strong' || explicit === 'related' || explicit === 'background' || explicit === 'unmatched') return explicit;
  const kind = relevanceKind(item);
  if (kind === 'unmatched') return 'unmatched';
  if ((kind === 'direct_material' || kind === 'route') && item.matched_operations?.length) return 'strong';
  if (kind === 'direct_material' || kind === 'route' || kind === 'sector') return 'related';
  return 'background';
};
const fitOrder: Record<NewsFit, number> = { strong: 0, related: 1, background: 2, unmatched: 3 };
export const rankNewsItems = (items: NewsItem[], sort: NewsSort): NewsItem[] => [...items].sort((a, b) => {
  // The API already diversifies exposures within each relevance tier.
  if (sort === 'relevant') return fitOrder[newsFit(a)] - fitOrder[newsFit(b)];
  const aTime = publishedTime(a), bTime = publishedTime(b);
  if (aTime !== bTime) return bTime === null ? -1 : aTime === null ? 1 : bTime - aTime;
  return a.item_id.localeCompare(b.item_id);
});
export const filterNewsItems = (items: NewsItem[], material: string, category: string, showUnmatched = false, options?: NewsFilterOptions): NewsItem[] =>
  items.filter(item => {
    const fit = newsFit(item);
    if ((!showUnmatched && fit === 'unmatched') || (options && !options.includeBackground && fit === 'background')) return false;
    if ((material !== 'all' && !item.matched_materials.includes(material)) || (category !== 'all' && item.category !== category)) return false;
    if (!options) return true;
    const stamp = publishedTime(item);
    if (stamp === null) return !!options.includeUnknown;
    const now = options.now ?? Date.now();
    return stamp <= now && stamp >= now - ageMs[options.age ?? '7d'];
  });
export function selectNewsItem<T extends { item_id: string }>(id: string | null, items: T[]): T | null {
  return id === null ? items[0] ?? null : items.find(item => item.item_id === id) ?? null;
}
export function shownNewsItems<T extends { item_id: string }>(items: T[], showAll: boolean, selectedId: string | null): T[] {
  return showAll || items.slice(5).some(item => item.item_id === selectedId) ? items : items.slice(0, 5);
}
export function SelectionUnavailableNotice({ kind, filtered }: { kind: 'story' | 'global event'; filtered: boolean }) {
  return <p className="demo-news-selection-notice" role="status">The selected {kind} {filtered ? 'is hidden by the current filters' : 'is no longer in this collection'}. Choose another {kind} to inspect its evidence.</p>;
}
export const focusNewsTransition = (destination: 'detail' | 'list', mobileBackVisible: boolean, back: { focus: () => void } | null, row: { focus: () => void } | null) => {
  if (mobileBackVisible) (destination === 'detail' ? back : row)?.focus();
};
const briefAge = (item: NewsItem, now: number) => {
  const stamp = publishedTime(item);
  if (stamp === null) return 'Publication time unavailable';
  const hours = Math.floor((now - stamp) / 3_600_000);
  if (hours < 0) return `Published ${dateLabel(item.published_at)}`;
  if (hours < 1) return 'Published less than 1 hour ago';
  if (hours < 24) return `Published ${hours}h ago`;
  return `Published ${Math.floor(hours / 24)}d ago`;
};

function GlobalWatchPanel({ watch }: { watch?: GlobalWatch }) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [readingEvent, setReadingEvent] = useState(false);
  const selected = selectNewsItem(selectedId, watch?.events ?? []);
  const selectionMissing = selectedId !== null && !selected;
  const backRef = useRef<HTMLButtonElement>(null);
  const selectedRowRef = useRef<HTMLButtonElement>(null);
  const pendingFocus = useRef<'detail' | 'list' | null>(null);
  useLayoutEffect(() => {
    if (!pendingFocus.current) return;
    focusNewsTransition(pendingFocus.current, backRef.current !== null && window.getComputedStyle(backRef.current).display !== 'none', backRef.current, selectedRowRef.current);
    pendingFocus.current = null;
  }, [readingEvent, selectedId]);
  return <section className="demo-global-watch" aria-label="Global Watch">
    <div className="demo-global-watch-head"><div><span className="demo-kicker">Shared public event discovery</span><h3>Global Watch</h3></div><span className="demo-global-watch-state">{watch?.status ?? 'unavailable'}</span></div>
    <p>Conflict, chokepoint, port and trade restriction headlines are context for review. A headline alone does not confirm a route closure, supplier delay or price change.</p>
    {!watch ? <p className="demo-global-watch-empty">Global coverage unavailable. No public events have been verified for this view.</p> : <>
      <p className="demo-global-watch-clock">Last successful global collection: {dateLabel(watch.success_at)} · Last attempt: {dateLabel(watch.attempted_at)}</p>
      {watch.sources.length > 0 && <details className="demo-global-watch-sources"><summary>Global source health · {watch.sources.length} sources</summary><div aria-label="Global Watch source status">{watch.sources.map(source =>
        <p key={source.source_id}><strong>{source.name}: {source.state}</strong> · checked {dateLabel(source.last_checked_at)}{source.reason ? ` · ${source.reason}` : ''}</p>)}</div></details>}
      {selectionMissing && <SelectionUnavailableNotice kind="global event" filtered={false} />}
      <div className="demo-global-watch-layout" data-reading={readingEvent}>{watch.events.length ? <>
        <div className="demo-global-watch-events" aria-label="Global events">{watch.events.map(event =>
          <button key={event.item_id} ref={selected?.item_id === event.item_id ? selectedRowRef : undefined} type="button" className="demo-global-watch-row" aria-current={selected?.item_id === event.item_id ? 'true' : undefined} onClick={() => { pendingFocus.current = 'detail'; setSelectedId(event.item_id); setReadingEvent(true); }}>
            <span className="demo-global-watch-meta"><span>{event.topic}</span><span>{event.exposure.kind === 'background' ? 'background context' : `${event.exposure.kind} exposure to check`}</span></span>
            <strong>{event.title}</strong><span>{event.publisher ?? event.provider} · {feedPublicationLabel(event.published_at)}</span>
          </button>)}</div>
        {(selected || selectionMissing) && <article className="demo-global-watch-event" aria-label="Selected global event"><button ref={backRef} type="button" className="demo-news-back" onClick={() => { pendingFocus.current = 'list'; setReadingEvent(false); }}>← Back to events</button>
          {selected && <>
          <div className="demo-global-watch-meta"><span>{selected.topic}</span><span>{selected.exposure.kind === 'background' ? 'background context' : `${selected.exposure.kind} exposure to check`}</span></div>
          <h4>{selected.title}</h4><p>{selected.exposure.reason}</p><p><strong>Next check:</strong> {selected.exposure.next_action}</p>
          <p className="demo-global-watch-clock">Publisher listed: {selected.publisher ?? 'Unavailable'} · Discovered via: {selected.provider} · Published: {feedPublicationLabel(selected.published_at)} · First seen: {dateLabel(selected.first_seen_at)} · Retrieved: {dateLabel(selected.retrieved_at)}</p>
          {sourceHref(selected.url) && <a href={sourceHref(selected.url)!} target="_blank" rel="noopener noreferrer">Open news link ↗</a>}
          </>}
        </article>}</> : <p className="demo-global-watch-empty">No public events returned by this collection. Source status and collection times remain available above.</p>}</div>
    </>}
  </section>;
}

export function CompanyOverview({ company }: { company: CompanyProfile }) {
  return <section className="demo-company-panel" aria-label="Company overview">
    <div className="demo-heading"><div><span className="demo-kicker">Company news</span><h2>{company.name}</h2></div><span className="demo-session">Profile as of {company.as_of}</span></div>
    <p>{company.description}</p><p className="demo-muted">{company.sector} · {company.size} · Reporting currency {company.reporting_currency} · {company.locations.map(location => `${location.city}, ${location.country}`).join('; ')}</p>
    <div className="demo-company-grid"><div><h3>Inventory and demand</h3><div className="demo-table-wrap"><table><thead><tr><th>Material</th><th>Stock</th><th>Daily use</th><th>Monthly need</th><th>Criticality</th></tr></thead><tbody>{company.materials.map(material => <tr key={material.material_id}><td>{material.name}</td><td>{material.stock} {material.quantity_unit}</td><td>{material.consumption_per_day} {material.quantity_unit}</td><td>{material.monthly_requirement} {material.quantity_unit}</td><td>{material.criticality}</td></tr>)}</tbody></table></div></div>
      <div><h3>Orders</h3>{company.orders.length ? <div className="demo-table-wrap"><table><thead><tr><th>Order</th><th>Material</th><th>Quantity</th><th>Needed</th><th>Status</th></tr></thead><tbody>{company.orders.map(order => <tr key={order.order_id}><td>{order.order_id}</td><td>{order.material_id}</td><td>{order.quantity} {order.unit}</td><td>{order.need_by}</td><td>{order.status}</td></tr>)}</tbody></table></div> : <p className="demo-muted">No orders listed for this company.</p>}</div></div>
    <div className="demo-company-grid"><div><h3>Suppliers</h3>{company.suppliers.length ? <ul>{company.suppliers.map(supplier => <li key={supplier.supplier_id}>{supplierDisplayName(supplier.name)} · {supplier.country} · {supplier.materials.join(', ')} · {supplier.lead_time_days} days · {supplier.settlement_currency}</li>)}</ul> : <p className="demo-muted">No profile suppliers listed.</p>}</div>
      <div><h3>Trade routes</h3>{company.routes.length ? <ul>{company.routes.map(route => <li key={route.route_id}>{route.direction}: {route.origin} → {route.via.length ? `${route.via.join(' → ')} → ` : ''}{route.destination} · {route.materials.join(', ')} · {route.currency}</li>)}</ul> : <p className="demo-muted">No profile routes listed.</p>}</div></div>
    <details className="demo-boundary"><summary>Quote coverage details</summary><p>{company.quote_coverage.note}</p><p>Materials: {company.quote_coverage.materials.length ? company.quote_coverage.materials.join(', ') : 'None available'}. Quote comparisons use each covered material's listed unit. Profile values do not establish observed operations.</p></details>
  </section>;
}

export function NewsWorkspace({ company, view, loading, error, onRefresh, onAction }: { company: CompanyProfile; view: NewsView | null; loading: boolean; error: string; onRefresh: () => void; onAction?: (action: 'ask' | 'inventory' | 'draft' | 'watch' | 'research', item: NewsItem) => void }) {
  const tabsId = useId();
  const backRef = useRef<HTMLButtonElement>(null);
  const selectedRowRef = useRef<HTMLButtonElement>(null);
  const pendingFocus = useRef<'detail' | 'list' | null>(null);
  const [newsView, setNewsView] = useState<'company' | 'global'>('company');
  const [readingStory, setReadingStory] = useState(false);
  const [material, setMaterial] = useState('all');
  const [category, setCategory] = useState('all');
  const [age, setAge] = useState<NewsAge>('7d');
  const [sort, setSort] = useState<NewsSort>('relevant');
  const [includeUnknown, setIncludeUnknown] = useState(false);
  const [includeBackground, setIncludeBackground] = useState(false);
  const [showUnmatched, setShowUnmatched] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  useLayoutEffect(() => {
    if (!pendingFocus.current) return;
    focusNewsTransition(pendingFocus.current, backRef.current !== null && window.getComputedStyle(backRef.current).display !== 'none', backRef.current, selectedRowRef.current);
    pendingFocus.current = null;
  }, [readingStory, selectedId]);
  useEffect(() => { setNewsView('company'); setReadingStory(false); setSelectedId(null); setShowAll(false); setMaterial('all'); setCategory('all'); setAge('7d'); setSort('relevant'); setIncludeUnknown(false); setIncludeBackground(false); setShowUnmatched(false); }, [company.company_id, view?.context_version]);
  const categories = [...new Set(view?.items.map(item => item.category) ?? [])].sort();
  const relevant = (view?.items ?? []).filter(item => newsFit(item) !== 'unmatched');
  const directCount = relevant.filter(item => relevanceKind(item) === 'direct_material').length;
  const now = Date.now();
  const visible = rankNewsItems(filterNewsItems(view?.items ?? [], material, category, showUnmatched, { age, now, includeUnknown, includeBackground }), sort);
  const listed = shownNewsItems(visible, showAll, selectedId);
  const status = newsPresentationStatus(view, now);
  const selected = selectNewsItem(selectedId, visible) as EnrichedNewsItem | null;
  const selectionMissing = selectedId !== null && !selected;
  const repeatedImplication = !!selected?.summary && normalizedStoryCopy(selected.summary) === normalizedStoryCopy(selected.implication);
  const distinctRelevance = repeatedImplication && !!normalizedStoryCopy(selected?.relevance_reason) && normalizedStoryCopy(selected?.relevance_reason) !== normalizedStoryCopy(selected?.summary);
  const matchingScope = (view?.items ?? []).filter(item => (material === 'all' || item.matched_materials.includes(material)) && (category === 'all' || item.category === category) && (showUnmatched || newsFit(item) !== 'unmatched'));
  const hasOlderStories = matchingScope.some(item => { const stamp = publishedTime(item); return stamp !== null && stamp <= now && stamp < now - ageMs[age] && stamp >= now - ageMs['30d']; });
  const hasUndatedStories = matchingScope.some(item => publishedTime(item) === null);
  const hasBackgroundStories = !includeBackground && matchingScope.some(item => newsFit(item) === 'background');
  const olderThanThirtyOnly = matchingScope.length > 0 && matchingScope.every(item => { const stamp = publishedTime(item); return stamp !== null && stamp < now - ageMs['30d']; });
  const collectionLabel = view?.collection?.mode === 'manual' || view?.collection?.status === 'manual' ? 'Manual source check' : view?.collection?.mode === 'live_fallback' || view?.collection?.status === 'live_fallback' ? 'Live source check' : 'Hourly background collection';
  const additionalFilterCount = activeAdditionalFilters(sort, category, includeUnknown, includeBackground, showUnmatched);
  const chooseView = (next: 'company' | 'global') => { setNewsView(next); setReadingStory(false); };
  const onTabKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === 'Home' ? 'company' : event.key === 'End' ? 'global' : newsView === 'company' ? 'global' : 'company';
    chooseView(next);
    event.currentTarget.parentElement?.querySelector<HTMLButtonElement>(`[data-news-view="${next}"]`)?.focus();
  };
  return <section className="demo-company-panel demo-news-panel" aria-label="Live news workspace">
    <div className="demo-heading"><div><span className="demo-kicker">Observed public signals · {company.name}</span><h2>News inbox</h2><p className="demo-news-intro">Company materials and routes · some sources provide headlines only.</p></div></div>
    {loading && <ActivityIndicator state="searching" label="Checking public news sources" detail={view ? `The items below are from the previous check at ${dateLabel(view.checked_at)}.` : 'Fetching observed headline metadata and source health for this company.'} />}
    {view && <div className="demo-news-status"><strong>{status.label}</strong><span>{relevant.length} company-relevant of {view.items.length} observed items · {directCount} direct material {directCount === 1 ? 'match' : 'matches'} · {relevant.length - directCount} indirect {relevant.length - directCount === 1 ? 'match' : 'matches'}</span>{status.detail && <span>{status.detail}</span>}</div>}
    <div className="demo-news-toolbar">
    <div className="demo-news-tabs" role="tablist" aria-label="News views">
      <button type="button" role="tab" id={`${tabsId}-company-tab`} data-news-view="company" aria-controls={`${tabsId}-company-panel`} aria-selected={newsView === 'company'} tabIndex={newsView === 'company' ? 0 : -1} onClick={() => chooseView('company')} onKeyDown={onTabKeyDown}>For your company</button>
      <button type="button" role="tab" id={`${tabsId}-global-tab`} data-news-view="global" aria-controls={`${tabsId}-global-panel`} aria-selected={newsView === 'global'} tabIndex={newsView === 'global' ? 0 : -1} onClick={() => chooseView('global')} onKeyDown={onTabKeyDown}>Global Watch</button>
    </div>
    <div className="demo-news-toolbar-actions"><button type="button" className="demo-button demo-news-refresh" disabled={loading} onClick={onRefresh}>{loading ? 'Checking sources…' : 'Refresh sources'}</button></div>
    {newsView === 'company' && view && <div className="demo-news-inbox-head" data-reading={readingStory}>
      <div className="demo-news-filters"><label><span>Story age</span><select aria-label="Story age" value={age} onChange={event => setAge(event.target.value as NewsAge)}><option value="24h">Past 24 hours</option><option value="7d">Past 7 days</option><option value="30d">Past 30 days</option></select></label><label><span>Material</span><select aria-label="Material relevance" value={material} onChange={event => setMaterial(event.target.value)}><option value="all">All materials</option>{company.materials.map(item => <option key={item.material_id} value={item.material_id}>{item.name}</option>)}</select></label></div>
      <details className="demo-news-filter-options"><summary>More filters{additionalFilterCount > 0 ? ` · ${additionalFilterCount} active` : ''}</summary><div className="demo-news-coverage-toggles"><label><span>Sort stories</span><select aria-label="Sort stories" value={sort} onChange={event => setSort(event.target.value as NewsSort)}><option value="relevant">Most relevant</option><option value="newest">Newest published</option></select></label><label><span>Category</span><select aria-label="Story category" value={category} onChange={event => setCategory(event.target.value)}><option value="all">All categories</option>{categories.map(item => <option key={item} value={item}>{item}</option>)}</select></label><label><input type="checkbox" checked={includeUnknown} onChange={event => setIncludeUnknown(event.target.checked)} />Include unknown publication dates</label><label><input type="checkbox" checked={includeBackground} onChange={event => setIncludeBackground(event.target.checked)} />Show background matches</label><label><input type="checkbox" checked={showUnmatched} onChange={event => setShowUnmatched(event.target.checked)} />Show unmatched headlines</label></div></details>
      <p className="demo-news-result-count">{listed.length} of {visible.length} stories shown · {age === '24h' ? 'past 24 hours' : age === '7d' ? 'past 7 days' : 'past 30 days'}</p></div>}
    </div>
    <div role="tabpanel" id={`${tabsId}-company-panel`} aria-labelledby={`${tabsId}-company-tab`} aria-label="For your company" hidden={newsView !== 'company'}>
    {!view ? <><p className="demo-news-inline-note">{loading ? 'Checking public sources…' : 'No stories available yet. Refresh sources to try again.'}</p>{error && <details className="demo-news-more"><summary>Collection details</summary><p>Source check unavailable: {error}. No stories were returned for this check.</p></details>}</> : <>
      {selectionMissing && <SelectionUnavailableNotice kind="story" filtered={view.items.some(item => item.item_id === selectedId)} />}
      <div className="demo-news-layout" data-reading={readingStory}><div className="demo-news-inbox">

        {relevant.length === 0 && view.items.length > 0 && <p className="demo-news-note">{view.items.length} observed headlines have no recorded company match. Show unmatched headlines to inspect them without implying relevance.</p>}
        <div className="demo-news-list" aria-label="Ranked news stories">{visible.length ? listed.map(item => <button key={item.item_id} ref={selected?.item_id === item.item_id ? selectedRowRef : undefined} type="button" className="demo-news-row" aria-current={selected?.item_id === item.item_id ? 'true' : undefined} onClick={() => { pendingFocus.current = 'detail'; setSelectedId(item.item_id); setReadingStory(true); }}><span className="demo-news-row-top"><span className={`demo-news-fit demo-news-fit-${newsFit(item)}`}>{newsFit(item)} fit</span><span>{briefAge(item, now)}</span></span><span className="demo-news-row-main"><span className="demo-news-row-copy"><strong>{item.title}</strong><span className="demo-news-row-meta">{item.publisher ?? item.provider} · {item.category} · {item.matched_materials.join(', ') || item.matched_routes.join(', ') || 'Company context'}</span></span><StoryImage item={item} companyId={company.company_id} /></span></button>) : <div className="demo-news-empty"><p>{view.items.length === 0 ? 'No observed headlines were returned by the checked sources.' : olderThanThirtyOnly ? 'Observed matching headlines are outside the past 30 days. Review source health and collection times below.' : 'No stories match these controls. Use the recovery actions or More filters to inspect broader coverage.'}</p>{view.items.length > 0 && <div className="demo-news-empty-actions">{hasOlderStories && age !== '30d' && <button type="button" onClick={() => setAge('30d')}>Show past 30 days</button>}{hasUndatedStories && !includeUnknown && <button type="button" onClick={() => setIncludeUnknown(true)}>Include undated stories</button>}{hasBackgroundStories && <button type="button" onClick={() => setIncludeBackground(true)}>Show background matches</button>}{material !== 'all' || category !== 'all' ? <button type="button" onClick={() => { setMaterial('all'); setCategory('all'); }}>Clear material and category</button> : null}{relevant.length === 0 && !showUnmatched && <button type="button" onClick={() => setShowUnmatched(true)}>Inspect unmatched headlines</button>}</div>}{hasUndatedStories && !includeUnknown && <p>Publication time unavailable for some collected headlines.</p>}{hasBackgroundStories && <p>Background headlines are hidden by the current filter.</p>}</div>}{visible.length > listed.length && <button type="button" className="demo-news-show-more" onClick={() => setShowAll(true)}>Show {visible.length - listed.length} more stories</button>}</div>
      </div><article className="demo-news-detail" aria-label="Selected story">{selected ? <>
        <button ref={backRef} type="button" className="demo-news-back" onClick={() => { pendingFocus.current = 'list'; setReadingStory(false); }}>← Back to stories</button>
        <div className="demo-news-detail-kicker"><span>{newsFit(selected)} fit</span><span>{selected.category}</span><span>{selected.provider}</span></div><h3>{selected.title}</h3><StoryImage item={selected} companyId={company.company_id} large />{selected.summary && <p className="demo-news-lead">{selected.summary}</p>}
        <div className="demo-news-detail-meta"><span>{selected.publisher ?? 'Publisher unavailable'}</span><span>Feed published: {feedPublicationLabel(selected.published_at)}</span><span>{selected.article?.status === 'enriched' && selected.article.excerpt ? 'Bounded article excerpt' : 'Headline metadata only'}</span><span>First seen {dateLabel(selected.first_seen_at)}</span></div>
        {sourceHref(selected.url) && <p className="demo-news-original"><a href={sourceHref(selected.url)!} target="_blank" rel="noopener noreferrer">Read original source ↗</a></p>}
        {onAction && <><div className="demo-news-actions"><button type="button" className="demo-button" onClick={() => onAction('ask', selected)}>Ask operator</button></div><details className="demo-news-secondary"><summary>More actions</summary><div className="demo-news-actions">{(newsFit(selected) === 'strong' || newsFit(selected) === 'related') && selected.matched_materials.some(id => company.materials.some(materialItem => materialItem.material_id === id)) && <><button type="button" onClick={() => onAction('inventory', selected)}>Check inventory</button><button type="button" onClick={() => onAction('draft', selected)}>Prepare unsent draft</button></>}{newsFit(selected) !== 'unmatched' && <button type="button" onClick={() => onAction('watch', selected)}>Watch topic</button>}<button type="button" onClick={() => onAction('research', selected)}>Research source</button></div></details></>}
        <div className="demo-news-next-check"><strong>Recommended next check</strong><p>{selected.next_steps.length ? selected.next_steps.join('; ') : 'No specific next check was supplied. Verify the source before changing a plan.'}</p></div>
        <section><h4>What happened</h4><p>{selected.article?.status === 'enriched' && selected.article.excerpt && normalizedStoryCopy(selected.article.excerpt) !== normalizedStoryCopy(selected.summary) ? selected.article.excerpt : selected.summary ? 'No additional article text available. The source facts above come from feed metadata.' : 'Only headline metadata is available. Read the original source before drawing conclusions.'}</p>{selected.article && <p className="demo-news-evidence-state">Article evidence: {selected.article.status}{selected.article.status === 'enriched' ? <>{selected.article.published_at && <> · Article page date: {dateLabel(selected.article.published_at)}</>} · Article retrieved: {dateLabel(selected.article.fetched_at)} via {selected.article.method}</> : selected.article.reason ? ` · ${selected.article.reason}` : ''}</p>}</section>
        <section><h4>Why it may matter</h4>{!repeatedImplication && <p>{selected.implication}</p>}{distinctRelevance && <p><strong>Company relevance:</strong> {selected.relevance_reason}</p>}<p>Conditional implication ({selected.implication_confidence}). This is not a verified company impact.</p></section>
        <details className="demo-news-evidence"><summary>Evidence and source details</summary><div>{!distinctRelevance && <p><strong>Company relevance:</strong> {selected.relevance_reason}</p>}{selected.ranking_reason && <p><strong>Ranking reason:</strong> {selected.ranking_reason}</p>}<p><strong>Matched exposure:</strong> Materials {selected.matched_materials.join(', ') || 'none'} · Operations {selected.matched_operations?.join(', ') || 'none'} · Routes {selected.matched_routes.join(', ') || 'none'} · Currencies {selected.matched_currencies.join(', ') || 'none'}</p>
        <div className="demo-news-provenance"><strong>Source:</strong> {selected.source_tier} · {selected.source_reliability}. {selected.reliability_reason}<br />Feed published: {feedPublicationLabel(selected.published_at)} · First seen: {dateLabel(selected.first_seen_at)} · Feed retrieved: {dateLabel(selected.retrieved_at)}{typeof selected.relevance_score === 'number' && Number.isFinite(selected.relevance_score) && <p>Uncalibrated relevance heuristic: {selected.relevance_score.toFixed(2)} / 1. Ranking aid only; not a probability or source-quality score.</p>}{sourceHref(selected.url) && <p><a href={sourceHref(selected.url)!} target="_blank" rel="noopener noreferrer">Read original source ↗</a></p>}</div>
        </div></details>
      </> : <><button ref={backRef} type="button" className="demo-news-back" onClick={() => { pendingFocus.current = 'list'; setReadingStory(false); }}>← Back to stories</button><p className="demo-news-empty">{selectionMissing ? 'Choose another story from the list to inspect its evidence and next checks.' : 'Select a story to inspect its evidence and next checks.'}</p></>}</article></div>
      <details className="demo-news-more"><summary>Collection details</summary>{error && <p className="demo-news-collection">Source check unavailable: {error}. Previously displayed results retain their recorded times.</p>}<p className="demo-news-collection">Snapshot read: {dateLabel(view.checked_at)} · Cache state: {view.cache_state}</p>{view.collection && <p className="demo-news-collection"><strong>{collectionLabel}</strong> · Last successful news collection: {dateLabel(view.collection.news_success_at)} · Last successful FX collection: {dateLabel(view.collection.fx_success_at)} · Last recorded attempt: {dateLabel(view.collection.attempted_at)} · Collection status: {view.collection.status}</p>}<div className="demo-news-more-grid"><div><h3>Source health</h3>{view.sources.length ? <ul>{view.sources.map(source => <li key={source.source_id}><strong>{source.name}: {source.state}</strong> · checked {dateLabel(source.last_checked_at)}{source.reason ? ` · ${source.reason}` : ''}</li>)}</ul> : <p>No source status returned.</p>}</div><div><h3>Dated FX reference</h3>{view.fx.length ? <ul>{view.fx.map(rate => <li key={`${rate.provider}-${rate.base}-${rate.quote}-${rate.observation_date}`}>{rate.base}/{rate.quote} {rate.rate} · {rate.observation_date} · {rate.provider} · retrieved {dateLabel(rate.retrieved_at)} · informational, not an executable offer</li>)}</ul> : <p>No FX observation available.</p>}</div></div>{view.limitations.length > 0 && <div><h3>Coverage limits</h3><ul>{view.limitations.map((limit, index) => <li key={index}>{limit}</li>)}</ul></div>}</details>
    </>}
    </div>
    <div role="tabpanel" id={`${tabsId}-global-panel`} aria-labelledby={`${tabsId}-global-tab`} aria-label="Global Watch" hidden={newsView !== 'global'}><GlobalWatchPanel key={`${company.company_id}:${view?.context_version ?? 'none'}`} watch={view?.global_watch} /></div>
  </section>;
}

export function OutboxWorkspace({ company, messages }: { company: CompanyProfile; messages: SimulatedOutboxMessage[] }) {
  const own = messages.filter(message => message.company_id === company.company_id && message.status === 'simulated_sent' && message.simulated === true && message.external_delivery === false);
  return <section className="demo-company-panel demo-outbox-panel" aria-label="Simulated outbox"><div className="demo-heading"><div><span className="demo-kicker">Server-confirmed simulations</span><h2>Simulated outbox</h2></div><span className="demo-session">No external delivery</span></div>
    <p>These messages are recorded in this temporary workspace session. No email was sent to a supplier.</p>
    {own.length ? own.map(message => <article key={message.message_id} className="demo-outbox-message"><span className="demo-kicker">Simulated sent · No external delivery</span><h3>{message.subject}</h3><p>To: {supplierDisplayName(message.recipient_label)} · Material: {message.material_id} · Recorded {dateLabel(message.created_at)}</p><p>{message.body}</p></article>) : <p className="demo-muted">No simulated messages in this company workspace.</p>}
  </section>;
}
