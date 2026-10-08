import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import type { ActionApproval, ActionReceipt, AuditEvent, ExecuteActionRequest, Material, MaterialsResponse, Money, PlanResponse, SavedPlan, SessionResponse, SourceEvidence } from '../../../contracts/generated/types';
import { ApiError, createPlanningRequest, draftFromRequest, fetchMaterials, fetchPlan, formatDecimal, initialDraft, responseState, validateDraft } from './planning';
import type { PlanningDraft } from './planning';
import { actionArguments, confirmAction, executeAction, executionFor, fetchSession, getPlan, listPlans, loadHistory, planVersion, revisePlan, savePlan, StateError } from './state';

type CatalogState = { kind: 'loading' } | { kind: 'error'; message: string } | { kind: 'loaded'; response: MaterialsResponse };
type ResultState = { kind: 'none' | 'loading' } | { kind: 'error'; message: string } | { kind: 'response'; response: PlanResponse };
type FieldKey = keyof PlanningDraft;

const FIELD_MAP: Record<string, FieldKey> = {
  workspace_id: 'workspaceId', buyer: 'buyerId', destination_country: 'destinationCountry', material: 'material',
  quantity: 'quantity', current_stock: 'currentStock', monthly_consumption: 'monthlyConsumption',
  need_by_month: 'needByMonth', lead_time_months: 'leadTimeMonths', quote_currency: 'quoteCurrency',
  settlement_currency: 'settlementCurrency', reporting_currency: 'reportingCurrency', budget: 'budget',
};

function labelMoney(money: Money) { return `${money.currency} ${formatDecimal(money.amount)}`; }
function dateText(value: string | null) { return value || 'Unavailable'; }

export function sessionPresentation(session: SessionResponse | null, sessionError: string) {
  if (sessionError) return {
    masthead: 'SESSION UNAVAILABLE',
    details: 'Interactive sign-in unavailable. Saved plans require an authenticated session.',
  };
  if (!session) return {
    masthead: 'CHECKING SESSION',
    details: 'Loading local session…',
  };
  if (session.identity_mode === 'private_machine') return {
    masthead: `PRIVATE MACHINE / ${session.role.toUpperCase()} / ${session.principal_id}`,
    details: `Workspace ${session.workspace_id} · service caller · interactive sign-in unavailable`,
  };
  return {
    masthead: `LOCAL DEVELOPMENT / ${session.role.toUpperCase()} / ${session.principal_id}`,
    details: `Workspace ${session.workspace_id} · local development identity · ${session.role} access`,
  };
}

export function PlanningPage() {
  const [draft, setDraft] = useState<PlanningDraft>(initialDraft);
  const [catalog, setCatalog] = useState<CatalogState>({ kind: 'loading' });
  const [result, setResult] = useState<ResultState>({ kind: 'none' });
  const [errors, setErrors] = useState<Partial<Record<FieldKey, string>>>({});
  const [session, setSession] = useState<SessionResponse | null>(null);
  const [sessionError, setSessionError] = useState('');
  const [savedPlans, setSavedPlans] = useState<SavedPlan[]>([]);
  const [savedPlansError, setSavedPlansError] = useState('');
  const [selected, setSelected] = useState<SavedPlan | null>(null);
  const [dirty, setDirty] = useState(false);
  const [review, setReview] = useState(false);
  const [approval, setApproval] = useState<ActionApproval | null>(null);
  const [receipts, setReceipts] = useState<ActionReceipt[]>([]);
  const [approvals, setApprovals] = useState<ActionApproval[]>([]);
  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [stateMessage, setStateMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const execution = useRef<ExecuteActionRequest | null>(null);
  const sessionSerial = useRef(0);
  const stateSerial = useRef(0);
  const planRequest = useRef<AbortController | null>(null);
  const catalogRequest = useRef<AbortController | null>(null);
  const formRef = useRef<HTMLFormElement | null>(null);
  const serial = useRef(0);

  function loadCatalog() {
    catalogRequest.current?.abort();
    const controller = new AbortController();
    catalogRequest.current = controller;
    setCatalog({ kind: 'loading' });
    fetchMaterials(controller.signal).then(response => {
      setCatalog({ kind: 'loaded', response });
      if (response.materials.length && response.availability.code === 'available') {
        setDraft(current => current.material ? current : { ...current, material: response.materials[0].key });
      }
    }).catch(error => {
      if (!controller.signal.aborted) setCatalog({ kind: 'error', message: error instanceof Error ? error.message : 'Catalog request failed.' });
    });
    return () => controller.abort();
  }

  useEffect(() => {
    const stopCatalog = loadCatalog();
    loadSession();
    return () => { stopCatalog(); serial.current += 1; sessionSerial.current += 1; stateSerial.current += 1; planRequest.current?.abort(); };
  }, []);

  async function loadSession() {
    const token = ++sessionSerial.current;
    setSessionError('');
    let identity: SessionResponse;
    try {
      identity = await fetchSession();
    } catch (error) {
      if (token === sessionSerial.current) {
        stateSerial.current += 1; serial.current += 1; planRequest.current?.abort();
        setSession(null); setSelected(null); setApproval(null); execution.current = null; setReview(false);
        setSavedPlans([]); setSavedPlansError(''); setReceipts([]); setApprovals([]); setAudit([]); setResult({ kind: 'none' });
        setSessionError(error instanceof Error ? error.message : 'Session unavailable.');
      }
      return;
    }
    if (token !== sessionSerial.current) return;
    stateSerial.current += 1;
    serial.current += 1; planRequest.current?.abort();
    setSelected(null); setApproval(null); execution.current = null; setReview(false);
    setReceipts([]); setApprovals([]); setAudit([]); setSavedPlans([]); setSavedPlansError(''); setResult({ kind: 'none' });
    setSession(identity);
    setDraft(current => ({ ...current, workspaceId: identity.workspace_id }));
    try {
      const saved = await listPlans(identity.workspace_id);
      if (token === sessionSerial.current) setSavedPlans(saved.plans);
    } catch (error) {
      if (token === sessionSerial.current) setSavedPlansError(error instanceof Error ? error.message : 'Saved plans unavailable.');
    }
  }

  async function refreshPlans() {
    if (!session) return;
    const token = stateSerial.current;
    setSavedPlansError('');
    try { const response = await listPlans(session.workspace_id); if (token === stateSerial.current) setSavedPlans(response.plans); }
    catch (error) { if (token === stateSerial.current) setSavedPlansError(error instanceof Error ? error.message : 'Saved plans unavailable.'); }
  }

  async function selectPlan(id: string) {
    const token = ++stateSerial.current;
    serial.current += 1;
    planRequest.current?.abort();
    setApproval(null); execution.current = null; setReview(false); setSelected(null); setResult({ kind: 'none' }); setStateMessage('Loading saved plan…');
    try {
      const [current, history] = await Promise.all([getPlan(id), loadHistory(id, () => token === stateSerial.current)]);
      if (token !== stateSerial.current) return;
      if (!history) return;
      setSelected(current.plan); setDraft(draftFromRequest(current.plan.snapshot.request)); setDirty(false);
      setApprovals(history.approvals); setReceipts(history.receipts); setAudit(history.audit); setStateMessage('');
    } catch (error) { if (token === stateSerial.current) setStateMessage(error instanceof Error ? error.message : 'Saved plan unavailable.'); }
  }

  async function reloadCurrent(id: string) {
    await refreshPlans();
    await selectPlan(id);
  }

  function change<K extends FieldKey>(field: K, value: PlanningDraft[K]) {
    if (field === 'workspaceId') return;
    serial.current += 1;
    stateSerial.current += 1;
    planRequest.current?.abort();
    setResult({ kind: 'none' });
    setDirty(true); setReview(false); setApproval(null); execution.current = null; setStateMessage('');
    setErrors(current => ({ ...current, [field]: undefined }));
    setDraft(current => ({ ...current, [field]: value }));
  }

  async function persistPlan() {
    if (!session || session.role !== 'editor' || busy) return;
    const validation = validateDraft(draft); setErrors(validation);
    if (Object.keys(validation).length) { setStateMessage('Check the marked fields before saving.'); return; }
    const token = ++stateSerial.current;
    setBusy(true); setStateMessage(selected ? 'Computing and revising saved plan…' : 'Computing and saving plan…');
    try {
      const request = createPlanningRequest({ ...draft, workspaceId: session.workspace_id });
      const response = selected ? await revisePlan(selected, request) : await savePlan(session.workspace_id, request);
      if (token !== stateSerial.current) return;
      setSelected(response.plan); setDirty(false); setReview(false); setApproval(null); execution.current = null;
      setResult({ kind: 'none' }); setStateMessage('Saved snapshot is current.');
      await refreshPlans();
      await selectPlan(response.plan.saved_plan_id);
    } catch (error) {
      if (token !== stateSerial.current) return;
      setStateMessage(error instanceof Error ? error.message : 'Could not save plan.');
      if (error instanceof StateError && error.status === 409 && selected) { await reloadCurrent(selected.saved_plan_id); setStateMessage('The saved plan changed. Current revision loaded; review again.'); }
    } finally { setBusy(false); }
  }

  async function confirm() {
    if (!selected || dirty || !session || session.role !== 'editor' || busy) return;
    const token = stateSerial.current; setBusy(true); setStateMessage('Persisting confirmation…');
    try {
      const response = await confirmAction(selected);
      if (token !== stateSerial.current) return;
      setApproval(response.approval); execution.current = null; setReview(false); setStateMessage('Confirmed for local draft preparation.');
      try { const history = await loadHistory(selected.saved_plan_id, () => token === stateSerial.current); if (history) { setApprovals(history.approvals); setReceipts(history.receipts); setAudit(history.audit); } }
      catch { if (token === stateSerial.current) setStateMessage('Confirmation saved. History could not refresh; reload the saved plan to inspect it.'); }
    } catch (error) {
      if (token !== stateSerial.current) return;
      setApproval(null); setReview(false); setStateMessage(error instanceof Error ? error.message : 'Confirmation failed.');
      if (error instanceof StateError && error.status === 409) { await reloadCurrent(selected.saved_plan_id); setStateMessage('The saved plan changed. Current revision loaded; review again.'); }
    } finally { setBusy(false); }
  }

  async function execute() {
    if (!selected || !approval || dirty || !session || session.role !== 'editor' || busy) return;
    const token = stateSerial.current;
    if (!execution.current || execution.current.approval_id !== approval.approval_id) execution.current = executionFor(selected, approval.approval_id, crypto.randomUUID());
    setBusy(true); setStateMessage('Preparing local RFQ draft…');
    try {
      const response = await executeAction(execution.current);
      if (token !== stateSerial.current) return;
      setStateMessage(`Local draft prepared. Receipt ${response.receipt.receipt_id}. No supplier communication.`);
      setApproval(null); execution.current = null;
      try {
        const history = await loadHistory(selected.saved_plan_id, () => token === stateSerial.current);
        if (history) { setReceipts(history.receipts); setApprovals(history.approvals); setAudit(history.audit); }
      } catch { if (token === stateSerial.current) { setReceipts(current => [response.receipt, ...current]); setStateMessage(`Local draft prepared. Receipt ${response.receipt.receipt_id}. History could not refresh.`); } }
    } catch (error) {
      if (token !== stateSerial.current) return;
      setStateMessage(`${error instanceof Error ? error.message : 'Preparation failed.'} Retry uses the same request key.`);
      if (error instanceof StateError && error.status === 409) { execution.current = null; await reloadCurrent(selected.saved_plan_id); setStateMessage('The saved plan changed. Current revision loaded; review again.'); }
    } finally { setBusy(false); }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const validation = validateDraft(draft);
    setErrors(validation);
    if (Object.keys(validation).length) {
      setResult({ kind: 'error', message: 'Check the marked fields before requesting a plan.' });
      return;
    }
    planRequest.current?.abort();
    const controller = new AbortController();
    planRequest.current = controller;
    const token = ++serial.current;
    setResult({ kind: 'loading' });
    try {
      const response = await fetchPlan(createPlanningRequest(draft), controller.signal);
      if (token === serial.current && !controller.signal.aborted) setResult({ kind: 'response', response });
    } catch (error) {
      if (token !== serial.current || controller.signal.aborted) return;
      if (error instanceof ApiError && error.field) {
        const field = FIELD_MAP[error.field];
        if (field) setErrors(current => ({ ...current, [field]: error.message }));
      }
      setResult({ kind: 'error', message: error instanceof Error ? error.message : 'Recommendation request failed.' });
    }
  }

  function input(field: FieldKey, label: string, extra?: { hint?: string; required?: boolean; className?: string }) {
    return <label className={`field ${extra?.className || ''}`} key={field}>
      <span className="field-label">{label}</span>
      <input name={field} value={draft[field]} onChange={event => change(field, event.target.value as never)} disabled={field === 'workspaceId'}
        aria-invalid={!!errors[field]} aria-describedby={errors[field] ? `${field}-error` : extra?.hint ? `${field}-hint` : undefined}
        required={extra?.required} autoComplete="off" />
      {errors[field] ? <small className="field-error" id={`${field}-error`}>{errors[field]}</small>
        : extra?.hint ? <small className="field-hint" id={`${field}-hint`}>{extra.hint}</small> : null}
    </label>;
  }

  const materials: Material[] = catalog.kind === 'loaded' ? catalog.response.materials : [];
  const catalogUnavailable = catalog.kind === 'loaded' && catalog.response.availability.code !== 'available';
  const canSubmit = catalog.kind === 'loaded' && !catalogUnavailable && materials.length > 0 && result.kind !== 'loading' && !busy;
  const readySaved = selected && !dirty && selected.snapshot.status === 'ready' && selected.snapshot.plan;
  const canWrite = session?.role === 'editor' && !sessionError && !busy;
  const args = readySaved && selected ? actionArguments(selected) : null;
  const identityCopy = sessionPresentation(session, sessionError);

  return <div className="shell">
    <header className="masthead">
      <div className="brand">CLOSED <span>SOURCE</span><sup> / PLANNING</sup></div>
      <a className="recovery-back" href="/recovery">Synthetic recovery comparison</a>
      <a className="recovery-back" href="/shipment-recovery">Shipment recovery evidence</a>
      <a className="recovery-back" href="/scenarios">Typed scenarios</a>
      <div className="masthead-meta"><span className="red-dot" aria-hidden="true">●</span> {identityCopy.masthead}</div>
    </header>
    <main>
      <section className="intro" aria-labelledby="page-title">
        <div className="eyebrow">01 / SOURCING DECISION WORKSPACE</div>
        <h1 id="page-title">Demand to<br />recommendation<span className="accent">.</span></h1>
        <p>Enter a material need and current stock. Compare a computed buying schedule with its source evidence and stated limits.</p>
        <p><a className="recovery-back" href="/offers">Review synthetic supplier offers →</a></p>
      </section>
      <section className="saved-strip" aria-label="Saved plans">
        <div><span className="eyebrow">SAVED PLANS</span><p>{identityCopy.details}</p></div>
        {sessionError && <div className="notice notice-error" role="alert">{sessionError} <button type="button" className="text-button" disabled={busy} onClick={loadSession}>Retry session →</button></div>}
        {session && savedPlansError && <div className="notice notice-error" role="alert"><strong>Saved-plan storage unavailable</strong><p>{savedPlansError}</p><button type="button" className="text-button" disabled={busy} onClick={refreshPlans}>Retry list →</button></div>}
        {session && <div className="saved-controls"><button type="button" className="text-button" disabled={busy} onClick={() => { stateSerial.current += 1; serial.current += 1; planRequest.current?.abort(); setSelected(null); setDraft({ ...initialDraft, workspaceId: session.workspace_id }); setDirty(false); setResult({ kind: 'none' }); setApproval(null); execution.current = null; setReview(false); setReceipts([]); setApprovals([]); setAudit([]); setStateMessage(''); }}>New plan</button><button type="button" className="text-button" disabled={busy} onClick={refreshPlans}>Refresh list</button>{savedPlans.map(item => <button type="button" disabled={busy} className={selected?.saved_plan_id === item.saved_plan_id ? 'saved-choice selected-choice' : 'saved-choice'} key={item.saved_plan_id} onClick={() => selectPlan(item.saved_plan_id)}>{item.snapshot.request.material} · {item.snapshot.request.buyer.id} · revision {item.revision} · {item.saved_plan_id.slice(0, 6)}</button>)}</div>}
      </section>
      <div className="workspace-grid">
        <section className="panel form-panel" aria-labelledby="inputs-title">
          <div className="panel-head"><span className="section-num">01</span><div><h2 id="inputs-title">Planning inputs</h2><p>Use values from the current buying scenario.</p></div></div>
          {catalog.kind === 'loading' && <p className="notice" role="status">Loading material catalog…</p>}
          {catalog.kind === 'error' && <div className="notice notice-error" role="alert"><strong>Catalog unavailable</strong><p>{catalog.message}</p><button type="button" className="text-button" onClick={loadCatalog}>Retry catalog →</button></div>}
          {catalogUnavailable && <div className="notice notice-error" role="status"><strong>Catalog {catalog.response.availability.code.replace('_', ' ')}</strong><p>{catalog.response.availability.reason || 'No material coverage is available.'}</p><button type="button" className="text-button" onClick={loadCatalog}>Retry catalog →</button></div>}
          {catalog.kind === 'loaded' && !catalogUnavailable && materials.length === 0 && <div className="notice" role="status"><strong>No catalog materials</strong><p>The gateway returned an empty catalog. Retry after coverage is available.</p><button type="button" className="text-button" onClick={loadCatalog}>Retry catalog →</button></div>}
          <form ref={formRef} onSubmit={submit} noValidate>
            <fieldset disabled={!canSubmit}>
              <legend className="group-title">01A / CONTEXT</legend>
              <div className="form-grid">
                {input('workspaceId', 'Workspace (from session)', { required: true })}
                {input('buyerId', 'Buyer ID', { required: true })}
                {input('destinationCountry', 'Destination country', { hint: 'Two-letter ISO code, e.g. IN', required: true })}
                <label className="field"><span className="field-label">Material</span><select value={draft.material} onChange={event => change('material', event.target.value)} aria-invalid={!!errors.material} aria-describedby={errors.material ? 'material-error' : undefined} required>
                  <option value="">Select a material</option>{materials.map(material => <option key={material.key} value={material.key}>{material.display_name}</option>)}
                </select>{errors.material && <small className="field-error" id="material-error">{errors.material}</small>}</label>
              </div>
              <legend className="group-title">01B / DEMAND & STOCK</legend>
              <div className="form-grid">
                {input('unit', 'Quantity unit', { hint: 'Use one unit throughout; e.g. kg', required: true })}
                {input('quantity', 'Purchase quantity', { required: true })}
                {input('currentStock', 'Current stock', { required: true })}
                {input('monthlyConsumption', 'Monthly consumption', { required: true })}
                {input('needByMonth', 'Need by month', { hint: '1–6 months from now', required: true })}
                {input('leadTimeMonths', 'Lead time', { hint: '0–3 months', required: true })}
              </div>
              <legend className="group-title">01C / COMMERCIAL CONTEXT</legend>
              <div className="form-grid">
                {input('quoteCurrency', 'Quote currency', { required: true })}
                {input('settlementCurrency', 'Settlement currency', { required: true })}
                {input('reportingCurrency', 'Reporting currency', { required: true })}
                {input('budget', 'Budget (optional)', { hint: `In reporting currency ${draft.reportingCurrency}` })}
                <label className="field"><span className="field-label">Risk appetite</span><select value={draft.riskAppetite} onChange={event => change('riskAppetite', event.target.value as PlanningDraft['riskAppetite'])}>
                  <option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option>
                </select></label>
              </div>
            </fieldset>
            <p className="limit-note">Current planning coverage: IN destination · USD quote · INR settlement/reporting · kg. Other valid contexts show an availability reason. Quantities and budgets are limited to 15 significant digits and 6 fractional digits.</p>
            <button type="submit" className="primary-button" disabled={!canSubmit || result.kind === 'loading'}>{result.kind === 'loading' ? 'Computing…' : 'Compute recommendation'}<span aria-hidden="true">↗</span></button>
            <button type="button" className="secondary-button" disabled={!canWrite || !canSubmit} onClick={persistPlan}>{busy ? 'Working…' : selected ? `Compute & save revision ${selected.revision + 1}` : 'Compute & save new plan'}</button>
            {session?.role === 'viewer' && <p className="limit-note">Viewer access: plan changes and actions are disabled.</p>}
          </form>
        </section>
        <section className="panel result-panel" aria-labelledby="result-title">
          <div className="panel-head"><span className="section-num">02</span><div><h2 id="result-title">Recommendation</h2><p>Plan, alternatives and evidence</p></div></div>
          <div aria-live="polite" aria-atomic="true">
            {result.kind === 'none' && selected && !dirty && <><p className="snapshot-label">SAVED SNAPSHOT · REVISION {selected.revision}</p><PlanView response={selected.snapshot} /></>}
            {result.kind === 'none' && (!selected || dirty) && <div className="empty-state"><span className="empty-mark" aria-hidden="true">—</span><h3>{dirty ? 'Inputs changed' : 'Awaiting inputs'}</h3><p>{dirty ? 'Compute a temporary preview, then save to create a fresh server snapshot.' : 'Complete the form, then compute a recommendation. The result will appear here.'}</p></div>}
            {result.kind === 'loading' && <div className="empty-state"><span className="empty-mark" aria-hidden="true">⋯</span><h3>Computing plan</h3><p>Waiting for the recommendation service.</p></div>}
            {result.kind === 'error' && <div className="notice notice-error result-notice" role="alert"><strong>Plan unavailable</strong><p>{result.message}</p><p>Review the inputs or retry the request.</p><button type="button" className="text-button" onClick={() => formRef.current?.requestSubmit()}>Retry recommendation →</button></div>}
            {result.kind === 'response' && <><p className="snapshot-label">TEMPORARY PREVIEW · NOT SAVED</p><PlanView response={result.response} /></>}
          </div>
          <section className="action-panel" aria-labelledby="action-title">
            <h3 id="action-title">RFQ draft preparation</h3>
            <p>Prepare a local draft for review. This does not contact suppliers or place an order.</p>
            {stateMessage && <p className="action-message" role="status">{stateMessage}</p>}
            {selected && <p className="revision-line">Saved plan {selected.saved_plan_id} · revision {selected.revision}{dirty ? ' · unsaved edits' : ''}</p>}
            {readySaved && args && !review && !approval && <button type="button" className="secondary-button" disabled={!canWrite} onClick={() => setReview(true)}>Review exact action</button>}
            {review && readySaved && selected && args && <div className="review-card"><h4>Confirm local preparation</h4><dl>
              <div><dt>Saved revision / version</dt><dd>{selected.revision} / {planVersion(selected)}</dd></div>
              <div><dt>Action</dt><dd>prepare_rfq</dd></div><div><dt>Buyer</dt><dd>{args.buyer_id}</dd></div>
              <div><dt>Material</dt><dd>{args.material}</dd></div><div><dt>Quantity</dt><dd>{args.quantity.amount} {args.quantity.unit}</dd></div>
              <div><dt>Destination</dt><dd>{args.destination_country}</dd></div><div><dt>Quote currency</dt><dd>{args.quote_currency}</dd></div>
              <div><dt>Settlement currency</dt><dd>{args.settlement_currency}</dd></div><div><dt>Reporting currency</dt><dd>{args.reporting_currency}</dd></div>
            </dl><button type="button" className="primary-button" disabled={!canWrite} onClick={confirm}>Confirm these arguments</button><button type="button" className="text-button" onClick={() => setReview(false)}>Cancel review</button></div>}
            {approval && readySaved && <div className="review-card"><strong>Confirmed approval {approval.approval_id}</strong><p>Prepare the local draft from this exact saved revision.</p><button type="button" className="primary-button" disabled={!canWrite} onClick={execute}>{busy ? 'Preparing…' : 'Prepare local draft'}</button></div>}
            {selected && <div className="history-grid"><div><h4>Receipts</h4>{receipts.length ? receipts.map(item => <p key={item.receipt_id}>#{item.receipt_id} · revision {item.revision} · {item.status} · no supplier communication</p>) : <p>No prepared drafts.</p>}</div><div><h4>Approvals</h4>{approvals.length ? approvals.map(item => <p key={item.approval_id}>#{item.approval_id} · revision {item.revision} · {item.status}</p>) : <p>No approvals.</p>}</div><div><h4>Audit</h4>{audit.length ? audit.map(item => <p key={item.audit_id}>{item.event_type.replaceAll('_', ' ')} · revision {item.revision} · {item.created_at}</p>) : <p>No audit events.</p>}</div></div>}
          </section>
        </section>
      </div>
    </main>
    <footer>Closed Source / Sourcing workspace <span>Recommendation snapshots are for review.</span></footer>
  </div>;
}

function PlanView({ response }: { response: PlanResponse }) {
  const state = responseState(response);
  if (state !== 'ready' || !response.plan) return <div className="notice result-notice" role="status">
    <strong>{state === 'empty' ? 'No recommendation returned' : 'Context unavailable'}</strong>
    <p>{response.availability.reason || (state === 'empty' ? 'No schedule is available for these inputs.' : 'The service cannot plan this context yet.')}</p>
    <p className="availability-code">{response.availability.code.replaceAll('_', ' ')}</p>
    {response.warnings.length > 0 && <Warnings warnings={response.warnings} />}
  </div>;
  const plan = response.plan;
  return <div className="plan-content">
    <div className="plan-banner"><span className="eyebrow">COMPUTED RECOMMENDATION</span><h3>{plan.action}</h3><p>{plan.summary}</p><div className="price-line"><span>Current unit price</span><strong>{labelMoney(plan.current_price.money)} / {plan.current_price.per_unit}</strong></div></div>
    <div className="limitation"><strong>Inventory feasibility is not verified.</strong> Review whether delivery timing covers demand. Estimates include rounded values.</div>
    <Warnings warnings={response.warnings} />
    <div className="subsection"><h4>Purchase schedule</h4>{plan.schedule.length ? <div className="table-scroll"><table><thead><tr><th>Month</th><th>Quantity</th><th>Share</th><th>Expected unit price</th></tr></thead><tbody>{plan.schedule.map((line, index) => <tr key={`${line.purchase_month}-${index}`}><td>{line.calendar_month || `Month ${line.purchase_month}`}</td><td>{formatDecimal(line.quantity.amount)} {line.quantity.unit}</td><td>{formatDecimal(line.percent)}%</td><td>{labelMoney(line.expected_unit_price.money)} / {line.expected_unit_price.per_unit}</td></tr>)}</tbody></table></div> : <p className="inline-empty">No purchase lines returned.</p>}</div>
    <div className="subsection"><h4>Policy comparison</h4>{plan.comparisons.length ? <div className="table-scroll"><table><thead><tr><th>Policy</th><th>Schedule</th><th>Expected cost</th><th>P90 cost</th><th>Stockout penalty</th></tr></thead><tbody>{plan.comparisons.map((row, index) => <tr key={`${row.name}-${index}`}><td className="emphasis">{row.name}</td><td>{row.schedule}</td><td>{labelMoney(row.expected_cost)}</td><td>{labelMoney(row.p90_cost)}</td><td>{labelMoney(row.stockout_penalty)}</td></tr>)}</tbody></table></div> : <p className="inline-empty">No comparison rows returned.</p>}</div>
    <div className="subsection"><h4>Source evidence</h4>{plan.evidence.length ? <div className="evidence-list">{plan.evidence.map((item, index) => <Evidence key={`${item.source}-${index}`} item={item} />)}</div> : <p className="inline-empty">No source evidence returned.</p>}</div>
    <details className="plan-details"><summary>Snapshot details</summary><dl><div><dt>Plan ID</dt><dd>{plan.plan_id}</dd></div><div><dt>Plan version</dt><dd>{plan.plan_version}</dd></div><div><dt>Precision</dt><dd>Rounded estimates</dd></div><div><dt>Calculation reference</dt><dd>{plan.legacy_recommendation_id || 'Unavailable'}</dd></div></dl></details>
  </div>;
}

function Warnings({ warnings }: { warnings: string[] }) {
  return warnings.length ? <div className="warnings"><h4>Warnings</h4><ul>{warnings.map((warning, index) => <li key={`${warning}-${index}`}>{warning}</li>)}</ul></div> : null;
}

function Evidence({ item }: { item: SourceEvidence }) {
  return <article className="evidence-item"><div><strong>{item.source}</strong><span className="evidence-tag">{item.used}</span></div><dl><div><dt>Status</dt><dd>{item.status || 'Unavailable'}</dd></div><div><dt>Retrieved</dt><dd>{dateText(item.retrieved_at)}</dd></div><div><dt>Observation period</dt><dd>{dateText(item.observation_end)}</dd></div></dl><p>{item.basis || 'Basis unavailable'}</p></article>;
}
