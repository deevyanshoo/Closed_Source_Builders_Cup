import { useState } from 'react';
import type { CompanyProfile, NewsView } from './demo-api';
import { filterNewsItems, rankNewsItems } from './demo-company-news';

export type WorkspaceView = 'hub' | 'overview' | 'news' | 'procurement' | 'inventory' | 'drafts';
export type ProcurementStage = 'add' | 'review' | 'compare' | 'rfq';
export const workspaceViews: Exclude<WorkspaceView, 'hub'>[] = ['overview', 'news', 'procurement', 'inventory', 'drafts'];
export const procurementStages: ProcurementStage[] = ['add', 'review', 'compare', 'rfq'];
const viewNames: Record<Exclude<WorkspaceView, 'hub'>, string> = { overview: 'Overview', news: 'News', procurement: 'Procurement', inventory: 'Inventory & supply', drafts: 'Drafts & activity' };
const stageNames: Record<ProcurementStage, string> = { add: 'Add quotes', review: 'Review', compare: 'Compare', rfq: 'Draft RFQ' };

export function readWorkspaceLocation(search: string): { view: WorkspaceView; stage: ProcurementStage } {
  const params = new URLSearchParams(search);
  const rawView = params.get('view');
  const rawStage = params.get('stage');
  return {
    view: rawView && workspaceViews.includes(rawView as Exclude<WorkspaceView, 'hub'>) ? rawView as WorkspaceView : 'hub',
    stage: rawStage && procurementStages.includes(rawStage as ProcurementStage) ? rawStage as ProcurementStage : 'add',
  };
}

export function workspaceSearch(search: string, view: WorkspaceView, stage: ProcurementStage) {
  const params = new URLSearchParams(search);
  params.delete('company');
  if (view === 'hub') { params.delete('view'); params.delete('stage'); }
  else { params.set('view', view); if (view === 'procurement') params.set('stage', stage); else params.delete('stage'); }
  const next = params.toString();
  return next ? `?${next}` : '';
}

export function showWorkspaceActivity(view: WorkspaceView, stage: ProcurementStage, busy: string): boolean {
  const stages: Record<string, ProcurementStage> = { extract: 'add', plan: 'compare', recovery: 'compare', rfq: 'rfq' };
  return !(view === 'procurement' && stages[busy] === stage);
}

export function CompanyHub({ companies, current, busy, onOpen }: { companies: CompanyProfile[]; current?: CompanyProfile; busy: boolean; onOpen: (id: string) => void }) {
  const [query, setQuery] = useState('');
  const matches = companies.filter(item => `${item.name} ${item.sector}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  return <section className="zoorq-hub" aria-label="All companies">
    <div className="zoorq-hub-intro"><span className="demo-kicker">Company intelligence</span><h1>Where resilience begins.</h1><p>Choose a company to see the signals, supply position and decisions that matter now.</p>
      {current && <button type="button" className="demo-button" disabled={busy} onClick={() => onOpen(current.company_id)}>Continue {current.name} →</button>}</div>
    <div className="zoorq-hub-list"><div className="zoorq-hub-list-head"><div><span className="demo-kicker">Your workspaces</span><h2>All companies <small>{companies.length}</small></h2></div><label><span>Search companies</span><input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Company or sector" /></label></div>
      <div className="zoorq-company-grid">{matches.map(item => <article key={item.company_id} className="zoorq-company-card"><div><span className="demo-kicker">{item.sector}</span><h3>{item.name}</h3><p>{item.size} · {[...new Set(item.locations.map(location => location.country))].join(' · ')}</p></div><div className="zoorq-company-card-foot"><span>{item.materials.length} materials · {item.orders.length} orders</span><button type="button" className="demo-link" aria-label={`Open ${item.name} workspace`} disabled={busy} onClick={() => onOpen(item.company_id)}>Open workspace →</button></div></article>)}</div>
      {!matches.length && <p role="status">No companies match “{query}”. Try a company or sector name.</p>}
    </div>
  </section>;
}

export function WorkspaceNavigation({ company, view, onView }: { company: CompanyProfile; view: WorkspaceView; onView: (view: WorkspaceView) => void }) {
  return <nav className="zoorq-nav" aria-label="Workspace destinations"><button type="button" className="zoorq-all-companies" onClick={() => onView('hub')}>← All companies</button><div className="zoorq-nav-company"><span>Current company</span><strong>{company.name}</strong></div><div className="zoorq-nav-links">{workspaceViews.map(destination => <button type="button" key={destination} className={view === destination ? 'active' : ''} aria-current={view === destination ? 'page' : undefined} onClick={() => onView(destination)}>{viewNames[destination]}</button>)}</div></nav>;
}

export function ProcurementNavigation({ stage, onStage }: { stage: ProcurementStage; onStage: (stage: ProcurementStage) => void }) {
  return <nav className="zoorq-stage-nav" aria-label="Procurement stages">{procurementStages.map((item, index) => <button type="button" key={item} className={stage === item ? 'active' : ''} aria-current={stage === item ? 'step' : undefined} onClick={() => onStage(item)}><span>0{index + 1}</span>{stageNames[item]}</button>)}</nav>;
}

export function WorkspaceOverview({ company, news, quoteCount, onView }: { company: CompanyProfile; news: NewsView | null; quoteCount: number; onView: (view: WorkspaceView) => void }) {
  const cover = (material: CompanyProfile['materials'][number]) => {
    const stock = Number(material.stock), daily = Number(material.consumption_per_day);
    return Number.isFinite(stock) && Number.isFinite(daily) && stock >= 0 && daily > 0 ? stock / daily : null;
  };
  const urgent = [...company.materials].sort((a, b) => {
    const criticality = Number(b.criticality === 'high') - Number(a.criticality === 'high');
    return criticality || (cover(a) ?? Infinity) - (cover(b) ?? Infinity);
  })[0];
  const days = urgent && cover(urgent);
  const incompleteNews = !!news && (news.cache_state === 'stale' || news.cache_state === 'partial' || news.collection?.status === 'partial' || news.sources.some(source => source.state === 'stale' || source.state === 'unavailable'));
  const latest = news && !incompleteNews ? rankNewsItems(filterNewsItems(news.items, 'all', 'all', false, { age: '7d', now: Date.now(), includeUnknown: false, includeBackground: false }), 'relevant')[0] : undefined;
  return <section className="zoorq-overview" aria-label="Company overview"><div className="zoorq-section-heading"><span className="demo-kicker">Company overview</span><h2>What needs attention</h2><p>Start with these three priorities, then open the area you need.</p></div>
    <div className="zoorq-priority-grid"><article><span>01 / Supply</span><h3>{urgent ? `${urgent.name} availability` : 'Material coverage'}</h3><p>{urgent ? `${days === null ? 'Stock cover unavailable' : `${Number(days.toFixed(1))} days of stock cover`} · ${urgent.stock} ${urgent.quantity_unit} recorded stock · ${urgent.criticality} criticality. Synthetic company profile.` : 'No materials are listed in this synthetic profile.'}</p><button type="button" className="demo-link" onClick={() => onView('inventory')}>Inspect inventory →</button></article><article><span>02 / Market signals</span><h3>{incompleteNews ? 'Coverage needs review' : latest?.title ?? 'News coverage'}</h3><p>{incompleteNews ? `Coverage may be incomplete or dated · checked ${news?.checked_at}. Read source status and publication dates in News.` : latest ? `${latest.publisher ?? latest.provider} · Published ${latest.published_at}` : 'No company story in the past 7 days under the default News filters. Check source status in News.'}</p><button type="button" className="demo-link" onClick={() => onView('news')}>Open news →</button></article><article><span>03 / Sourcing</span><h3>{quoteCount ? `${quoteCount} quote${quoteCount === 1 ? '' : 's'} in this workspace` : 'Review supplier quotes'}</h3><p>Compare accepted offers with the current buyer inputs, then review any draft before use.</p><button type="button" className="demo-link" onClick={() => onView('procurement')}>Open procurement →</button></article></div>
    <div className="zoorq-next"><h3>Next actions</h3><button type="button" onClick={() => onView('news')}>Read company signals</button><button type="button" onClick={() => onView('procurement')}>Review sourcing options</button><button type="button" onClick={() => onView('drafts')}>Check drafts & activity</button></div>
  </section>;
}
