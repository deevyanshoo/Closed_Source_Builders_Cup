import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { Link } from 'react-router-dom';
import type { ProductionScheduleScreen, RecoveryComparisonRequest, ReceiptRecoveryComparison, SessionResponse } from '../../../contracts/generated/types';
import { fetchSession } from './state';
import { buildComparisonRequest, createExample, requestComparison, RecoveryError } from './recovery';
import type { ComparisonResult, RecoveryExample } from './recovery';

type ViewState = { kind: 'idle' | 'loading' } | { kind: 'error'; message: string; source: 'validation' | 'service' } | { kind: 'result'; value: ComparisonResult; noop: boolean };
const utcLabel = (value: string) => value.replace('T', ' ').replace('Z', ' UTC').replace('+00:00', ' UTC');
const humanReason = (value: string) => value.replaceAll('_', ' ');
const unsupportedCopy: Record<string, string> = {
  invalid_quantity: 'A quantity is invalid.', invalid_timestamp: 'A date or time is invalid.',
  incomplete_snapshot: 'The production snapshot is incomplete.', incomplete_assignment: 'A production assignment is missing.',
  unsupported_constraint: 'This production constraint is outside the model.', conflicting_snapshot: 'The snapshot contains conflicting information.',
  invalid_scope: 'The comparison scope is invalid.', incomplete_inventory: 'Inventory details are incomplete.',
  invalid_snapshot: 'The production snapshot is invalid.', incomplete_bom: 'A product component list is incomplete.',
  incompatible_unit: 'Component units do not match.', incomplete_resource: 'A resource is incomplete.',
  incomplete_routing: 'A production routing is incomplete.', incomplete_period: 'A production period is incomplete.',
  pre_as_of: 'An arrival or production period precedes the snapshot date.', invalid_period: 'A production period is invalid.',
  overlapping_periods: 'Production periods overlap.', incomplete_maintenance: 'Maintenance details are incomplete.',
  incomplete_receipt: 'A receipt is incomplete.', incomplete_order: 'An order is incomplete.',
  resource_mismatch: 'An order and period use different resources.', workspace_mismatch: 'The workspaces do not match.',
  stale_plan_version: 'The supplied plan versions do not match.', snapshot_mismatch: 'The supplied snapshot IDs do not match.',
  invalid_scenario: 'The receipt scenario is invalid.', invalid_recovery: 'The supplied recovery is invalid.',
  invalid_replacement: 'A receipt replacement is invalid.', duplicate_replacement: 'A receipt was replaced more than once.',
  unknown_receipt: 'A replacement names an unknown receipt.', stale_receipt: 'The original receipt arrival does not match the snapshot.',
};

function Schedule({ title, value }: { title: string; value: ProductionScheduleScreen }) {
  return <section className="recovery-schedule" aria-label={title}>
    <h3>{title}</h3>
    <div className="recovery-checks" aria-label={`${title} modeled checks`}>
      <span>Material: {value.orders.some(order => !order.material_pass) ? 'shortfall' : 'covered in model'}</span>
      <span>Capacity: {value.capacity.some(row => !row.capacity_pass) ? 'excess' : 'within modeled capacity'}</span>
      <span>Due dates: {value.orders.some(order => !order.modeled_due_pass) ? 'late in model' : 'within modeled dates'}</span>
    </div>
    <p className="recovery-caption">Material balances through {utcLabel(value.material_balance_end_at)}. Checks are separate; none proves full production feasibility.</p>
    <div className="table-scroll"><table><caption>Every order in {title}</caption><thead><tr><th>Order / product</th><th>Output</th><th>Resource / period</th><th>Material needed</th><th>Modeled completion / due</th><th>Material and due</th></tr></thead><tbody>
      {value.orders.map(order => <tr key={order.order_id}><th scope="row">{order.order_id}<small>{order.product_id}</small></th><td>{order.output_quantity} {order.output_unit}</td><td>{order.resource_id} / {order.period_id}</td><td>{utcLabel(order.material_required_at)}</td><td>{utcLabel(order.modeled_completion_at)}<small>Due {utcLabel(order.due_at)}</small></td><td>{order.material_pass ? 'Covered in model' : 'Shortfall'}<small>{order.modeled_due_pass ? 'On modeled date' : `Late ${order.late_by_seconds} seconds`}</small></td></tr>)}
    </tbody></table></div>
    <div className="table-scroll"><table><caption>Component demand and stock in {title}</caption><thead><tr><th>Order</th><th>Component</th><th>Required</th><th>Used</th><th>Shortfall</th><th>Remaining</th></tr></thead><tbody>
      {value.orders.flatMap(order => order.demands.map(demand => <tr key={`${order.order_id}:${demand.component_id}`}><th scope="row">{order.order_id}</th><td>{demand.component_id}</td><td>{demand.quantity} {demand.unit}</td><td>{demand.used} {demand.unit}</td><td>{demand.shortfall} {demand.unit}</td><td>{demand.remaining_stock} {demand.unit}</td></tr>))}
    </tbody></table></div>
    <div className="table-scroll"><table><caption>Capacity by resource and period in {title}</caption><thead><tr><th>Resource / period</th><th>Nominal</th><th>Blocked</th><th>Available</th><th>Workload</th><th>Excess</th><th>Remaining</th></tr></thead><tbody>
      {value.capacity.map(row => <tr key={`${row.resource_id}:${row.period_id}`}><th scope="row">{row.resource_id} / {row.period_id}</th><td>{row.nominal_seconds} s</td><td>{row.blocked_seconds} s</td><td>{row.available_seconds} s</td><td>{row.workload_seconds} s</td><td>{row.excess_seconds} s</td><td>{row.remaining_seconds} s</td></tr>)}
    </tbody></table></div>
    <div className="table-scroll"><table><caption>Component balances in {title}</caption><thead><tr><th>Component</th><th>Opening</th><th>Ending</th></tr></thead><tbody>
      {value.material_balances.map(row => <tr key={row.component_id}><th scope="row">{row.component_id}</th><td>{row.starting_stock} {row.unit}</td><td>{row.ending_stock} {row.unit}</td></tr>)}
    </tbody></table></div>
    <p className="recovery-caption">Modeled checks: {value.failed_checks.length ? value.failed_checks.join(', ') : 'No failed modeled checks'}. This is not a full feasibility decision.</p>
  </section>;
}

function Comparison({ value, noop }: { value: ReceiptRecoveryComparison; noop: boolean }) {
  return <div data-result="comparison" className="recovery-result">
    <div className="recovery-result-head"><p className="eyebrow">SYNTHETIC COMPARISON · CALLER SUPPLIED RECOVERY</p><h2>{noop ? 'No changes in this comparison' : 'Three schedules, one set of inputs'}</h2><p>Snapshot {value.snapshot_id} · version {value.plan_version} · as of {utcLabel(value.as_of)}. These IDs check caller consistency; they do not prove this is the current saved plan.</p></div>
    <div className="recovery-three">
      <Schedule title="Original baseline" value={value.original_baseline} />
      <Schedule title="Disrupted baseline" value={value.disrupted_baseline} />
      <Schedule title="Supplied recovery" value={value.recovery} />
    </div>
    <section className="recovery-limits" aria-label="Scope and limits">
      <h3>What changed</h3>
      {value.receipt_changes.length ? <ul>{value.receipt_changes.map(change => <li key={change.receipt_id}>Receipt {change.receipt_id}: {utcLabel(change.before_arrival_at)} → {utcLabel(change.after_arrival_at)}</li>)}</ul> : <p>Receipt arrivals were unchanged.</p>}
      <p>Potentially exposed orders: {value.potentially_exposed_order_ids.length ? value.potentially_exposed_order_ids.join(', ') : 'none'}. Changed by disruption: {value.disruption_changed_order_ids.length ? value.disruption_changed_order_ids.join(', ') : 'none'}. Changed by supplied recovery: {value.recovery_changed_order_ids.length ? value.recovery_changed_order_ids.join(', ') : 'none'}.</p>
      <h3>Modeled checks</h3><ul>{value.modeled_checks.map(check => <li key={check}>{humanReason(check)}</li>)}</ul>
      <h3>Not modeled</h3><ul>{value.unmodeled_constraints.map((item, index) => <li key={`${index}:${item}`}>{item}</li>)}</ul>
      <p>This synthetic result does not apply a plan, send a supplier message, or promise a customer date.</p>
    </section>
  </div>;
}

export function RecoveryPage() {
  const [session, setSession] = useState<SessionResponse | null>(null);
  const [sessionError, setSessionError] = useState('');
  const [checkingSession, setCheckingSession] = useState(true);
  const [example, setExample] = useState<RecoveryExample>('recovery');
  const [base, setBase] = useState<RecoveryComparisonRequest | null>(null);
  const [arrivals, setArrivals] = useState<Record<string, string>>({});
  const [assignments, setAssignments] = useState<Record<string, string>>({});
  const [view, setView] = useState<ViewState>({ kind: 'idle' });
  const sessionSerial = useRef(0);
  const requestSerial = useRef(0);
  const request = useRef<AbortController | null>(null);
  const sessionRequest = useRef<AbortController | null>(null);

  function invalidate() {
    requestSerial.current += 1;
    request.current?.abort();
    request.current = null;
    setView({ kind: 'idle' });
  }

  async function loadSession() {
    const serial = ++sessionSerial.current;
    sessionRequest.current?.abort();
    const controller = new AbortController();
    sessionRequest.current = controller;
    invalidate();
    setCheckingSession(true);
    setSessionError('');
    setSession(null);
    setBase(null);
    try {
      const identity = await fetchSession(controller.signal);
      if (serial !== sessionSerial.current || controller.signal.aborted) return;
      const source = createExample(example, identity.workspace_id);
      setSession(identity);
      setBase(source);
      setArrivals(Object.fromEntries(source.snapshot.receipts.map(item => [item.receipt_id, source.scenario.arrival_replacements.find(replacement => replacement.receipt_id === item.receipt_id)?.arrival_at ?? item.arrival_at])));
      setAssignments(Object.fromEntries(source.recovery.assignments.map(item => [item.order_id, item.period_id])));
    } catch (error) {
      if (serial !== sessionSerial.current || controller.signal.aborted) return;
      setSessionError(error instanceof Error ? error.message : 'Session unavailable.');
    } finally {
      if (serial === sessionSerial.current && !controller.signal.aborted) setCheckingSession(false);
    }
  }

  useEffect(() => {
    loadSession();
    return () => { sessionSerial.current += 1; requestSerial.current += 1; sessionRequest.current?.abort(); request.current?.abort(); };
  }, []);

  function chooseExample(next: RecoveryExample) {
    invalidate();
    setExample(next);
    if (!session) return;
    const source = createExample(next, session.workspace_id);
    setBase(source);
    setArrivals(Object.fromEntries(source.snapshot.receipts.map(item => [item.receipt_id, source.scenario.arrival_replacements.find(replacement => replacement.receipt_id === item.receipt_id)?.arrival_at ?? item.arrival_at])));
    setAssignments(Object.fromEntries(source.recovery.assignments.map(item => [item.order_id, item.period_id])));
  }

  async function compare(event: FormEvent) {
    event.preventDefault();
    if (!session || !base) return;
    invalidate();
    let payload: RecoveryComparisonRequest;
    try { payload = buildComparisonRequest(base, arrivals, assignments); }
    catch (error) { setView({ kind: 'error', source: 'validation', message: error instanceof Error ? error.message : 'Review the inputs.' }); return; }
    const serial = ++requestSerial.current;
    const controller = new AbortController();
    request.current = controller;
    setView({ kind: 'loading' });
    try {
      const result = await requestComparison(payload, controller.signal);
      if (serial !== requestSerial.current || controller.signal.aborted) return;
      const noop = result.kind === 'compared' && result.response.comparison.receipt_changes.length === 0 && result.response.comparison.disruption_changed_order_ids.length === 0 && result.response.comparison.recovery_changed_order_ids.length === 0;
      setView({ kind: 'result', value: result, noop });
    } catch (error) {
      if (serial !== requestSerial.current || controller.signal.aborted) return;
      if (error instanceof RecoveryError && error.kind === 'session') { void loadSession(); return; }
      setView({ kind: 'error', source: error instanceof RecoveryError && error.kind === 'validation' ? 'validation' : 'service', message: error instanceof Error ? error.message : 'Comparison unavailable.' });
    } finally {
      if (serial === requestSerial.current) request.current = null;
    }
  }

  return <div className="shell recovery-shell">
    <header className="masthead"><div className="brand">CLOSED <span>SOURCE</span><sup> / RECOVERY</sup></div><Link className="recovery-back" to="/planning">Planning workspace</Link></header>
    <main>
      <div className="recovery-intro"><p className="eyebrow">SYNTHETIC RECEIPT RECOVERY</p><h1>Compare the change.</h1><p>Move a supplied receipt arrival, then assign each order to a production period. Compare the original, disrupted, and supplied recovery schedules. This view makes no operational change.</p></div>
      <section className="recovery-session" aria-label="Session">
        {checkingSession && <p role="status">Checking session…</p>}
        {!checkingSession && sessionError && <div className="notice notice-error" role="alert"><strong>Session unavailable</strong><p>{sessionError}</p><button type="button" className="text-button" onClick={loadSession}>Retry session</button></div>}
        {session && <p><strong>{session.identity_mode === 'private_machine' ? 'Private machine service caller' : 'Local development identity'}</strong> · workspace {session.workspace_id} · {session.role}. Interactive sign-in is unavailable.</p>}
      </section>
      {session && base && <form className="recovery-form" aria-label="Recovery comparison inputs" onSubmit={compare}>
        <div className="recovery-form-head"><div><h2>Example and inputs</h2><p>Source: synthetic example. Receipt arrivals and recovery periods below are supplied for this comparison. Snapshot and version are caller consistency checks, not a current saved plan.</p></div><label className="field"><span className="field-label">Synthetic example</span><select value={example} onChange={event => chooseExample(event.target.value as RecoveryExample)}><option value="recovery">Stock delay</option><option value="recovery_precision">Fractional quantities</option></select></label></div>
        <div className="recovery-input-grid">
          <section><h3>Starting material and product demand</h3><p>Snapshot as of {utcLabel(base.snapshot.as_of)}</p><ul>{base.snapshot.inventory.map(item => <li key={item.component_id}>{item.component_id}: {item.starting_stock} {item.unit} at {utcLabel(item.stock_as_of)}</li>)}</ul><ul>{base.snapshot.products.map(product => <li key={product.product_id}>{product.product_id}: {product.bom.map(part => `${part.quantity_per_output_unit} ${part.unit} ${part.component_id} per ${product.output_unit}`).join('; ')}</li>)}</ul></section>
          <section><h3>Receipts · UTC arrival</h3>{base.snapshot.receipts.map(receipt => <label className="field" key={receipt.receipt_id}><span className="field-label">{receipt.receipt_id} · {receipt.quantity} {receipt.unit} {receipt.component_id}</span><span className="field-hint">Original: {utcLabel(receipt.arrival_at)}</span><input name={receipt.receipt_id} type="text" value={arrivals[receipt.receipt_id] ?? receipt.arrival_at} onChange={event => { invalidate(); setArrivals(current => ({ ...current, [receipt.receipt_id]: event.target.value })); }} aria-describedby={`arrival-hint-${receipt.receipt_id}`} /><small id={`arrival-hint-${receipt.receipt_id}`} className="field-hint">UTC format: YYYY-MM-DDTHH:mm:ssZ</small></label>)}</section>
          <section><h3>Resources, routing, periods and maintenance</h3><ul>{base.snapshot.resources.map(resource => <li key={resource.resource_id}>{resource.resource_id}: {resource.capacity_model.replaceAll('_', ' ')}</li>)}</ul><ul>{base.snapshot.routings.map(route => <li key={`${route.product_id}:${route.resource_id}`}>{route.product_id} on {route.resource_id}: {route.run_seconds_per_output_unit} seconds per {route.output_unit}</li>)}</ul><ul>{base.snapshot.periods.map(period => <li key={period.period_id}>{period.period_id} · {period.resource_id}: {utcLabel(period.start)} to {utcLabel(period.end)}</li>)}</ul><ul>{base.snapshot.maintenance.map(item => <li key={item.maintenance_id}>Maintenance {item.maintenance_id} · {item.resource_id}: {utcLabel(item.start)} to {utcLabel(item.end)}</li>)}</ul></section>
          <section><h3>Orders · supplied recovery period</h3>{base.snapshot.orders.map(order => <label className="field" key={order.order_id}><span className="field-label">{order.order_id} · {order.output_quantity} {order.output_unit} {order.product_id}</span><span className="field-hint">Due {utcLabel(order.due_at)} · original {base.snapshot.baseline_assignments.find(item => item.order_id === order.order_id)?.period_id}</span><select value={assignments[order.order_id] ?? ''} onChange={event => { invalidate(); setAssignments(current => ({ ...current, [order.order_id]: event.target.value })); }}><option value="">Choose a period</option>{base.snapshot.periods.filter(period => period.resource_id === order.resource_id).map(period => <option key={period.period_id} value={period.period_id}>{period.period_id} · {utcLabel(period.start)}</option>)}</select></label>)}</section>
        </div>
        <div className="recovery-controls"><button className="primary-button" type="submit" disabled={view.kind === 'loading'}>Compare schedules</button>{view.kind === 'loading' && <button className="secondary-button" type="button" onClick={invalidate}>Cancel comparison</button>}</div>
      </form>}
      <div className="recovery-feedback" aria-live="polite">
        {view.kind === 'idle' && session && <p role="status">Choose receipt arrivals and periods, then compare. No plan has been changed.</p>}
        {view.kind === 'loading' && <p role="status">Comparing three schedules…</p>}
        {view.kind === 'error' && <div className="notice notice-error" role="alert"><strong>{view.source === 'validation' ? 'Review comparison inputs' : 'Comparison unavailable'}</strong><p>{view.message}</p><p>{view.source === 'validation' ? 'Correct the input and compare again.' : 'Retry when the service is available.'}</p></div>}
        {view.kind === 'result' && view.value.kind === 'unsupported' && <div className="notice notice-error" role="alert"><strong>Input unsupported</strong><p>{unsupportedCopy[view.value.response.reason] ?? 'These inputs cannot be compared.'}</p></div>}
        {view.kind === 'result' && view.value.kind === 'compared' && <Comparison value={view.value.response.comparison} noop={view.noop} />}
      </div>
    </main>
    <footer>Closed Source / synthetic recovery comparison <span>No plan is applied or supplier contacted.</span></footer>
  </div>;
}
