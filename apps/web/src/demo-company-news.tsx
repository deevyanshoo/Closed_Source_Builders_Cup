import { useEffect, useState } from 'react';
import type { CompanyProfile, NewsItem, NewsView, SimulatedOutboxMessage } from './demo-api';
import { ActivityIndicator } from './demo-activity';
import { supplierDisplayName } from './demo-display';
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
type NewsFilterOptions = { age?: NewsAge; now?: number; includeUnknown?: boolean; includeBackground?: boolean };
const ageMs: Record<NewsAge, number> = { '24h': 86_400_000, '7d': 604_800_000, '30d': 2_592_000_000 };
const publishedTime = (item: NewsItem) => {
  const time = item.published_at ? Date.parse(item.published_at) : NaN;
  return Number.isFinite(time) ? time : null;
};
export const feedPublicationLabel = (value: string | null | undefined) =>
  value && Number.isFinite(Date.parse(value)) ? dateLabel(value) : 'Publication time unavailable';
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
  if (sort === 'relevant') {
    const fit = fitOrder[newsFit(a)] - fitOrder[newsFit(b)];
    if (fit) return fit;
  }
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
export const selectNewsItem = (id: string | null, items: NewsItem[]): NewsItem | null => items.find(item => item.item_id === id) ?? items[0] ?? null;
const briefAge = (item: NewsItem, now: number) => {
  const stamp = publishedTime(item);
  if (stamp === null) return 'Publication time unavailable';
  const hours = Math.floor((now - stamp) / 3_600_000);
  if (hours < 0) return `Published ${dateLabel(item.published_at)}`;
  if (hours < 1) return 'Published less than 1 hour ago';
  if (hours < 24) return `Published ${hours}h ago`;
  return `Published ${Math.floor(hours / 24)}d ago`;
};

export function CompanyOverview({ company }: { company: CompanyProfile }) {
  return <section className="demo-company-panel" aria-label="Company overview">
    <div className="demo-heading"><div><span className="demo-kicker">Fictional company workspace</span><h2>{company.name}</h2></div><span className="demo-session">Synthetic profile · as of {company.as_of}</span></div>
    <p>{company.description}</p><p className="demo-muted">{company.sector} · {company.size} · Reporting currency {company.reporting_currency} · {company.locations.map(location => `${location.city}, ${location.country}`).join('; ')}</p>
    <div className="demo-company-grid"><div><h3>Inventory and demand</h3><div className="demo-table-wrap"><table><thead><tr><th>Material</th><th>Stock</th><th>Daily use</th><th>Monthly need</th><th>Criticality</th></tr></thead><tbody>{company.materials.map(material => <tr key={material.material_id}><td>{material.name}</td><td>{material.stock} {material.quantity_unit}</td><td>{material.consumption_per_day} {material.quantity_unit}</td><td>{material.monthly_requirement} {material.quantity_unit}</td><td>{material.criticality}</td></tr>)}</tbody></table></div></div>
      <div><h3>Orders</h3>{company.orders.length ? <div className="demo-table-wrap"><table><thead><tr><th>Order</th><th>Material</th><th>Quantity</th><th>Needed</th><th>Status</th></tr></thead><tbody>{company.orders.map(order => <tr key={order.order_id}><td>{order.order_id}</td><td>{order.material_id}</td><td>{order.quantity} {order.unit}</td><td>{order.need_by}</td><td>{order.status}</td></tr>)}</tbody></table></div> : <p className="demo-muted">No orders in this fictional profile.</p>}</div></div>
    <div className="demo-company-grid"><div><h3>Suppliers</h3>{company.suppliers.length ? <ul>{company.suppliers.map(supplier => <li key={supplier.supplier_id}>{supplierDisplayName(supplier.name)} · {supplier.country} · {supplier.materials.join(', ')} · {supplier.lead_time_days} days · {supplier.settlement_currency}</li>)}</ul> : <p className="demo-muted">No profile suppliers listed.</p>}</div>
      <div><h3>Trade routes</h3>{company.routes.length ? <ul>{company.routes.map(route => <li key={route.route_id}>{route.direction}: {route.origin} → {route.via.length ? `${route.via.join(' → ')} → ` : ''}{route.destination} · {route.materials.join(', ')} · {route.currency}</li>)}</ul> : <p className="demo-muted">No profile routes listed.</p>}</div></div>
    <div className="demo-alert"><strong>Runnable quote coverage</strong><p>{company.quote_coverage.note}</p><p>Materials: {company.quote_coverage.materials.length ? company.quote_coverage.materials.join(', ') : 'None in this demo'}. Quote comparisons use each covered material's listed unit. Fictional profile values are not observed operations.</p></div>
  </section>;
}

export function NewsWorkspace({ company, view, loading, error, onRefresh, onAction }: { company: CompanyProfile; view: NewsView | null; loading: boolean; error: string; onRefresh: () => void; onAction?: (action: 'ask' | 'inventory' | 'draft' | 'watch' | 'research', item: NewsItem) => void }) {
  const [material, setMaterial] = useState('all');
  const [category, setCategory] = useState('all');
  const [age, setAge] = useState<NewsAge>('7d');
  const [sort, setSort] = useState<NewsSort>('relevant');
  const [includeUnknown, setIncludeUnknown] = useState(false);
  const [includeBackground, setIncludeBackground] = useState(false);
  const [showUnmatched, setShowUnmatched] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  useEffect(() => { setSelectedId(null); setMaterial('all'); setCategory('all'); setAge('7d'); setSort('relevant'); setIncludeUnknown(false); setIncludeBackground(false); setShowUnmatched(false); }, [company.company_id, view?.context_version]);
  const categories = [...new Set(view?.items.map(item => item.category) ?? [])].sort();
  const relevant = (view?.items ?? []).filter(item => newsFit(item) !== 'unmatched');
  const directCount = relevant.filter(item => relevanceKind(item) === 'direct_material').length;
  const now = Date.now();
  const visible = rankNewsItems(filterNewsItems(view?.items ?? [], material, category, showUnmatched, { age, now, includeUnknown, includeBackground }), sort);
  const selected = selectNewsItem(selectedId, visible) as EnrichedNewsItem | null;
  const collectionLabel = view?.collection?.mode === 'manual' || view?.collection?.status === 'manual' ? 'Manual source check' : view?.collection?.mode === 'live_fallback' || view?.collection?.status === 'live_fallback' ? 'Live source check' : 'Hourly background collection';
  const sourceProblem = view?.sources.some(source => source.state === 'stale' || source.state === 'unavailable');
  return <section className="demo-company-panel demo-news-panel" aria-label="Live news workspace">
    <div className="demo-heading"><div><span className="demo-kicker">Observed public signals · {company.name}</span><h2>News inbox</h2></div><button type="button" className="demo-button" disabled={loading} onClick={onRefresh}>{loading ? 'Checking sources…' : 'Refresh sources'}</button></div>
    <p className="demo-muted">Ranked for this fictional company. Open a story to inspect the evidence before changing a plan. A topic match is not a confirmed supplier delay, price change, or forecast.</p>
    {error && <div className="demo-alert" role="alert">Source check unavailable: {error}. Previously displayed results, if any, keep their recorded check time.</div>}
    {loading && <ActivityIndicator state="searching" label="Checking public news sources" detail={view ? `The items below are from the previous check at ${dateLabel(view.checked_at)}.` : 'Fetching observed headline metadata and source health for this company.'} />}
    {!view ? <p className="demo-alert">{loading ? 'Checking reviewed public sources…' : 'News coverage unavailable. No synthetic articles are substituted.'}</p> : <>
      <div className="demo-news-status"><strong>Coverage: {view.cache_state}</strong><span>Checked {dateLabel(view.checked_at)}</span><span>{relevant.length} company-relevant of {view.items.length} observed items · {directCount} direct material {directCount === 1 ? 'match' : 'matches'} · {relevant.length - directCount} indirect {relevant.length - directCount === 1 ? 'match' : 'matches'}</span></div>
      {(view.cache_state === 'stale' || sourceProblem || view.collection?.status === 'partial') && <p className="demo-news-warning">Coverage may be incomplete or dated. Read source and collection times before acting.</p>}
      {view.collection && <p className="demo-news-collection"><strong>{collectionLabel}</strong> · Last successful news collection: {dateLabel(view.collection.news_success_at)} · Last successful FX collection: {dateLabel(view.collection.fx_success_at)} · Last recorded attempt: {dateLabel(view.collection.attempted_at)} · Collection status: {view.collection.status}</p>}
      <div className="demo-news-layout"><div className="demo-news-inbox">
        <div className="demo-news-inbox-head"><div className="demo-news-age" role="group" aria-label="Story age">{(['24h', '7d', '30d'] as NewsAge[]).map(value => <button key={value} type="button" aria-pressed={age === value} onClick={() => setAge(value)}>{value === '24h' ? '24 hours' : value === '7d' ? '7 days' : '30 days'}</button>)}</div>
          <div className="demo-news-filters"><label><span>Sort stories</span><select aria-label="Sort stories" value={sort} onChange={event => setSort(event.target.value as NewsSort)}><option value="relevant">Most relevant</option><option value="newest">Newest published</option></select></label><label><span>Material</span><select aria-label="Material relevance" value={material} onChange={event => setMaterial(event.target.value)}><option value="all">All materials</option>{company.materials.map(item => <option key={item.material_id} value={item.material_id}>{item.name}</option>)}</select></label><label><span>Category</span><select value={category} onChange={event => setCategory(event.target.value)}><option value="all">All categories</option>{categories.map(item => <option key={item} value={item}>{item}</option>)}</select></label></div>
          <div className="demo-news-coverage-toggles"><label><input type="checkbox" checked={includeUnknown} onChange={event => setIncludeUnknown(event.target.checked)} />Include unknown publication dates</label><label><input type="checkbox" checked={includeBackground} onChange={event => setIncludeBackground(event.target.checked)} />Show background matches</label><label><input type="checkbox" checked={showUnmatched} onChange={event => setShowUnmatched(event.target.checked)} />Show unmatched headlines</label></div>
          <p className="demo-news-result-count">{visible.length} stories shown · {age === '24h' ? 'past 24 hours' : age === '7d' ? 'past 7 days' : 'past 30 days'}</p></div>
        {relevant.length === 0 && view.items.length > 0 && <p className="demo-news-note">{view.items.length} observed headlines have no recorded company match. Show unmatched headlines to inspect them without implying relevance.</p>}
        <div className="demo-news-list" aria-label="Ranked news stories">{visible.length ? visible.map(item => <button key={item.item_id} type="button" className="demo-news-row" aria-current={selected?.item_id === item.item_id ? 'true' : undefined} onClick={() => setSelectedId(item.item_id)}><span className="demo-news-row-top"><span className={`demo-news-fit demo-news-fit-${newsFit(item)}`}>{newsFit(item)} fit</span><span>{briefAge(item, now)}</span></span><strong>{item.title}</strong><span className="demo-news-row-meta">{item.publisher ?? item.provider} · {item.category} · {item.matched_materials.join(', ') || item.matched_routes.join(', ') || 'Company context'}</span></button>) : <p className="demo-news-empty">{view.items.length === 0 ? 'No observed headlines were returned by the checked sources.' : 'No stories match these controls. Try 30 days, include unknown dates, or inspect broader coverage.'}</p>}</div>
      </div><article className="demo-news-detail" aria-label="Selected story">{selected ? <>
        <div className="demo-news-detail-kicker"><span>{newsFit(selected)} fit</span><span>{selected.category}</span><span>{selected.provider}</span></div><h3>{selected.title}</h3><p className="demo-news-lead">{selected.summary || selected.implication}</p>
        <div className="demo-news-detail-meta"><span>{selected.publisher ?? 'Publisher unavailable'}</span><span>{briefAge(selected, now)}</span><span>First seen {dateLabel(selected.first_seen_at)}</span></div>
        {onAction && <div className="demo-news-actions"><button type="button" className="demo-button" onClick={() => onAction('ask', selected)}>Ask operator</button>{(newsFit(selected) === 'strong' || newsFit(selected) === 'related') && selected.matched_materials.some(id => company.materials.some(materialItem => materialItem.material_id === id)) && <><button type="button" onClick={() => onAction('inventory', selected)}>Check inventory</button><button type="button" onClick={() => onAction('draft', selected)}>Prepare unsent draft</button></>}{newsFit(selected) !== 'unmatched' && <button type="button" onClick={() => onAction('watch', selected)}>Watch topic</button>}<button type="button" onClick={() => onAction('research', selected)}>Research source</button></div>}
        <section><h4>What happened</h4><p>{selected.article?.status === 'enriched' && selected.article.excerpt ? selected.article.excerpt : selected.summary || 'Only headline metadata is available. Read the original source before drawing conclusions.'}</p>{selected.article && <p className="demo-news-evidence-state">Article evidence: {selected.article.status}{selected.article.status === 'enriched' ? <>{selected.article.published_at && <> · Article page date: {dateLabel(selected.article.published_at)}</>} · Article retrieved: {dateLabel(selected.article.fetched_at)} via {selected.article.method}</> : selected.article.reason ? ` · ${selected.article.reason}` : ''}</p>}</section>
        <section><h4>Why it may matter</h4><p>{selected.implication}</p><p><strong>Company relevance:</strong> {selected.relevance_reason}</p>{selected.ranking_reason && <p><strong>Ranking reason:</strong> {selected.ranking_reason}</p>}<p><strong>Matched exposure:</strong> Materials {selected.matched_materials.join(', ') || 'none'} · Operations {selected.matched_operations?.join(', ') || 'none'} · Routes {selected.matched_routes.join(', ') || 'none'} · Currencies {selected.matched_currencies.join(', ') || 'none'}</p><p>Conditional implication ({selected.implication_confidence}). This is not a verified company impact.</p></section>
        <section><h4>Recommended next check</h4><p>{selected.next_steps.length ? selected.next_steps.join('; ') : 'Read the original source and check current operational evidence.'}</p></section>
        <div className="demo-news-provenance"><strong>Source:</strong> {selected.source_tier} · {selected.source_reliability}. {selected.reliability_reason}<br />Feed published: {feedPublicationLabel(selected.published_at)} · First seen: {dateLabel(selected.first_seen_at)} · Feed retrieved: {dateLabel(selected.retrieved_at)}{typeof selected.relevance_score === 'number' && Number.isFinite(selected.relevance_score) && <p>Uncalibrated relevance heuristic: {selected.relevance_score.toFixed(2)} / 1. Ranking aid only; not a probability or source-quality score.</p>}{sourceHref(selected.url) && <p><a href={sourceHref(selected.url)!} target="_blank" rel="noopener noreferrer">Read original source ↗</a></p>}</div>
      </> : <p className="demo-news-empty">Select a story to inspect its evidence and next checks.</p>}</article></div>
      <details className="demo-news-more"><summary>Source health, FX reference and coverage limits</summary><div className="demo-news-more-grid"><div><h3>Source health</h3>{view.sources.length ? <ul>{view.sources.map(source => <li key={source.source_id}><strong>{source.name}: {source.state}</strong> · checked {dateLabel(source.last_checked_at)}{source.reason ? ` · ${source.reason}` : ''}</li>)}</ul> : <p>No source status returned.</p>}</div><div><h3>Dated FX reference</h3>{view.fx.length ? <ul>{view.fx.map(rate => <li key={`${rate.provider}-${rate.base}-${rate.quote}-${rate.observation_date}`}>{rate.base}/{rate.quote} {rate.rate} · {rate.observation_date} · {rate.provider} · retrieved {dateLabel(rate.retrieved_at)} · informational, not an executable offer</li>)}</ul> : <p>No FX observation available.</p>}</div></div>{view.limitations.length > 0 && <div><h3>Coverage limits</h3><ul>{view.limitations.map((limit, index) => <li key={index}>{limit}</li>)}</ul></div>}</details>
    </>}
  </section>;
}

export function OutboxWorkspace({ company, messages }: { company: CompanyProfile; messages: SimulatedOutboxMessage[] }) {
  const own = messages.filter(message => message.company_id === company.company_id && message.status === 'simulated_sent' && message.simulated === true && message.external_delivery === false);
  return <section className="demo-company-panel demo-outbox-panel" aria-label="Simulated outbox"><div className="demo-heading"><div><span className="demo-kicker">Server-confirmed simulations</span><h2>Simulated outbox</h2></div><span className="demo-session">No external delivery</span></div>
    <p>These messages are recorded in this temporary demo session. No email was sent to a supplier.</p>
    {own.length ? own.map(message => <article key={message.message_id} className="demo-outbox-message"><span className="demo-kicker">Simulated sent · No external delivery</span><h3>{message.subject}</h3><p>To: {supplierDisplayName(message.recipient_label)} · Material: {message.material_id} · Recorded {dateLabel(message.created_at)}</p><p>{message.body}</p></article>) : <p className="demo-muted">No simulated messages in this company workspace.</p>}
  </section>;
}
