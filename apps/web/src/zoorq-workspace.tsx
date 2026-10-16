import { useState } from 'react';
import type { CompanyProfile, NewsView, RiskReport } from './demo-api';
import { assessMaterialSupply, displayDays, displayGap, SupplyTimeline } from './zoorq-supply-timeline';
import { ZoorqOverviewSignal } from './zoorq-overview-signal';
import { ZoorqLens } from './zoorq-lens';
import { ThemeControl } from './demo-theme';
import { ZoorqEcosystem } from './zoorq-ecosystem';
import { ZoorqBrand } from './zoorq-brand';
import { destinationIcon, ArrowLeftIcon, ArrowRightIcon, ChatBubbleIcon, CompanyIcon, MagnifyingGlassIcon, SpeakerLoudIcon } from './zoorq-icons';
import './zoorq-workspace.css';
import './zoorq-landing.css';

export type WorkspaceView = 'hub' | 'overview' | 'news' | 'procurement' | 'inventory' | 'drafts';
export type ProcurementStage = 'add' | 'review' | 'compare' | 'rfq';
export type SupplyTab = 'inventory' | 'shipments';
export const workspaceViews: Exclude<WorkspaceView, 'hub'>[] = ['overview', 'news', 'procurement', 'inventory', 'drafts'];
export const procurementStages: ProcurementStage[] = ['add', 'review', 'compare', 'rfq'];
const viewNames: Record<Exclude<WorkspaceView, 'hub'>, string> = { overview: 'Overview', news: 'News', procurement: 'Procurement', inventory: 'Inventory & supply', drafts: 'Drafts & activity' };
const mobileViewNames: Record<Exclude<WorkspaceView, 'hub'>, string> = { overview: 'Home', news: 'News', procurement: 'Procure', inventory: 'Supply', drafts: 'Drafts' };
const stageNames: Record<ProcurementStage, string> = { add: 'Add quotes', review: 'Review', compare: 'Compare', rfq: 'Draft RFQ' };

export function readWorkspaceLocation(search: string): { view: WorkspaceView; stage: ProcurementStage } {
  const params = new URLSearchParams(search), rawView = params.get('view'), rawStage = params.get('stage');
  return { view: rawView && workspaceViews.includes(rawView as Exclude<WorkspaceView, 'hub'>) ? rawView as WorkspaceView : rawView === 'hub' ? 'hub' : 'overview', stage: rawStage && procurementStages.includes(rawStage as ProcurementStage) ? rawStage as ProcurementStage : 'add' };
}
export function readSupplyLocation(search: string): SupplyTab {
  return new URLSearchParams(search).get('supply') === 'shipments' ? 'shipments' : 'inventory';
}
export function workspaceSearch(search: string, view: WorkspaceView, stage: ProcurementStage, supply: SupplyTab = 'inventory') {
  const params = new URLSearchParams(search); params.delete('company'); params.delete('supply');
  if (view === 'hub') { params.set('view', 'hub'); params.delete('stage'); }
  else { params.set('view', view); if (view === 'procurement') params.set('stage', stage); else params.delete('stage'); if (view === 'inventory' && supply === 'shipments') params.set('supply', 'shipments'); }
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
      <div className="zoorq-entrance-heading">
        <span className="zoorq-entrance-kicker"><span className="zoorq-kicker-rule" /> Resilience intelligence for procurement</span>
        <h1>See disruption.<br /><span>Make your next move.</span></h1>
      </div>
      <div className="zoorq-entrance-brief">
        <p>Connect world signals to your supply exposure, then review the next procurement decision.</p>
        <div className="zoorq-entrance-actions">
          {current ? <button type="button" className="zoorq-primary" disabled={busy} onClick={() => onOpen(current.company_id)}>Continue {current.name} <ArrowRightIcon /></button> : <a className="zoorq-primary" href="#zoorq-companies">Choose a company <ArrowRightIcon /></a>}
          {current && <a className="zoorq-entrance-link" href="#zoorq-companies">Choose another company <ArrowRightIcon /></a>}
        </div>
      </div>
      <div className="zoorq-entrance-visual"><ZoorqEcosystem /><div className="zoorq-sculpture-callouts" aria-label="The resilience loop: world signal, supply exposure, reviewed response"><span className="zoorq-callout-signal"><b>01</b> World signal</span><span className="zoorq-callout-break"><b>02</b> Supply exposure</span><span className="zoorq-callout-route"><b>03</b> Reviewed response</span></div></div>
    </div>
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
  return <nav className="zoorq-nav" aria-label="Workspace destinations"><div className="zoorq-nav-brand"><button type="button" className="zoorq-brand-button" aria-label="ZOORQ home" disabled={disabled} onClick={() => onView('hub')}><ZoorqBrand compact /><small>A product by ClosedSource</small></button></div>{company && <div className="zoorq-nav-company"><CompanyIcon className="zoorq-nav-company-icon" aria-hidden="true" /><div><strong>{company.name}</strong><small>{company.sector}</small></div></div>}<button type="button" className="zoorq-all-companies" disabled={disabled} onClick={() => onView('hub')}><ArrowLeftIcon /> All companies</button><div className="zoorq-nav-links">{workspaceViews.map(destination => { const Icon = destinationIcon[destination]; return <button type="button" key={destination} className={`zoorq-nav-link ${view === destination ? 'active' : ''}`} aria-label={viewNames[destination]} aria-current={view === destination ? 'page' : undefined} disabled={disabled || !company} onClick={() => onView(destination)}><Icon />{destination === 'news' ? 'News' : <><span className="zoorq-nav-full">{viewNames[destination]}</span><span className="zoorq-nav-short" aria-hidden="true">{mobileViewNames[destination]}</span></>}</button>; })}</div><div className="zoorq-nav-foot"><span>Sharper procurement.</span><span>A more resilient tomorrow.</span></div></nav>;
}

export function WorkspaceHeader({ company, view, onTalk, onChat, onHub, disabled = false }: { company?: CompanyProfile; view: WorkspaceView; onTalk: () => void; onChat: () => void; onHub?: () => void; disabled?: boolean }) {
  if (view === 'hub') return <header className="zoorq-landing-nav"><button type="button" className="zoorq-landing-brand" aria-label="ZOORQ home" onClick={onHub}><ZoorqBrand /><span>A product by ClosedSource</span></button><a className="zoorq-company-jump" href="#zoorq-companies">Explore companies</a><ThemeControl /></header>;
  return <header className="zoorq-workspace-header">{onHub ? <button type="button" className="zoorq-header-location zoorq-header-hub" aria-label="All companies" disabled={disabled} onClick={onHub}><span>{company?.name ?? 'All companies'}</span><span aria-hidden="true">/</span><strong>{viewNames[view]}</strong></button> : <div className="zoorq-header-location"><span>{company?.name ?? 'All companies'}</span><span aria-hidden="true">/</span><strong>{viewNames[view]}</strong></div>}<button id="zoorq-talk-live" type="button" className="zoorq-header-talk" aria-label="Talk to ZOORQ" disabled={disabled || !company} onClick={onTalk}><SpeakerLoudIcon /><span className="zoorq-header-full">Talk to ZOORQ</span><span className="zoorq-header-short" aria-hidden="true">Talk</span></button><button type="button" className="zoorq-header-chat" aria-label="Type to ZOORQ" disabled={disabled || !company} onClick={onChat}><ChatBubbleIcon /><span className="zoorq-header-full">Type to ZOORQ</span><span className="zoorq-header-short" aria-hidden="true">Type</span></button><ThemeControl /></header>;
}

export function ProcurementNavigation({ stage, onStage }: { stage: ProcurementStage; onStage: (stage: ProcurementStage) => void }) {
  return <nav className="zoorq-stage-nav" aria-label="Procurement stages">{procurementStages.map((item, index) => <button type="button" key={item} className={stage === item ? 'active' : ''} aria-current={stage === item ? 'step' : undefined} onClick={() => onStage(item)}><span>0{index + 1}</span>{stageNames[item]}</button>)}</nav>;
}
export function WorkspaceOverview({ company, news, newsLoading = false, newsError = '', quoteCount, onView, onTalk, onSource, report }: { company: CompanyProfile; news: NewsView | null; newsLoading?: boolean; newsError?: string; quoteCount: number; onView: (view: WorkspaceView) => void; onTalk?: () => void; onSource?: (materialId: string) => void; report?: RiskReport | null }) {
  const urgent = [...company.materials].sort((a, b) => (assessMaterialSupply(b, company.suppliers).gapDays ?? 0) - (assessMaterialSupply(a, company.suppliers).gapDays ?? 0) || Number(b.criticality === 'high') - Number(a.criticality === 'high') || (assessMaterialSupply(a, company.suppliers).coverDays ?? Infinity) - (assessMaterialSupply(b, company.suppliers).coverDays ?? Infinity))[0];
  const assessment = urgent ? assessMaterialSupply(urgent, company.suppliers) : null;
  const days = assessment?.coverDays ?? null;
  const lead = assessment?.profileLeadDays ?? null;
  const gap = assessment?.gapDays && assessment.gapDays > 0 ? assessment.gapDays : null;
  const companyReport = report?.company_id === company.company_id ? report.companies.find(item => item.company_id === company.company_id) : null;
  const monitorAlerts = companyReport?.alerts.filter(alert => alert.company_id === company.company_id) ?? [];
  return <section className="zoorq-overview" aria-label="Company overview"><div className="zoorq-overview-heading"><span className="zoorq-eyebrow">Company overview</span><h2>Your procurement, in focus.</h2><p>{company.name} · {company.sector}</p></div><div className="zoorq-overview-grid">
    <article className="zoorq-supply-card"><div className="zoorq-card-head"><span className="zoorq-attention">{gap !== null ? 'Needs attention' : 'Supply position'}</span><button type="button" onClick={() => onView('inventory')}>Inventory & supply <ArrowRightIcon /></button></div><h3>{urgent ? `${urgent.name} cover ${gap !== null ? 'needs review' : 'at a glance'}` : 'Material coverage'}</h3>{urgent ? <><strong className="zoorq-cover-number" aria-label={days === null ? 'Stock cover unavailable' : `${displayDays(days)} of stock cover`}>{days === null ? 'Unavailable' : displayDays(days)}</strong><span className="zoorq-cover-label">{days === null ? 'Stock cover unavailable' : 'of stock cover'}</span><p className="zoorq-supply-detail">{urgent.stock} {urgent.quantity_unit} recorded stock · {urgent.criticality} criticality.</p>{assessment && <SupplyTimeline assessment={assessment} compact />}<div className="zoorq-gap-readout"><div><span>Matching supplier profile lead</span><strong>{lead === null ? 'Profile lead unavailable' : displayDays(lead)}</strong></div><div><span>Estimated uncovered gap</span><strong>{gap === null ? days === null || lead === null ? 'Unavailable' : 'No gap indicated' : displayGap(gap)}</strong></div></div><p className="zoorq-gap-note">Profile lead is an estimate; review accepted offers and buyer inputs before deciding.{companyReport && ` ${monitorAlerts.length} current-company monitor alert${monitorAlerts.length === 1 ? '' : 's'}.`}</p></> : <p>No materials are listed in this profile.</p>}<div className="zoorq-card-end"><span>Compare with reviewed quotes for a sourcing decision.</span><button type="button" className="zoorq-primary" onClick={() => urgent && onSource ? onSource(urgent.material_id) : onView('procurement')}>Review sourcing <ArrowRightIcon /></button></div></article>
    <article className="zoorq-voice-card"><h3>Meet your procurement operator</h3><p>Ask, analyse or prepare your next action.</p><ZoorqLens compact /><button type="button" className="zoorq-primary" disabled={!onTalk} onClick={onTalk}><SpeakerLoudIcon /> Start a live conversation</button><span>Voice is available in this workspace.</span></article>
    <ZoorqOverviewSignal news={news} loading={newsLoading} error={newsError} onOpenNews={() => onView('news')} />
    <article className="zoorq-work-card"><h3>Continue your work</h3><button type="button" onClick={() => onView('procurement')}><span>Procurement <small>{quoteCount ? `${quoteCount} quote${quoteCount === 1 ? '' : 's'} in this workspace` : 'Review supplier quotes'}</small></span><ArrowRightIcon /></button><button type="button" onClick={() => onView('drafts')}><span>Drafts & activity <small>Review before use</small></span><ArrowRightIcon /></button></article>
  </div></section>;
}
