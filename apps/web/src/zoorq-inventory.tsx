import { useState } from 'react';
import type { CompanyProfile, RiskAlert, RiskReport } from './demo-api';
import { assessMaterialSupply, displayDays, displayGap, SupplyTimeline } from './zoorq-supply-timeline';
import { supplierDisplayName } from './demo-display';
import { operationalRiskText } from './demo-operational-copy';
import { ArrowRightIcon, MagnifyingGlassIcon } from './zoorq-icons';
import './zoorq-inventory.css';

type Material = CompanyProfile['materials'][number];
type InventoryProps = { company: CompanyProfile; report?: RiskReport | null; onSource?: (materialId: string) => void; onAsk?: (message: string) => void };

function companyAlerts(company: CompanyProfile, report?: RiskReport | null): RiskAlert[] {
  if (!report || report.company_id !== company.company_id) return [];
  const scope = report.companies.find(item => item.company_id === company.company_id);
  return scope?.alerts.filter(alert => alert.company_id === company.company_id) ?? [];
}

export function materialRiskSummary(company: CompanyProfile, report?: RiskReport | null) {
  const alerts = companyAlerts(company, report);
  const materialIds = new Set(company.materials.map(item => item.material_id));
  return { alerts: alerts.length, affectedMaterials: new Set(alerts.flatMap(alert => alert.material_ids).filter(id => materialIds.has(id))).size };
}

function materialAlerts(material: Material, alerts: RiskAlert[]) {
  return alerts.filter(alert => alert.material_ids.includes(material.material_id));
}

const quantity = (value: string, unit: string) => `${value} ${unit}`;

export function materialHasRunnableSourcing(company: CompanyProfile, materialId: string): boolean {
  return company.quote_coverage.materials.includes(materialId)
    || (company.quote_coverage.scenarios?.some(item => item.material_id === materialId) ?? false);
}

export function InventoryOrders({ company }: { company: CompanyProfile }) {
  const materials = new Map(company.materials.map(material => [material.material_id, material.name]));
  return <section className="zoorq-orders" aria-label={`Orders for ${company.name}`}>
    <div className="zoorq-orders-head"><h2>Orders</h2><p>All {company.orders.length} orders in this company profile, including orders without a listed inventory material.</p></div>
    {company.orders.length ? <table><thead><tr><th scope="col">Order</th><th scope="col">Material</th><th scope="col">Quantity</th><th scope="col">Need by</th><th scope="col">Status</th></tr></thead><tbody>{company.orders.map(order => <tr key={order.order_id}><th scope="row" data-label="Order">{order.order_id}</th><td data-label="Material">{materials.get(order.material_id) ?? <>{order.material_id}<small>Not listed in material profile</small></>}</td><td data-label="Quantity">{quantity(order.quantity, order.unit)}</td><td data-label="Need by">{order.need_by}</td><td data-label="Status">{order.status}</td></tr>)}</tbody></table> : <div className="zoorq-inventory-empty" role="status"><strong>No orders in this profile</strong><p>Orders will appear here when the selected company has them.</p></div>}
  </section>;
}

export function delayForMaterial(scenario: { companyId: string; materialId: string; days: number }, companyId: string, materialId: string): number {
  return scenario.companyId === companyId && scenario.materialId === materialId ? scenario.days : 0;
}

export function materialGapLabel(gapDays: number | null, scenarioDelayDays: number): string {
  const prefix = scenarioDelayDays > 0 ? 'Scenario: ' : 'Profile: ';
  if (gapDays === null) return `${prefix}gap unknown`;
  return gapDays > 0 ? `${prefix}${displayDays(gapDays)} gap` : `${prefix}no stock gap`;
}

function WatchSignals({ alerts, onAsk }: { alerts: RiskAlert[]; onAsk?: (message: string) => void }) {
  if (!alerts.length) return null;
  const severity = { high: 0, medium: 1, low: 2 };
  const ordered = [...alerts].sort((a, b) => severity[a.severity] - severity[b.severity]);
  return <details className="zoorq-material-watch"><summary>Watch signals · {alerts.length}</summary><ul>{ordered.map(alert => <li key={alert.alert_id}><span className="zoorq-watch-type">{alert.category.replace('_', ' ')} · {alert.severity}</span><strong>{operationalRiskText(alert, alert.title)}</strong><p>{operationalRiskText(alert, alert.impact || alert.summary || 'Impact not stated.')}</p>{alert.recovery_actions.length > 0 && <div className="zoorq-watch-actions">{alert.recovery_actions.map(action => <div key={action.action_id}><span>Suggested: {operationalRiskText(alert, action.label)}</span>{onAsk && <button type="button" onClick={() => onAsk(action.message)}>Ask about this action</button>}</div>)}</div>}</li>)}</ul><p className="zoorq-watch-note">Signals are context for review; they do not establish a stock shortage or apply an action.</p></details>;
}

export function InventoryWorkspace({ company, report, onSource, onAsk }: InventoryProps) {
  const [searchState, setSearchState] = useState({ companyId: company.company_id, value: '' });
  const [filterState, setFilterState] = useState({ companyId: company.company_id, value: 'all' as 'all' | 'risk' });
  const [scenario, setScenario] = useState({ companyId: company.company_id, materialId: '', days: 0 });
  const [viewState, setViewState] = useState({ companyId: company.company_id, value: 'materials' as 'materials' | 'orders' });
  const search = searchState.companyId === company.company_id ? searchState.value : '';
  const filter = filterState.companyId === company.company_id ? filterState.value : 'all';
  const view = viewState.companyId === company.company_id ? viewState.value : 'materials';
  const alerts = companyAlerts(company, report);
  const riskSummary = materialRiskSummary(company, report);
  const rows = company.materials.map(material => {
    const assessment = assessMaterialSupply(material, company.suppliers);
    const matchedAlerts = materialAlerts(material, alerts);
    return { material, assessment, matchedAlerts, atRisk: (assessment.gapDays ?? 0) > 0 };
  }).sort((a, b) => (b.assessment.gapDays ?? 0) - (a.assessment.gapDays ?? 0)
    || Number(b.atRisk) - Number(a.atRisk)
    || Number(b.material.criticality === 'high') - Number(a.material.criticality === 'high')
    || (a.assessment.coverDays ?? Infinity) - (b.assessment.coverDays ?? Infinity));
  const atRiskCount = rows.filter(row => row.atRisk).length;
  const unknownCount = rows.filter(row => row.assessment.gapDays === null).length;
  const primary = rows.find(row => row.atRisk);
  const primaryGap = primary?.assessment.gapDays ?? 0;
  const primarySourcing = primary ? materialHasRunnableSourcing(company, primary.material.material_id) : false;
  const shown = rows.filter(row => (filter === 'all' || row.atRisk) && `${row.material.name} ${row.material.material_id}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
  return <section className="zoorq-inventory" aria-label="Inventory and demand">
    <header className="zoorq-inventory-head"><div><span className="zoorq-eyebrow">Inventory &amp; demand</span><h1>Know which material needs attention.</h1><p>{company.name} · Stock cover against matching supplier profile lead</p></div><span className="zoorq-inventory-date">Profile as of {company.as_of}</span></header>
    <section className="zoorq-inventory-summary" aria-label="Inventory summary"><div className="zoorq-inventory-summary-main"><span className="zoorq-summary-kicker">Next stock gap to review</span><h2>{primary ? primary.material.name : 'No measured stock gap'}</h2><p>{primary ? `${displayGap(primaryGap)} of stock cover may be uncovered before the shortest matching profile lead.` : company.materials.length ? `No positive gap is indicated by the available profile inputs.${unknownCount ? ` Coverage remains unknown for ${unknownCount} material${unknownCount === 1 ? '' : 's'}.` : ''} Monitor alerts remain separate watch signals.` : 'This company profile has no materials yet.'}</p>{primary && <><button type="button" className="zoorq-inventory-next" onClick={() => primarySourcing && onSource?.(primary.material.material_id)} disabled={!onSource || !primarySourcing}>Review sourcing for {primary.material.name} <ArrowRightIcon /></button>{!primarySourcing && <span className="zoorq-source-unavailable">No runnable quote scenario for {primary.material.name}.</span>}</>}</div><dl className="zoorq-inventory-totals"><div><dt>Materials</dt><dd>{company.materials.length}</dd></div><div><dt>Stock gaps</dt><dd>{atRiskCount}</dd></div><div><dt>Unknown coverage</dt><dd>{unknownCount}</dd></div>{report?.company_id === company.company_id && <div><dt>Monitor alerts</dt><dd>{riskSummary.alerts}<small>across {riskSummary.affectedMaterials} material{riskSummary.affectedMaterials === 1 ? '' : 's'}</small></dd></div>}</dl></section>
    <div className="zoorq-inventory-tabs" role="tablist" aria-label="Inventory and demand views"><button id="zoorq-materials-tab" type="button" role="tab" aria-selected={view === 'materials'} aria-controls="zoorq-materials-panel" onClick={() => setViewState({ companyId: company.company_id, value: 'materials' })}>Materials <span>{company.materials.length}</span></button><button id="zoorq-orders-tab" type="button" role="tab" aria-selected={view === 'orders'} aria-controls="zoorq-orders-panel" onClick={() => setViewState({ companyId: company.company_id, value: 'orders' })}>Orders <span>{company.orders.length}</span></button></div>
    <section id="zoorq-materials-panel" className="zoorq-materials-panel" role="tabpanel" aria-labelledby="zoorq-materials-tab" hidden={view !== 'materials'}>
    <div className="zoorq-inventory-list-head"><div><h2>Material position</h2><p>Each line keeps its own unit and matching supplier lead.</p></div><div className="zoorq-inventory-controls"><div role="group" aria-label="Material filter"><button type="button" aria-pressed={filter === 'all'} onClick={() => setFilterState({ companyId: company.company_id, value: 'all' })}>All <span>{rows.length}</span></button><button type="button" aria-pressed={filter === 'risk'} onClick={() => setFilterState({ companyId: company.company_id, value: 'risk' })}>Stock gaps <span>{atRiskCount}</span></button></div><label className="zoorq-inventory-search"><MagnifyingGlassIcon /><span className="sr-only">Search materials</span><input type="search" value={search} onChange={event => setSearchState({ companyId: company.company_id, value: event.target.value })} placeholder="Search materials" /></label></div></div>
    <div className="zoorq-inventory-table-head" aria-hidden="true"><span>Material</span><span>Recorded stock</span><span>Daily demand</span><span>Stock cover</span><span>Profile lead</span><span>Uncovered gap</span><span /></div>
    <div className="zoorq-inventory-list">{shown.map(({ material, assessment, matchedAlerts, atRisk }) => {
      const delay = delayForMaterial(scenario, company.company_id, material.material_id);
      const scenarioAssessment = assessMaterialSupply(material, company.suppliers, delay);
      const suppliers = company.suppliers.filter(supplier => supplier.materials.includes(material.material_id));
      const orders = company.orders.filter(order => order.material_id === material.material_id);
      const routes = [...new Map(company.routes.filter(route => route.materials.includes(material.material_id)).map(route => [route.route_id, route])).values()];
      const sourcingAvailable = materialHasRunnableSourcing(company, material.material_id);
      return <details className={`zoorq-material ${atRisk ? 'zoorq-material-risk' : ''}`} key={material.material_id}><summary><span className="zoorq-material-identity"><strong>{material.name}</strong><small>{material.criticality} criticality · {atRisk ? 'Cover needs review' : assessment.gapDays === null ? 'Coverage unknown' : 'No gap indicated'}{matchedAlerts.length ? ` · ${matchedAlerts.length} watch signal${matchedAlerts.length === 1 ? '' : 's'}` : ''}</small></span><span data-label="Recorded stock">{quantity(material.stock, material.quantity_unit)}</span><span data-label="Daily demand">{quantity(material.consumption_per_day, material.quantity_unit)}/day</span><span data-label="Stock cover">{assessment.coverDays === null ? 'Unknown' : displayDays(assessment.coverDays)}</span><span data-label="Profile lead">{assessment.profileLeadDays === null ? 'Unknown' : displayDays(assessment.profileLeadDays)}</span><span className="zoorq-material-gap" data-label="Uncovered gap">{assessment.gapDays === null ? 'Unknown' : assessment.gapDays > 0 ? displayDays(assessment.gapDays) : 'No gap'}</span><span className="zoorq-material-expand" aria-hidden="true">⌄</span></summary><div className="zoorq-material-detail">
        <div className="zoorq-material-detail-main"><div className="zoorq-material-detail-heading"><div><span className="zoorq-summary-kicker">Cover → possible gap → profile lead</span><h3>{material.name} supply window</h3></div><span className={(scenarioAssessment.gapDays ?? 0) > 0 ? 'is-gap' : undefined}>{materialGapLabel(scenarioAssessment.gapDays, delay)}</span></div><SupplyTimeline assessment={scenarioAssessment} /><div className="zoorq-material-scenario"><label htmlFor={`zoorq-delay-${company.company_id}-${material.material_id}`}>What if supplier lead slips by <strong>{delay} days</strong>?</label><input id={`zoorq-delay-${company.company_id}-${material.material_id}`} type="range" min="0" max="14" step="1" value={delay} onChange={event => setScenario({ companyId: company.company_id, materialId: material.material_id, days: Number(event.target.value) })} aria-label={`Scenario delay for ${material.name}`} /><button type="button" onClick={() => setScenario({ companyId: company.company_id, materialId: material.material_id, days: 0 })} disabled={delay === 0}>Reset scenario</button><p>Local scenario only. Profile lead stays {assessment.profileLeadDays === null ? 'unknown' : displayDays(assessment.profileLeadDays)}; no quote, plan, supplier or order changes.</p></div><details className="zoorq-material-provenance"><summary>How this comparison is made</summary><p>Recorded stock ÷ daily consumption gives stock cover. The shortest nonnegative lead from suppliers listing this material is compared with that cover. These are company profile values, not an accepted quote arrival, forecast, or purchase commitment. Missing or invalid inputs leave the gap unknown.</p></details><WatchSignals alerts={matchedAlerts} onAsk={onAsk} /></div>
        <aside className="zoorq-material-context"><h4>Related operations</h4><dl><div><dt>Monthly requirement</dt><dd>{quantity(material.monthly_requirement, material.quantity_unit)}</dd></div><div><dt>Orders</dt><dd>{orders.length ? orders.map(order => <span key={order.order_id}>{order.order_id} · {quantity(order.quantity, order.unit)} · due {order.need_by} · {order.status}</span>) : 'No linked orders'}</dd></div><div><dt>Suppliers</dt><dd>{suppliers.length ? suppliers.map(supplier => <span key={supplier.supplier_id}>{supplierDisplayName(supplier.name)} · {supplier.country} · {Number.isFinite(supplier.lead_time_days) && supplier.lead_time_days >= 0 ? `${supplier.lead_time_days} day profile lead` : 'lead unknown'}</span>) : 'No matching suppliers'}</dd></div><div><dt>Routes</dt><dd>{routes.length ? routes.map(route => <span key={route.route_id}>{route.origin} → {route.destination}{route.via.length ? ` via ${route.via.join(', ')}` : ''}</span>) : 'No linked routes'}</dd></div></dl><div className="zoorq-material-actions"><button type="button" onClick={() => sourcingAvailable && onSource?.(material.material_id)} disabled={!onSource || !sourcingAvailable}>Review sourcing <ArrowRightIcon /></button>{onAsk && <button type="button" onClick={() => onAsk(`Explain the inventory position and sourcing options for ${material.name} at ${company.name}.`)}>Ask ZOORQ</button>}</div>{!sourcingAvailable && <details className="zoorq-quote-coverage"><summary>No runnable quote scenario for {material.name}</summary><p>{company.quote_coverage.note || 'This profile does not have runnable quotes for this material.'}</p></details>}</aside>
      </div></details>;
    })}</div>
    {!shown.length && <div className="zoorq-inventory-empty" role="status"><strong>{company.materials.length ? 'No materials match this view' : 'No materials in this profile'}</strong><p>{company.materials.length ? 'Try another material search or show all materials.' : 'Inventory coverage will appear when materials are available.'}</p></div>}
    </section>
    <section id="zoorq-orders-panel" role="tabpanel" aria-labelledby="zoorq-orders-tab" hidden={view !== 'orders'}><InventoryOrders company={company} /></section>
  </section>;
}
