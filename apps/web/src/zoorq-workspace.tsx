import { useState } from 'react';
import type { CompanyProfile, NewsView } from './demo-api';
import { filterNewsItems, rankNewsItems } from './demo-company-news';
import { ZoorqLens } from './zoorq-lens';
import { ThemeControl } from './demo-theme';
import { ZoorqEcosystem } from './zoorq-ecosystem';
import { destinationIcon, ArrowLeftIcon, ArrowRightIcon, ChatBubbleIcon, CompanyIcon, MagnifyingGlassIcon, SpeakerLoudIcon } from './zoorq-icons';
import './zoorq-workspace.css';
import './zoorq-landing.css';

export type WorkspaceView = 'hub' | 'overview' | 'news' | 'procurement' | 'inventory' | 'drafts';
export type ProcurementStage = 'add' | 'review' | 'compare' | 'rfq';
export const workspaceViews: Exclude<WorkspaceView, 'hub'>[] = ['overview', 'news', 'procurement', 'inventory', 'drafts'];
export const procurementStages: ProcurementStage[] = ['add', 'review', 'compare', 'rfq'];
const viewNames: Record<Exclude<WorkspaceView, 'hub'>, string> = { overview: 'Overview', news: 'News', procurement: 'Procurement', inventory: 'Inventory & supply', drafts: 'Drafts & activity' };
const mobileViewNames: Record<Exclude<WorkspaceView, 'hub'>, string> = { overview: 'Home', news: 'News', procurement: 'Procure', inventory: 'Supply', drafts: 'Drafts' };
const stageNames: Record<ProcurementStage, string> = { add: 'Add quotes', review: 'Review', compare: 'Compare', rfq: 'Draft RFQ' };

export function readWorkspaceLocation(search: string): { view: WorkspaceView; stage: ProcurementStage } {
  const params = new URLSearchParams(search), rawView = params.get('view'), rawStage = params.get('stage');
  return { view: rawView && workspaceViews.includes(rawView as Exclude<WorkspaceView, 'hub'>) ? rawView as WorkspaceView : 'hub', stage: rawStage && procurementStages.includes(rawStage as ProcurementStage) ? rawStage as ProcurementStage : 'add' };
}
export function workspaceSearch(search: string, view: WorkspaceView, stage: ProcurementStage) {
  const params = new URLSearchParams(search); params.delete('company');
  if (view === 'hub') { params.delete('view'); params.delete('stage'); }
  else { params.set('view', view); if (view === 'procurement') params.set('stage', stage); else params.delete('stage'); }
  const next = params.toString(); return next ? `?${next}` : '';
}
export function showWorkspaceActivity(view: WorkspaceView, stage: ProcurementStage, busy: string): boolean {
  const stages: Record<string, ProcurementStage> = { extract: 'add', plan: 'compare', recovery: 'compare', rfq: 'rfq' };
  return !(view === 'procurement' && stages[busy] === stage);
}
export function filterWorkspaceCompanies(companies: CompanyProfile[], query: string) {
  const needle = query.trim().toLocaleLowerCase();
  return companies.filter(item => `${item.name} ${item.sector}`.toLocaleLowerCase().includes(needle));
}

export function CompanyHub({ companies, current, busy, onOpen }: { companies: CompanyProfile[]; current?: CompanyProfile; busy: boolean; onOpen: (id: string) => void }) {
  const [query, setQuery] = useState(''); const matches = filterWorkspaceCompanies(companies, query);
  return <section className="zoorq-hub zoorq-landing" aria-label="All companies">
    <div className="zoorq-entrance">
      <div className="zoorq-entrance-copy">
        <span className="zoorq-entrance-kicker">An ecosystem of resilience intelligence</span>
        <h1>A clearer view.<br /><span>A stronger next move.</span></h1>
        <p>Connect world signals, supply decisions and an operator that helps you act.</p>
        <div className="zoorq-entrance-actions">
          {current ? <button type="button" className="zoorq-primary" disabled={busy} onClick={() => onOpen(current.company_id)}>Continue {current.name} <ArrowRightIcon /></button> : <a className="zoorq-primary" href="#zoorq-companies">Choose a company <ArrowRightIcon /></a>}
          {current && <a className="zoorq-entrance-link" href="#zoorq-companies">Explore companies <ArrowRightIcon /></a>}
        </div>
      </div>
      <div className="zoorq-entrance-visual"><ZoorqEcosystem /><span className="zoorq-network-caption">Signals. Supply. Decisions. Connected.</span></div>
    </div>
    <div className="zoorq-entrance-path" aria-label="What you can do"><span>Read the signal</span><ArrowRightIcon /><span>Explore the impact</span><ArrowRightIcon /><span>Make your next move</span></div>
    <div className="zoorq-hub-list" id="zoorq-companies">
      <div className="zoorq-hub-list-head"><div><h2>Choose your company.<small>{companies.length} companies. A world of decisions.</small></h2></div>
        <label className="zoorq-search"><span className="sr-only">Search companies</span><MagnifyingGlassIcon /><input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Search company or sector" aria-label="Search companies" /></label>
      </div>
      <div className="zoorq-company-grid">{matches.map((item, index) => <article key={item.company_id} className="zoorq-company-card">
        <div className="zoorq-company-identity"><span className="zoorq-company-number" aria-hidden="true">{String(index + 1).padStart(2, '0')}</span><div><span className="zoorq-company-sector">{item.sector}</span><h3>{item.name}</h3><p>{item.size} · {[...new Set(item.locations.map(location => location.country))].join(' · ')}</p></div></div>
        <div className="zoorq-company-card-foot"><span>{item.materials.length} materials · {item.orders.length} orders</span><button type="button" aria-label={'Open ' + item.name + ' workspace'} disabled={busy} onClick={() => onOpen(item.company_id)}>Open workspace <ArrowRightIcon /></button></div>
      </article>)}</div>
      {!matches.length && <div className="zoorq-hub-empty" role="status"><strong>{companies.length ? 'No matching companies' : 'No companies available'}</strong><p>{companies.length ? 'Try a company or sector name.' : 'The company index is unavailable. Try refreshing the workspace.'}</p></div>}
    </div>
  </section>;
}

export function WorkspaceNavigation({ company, view, onView, disabled = false }: { company?: CompanyProfile; view: WorkspaceView; onView: (view: WorkspaceView) => void; disabled?: boolean }) {
  return <nav className="zoorq-nav" aria-label="Workspace destinations"><div className="zoorq-nav-brand"><button type="button" className="zoorq-brand-button" aria-label="ZOORQ home" disabled={disabled} onClick={() => onView('hub')}><strong>ZOORQ</strong><small>A product by ClosedSource</small></button></div>{company && <div className="zoorq-nav-company"><CompanyIcon className="zoorq-nav-company-icon" aria-hidden="true" /><div><strong>{company.name}</strong><small>{company.sector}</small></div></div>}<button type="button" className="zoorq-all-companies" disabled={disabled} onClick={() => onView('hub')}><ArrowLeftIcon /> All companies</button><div className="zoorq-nav-links">{workspaceViews.map(destination => { const Icon = destinationIcon[destination]; return <button type="button" key={destination} className={`zoorq-nav-link ${view === destination ? 'active' : ''}`} aria-label={viewNames[destination]} aria-current={view === destination ? 'page' : undefined} disabled={disabled || !company} onClick={() => onView(destination)}><Icon />{destination === 'news' ? 'News' : <><span className="zoorq-nav-full">{viewNames[destination]}</span><span className="zoorq-nav-short" aria-hidden="true">{mobileViewNames[destination]}</span></>}</button>; })}</div><div className="zoorq-nav-foot"><span>Sharper procurement.</span><span>A more resilient tomorrow.</span></div></nav>;
}

export function WorkspaceHeader({ company, view, onTalk, onChat, onHub, disabled = false }: { company?: CompanyProfile; view: WorkspaceView; onTalk: () => void; onChat: () => void; onHub?: () => void; disabled?: boolean }) {
  if (view === 'hub') return <header className="zoorq-landing-nav"><button type="button" className="zoorq-landing-brand" aria-label="ZOORQ home" onClick={onHub}><strong>ZOORQ</strong><span>A product by ClosedSource</span></button><a className="zoorq-company-jump" href="#zoorq-companies">Explore companies</a><ThemeControl /></header>;
  return <header className="zoorq-workspace-header">{onHub ? <button type="button" className="zoorq-header-location zoorq-header-hub" aria-label="All companies" disabled={disabled} onClick={onHub}><span>{company?.name ?? 'All companies'}</span><span aria-hidden="true">/</span><strong>{viewNames[view]}</strong></button> : <div className="zoorq-header-location"><span>{company?.name ?? 'All companies'}</span><span aria-hidden="true">/</span><strong>{viewNames[view]}</strong></div>}<button id="zoorq-talk-live" type="button" className="zoorq-header-talk" aria-label="Talk to ZOORQ" disabled={disabled || !company} onClick={onTalk}><SpeakerLoudIcon /><span className="zoorq-header-full">Talk to ZOORQ</span><span className="zoorq-header-short" aria-hidden="true">Talk</span></button><button type="button" className="zoorq-header-chat" aria-label="Type to ZOORQ" disabled={disabled || !company} onClick={onChat}><ChatBubbleIcon /><span className="zoorq-header-full">Type to ZOORQ</span><span className="zoorq-header-short" aria-hidden="true">Type</span></button><ThemeControl /></header>;
}

export function ProcurementNavigation({ stage, onStage }: { stage: ProcurementStage; onStage: (stage: ProcurementStage) => void }) {
  return <nav className="zoorq-stage-nav" aria-label="Procurement stages">{procurementStages.map((item, index) => <button type="button" key={item} className={stage === item ? 'active' : ''} aria-current={stage === item ? 'step' : undefined} onClick={() => onStage(item)}><span>0{index + 1}</span>{stageNames[item]}</button>)}</nav>;
}
const stockCover = (material: CompanyProfile['materials'][number]) => { const stock = Number(material.stock), daily = Number(material.consumption_per_day); return Number.isFinite(stock) && Number.isFinite(daily) && stock >= 0 && daily > 0 ? stock / daily : null; };
const formattedDays = (days: number) => `${Number(days.toFixed(1))} days`;
const formattedGap = (days: number) => days < 0.1 ? 'less than 0.1 day' : `approximately ${formattedDays(days)}`;
const materialLead = (company: CompanyProfile, material: CompanyProfile['materials'][number]) => {
  const leads = company.suppliers.filter(supplier => supplier.materials.includes(material.material_id)).map(supplier => Number(supplier.lead_time_days)).filter(lead => Number.isFinite(lead) && lead >= 0);
  return leads.length ? Math.min(...leads) : null;
};
const uncoveredGap = (company: CompanyProfile, material: CompanyProfile['materials'][number]) => {
  const cover = stockCover(material), lead = materialLead(company, material);
  return cover !== null && lead !== null ? Math.max(0, lead - cover) : 0;
};

export function WorkspaceOverview({ company, news, quoteCount, onView, onTalk }: { company: CompanyProfile; news: NewsView | null; quoteCount: number; onView: (view: WorkspaceView) => void; onTalk?: () => void }) {
  const urgent = [...company.materials].sort((a, b) => uncoveredGap(company, b) - uncoveredGap(company, a) || Number(b.criticality === 'high') - Number(a.criticality === 'high') || (stockCover(a) ?? Infinity) - (stockCover(b) ?? Infinity))[0];
  const days = urgent ? stockCover(urgent) : null;
  const lead = urgent ? materialLead(company, urgent) : null;
  const gap = days !== null && lead !== null && lead > days ? lead - days : null;
  const incompleteNews = !!news && (news.cache_state === 'stale' || news.cache_state === 'partial' || news.collection?.status === 'partial' || news.sources.some(source => source.state === 'stale' || source.state === 'unavailable'));
  const latest = news && !incompleteNews ? rankNewsItems(filterNewsItems(news.items, 'all', 'all', false, { age: '7d', now: Date.now(), includeUnknown: false, includeBackground: false }), 'relevant')[0] : undefined;
  return <section className="zoorq-overview" aria-label="Company overview"><div className="zoorq-overview-heading"><span className="zoorq-eyebrow">Company overview / synthetic profile</span><h2>Your procurement, in focus.</h2><p>{company.name} · {company.sector}</p></div><div className="zoorq-overview-grid">
    <article className="zoorq-supply-card"><div className="zoorq-card-head"><span className="zoorq-attention">{gap !== null ? 'Needs attention' : 'Supply position'}</span><button type="button" onClick={() => onView('inventory')}>Inventory & supply <ArrowRightIcon /></button></div><h3>{urgent ? `${urgent.name} cover ${gap !== null ? 'needs review' : 'at a glance'}` : 'Material coverage'}</h3>{urgent ? <><strong className="zoorq-cover-number" aria-label={days === null ? 'Stock cover unavailable' : `${formattedDays(days)} of stock cover`}>{days === null ? 'Unavailable' : formattedDays(days)}</strong><span className="zoorq-cover-label">{days === null ? 'Stock cover unavailable' : 'of stock cover'}</span><p className="zoorq-supply-detail">{urgent.stock} {urgent.quantity_unit} recorded stock · {urgent.criticality} criticality · synthetic company profile.</p><div className="zoorq-gap-readout"><div><span>Matching supplier profile lead</span><strong>{lead === null ? 'Profile lead unavailable' : formattedDays(lead)}</strong></div><div><span>Estimated uncovered gap</span><strong>{gap === null ? days === null || lead === null ? 'Unavailable' : 'No gap indicated' : formattedGap(gap)}</strong></div></div><p className="zoorq-gap-note">Profile lead is a synthetic supplier estimate; review accepted offers and buyer inputs before deciding.</p></> : <p>No materials are listed in this synthetic profile.</p>}<div className="zoorq-card-end"><span>Compare with reviewed quotes for a sourcing decision.</span><button type="button" className="zoorq-primary" onClick={() => onView('procurement')}>Review sourcing <ArrowRightIcon /></button></div></article>
    <article className="zoorq-voice-card"><h3>Meet your procurement operator</h3><p>Ask, analyse or prepare your next action.</p><ZoorqLens compact /><button type="button" className="zoorq-primary" disabled={!onTalk} onClick={onTalk}><SpeakerLoudIcon /> Start a live conversation</button><span>Voice is available in this workspace.</span></article>
    <article className="zoorq-signals-card"><div className="zoorq-card-head"><h3>Latest relevant signal</h3><button type="button" onClick={() => onView('news')}>View all signals <ArrowRightIcon /></button></div><div className="zoorq-signal-content"><strong>{incompleteNews ? 'Coverage needs review' : latest?.title ?? 'News coverage'}</strong><p>{incompleteNews ? `Coverage may be incomplete or dated · checked ${news?.checked_at}. Read source status and publication dates in News.` : latest ? `${latest.publisher ?? latest.provider} · Published ${latest.published_at}` : 'No company story in the past 7 days under the default News filters. Check source status in News.'}</p></div></article>
    <article className="zoorq-work-card"><h3>Continue your work</h3><button type="button" onClick={() => onView('procurement')}><span>Procurement <small>{quoteCount ? `${quoteCount} quote${quoteCount === 1 ? '' : 's'} in this workspace` : 'Review supplier quotes'}</small></span><ArrowRightIcon /></button><button type="button" onClick={() => onView('drafts')}><span>Drafts & activity <small>Review before use</small></span><ArrowRightIcon /></button></article>
  </div></section>;
}
