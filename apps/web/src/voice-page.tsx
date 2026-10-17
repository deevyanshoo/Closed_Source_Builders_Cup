import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { Comparison } from './recovery-page';
import { VoiceController } from './voice';
import type { VoiceExample, VoiceView } from './voice';
import './voice.css';

const labels: Record<VoiceExample, string> = {
  recovery: 'Receipt arrives late', recovery_noop: 'No changes',
  recovery_precision: 'Fractional quantities', recovery_unsupported: 'Unsupported inputs',
};
const date = (value: string) => value.replace('T', ' ').replace('Z', ' UTC').replace('+00:00', ' UTC');
const text = (value: string) => value.replaceAll('_', ' ');
const busy = new Set(['checking', 'closing', 'creating', 'created', 'starting', 'pending']);

export function VoiceProposalPanel({ view, onConfirm }: { view: VoiceView; onConfirm: () => void }) {
  if (view.kind !== 'proposed' || !view.proposal) {
    return view.message === 'Text proposal unavailable. Use the typed comparison form.' ? <p role="alert">{view.message}</p> : null;
  }
  const changes = view.proposal.call.arguments.arrival_replacements;
  const assignments = view.proposal.call.arguments.assignments;
  return <section className="notice" aria-label="Unverified text interpretation">
    <h2>Unverified interpretation</h2>
    <p>This is a proposed reading of your text for a saved context. Review every input below. No comparison has run.</p>
    <h3>Changed receipt arrivals</h3>
    {changes.length ? <ul>{changes.map(change => <li key={change.receipt_id}>{change.receipt_id}: {change.expected_arrival_at} → {change.arrival_at} UTC</li>)}</ul> : <p>No receipt arrival changes proposed.</p>}
    <h3>Complete order assignments</h3>
    <ul>{assignments.map(item => <li key={item.order_id}>{item.order_id} → {item.period_id}</li>)}</ul>
    <button type="button" className="primary-button" onClick={onConfirm}>Compare these exact changes</button>
  </section>;
}

export function VoicePage() {
  const controllerRef = useRef<VoiceController | null>(null);
  const [view, setView] = useState<VoiceView>({ kind: 'checking', example: 'recovery', arrivals: {}, assignments: {}, source: 'inline', contexts: [], hasMore: false, snapshotText: '' });
  const [utterance, setUtterance] = useState('');
  useEffect(() => {
    const controller = new VoiceController();
    controllerRef.current = controller;
    const unsubscribe = controller.subscribe(setView);
    void controller.refreshIdentity();
    const onFocus = () => { void controller.refreshIdentity(); };
    window.addEventListener('focus', onFocus);
    return () => { window.removeEventListener('focus', onFocus); unsubscribe(); controller.dispose(); if (controllerRef.current === controller) controllerRef.current = null; };
  }, []);
  const controller = controllerRef.current;
  const base = view.base;
  const submit = (event: FormEvent) => { event.preventDefault(); void controller?.run(); };
  const noop = view.comparison ? view.comparison.receipt_changes.length === 0 && view.comparison.disruption_changed_order_ids.length === 0 && view.comparison.recovery_changed_order_ids.length === 0 : false;
  return <div className="shell voice-shell">
    <header className="masthead"><div className="brand">CLOSED <span>SOURCE</span><sup> / SCENARIOS</sup></div><Link className="recovery-back" to="/planning">Planning workspace</Link></header>
    <main>
      <div className="recovery-intro"><p className="eyebrow">Typed receipt scenario</p><h1>Try a receipt scenario.</h1><p>Supply a complete sample production snapshot, move receipt arrivals and assign every order to a period. The local comparison checks three schedules. Saved contexts also allow an opt-in unverified text interpretation before a deliberate comparison. Speech is unavailable; no plan is applied.</p></div>
      <section className="recovery-session" aria-label="Local session">
        {view.kind === 'checking' && <p role="status">Checking the current identity before showing a result…</p>}
        {view.kind === 'signed_out' && <div className="notice notice-error" role="alert"><strong>Local session unavailable</strong><p>{view.message || 'An authenticated local session is required.'}</p><button type="button" className="text-button" onClick={() => void controller?.refreshIdentity()}>Check session again</button></div>}
        {view.identity && view.kind !== 'checking' && <p><strong>{view.identity.identity_mode === 'private_machine' ? 'Private machine service caller' : 'Local development identity'}</strong> · workspace {view.identity.workspace_id} · {view.identity.role}. This comparison does not confirm a current saved plan.</p>}
      </section>
      {view.identity && <section className="voice-context" aria-label="Scenario source">
        <div className="voice-context-head"><div><p className="eyebrow">SOURCE</p><h2>Choose the production input</h2><p>Both paths use complete scenario input. Stored contexts keep caller supplied content with a server revision.</p></div><div className="voice-source-actions" role="group" aria-label="Scenario source"><button type="button" className={view.source === 'inline' ? 'primary-button' : 'secondary-button'} aria-pressed={view.source === 'inline'} onClick={() => controller?.selectSource('inline')}>Inline example</button><button type="button" className={view.source === 'saved' ? 'primary-button' : 'secondary-button'} aria-pressed={view.source === 'saved'} onClick={() => controller?.selectSource('saved')}>Saved context</button></div></div>
        {view.source === 'saved' && <div className="voice-context-body">
          <div className="voice-context-list"><div className="voice-context-list-head"><h3>Saved contexts</h3><button type="button" className="text-button" onClick={() => void controller?.refreshContexts()}>Refresh list</button></div>
            {view.kind === 'loading_contexts' && <p role="status">Loading saved contexts…</p>}
            {view.contexts.length === 0 && view.kind !== 'loading_contexts' && <p role="status">No saved production contexts in this workspace. Editors can load a complete example into the JSON editor and save it.</p>}
            <div className="voice-context-items">{view.contexts.map(item => <button type="button" key={item.context_id} className="voice-context-item" aria-pressed={view.selected?.context_id === item.context_id} onClick={() => void controller?.selectSaved(item.context_id)}><strong>{item.snapshot_id}</strong><span>Revision {item.revision} · {date(item.as_of)}</span></button>)}</div>
            {view.hasMore && <p className="field-hint">Showing the newest 100 contexts.</p>}
          </div>
          <div className="voice-context-editor"><h3>Complete scenario snapshot JSON</h3><p>Saving checks the complete input shape. Numerical acceptance is not claimed until a separate read-only comparison completes.</p>
            {view.selected && <p className="voice-context-meta">Context {view.selected.context_id} · revision {view.selected.revision} · {view.selected.metadata.source.replaceAll('_', ' ')} · syntactic validation {view.selected.metadata.syntactic_validation} · numerical acceptance {view.selected.metadata.numerical_acceptance.replaceAll('_', ' ')}</p>}
            <label className="field"><span className="field-label">Production snapshot</span><textarea value={view.snapshotText} onChange={event => controller?.editSnapshot(event.target.value)} spellCheck={false} rows={13} readOnly={view.identity.role !== 'editor'} aria-label="Complete production snapshot JSON" /></label>
            {view.identity.role === 'editor' ? <div className="voice-context-actions"><button type="button" className="secondary-button" onClick={() => controller?.useCompleteExample()}>Load complete inline example into editor</button><button type="button" className="primary-button" disabled={!view.snapshotText || busy.has(view.kind)} onClick={() => void controller?.saveContext(false)}>Save new context</button><button type="button" className="secondary-button" disabled={!view.selected || !view.snapshotText || busy.has(view.kind)} onClick={() => void controller?.saveContext(true)}>Replace current revision</button></div> : <p className="voice-context-meta">Viewer access is read-only. Saving and replacing require an editor.</p>}
          </div>
        </div>}
      </section>}
      {view.identity && view.source === 'saved' && view.selected && <section className="recovery-form voice-form" aria-label="Text proposal for saved context">
        <h2>Propose inputs from text</h2>
        <p>This optional interpretation uses the selected saved context. Review the exact proposed inputs before choosing a comparison.</p>
        <label className="field"><span className="field-label">Text request</span><textarea value={utterance} onChange={event => setUtterance(event.target.value)} rows={3} maxLength={4096} aria-label="Text proposal request" /></label>
        <button type="button" className="secondary-button" disabled={!utterance.trim() || busy.has(view.kind)} onClick={() => void controller?.proposeText(utterance)}>Show proposed changes</button>
      </section>}
      {base && view.identity && <form className="recovery-form voice-form" aria-label="Typed scenario" onSubmit={submit}>
        <div className="recovery-form-head"><div><h2>Snapshot and inputs</h2><p>Snapshot {base.snapshot.snapshot_id} · version {base.snapshot.plan_version} · as of {date(base.snapshot.as_of)}. {view.source === 'saved' ? `Stored input, revision ${view.selected?.revision}. Initial arrivals and baseline assignments are unchanged.` : 'All values below are caller supplied.'}</p></div>{view.source === 'inline' && <label className="field"><span className="field-label">Sample scenario</span><select value={view.example} onChange={event => controller?.selectExample(event.target.value as VoiceExample)}>{Object.entries(labels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>}</div>
        <div className="recovery-input-grid">
          <section><h3>Stock and product demand</h3><ul>{base.snapshot.inventory.map(item => <li key={item.component_id}>{item.component_id}: {item.starting_stock} {item.unit} at {date(item.stock_as_of)}</li>)}</ul><ul>{base.snapshot.products.map(product => <li key={product.product_id}>{product.product_id}: {product.bom.map(part => `${part.quantity_per_output_unit} ${part.unit} ${part.component_id} per ${product.output_unit}`).join('; ')}</li>)}</ul></section>
          <section><h3>Receipts · UTC arrivals</h3>{base.snapshot.receipts.map(receipt => <label className="field" key={receipt.receipt_id}><span className="field-label">{receipt.receipt_id} · {receipt.quantity} {receipt.unit} {receipt.component_id}</span><span className="field-hint">Snapshot arrival {date(receipt.arrival_at)}{base.scenario.arrival_replacements.find(item => item.receipt_id === receipt.receipt_id)?.expected_arrival_at !== undefined && ` · supplied expected ${date(base.scenario.arrival_replacements.find(item => item.receipt_id === receipt.receipt_id)!.expected_arrival_at)}`}</span><input type="text" value={view.arrivals[receipt.receipt_id] ?? receipt.arrival_at} onChange={event => controller?.editArrival(receipt.receipt_id, event.target.value)} aria-describedby={`voice-date-${receipt.receipt_id}`} /><small id={`voice-date-${receipt.receipt_id}`} className="field-hint">Complete UTC time: YYYY-MM-DDTHH:mm:ssZ</small></label>)}</section>
          <section><h3>Resources and periods</h3><ul>{base.snapshot.resources.map(resource => <li key={resource.resource_id}>{resource.resource_id}: {text(resource.capacity_model)}</li>)}</ul><ul>{base.snapshot.routings.map(route => <li key={`${route.product_id}:${route.resource_id}`}>{route.product_id} on {route.resource_id}: {route.run_seconds_per_output_unit} s per {route.output_unit}</li>)}</ul><ul>{base.snapshot.periods.map(period => <li key={period.period_id}>{period.period_id} · {period.resource_id}: {date(period.start)} to {date(period.end)}</li>)}</ul>{base.snapshot.maintenance.length > 0 && <ul>{base.snapshot.maintenance.map(item => <li key={item.maintenance_id}>Maintenance {item.maintenance_id} · {item.resource_id}: {date(item.start)} to {date(item.end)}</li>)}</ul>}</section>
          <section><h3>Orders · complete recovery assignment</h3>{base.snapshot.orders.map(order => <label className="field" key={order.order_id}><span className="field-label">{order.order_id} · {order.output_quantity} {order.output_unit} {order.product_id}</span><span className="field-hint">Due {date(order.due_at)} · original {base.snapshot.baseline_assignments.find(item => item.order_id === order.order_id)?.period_id}</span><select value={view.assignments[order.order_id] ?? ''} onChange={event => controller?.editAssignment(order.order_id, event.target.value)}><option value="">Choose a period</option>{base.snapshot.periods.filter(period => period.resource_id === order.resource_id).map(period => <option key={period.period_id} value={period.period_id}>{period.period_id} · {date(period.start)}</option>)}</select></label>)}</section>
        </div>
        <div className="recovery-controls"><button type="submit" className="primary-button" disabled={busy.has(view.kind) || view.kind === 'failed' && view.message?.includes('cleanup')}>Run local comparison</button>{['creating', 'created', 'starting', 'pending'].includes(view.kind) && <button type="button" className="secondary-button" onClick={() => void controller?.run()}>Replace comparison</button>}{view.kind !== 'idle' && view.kind !== 'cancelled' && view.kind !== 'closing' && <button type="button" className="secondary-button" onClick={() => void controller?.cancel()}>Cancel and close session</button>}<button type="button" className="text-button" onClick={() => void controller?.refreshIdentity()}>Refresh identity</button></div>
      </form>}
      <div className="recovery-feedback voice-feedback" aria-live="polite">
        {view.kind === 'idle' && view.identity && <p role="status">{view.message || 'Ready for a typed comparison. Reloading starts a fresh ephemeral session.'}</p>}
        {['creating', 'created', 'starting', 'pending'].includes(view.kind) && <p role="status">{view.kind === 'pending' ? 'Comparing the supplied schedules…' : 'Preparing an isolated local session…'}</p>}
        {view.kind === 'closing' && <p role="status">Stopping the previous comparison and closing its known session…</p>}
        {view.kind === 'cancelled' && <p role="status">The comparison was cancelled. Cleanup requests finished; inaccessible sessions expire on the server.</p>}
        {['failed', 'expired', 'capacity', 'unavailable', 'stale'].includes(view.kind) && <div className="notice notice-error" role="alert"><strong>{view.kind === 'stale' ? 'Saved context changed' : view.kind === 'capacity' ? 'Local capacity reached' : view.kind === 'expired' ? 'Session expired' : view.kind === 'failed' ? 'Review scenario inputs' : 'Comparison unavailable'}</strong><p>{view.message || 'Start a new local comparison after checking the session.'}</p>{view.kind === 'stale' && <button type="button" className="text-button" onClick={() => void controller?.refreshContexts()}>Refresh saved contexts</button>}{view.kind === 'failed' && view.message?.includes('cleanup') && !view.identity && <button type="button" className="text-button" onClick={() => void controller?.refreshIdentity()}>Check session again</button>}</div>}
        {view.kind === 'unsupported' && view.outcome?.status === 'unsupported' && <div className="notice notice-error" role="alert"><strong>Input unsupported</strong><p>{text(view.outcome.result.reason)}</p></div>}
        {view.kind === 'clarification' && view.outcome?.status === 'clarification_required' && <div className="notice notice-error" role="alert"><strong>Complete the arguments</strong><p>{text(view.outcome.reason)} · {view.outcome.missing_fields.join(', ')}</p></div>}
        {view.kind === 'rejected' && view.outcome?.status === 'rejected' && <div className="notice notice-error" role="alert"><strong>Comparison rejected</strong><p>{text(view.outcome.reason)}</p></div>}
        {view.kind === 'compared' && view.comparison && <Comparison value={view.comparison} noop={noop} />}
        <VoiceProposalPanel view={view} onConfirm={() => void controller?.confirmProposal()} />
      </div>
    </main>
    <footer>Closed Source / typed scenario <span>Speech unavailable · no plan applied · no supplier contacted.</span></footer>
  </div>;
}
