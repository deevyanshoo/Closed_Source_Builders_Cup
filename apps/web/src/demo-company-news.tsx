import { useEffect, useState } from 'react';
import type { CompanyProfile, NewsItem, NewsView, SimulatedOutboxMessage } from './demo-api';

const dateLabel = (value: string | null | undefined) => {
  if (!value) return 'Time unavailable';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
};
const sourceHref = (url: string) => {
  try { const parsed = new URL(url); return parsed.protocol === 'https:' && !parsed.username && !parsed.password ? parsed.href : null; }
  catch { return null; }
};
const relevanceKind = (item: NewsItem) => item.relevance_kind ?? (item.matched_materials.length ? 'direct_material' : item.matched_routes.length ? 'route' : item.matched_currencies.length ? 'currency' : item.matched_operations?.length ? 'sector' : 'unmatched');
export const filterNewsItems = (items: NewsItem[], material: string, category: string, showUnmatched = false) =>
  items.filter(item => (showUnmatched || relevanceKind(item) !== 'unmatched') &&
    (material === 'all' || item.matched_materials.includes(material)) && (category === 'all' || item.category === category));
const unmatchedOnly = (view: NewsView | null) => !!view?.items.length && filterNewsItems(view.items, 'all', 'all').length === 0;

export function CompanyOverview({ company }: { company: CompanyProfile }) {
  return <section className="demo-company-panel" aria-label="Company overview">
    <div className="demo-heading"><div><span className="demo-kicker">Fictional company workspace</span><h2>{company.name}</h2></div><span className="demo-session">Synthetic profile · as of {company.as_of}</span></div>
    <p>{company.description}</p><p className="demo-muted">{company.sector} · {company.size} · Reporting currency {company.reporting_currency} · {company.locations.map(location => `${location.city}, ${location.country}`).join('; ')}</p>
    <div className="demo-company-grid"><div><h3>Inventory and demand</h3><div className="demo-table-wrap"><table><thead><tr><th>Material</th><th>Stock</th><th>Daily use</th><th>Monthly need</th><th>Criticality</th></tr></thead><tbody>{company.materials.map(material => <tr key={material.material_id}><td>{material.name}</td><td>{material.stock} {material.quantity_unit}</td><td>{material.consumption_per_day} {material.quantity_unit}</td><td>{material.monthly_requirement} {material.quantity_unit}</td><td>{material.criticality}</td></tr>)}</tbody></table></div></div>
      <div><h3>Orders</h3>{company.orders.length ? <div className="demo-table-wrap"><table><thead><tr><th>Order</th><th>Material</th><th>Quantity</th><th>Needed</th><th>Status</th></tr></thead><tbody>{company.orders.map(order => <tr key={order.order_id}><td>{order.order_id}</td><td>{order.material_id}</td><td>{order.quantity} {order.unit}</td><td>{order.need_by}</td><td>{order.status}</td></tr>)}</tbody></table></div> : <p className="demo-muted">No orders in this fictional profile.</p>}</div></div>
    <div className="demo-company-grid"><div><h3>Suppliers</h3>{company.suppliers.length ? <ul>{company.suppliers.map(supplier => <li key={supplier.supplier_id}>{supplier.name} · {supplier.country} · {supplier.materials.join(', ')} · {supplier.lead_time_days} days · {supplier.settlement_currency}</li>)}</ul> : <p className="demo-muted">No profile suppliers listed.</p>}</div>
      <div><h3>Trade routes</h3>{company.routes.length ? <ul>{company.routes.map(route => <li key={route.route_id}>{route.direction}: {route.origin} → {route.via.length ? `${route.via.join(' → ')} → ` : ''}{route.destination} · {route.materials.join(', ')} · {route.currency}</li>)}</ul> : <p className="demo-muted">No profile routes listed.</p>}</div></div>
    <div className="demo-alert"><strong>Runnable quote coverage</strong><p>{company.quote_coverage.note}</p><p>Materials: {company.quote_coverage.materials.length ? company.quote_coverage.materials.join(', ') : 'None in this demo'}. Quote comparisons use each covered material's listed unit. Fictional profile values are not observed operations.</p></div>
  </section>;
}

export function NewsWorkspace({ company, view, loading, error, onRefresh }: { company: CompanyProfile; view: NewsView | null; loading: boolean; error: string; onRefresh: () => void }) {
  const [material, setMaterial] = useState('all');
  const [category, setCategory] = useState('all');
  const [showUnmatched, setShowUnmatched] = useState(() => unmatchedOnly(view));
  useEffect(() => { setShowUnmatched(unmatchedOnly(view)); }, [view]);
  const categories = [...new Set(view?.items.map(item => item.category) ?? [])].sort();
  const visible = filterNewsItems(view?.items ?? [], material, category, showUnmatched);
  const relevant = filterNewsItems(view?.items ?? [], 'all', 'all');
  const relevantCount = relevant.length;
  const directCount = relevant.filter(item => relevanceKind(item) === 'direct_material').length;
  const indirectCount = relevantCount - directCount;
  return <section className="demo-company-panel demo-news-panel" aria-label="Live news workspace">
    <div className="demo-heading"><div><span className="demo-kicker">Observed public sources · conditional company exposure</span><h2>News workspace</h2></div><button type="button" className="demo-button" disabled={loading} onClick={onRefresh}>{loading ? 'Checking sources…' : 'Refresh sources'}</button></div>
    <p className="demo-muted">News is observed public information. {company.name} is fictional; a matching topic is not a confirmed supplier delay, price change, or forecast. Source reliability and company relevance are separate assessments.</p>
    {error && <div className="demo-alert" role="alert">Source check unavailable: {error}. Previously displayed results, if any, keep their recorded check time.</div>}
    {loading && view && <p className="demo-muted" role="status">Checking sources now. The items below are from the previous check at {dateLabel(view.checked_at)}.</p>}
    {!view ? <p className="demo-alert">{loading ? 'Checking reviewed public sources…' : 'News coverage unavailable. No synthetic articles are substituted.'}</p> : <>
      <div className="demo-news-status"><strong>Coverage: {view.cache_state}</strong><span>Checked {dateLabel(view.checked_at)}</span><span>{relevantCount} company-relevant of {view.items.length} observed items · {directCount} direct material {directCount === 1 ? 'match' : 'matches'} · {indirectCount} indirect {indirectCount === 1 ? 'match' : 'matches'}</span></div>
      <div className="demo-company-grid"><div><h3>Source health</h3>{view.sources.length ? <ul>{view.sources.map(source => <li key={source.source_id}><strong>{source.name}: {source.state}</strong> · checked {dateLabel(source.last_checked_at)}{source.reason ? ` · ${source.reason}` : ''}</li>)}</ul> : <p className="demo-muted">No source status returned.</p>}</div>
        <div><h3>Dated FX reference</h3>{view.fx.length ? <ul>{view.fx.map(rate => <li key={`${rate.provider}-${rate.base}-${rate.quote}-${rate.observation_date}`}>{rate.base}/{rate.quote} {rate.rate} · {rate.observation_date} · {rate.provider} · retrieved {dateLabel(rate.retrieved_at)} · informational, not an executable offer</li>)}</ul> : <p className="demo-muted">No FX observation available.</p>}</div></div>
      {view.limitations.length > 0 && <div className="demo-alert"><strong>Coverage limits</strong><ul>{view.limitations.map((limit, index) => <li key={index}>{limit}</li>)}</ul></div>}
      <div className="demo-news-filters"><label><span>Material relevance</span><select value={material} onChange={event => setMaterial(event.target.value)}><option value="all">All materials</option>{company.materials.map(item => <option key={item.material_id} value={item.material_id}>{item.name}</option>)}</select></label><label><span>Category</span><select value={category} onChange={event => setCategory(event.target.value)}><option value="all">All categories</option>{categories.map(item => <option key={item} value={item}>{item}</option>)}</select></label><label className="demo-news-unmatched"><input type="checkbox" checked={showUnmatched} onChange={event => setShowUnmatched(event.target.checked)} />Show unmatched headlines</label></div>
      {relevantCount === 0 && view.items.length > 0 && <p className="demo-alert">{view.items.length} observed headlines have no recorded company match. {showUnmatched ? 'Showing unmatched observations for inspection; no company relevance is inferred.' : 'Turn on Show unmatched headlines to inspect them without implying relevance.'}</p>}
      {visible.length ? <div className="demo-news-items">{visible.map(item => {
        const href = sourceHref(item.url);
        const kind = relevanceKind(item);
        const unmatched = kind === 'unmatched';
        return <article key={item.item_id} className="demo-news-item"><span className="demo-kicker">{unmatched ? 'Unmatched observation · ' : ''}{item.category} · {item.provider}</span><h3>{href ? <a href={href} target="_blank" rel="noopener noreferrer">{item.title} ↗</a> : item.title}</h3>
          {item.summary && <p><strong>Feed summary:</strong> {item.summary}</p>}
          <p className="demo-muted">{item.publisher ?? 'Publisher unavailable'} · {item.published_at ? `Published ${dateLabel(item.published_at)}` : 'Publication time unavailable'} · First seen {dateLabel(item.first_seen_at)} · Retrieved {dateLabel(item.retrieved_at)}</p>
          <p><strong>Source:</strong> {item.source_tier} · {item.source_reliability}. {item.reliability_reason}</p>
          <p><strong>Company relevance:</strong> {kind.replace('_', ' ')} · {item.relevance_reason} · Materials {item.matched_materials.join(', ') || 'none'} · Operations {item.matched_operations?.map(value => value.charAt(0).toUpperCase() + value.slice(1)).join(', ') || 'none'} · Routes {item.matched_routes.join(', ') || 'none'} · Currencies {item.matched_currencies.join(', ') || 'none'}</p>
          {typeof item.relevance_score === 'number' && Number.isFinite(item.relevance_score) && item.relevance_score >= 0 && item.relevance_score <= 1 && <p className="demo-muted">Uncalibrated relevance heuristic: {item.relevance_score.toFixed(2)} / 1. This is a ranking aid, not a probability or source-quality score.</p>}
          <p><strong>Conditional implication ({item.implication_confidence}):</strong> {item.implication}</p>
          {item.next_steps.length > 0 && <p><strong>Next checks:</strong> {item.next_steps.join('; ')}</p>}
        </article>;
      })}</div> : <p className="demo-alert">{view.items.length === 0 ? 'No observed headlines were returned by the checked sources. Check source health and coverage limits above.' : 'No observed items match these filters. Change the filters or inspect unmatched headlines; check source health and coverage limits above.'}</p>}
    </>}
  </section>;
}

export function OutboxWorkspace({ company, messages }: { company: CompanyProfile; messages: SimulatedOutboxMessage[] }) {
  const own = messages.filter(message => message.company_id === company.company_id && message.status === 'simulated_sent' && message.simulated === true && message.external_delivery === false);
  return <section className="demo-company-panel demo-outbox-panel" aria-label="Simulated outbox"><div className="demo-heading"><div><span className="demo-kicker">Server-confirmed simulations</span><h2>Simulated outbox</h2></div><span className="demo-session">No external delivery</span></div>
    <p>These messages are recorded in this temporary demo session. No email was sent to a supplier.</p>
    {own.length ? own.map(message => <article key={message.message_id} className="demo-outbox-message"><span className="demo-kicker">Simulated sent · No external delivery</span><h3>{message.subject}</h3><p>To: {message.recipient_label} · Material: {message.material_id} · Recorded {dateLabel(message.created_at)}</p><p>{message.body}</p></article>) : <p className="demo-muted">No simulated messages in this company workspace.</p>}
  </section>;
}
