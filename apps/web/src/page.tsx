import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import type { Material, MaterialsResponse, Money, PlanResponse, SourceEvidence } from '../../../contracts/generated/types';
import { ApiError, createPlanningRequest, fetchMaterials, fetchPlan, formatDecimal, initialDraft, responseState, validateDraft } from './planning';
import type { PlanningDraft } from './planning';

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

export function PlanningPage() {
  const [draft, setDraft] = useState<PlanningDraft>(initialDraft);
  const [catalog, setCatalog] = useState<CatalogState>({ kind: 'loading' });
  const [result, setResult] = useState<ResultState>({ kind: 'none' });
  const [errors, setErrors] = useState<Partial<Record<FieldKey, string>>>({});
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
    return () => { stopCatalog(); serial.current += 1; planRequest.current?.abort(); };
  }, []);

  function change<K extends FieldKey>(field: K, value: PlanningDraft[K]) {
    serial.current += 1;
    planRequest.current?.abort();
    setResult({ kind: 'none' });
    setErrors(current => ({ ...current, [field]: undefined }));
    setDraft(current => ({ ...current, [field]: value }));
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
      <input name={field} value={draft[field]} onChange={event => change(field, event.target.value as never)}
        aria-invalid={!!errors[field]} aria-describedby={errors[field] ? `${field}-error` : extra?.hint ? `${field}-hint` : undefined}
        required={extra?.required} autoComplete="off" />
      {errors[field] ? <small className="field-error" id={`${field}-error`}>{errors[field]}</small>
        : extra?.hint ? <small className="field-hint" id={`${field}-hint`}>{extra.hint}</small> : null}
    </label>;
  }

  const materials: Material[] = catalog.kind === 'loaded' ? catalog.response.materials : [];
  const catalogUnavailable = catalog.kind === 'loaded' && catalog.response.availability.code !== 'available';
  const canSubmit = catalog.kind === 'loaded' && !catalogUnavailable && materials.length > 0 && result.kind !== 'loading';

  return <div className="shell">
    <header className="masthead">
      <div className="brand">CLOSED <span>SOURCE</span><sup> / PLANNING</sup></div>
      <div className="masthead-meta">LOCAL WORKSPACE <span className="red-dot" aria-hidden="true">●</span> COMPUTE ONLY</div>
    </header>
    <main>
      <section className="intro" aria-labelledby="page-title">
        <div className="eyebrow">01 / SOURCING DECISION WORKSPACE</div>
        <h1 id="page-title">Demand to<br />recommendation<span className="accent">.</span></h1>
        <p>Enter a material need and current stock. Compare a computed buying schedule with its source evidence and stated limits.</p>
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
                {input('workspaceId', 'Workspace ID', { required: true })}
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
          </form>
        </section>
        <section className="panel result-panel" aria-labelledby="result-title">
          <div className="panel-head"><span className="section-num">02</span><div><h2 id="result-title">Recommendation</h2><p>Plan, alternatives and evidence</p></div></div>
          <div aria-live="polite" aria-atomic="true">
            {result.kind === 'none' && <div className="empty-state"><span className="empty-mark" aria-hidden="true">—</span><h3>Awaiting inputs</h3><p>Complete the form, then compute a recommendation. The result will appear here.</p></div>}
            {result.kind === 'loading' && <div className="empty-state"><span className="empty-mark" aria-hidden="true">⋯</span><h3>Computing plan</h3><p>Waiting for the recommendation service.</p></div>}
            {result.kind === 'error' && <div className="notice notice-error result-notice" role="alert"><strong>Plan unavailable</strong><p>{result.message}</p><p>Review the inputs or retry the request.</p><button type="button" className="text-button" onClick={() => formRef.current?.requestSubmit()}>Retry recommendation →</button></div>}
            {result.kind === 'response' && <PlanView response={result.response} />}
          </div>
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
