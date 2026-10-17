import { useState } from 'react';
import type { DispatchShipment, DispatchView } from './demo-api';
import { supplierDisplayName } from './demo-display';
import './demo-dispatch.css';

export type DemoDispatchPanelProps = {
  view: DispatchView | null;
  busy: boolean;
  error?: string;
  onRefresh: () => void;
  onReviewDelay: (shipmentId: string, days: number) => void;
  disabled?: boolean;
};

export function dispatchReviewAction(view: DispatchView | null, shipmentId: string, daysText: string, disabled = false): { shipmentId: string; days: number } | null {
  if (!view || disabled || !/^\d+$/.test(daysText)) return null;
  const days = Number(daysText);
  if (!Number.isSafeInteger(days) || days > 3650) return null;
  const matches = view.shipments.filter(row => row.shipment_id === shipmentId);
  if (matches.length !== 1) return null;
  const row = matches[0];
  if (!row.recovery_available || row.status !== 'planned_inbound_scenario' || row.scenario_plan_id ||
      row.company_id !== view.company_id || !view.plan_id || row.plan_id !== view.plan_id ||
      !row.offer_id || row.offer_version == null || row.offer_version < 1) return null;
  return { shipmentId, days };
}

const label = (value: string) => value.replaceAll('_', ' ');
const arrival = (value: string | null | undefined) => value || 'Unknown';
const sourceLabel = (value: string) => value === 'synthetic_plan_demand_scenario' ? 'Accepted plan scenario' :
  value === 'fictional_company_profile_expected_order' ? 'Expected company order' :
  value === 'synthetic_plan_assumption' ? 'Accepted plan assumption' : label(value);

export function orderedDispatchRows(view: DispatchView | null): DispatchShipment[] {
  if (!view) return [];
  const rows = view.shipments.filter(row => row.company_id === view.company_id);
  const current = rows.filter(row => row.source === 'synthetic_plan_demand_scenario' && row.plan_id === view.plan_id);
  return [...current, ...rows.filter(row => !current.includes(row))];
}

export function dispatchSelection(key: string, shipmentId: string) {
  return { selected: { key, shipmentId }, delay: { key, shipmentId, value: '0' } };
}

function unavailableReason(row: DispatchShipment): string {
  switch (row.recovery_reason) {
    case 'expected_order_only': return 'This expected order has no accepted quote or current plan. Review sourcing before testing a delay.';
    case 'material_or_unit_unavailable': return 'Material or unit ownership is unavailable. Review the company order data before using a comparison.';
    case 'recovery_already_reviewed': return 'Already reviewed. The baseline and scenario are shown below; start a new current comparison to test another delay.';
    case 'scenario_infeasible': return 'The reviewed scenario has no feasible selected supplier. Review the comparison before taking an RFQ step.';
    case 'stale_recovery_context': return 'The recovery context changed. Run a current plan comparison before reviewing another delay.';
    default: return row.recovery_reason ? `${label(row.recovery_reason)}. Review the current plan before testing a delay.` : 'A current accepted plan-linked quote is required before testing a delay.';
  }
}

function ShipmentDetail({ row, view, days, onDays, onReviewDelay, disabled }: { row: DispatchShipment; view: DispatchView; days: string; onDays: (days: string) => void; onReviewDelay: (shipmentId: string, days: number) => void; disabled: boolean }) {
  const action = dispatchReviewAction(view, row.shipment_id, days, disabled);
  const reviewed = !!row.scenario_plan_id;
  return <article className="demo-dispatch-detail" aria-label={`Details for ${row.material_name}`}>
    <div className="demo-dispatch-detail-head"><div><span className="demo-dispatch-kicker">{sourceLabel(row.source)}</span><h2>{row.material_name}</h2><p>{row.order_id ? `Order ${row.order_id}` : row.demand_reference ? `Demand reference ${row.demand_reference}` : 'Unlinked demand'}</p></div><strong>{row.quantity} {row.quantity_unit}<small>{row.order_demand_quantity && row.order_demand_quantity !== row.quantity ? `Inbound purchase for ${row.order_demand_quantity} ${row.quantity_unit} order demand` : 'Recorded quantity'}</small></strong></div>
    <dl className="demo-dispatch-facts"><div><dt>Needed by</dt><dd>{row.need_by || 'Unknown'}</dd></div><div><dt>Expected arrival</dt><dd>{arrival(row.expected_arrival)}<small>{row.arrival_basis ? label(row.arrival_basis) : 'No accepted quote arrival is recorded'}</small></dd></div><div><dt>Status</dt><dd>{label(row.status)}</dd></div></dl>
    {reviewed ? <div className="demo-dispatch-comparison" aria-label="Reviewed recovery comparison"><section><span>Original baseline</span><strong>{arrival(row.original_expected_arrival)}</strong><p>{row.baseline_supplier ? supplierDisplayName(row.baseline_supplier) : 'Supplier unavailable'} · {row.baseline_plan_id ?? 'Plan unavailable'}{row.baseline_offer_version != null ? ` · quote v${row.baseline_offer_version}` : ''}</p></section><section><span>Reviewed scenario</span><strong>{arrival(row.scenario_expected_arrival)}</strong><p>{row.scenario_supplier ? supplierDisplayName(row.scenario_supplier) : 'No selected supplier'} · {row.scenario_plan_id}{row.scenario_status ? ` · ${label(row.scenario_status)}` : ''}</p></section></div> : null}
    <details className="demo-dispatch-evidence"><summary>Source and timeline</summary><p>Planning dates are not live carrier tracking or a confirmed shipment delay.</p><dl><div><dt>Record ID</dt><dd><code>{row.shipment_id}</code></dd></div><div><dt>Record source</dt><dd>{sourceLabel(row.source)}</dd></div><div><dt>Route</dt><dd>{row.route_reason ? label(row.route_reason) : row.route ? 'Declared route in company data' : 'No declared route'}</dd></div></dl>{row.timeline.length ? <ol>{row.timeline.map((event, index) => <li key={`${event.date}:${event.label}:${index}`}><time>{event.date}</time><span>{event.phase ? `${label(event.phase)} · ` : ''}{event.label}</span><span>{sourceLabel(event.source)}</span></li>)}</ol> : <p>No shipment timeline is available from the company profile.</p>}</details>
    <div className="demo-dispatch-review"><div><h3>Test a possible delay</h3><p>Reviewing opens the existing cost comparison. It does not change this order or confirm that a delay occurred.</p></div>{row.recovery_available ? <div className="demo-dispatch-review-controls"><label htmlFor="demo-dispatch-days">Hypothetical delay, whole days<input id="demo-dispatch-days" type="number" inputMode="numeric" min="0" max="3650" step="1" value={days} onChange={event => onDays(event.target.value)} disabled={disabled} /></label><button type="button" className="demo-button" disabled={!action} onClick={() => { if (action) onReviewDelay(action.shipmentId, action.days); }}>Review recovery</button>{!action && !disabled && <small role="status">Enter a whole number from 0 to 3650.</small>}</div> : <p className="demo-dispatch-unavailable">{unavailableReason(row)}</p>}</div>
  </article>;
}

export function DemoDispatchPanel({ view, busy, error, onRefresh, onReviewDelay, disabled = false }: DemoDispatchPanelProps) {
  const [selectedState, setSelectedState] = useState<{ key: string; shipmentId: string } | null>(null);
  const [delayState, setDelayState] = useState<{ key: string; shipmentId: string; value: string } | null>(null);
  const key = view ? `${view.company_id}:${view.context_version}:${view.plan_id ?? ''}` : '';
  const rows = orderedDispatchRows(view);
  const preferred = rows.find(row => row.recovery_available) ?? rows[0];
  const selectedId = selectedState?.key === key && rows.some(row => row.shipment_id === selectedState.shipmentId) ? selectedState.shipmentId : preferred?.shipment_id;
  const selected = rows.find(row => row.shipment_id === selectedId);
  const days = delayState?.key === key && delayState.shipmentId === selectedId ? delayState.value : '0';
  function chooseShipment(shipmentId: string) {
    const next = dispatchSelection(key, shipmentId);
    setSelectedState(next.selected);
    setDelayState(next.delay);
  }
  return <section className="demo-dispatch" aria-label="Shipment scenarios">
    <header className="demo-dispatch-head"><div><span className="demo-dispatch-kicker">Supply / planning</span><h1>Planned arrivals and expected orders.</h1><p>Inspect company-linked demand and test an explicit delay on a current accepted plan.</p></div><button type="button" className="demo-dispatch-refresh" onClick={onRefresh} disabled={busy}>{busy ? 'Refreshing…' : 'Refresh shipments'}</button></header>
    {view?.as_of && <p className="demo-dispatch-asof">Company profile as of {view.as_of}</p>}
    {error && <p className="demo-dispatch-error" role="alert">{error}</p>}
    {busy && !view && <div className="demo-dispatch-loading" role="status"><span>Loading shipment scenarios…</span><span aria-hidden="true" /><span aria-hidden="true" /></div>}
    {!busy && !error && !rows.length && <div className="demo-dispatch-empty" role="status"><h2>No shipment scenarios</h2><p>There is no company-linked order or current plan-demand row to inspect. Review company orders or run a current quote comparison.</p></div>}
    {rows.length > 0 && <div className="demo-dispatch-layout"><label className="demo-dispatch-chooser">Shipment<select id="demo-dispatch-chooser" value={selectedId} onChange={event => chooseShipment(event.target.value)}>{rows.map(row => <option key={row.shipment_id} value={row.shipment_id}>{row.material_name} · {sourceLabel(row.source)}{row.order_id ? ` · ${row.order_id}` : ''}</option>)}</select></label><div className="demo-dispatch-list" role="group" aria-label="Select a shipment scenario"><div className="demo-dispatch-list-title"><strong>{rows.length} scenario{rows.length === 1 ? '' : 's'}</strong><span>Expected arrivals are planning dates</span></div>{rows.map(row => <button key={row.shipment_id} type="button" aria-current={row.shipment_id === selectedId ? 'true' : undefined} onClick={() => chooseShipment(row.shipment_id)}><span className="demo-dispatch-row-top"><strong>{row.material_name}</strong><span className="demo-dispatch-row-label"><small>{sourceLabel(row.source)}</small>{row.shipment_id === selectedId && <em>Selected</em>}</span></span><span>{row.order_id ? `Order ${row.order_id}` : row.demand_reference ?? 'Unlinked demand'}</span><span className="demo-dispatch-row-bottom"><span>{row.quantity} {row.quantity_unit} · needed {row.need_by || 'unknown'}</span><strong>{row.expected_arrival ? `Expected ${row.expected_arrival}` : 'Arrival unknown'}</strong></span></button>)}</div>{selected && view && <ShipmentDetail row={selected} view={view} days={days} onDays={value => setDelayState({ key, shipmentId: selected.shipment_id, value })} onReviewDelay={onReviewDelay} disabled={disabled || busy} />}</div>}
    {view && view.limitations.length > 0 && <details className="demo-dispatch-limitations"><summary>Data limits</summary><ul>{view.limitations.map((item, index) => <li key={`${index}:${item}`}>{item}</li>)}</ul></details>}
  </section>;
}
