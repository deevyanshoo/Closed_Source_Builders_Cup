import { useEffect, useReducer, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { createDemoApi, DemoApiError } from './demo-api';
import type { DemoFixture, DemoSession, FieldResult, OfferView, PlanRequest, PlanView, RecoveryView, RfqView } from './demo-api';
import './demo.css';

export type DemoState = { epoch: number; session: DemoSession | null; offers: OfferView[]; plan: PlanView | null; planRequest: PlanRequest | null; recovery: RecoveryView | null; rfq: RfqView | null; reviewed: boolean; dirty: boolean; focusedId: string | null };
export const initialDemoState: DemoState = { epoch: 0, session: null, offers: [], plan: null, planRequest: null, recovery: null, rfq: null, reviewed: false, dirty: false, focusedId: null };
export type DemoAction = { type: 'session'; session: DemoSession; epoch: number } | { type: 'reset-start' } |
  { type: 'offer'; offer: OfferView; epoch: number; cached?: boolean } | { type: 'plan'; plan: PlanView; request?: PlanRequest; epoch: number } |
  { type: 'recovery'; recovery: RecoveryView; epoch: number } | { type: 'rfq'; rfq: RfqView; epoch: number } |
  { type: 'reviewed'; value: boolean } | { type: 'invalidate' } | { type: 'scenario-edit' } | { type: 'focus'; offerId: string };

const currentVersions = (offers: OfferView[], versions: Record<string, number>) => Object.entries(versions).every(([id, version]) => offers.some(o => o.offer_id === id && o.version === version && acceptedStatus(o.status)));

export function applyDemoResult(state: DemoState, action: DemoAction): DemoState {
  if (action.type === 'reset-start') return { ...initialDemoState, epoch: state.epoch + 1 };
  if (action.type === 'invalidate') return { ...state, epoch: state.epoch + 1, plan: null, planRequest: null, recovery: null, rfq: null, reviewed: false, dirty: true };
  if (action.type === 'scenario-edit') return { ...state, epoch: state.epoch + 1, rfq: null, reviewed: false, dirty: true };
  if (action.type === 'focus') return { ...state, focusedId: action.offerId };
  if (action.type === 'reviewed') return state.dirty ? state : { ...state, reviewed: action.value };
  if (action.epoch !== state.epoch) return state;
  if (action.type === 'session') {
    const request = action.session.plan_request ?? action.session.recovery?.baseline_request ?? null;
    const recovery = request ? action.session.recovery ?? null : null;
    const plan = request ? recovery?.scenario ?? action.session.plan ?? null : null;
    return { ...state, session: action.session, offers: action.session.offers, dirty: false,
      plan, planRequest: request, recovery, rfq: state.dirty ? null : action.session.rfq ?? null, reviewed: false,
      focusedId: action.session.offers.some(o => o.offer_id === state.focusedId) ? state.focusedId : action.session.offers.at(-1)?.offer_id ?? null };
  }
  if (action.type === 'offer') {
    const prior = state.offers.find(o => o.offer_id === action.offer.offer_id);
    if (prior && prior.version >= action.offer.version) return state;
    const nextOffer = { ...action.offer, served_from_cache: action.cached ?? action.offer.served_from_cache ?? prior?.served_from_cache };
    return { ...state, offers: [...state.offers.filter(o => o.offer_id !== action.offer.offer_id), nextOffer], focusedId: action.offer.offer_id, plan: null, planRequest: null, recovery: null, rfq: null, reviewed: false };
  }
  if (action.type === 'plan') {
    if (!currentVersions(state.offers, action.plan.offer_versions)) return state;
    return { ...state, plan: action.plan, planRequest: action.request ?? null, recovery: null, rfq: null, reviewed: false, dirty: false };
  }
  if (action.type === 'recovery') {
    if ((state.recovery?.baseline ?? state.plan)?.plan_id !== action.recovery.baseline.plan_id || !currentVersions(state.offers, action.recovery.baseline.offer_versions) || !currentVersions(state.offers, action.recovery.scenario.offer_versions)) return state;
    return { ...state, plan: action.recovery.scenario, recovery: action.recovery, rfq: null, reviewed: false, dirty: false };
  }
  if (action.type === 'rfq') {
    if (state.dirty || state.plan?.plan_id !== action.rfq.plan_id || !currentVersions(state.offers, action.rfq.offer_versions) || !state.reviewed) return state;
    return { ...state, rfq: action.rfq };
  }
  return state;
}

const api = createDemoApi();
const today = () => {
  const date = new Date();
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
};
const defaultInputs = () => ({ material: '', quantity_kg: '', current_stock_kg: '0', need_by: '', decision_date: today(), reporting_currency: 'USD', budget: '', additional_cost_per_kg: '0' });
export function canonicalBuyerState(session: DemoSession) {
  const request = session.plan_request ?? session.recovery?.baseline_request;
  const delayControls = session.recovery ? delayControlsForRecovery(session.recovery) : session.plan && request ? delayControlsForPlan(session.plan, request) : { offerId: '', days: '0' };
  return {
    selected: request?.offer_ids.filter(id => session.offers.some(o => o.offer_id === id && acceptedStatus(o.status))) ?? [],
    inputs: request ? { material: request.material, quantity_kg: request.quantity_kg, current_stock_kg: request.current_stock_kg, need_by: request.need_by, decision_date: request.decision_date, reporting_currency: request.reporting_currency, budget: request.budget, additional_cost_per_kg: request.additional_cost_per_kg } : defaultInputs(),
    delayControls,
  };
}
export const sampleBuyerInputs = () => ({ material: 'copper', quantity_kg: '10000', current_stock_kg: '2000', need_by: '2026-11-05', decision_date: '2026-10-14', reporting_currency: 'USD', budget: '100000', additional_cost_per_kg: '0.25' });
const words = (s: string) => s.replace(/^items\[(\d+)\]\./, (_, i) => `Item ${Number(i) + 1} · `).replaceAll('_', ' ');
const message = (error: unknown) => error instanceof DemoApiError ? error.message : 'Something went wrong. Please try again.';
const acceptedStatus = (status: string) => status === 'saved' || status === 'used_in_plan';
export const delayRequest = (prior: Record<string, number>, offerId: string, days: string): Record<string, number> =>
  offerId && /^\d+$/.test(days) && Number(days) <= 3650 ? { ...prior, [offerId]: Number(days) } : {};
export function delayControlsForPlan(plan: PlanView, request: PlanRequest): { offerId: string; days: string } {
  return { offerId: plan.selected_offer_id ?? request.offer_ids[0] ?? '', days: '0' };
}
export function delayControlsForRecovery(recovery: RecoveryView, preferredOfferId = ''): { offerId: string; days: string } {
  const delays = recovery.scenario_request.delay_days_by_offer ?? {};
  const delayedIds = Object.keys(delays);
  const offerId = delayedIds.includes(preferredOfferId) ? preferredOfferId :
    recovery.baseline.selected_offer_id && delayedIds.includes(recovery.baseline.selected_offer_id) ? recovery.baseline.selected_offer_id :
      delayedIds[0] ?? recovery.baseline.selected_offer_id ?? recovery.baseline_request.offer_ids[0] ?? '';
  return { offerId, days: String(delays[offerId] ?? 0) };
}

function show(value: unknown): string {
  if (value == null || value === '') return 'Not stated';
  if (typeof value === 'string' || typeof value === 'number') return String(value);
  if (Array.isArray(value)) return value.map(show).join('; ');
  if (typeof value === 'object') {
    const v = value as Record<string, unknown>;
    if ('unit_price' in v) return `${show(v.unit_price)} per ${show(v.per_unit)}`;
    if ('amount' in v && 'currency' in v) return `${show(v.amount)} ${show(v.currency)}`;
    if ('amount' in v && 'unit' in v) return `${show(v.amount)} ${show(v.unit)}`;
    if ('code' in v) return `${show(v.code)}${v.place ? ` · ${show(v.place)}` : ''}`;
    return Object.entries(v).map(([k, x]) => `${words(k)}: ${show(x)}`).join(' · ');
  }
  return String(value);
}

function fields(offer: OfferView): [string, FieldResult][] {
  return [...Object.entries(offer.fields), ...offer.items.flatMap((item, index) => Object.entries(item).map(([name, field]) => [`items[${index}].${name}`, field] as [string, FieldResult]))];
}

function editDefaults(field: FieldResult): Record<string, string> {
  const v = field.value && typeof field.value === 'object' ? field.value as Record<string, unknown> : {};
  const price = v.unit_price && typeof v.unit_price === 'object' ? v.unit_price as Record<string, unknown> : {};
  return { value: field.value == null ? '' : String(field.value), amount: String(price.amount ?? v.amount ?? ''), currency: String(price.currency ?? ''), unit: String(v.per_unit ?? v.unit ?? 'tonne'), code: String(v.code ?? 'FOB'), place: String(v.place ?? ''), period: String(v.period ?? '') };
}

export function correctionValue(field: FieldResult, edit: Record<string, string>): unknown {
  if (field.kind === 'integer') return /^\d+$/.test(edit.value.trim()) ? Number(edit.value.trim()) : edit.value.trim();
  if (field.kind === 'quantity') return { amount: edit.amount.trim(), unit: edit.unit, ...(field.name === 'capacity' && edit.period ? { period: edit.period } : {}) };
  if (field.kind === 'pricing') return { type: 'fixed', unit_price: { amount: edit.amount.trim(), currency: edit.currency.trim().toUpperCase() }, per_unit: edit.unit };
  if (field.kind === 'incoterm') return { code: edit.code, place: edit.place.trim() || null };
  return field.kind === 'currency' ? edit.value.trim().toUpperCase() : edit.value.trim();
}

function CorrectionEditor({ field, busy, onApply, onCancel }: { field: FieldResult; busy: boolean; onApply: (value: unknown) => void; onCancel: () => void }) {
  const [edit, setEdit] = useState(() => editDefaults(field));
  const set = (name: string, value: string) => setEdit(old => ({ ...old, [name]: value }));
  const input = (name: string, title: string, type = 'text') => <label><span>{title}</span><input type={type} value={edit[name] ?? ''} onChange={e => set(name, e.target.value)} required={name !== 'place' && name !== 'period'} /></label>;
  const unit = <label><span>Unit</span><select value={edit.unit} onChange={e => set('unit', e.target.value)}><option value="kg">kg</option><option value="tonne">tonne</option><option value="lb">lb</option></select></label>;
  return <form className="demo-editor" onSubmit={e => { e.preventDefault(); onApply(correctionValue(field, edit)); }}><div className="demo-editor-grid">
    {field.kind === 'quantity' ? <>{input('amount', 'Amount')}{unit}{field.name === 'capacity' && input('period', 'Period, if stated')}</> :
      field.kind === 'pricing' ? <>{input('amount', 'Fixed unit price')}{input('currency', 'Currency')}{unit}</> :
        field.kind === 'incoterm' ? <><label><span>Incoterm</span><select value={edit.code} onChange={e => set('code', e.target.value)}>{['EXW', 'FCA', 'FAS', 'FOB', 'CFR', 'CIF', 'CPT', 'CIP', 'DAP', 'DPU', 'DDP'].map(x => <option key={x}>{x}</option>)}</select></label>{input('place', 'Named place, if stated')}</> :
          input('value', field.kind === 'date' ? 'Date' : field.kind === 'currency' ? 'Currency' : 'Correct value', field.kind === 'date' ? 'date' : 'text')}
  </div><p>Reviewer entries stay separate from document evidence. The service checks the value again before acceptance.</p><div className="demo-actions"><button className="demo-button" disabled={busy}>Apply correction</button><button className="demo-link" type="button" onClick={onCancel}>Cancel</button></div></form>;
}

function SourceBadge({ offer, cached }: { offer: OfferView; cached: boolean }) {
  const real = offer.extractor === 'gemini' && offer.extractor_detail?.call === 'real';
  return <span className="demo-badge">{real ? cached ? 'Cached real Gemini extraction' : 'Real Gemini extraction' : offer.extractor === 'rules' ? 'Rule-based extraction' : 'Extraction source unavailable'}</span>;
}

function OfferReview({ offer, busy, cached, onCorrect, onSave }: { offer: OfferView; busy: boolean; cached: boolean; onCorrect: (field: string, value: unknown) => void; onSave: () => void }) {
  const [editing, setEditing] = useState<string | null>(null);
  const accepted = acceptedStatus(offer.status);
  return <section className="demo-review"><div className="demo-heading"><div><span className="demo-kicker">02 / Review the evidence</span><h2>{offer.document.filename}</h2></div><SourceBadge offer={offer} cached={cached} /></div>
    <p className="demo-muted">Quote status: <strong>{words(offer.status)}</strong> · version {offer.version}. Check the source passage before changing a term.</p>
    {offer.review_flags.length > 0 && <div className="demo-alert"><strong>Review flags</strong><ul>{offer.review_flags.map((f, i) => <li key={i}>{words(f.flag)}{f.locator ? ` · ${f.locator}` : ''}{f.text ? ` · ${f.text}` : ''}</li>)}</ul></div>}
    <div className="demo-field-list">{fields(offer).map(([name, field]) => <article className="demo-field" key={name}><div className="demo-field-top"><h3>{words(name)}</h3><span data-status={field.status}>{words(field.status)}</span></div><p className="demo-value">{show(field.value)}</p>
      {field.evidence?.length ? <div className="demo-evidence">{field.evidence.map((ref, i) => <p key={i}><code>{ref.locator}</code><q>{ref.quote}</q></p>)}</div> : <p className="demo-muted">No document passage linked to this value.</p>}
      {field.source === 'user' && <p className="demo-reviewer">Reviewer entry · document evidence is unchanged</p>}
      {field.reasons?.length > 0 && <p className="demo-muted">{field.reasons.map(words).join(' · ')}</p>}
      {!accepted && <button type="button" className="demo-link" onClick={() => setEditing(name)} disabled={busy}>Correct this term</button>}
      {editing === name && <CorrectionEditor key={`${name}-${offer.version}`} field={field} busy={busy} onApply={value => { onCorrect(name, value); setEditing(null); }} onCancel={() => setEditing(null)} />}
    </article>)}</div>
    {offer.blocking_reasons.length > 0 && <div className="demo-alert" role="alert"><strong>Resolve before accepting this quote</strong><ul>{offer.blocking_reasons.map((r, i) => <li key={i}>{words(r.field)}: {r.message}</li>)}</ul></div>}
    <div className="demo-actions"><button className="demo-button" type="button" onClick={onSave} disabled={busy || accepted || offer.blocking_reasons.length > 0}>{accepted ? 'Quote accepted' : 'Accept reviewed quote'}</button><span>Acceptance uses the latest checked version.</span></div>
  </section>;
}

const planReason: Record<string, string> = {
  material_unavailable: 'This material is not in the quote', ambiguous_material_item: 'Multiple matching material lines need review',
  expiry_unknown: 'Quote expiry is unknown', offer_expired: 'Quote expired before the decision date',
  lead_time_unknown: 'Lead time is unknown', arrives_after_need_by: 'Arrives after the needed date',
  minimum_order_unknown: 'Minimum order is unknown', below_min_order: 'Below the minimum order',
  capacity_unknown: 'Supplier capacity is unknown', capacity_period_unsupported: 'Capacity period cannot be compared', exceeds_capacity: 'Exceeds supplier capacity',
  offered_quantity_unknown: 'Offered quantity is unknown', exceeds_offered_quantity: 'Exceeds offered quantity',
  fixed_price_unavailable: 'Fixed price is unavailable', settlement_price_currency_mismatch: 'Price and settlement currencies differ',
  fx_unavailable: 'Currency rate unavailable', price_unavailable: 'Price could not be calculated', over_budget: 'Exceeds the budget',
  payment_terms_unknown: 'Payment terms are unknown', quantity_unknown: 'Required quantity is unknown',
};
const reasonText = (reason: string) => planReason[reason] ?? words(reason);

export function PlanResults({ plan, offers }: { plan: PlanView; offers: OfferView[] }) {
  const selected = plan.alternatives.find(a => a.offer_id === plan.selected_offer_id);
  const stockCoversNeed = /^0(?:\.0+)?$/.test(plan.required_purchase_kg);
  return <section className="demo-results" aria-label="Comparison result"><span className="demo-kicker">04 / Comparison</span><h2>{stockCoversNeed ? 'Stock covers this need' : selected ? `${selected.supplier} is feasible` : 'No feasible quote for these inputs'}</h2><p>{plan.explanation}</p>
    <div className="demo-result-numbers"><div><span>Purchase needed after stock</span><strong>{plan.required_purchase_kg} kg</strong></div><div><span>Selected total</span><strong>{plan.total_cost == null ? 'No selection' : `${plan.total_cost} ${plan.reporting_currency}`}</strong></div></div>
    {plan.alternatives.length > 0 && <div className="demo-table-wrap"><table><thead><tr><th>Supplier</th><th>Arrival</th><th>Unit price</th><th>Total</th><th>Decision</th></tr></thead><tbody>{plan.alternatives.map(a => <tr key={a.offer_id}><td>{a.supplier}<small>{offers.find(o => o.offer_id === a.offer_id)?.document.filename}</small></td><td>{a.arrival_date ?? 'Unknown'}</td><td>{a.unit_price_per_kg == null ? 'Unavailable' : `${a.unit_price_per_kg} ${plan.reporting_currency}/kg`}</td><td>{a.total_cost == null ? 'Unavailable' : `${a.total_cost} ${plan.reporting_currency}`}</td><td>{a.feasible ? a.offer_id === plan.selected_offer_id ? 'Selected' : 'Feasible' : a.reasons.map(reasonText).join('; ') || 'Infeasible'}</td></tr>)}</tbody></table></div>}
    <p className="demo-muted">Total includes your extra cost per kg. It is not proof of full freight, tariffs or landed cost. Currency rates are synthetic assumptions in the export. Forecast action: {plan.forecast_action.toLowerCase()}. No customer savings are claimed.</p>
  </section>;
}

function PlanSnapshot({ title, plan, offers }: { title: string; plan: PlanView; offers: OfferView[] }) {
  const choice = plan.alternatives.find(a => a.offer_id === plan.selected_offer_id);
  const stock = /^0(?:\.0+)?$/.test(plan.required_purchase_kg);
  return <article className="demo-snapshot"><h3>{title}</h3><dl><dt>Supplier</dt><dd>{stock ? 'No purchase needed; stock covers this need' : choice?.supplier ?? 'No feasible quote'}</dd><dt>Arrival</dt><dd>{choice?.arrival_date ?? 'No selected arrival'}</dd><dt>Buyer total</dt><dd>{plan.total_cost == null ? 'No selection' : `${plan.total_cost} ${plan.reporting_currency}`}</dd><dt>Feasibility</dt><dd>{stock ? 'Stock covers need' : choice ? 'Feasible' : 'No feasible quote'}</dd></dl>
    {plan.alternatives.length > 0 && <div className="demo-table-wrap"><table><thead><tr><th>Supplier</th><th>Arrival</th><th>Decision</th></tr></thead><tbody>{plan.alternatives.map(a => <tr key={a.offer_id}><td>{a.supplier}<small>{offers.find(o => o.offer_id === a.offer_id)?.document.filename}</small></td><td>{a.arrival_date ?? 'Unknown'}</td><td>{a.offer_id === plan.selected_offer_id ? 'Selected' : a.feasible ? 'Feasible alternative' : a.reasons.map(reasonText).join('; ') || 'Infeasible'}</td></tr>)}</tbody></table></div>}</article>;
}

export function RecoveryResults({ recovery, offers }: { recovery: RecoveryView; offers: OfferView[] }) {
  return <section className="demo-results demo-recovery-results" aria-label="Hypothetical delay comparison"><span className="demo-kicker">05 / Hypothetical supplier delay</span><h2>Original and changed plan</h2><p>This is a synthetic what-if comparison. No shipment delay or production effect has been observed.</p>
    <div className="demo-paired"><PlanSnapshot title="Original baseline" plan={recovery.baseline} offers={offers} /><PlanSnapshot title="Hypothetical scenario" plan={recovery.scenario} offers={offers} /></div>
    <p><strong>Buyer cost change:</strong> {recovery.cost_delta == null ? 'Unavailable because a plan has no selection' : `${recovery.cost_delta} ${recovery.scenario.reporting_currency}`} · Outcome: {words(recovery.outcome)}. This is an illustrative cost difference, not customer savings.</p>
  </section>;
}

export function DemoPage({ initialState = initialDemoState }: { initialState?: DemoState }) {
  const [state, dispatch] = useReducer(applyDemoResult, initialState);
  const [fixtures, setFixtures] = useState<DemoFixture[]>([]);
  const [fixtureId, setFixtureId] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [mode, setMode] = useState<'gemini' | 'rules'>('gemini');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [selected, setSelected] = useState<string[]>(() => initialState.session ? canonicalBuyerState(initialState.session).selected : []);
  const [inputs, setInputs] = useState(() => initialState.session ? canonicalBuyerState(initialState.session).inputs : defaultInputs());
  const [delayOfferId, setDelayOfferId] = useState(() => initialState.session ? canonicalBuyerState(initialState.session).delayControls.offerId : '');
  const [delayDays, setDelayDays] = useState(() => initialState.session ? canonicalBuyerState(initialState.session).delayControls.days : '0');
  const epochRef = useRef(initialState.epoch);
  const actionRef = useRef(0);
  const offer = state.offers.find(o => o.offer_id === state.focusedId) ?? null;
  const accepted = state.offers.filter(o => acceptedStatus(o.status));
  const chosen = accepted.filter(o => selected.includes(o.offer_id));

  function restoreSession(session: DemoSession, epoch: number) {
    const buyer = canonicalBuyerState(session);
    dispatch({ type: 'session', session, epoch });
    setSelected(buyer.selected); setInputs(buyer.inputs);
    setDelayOfferId(buyer.delayControls.offerId);
    setDelayDays(buyer.delayControls.days);
  }

  useEffect(() => {
    let cancelled = false;
    const epoch = epochRef.current;
    if (!initialState.session) api.session().then(session => { if (!cancelled && epoch === epochRef.current) restoreSession(session, epoch); }).catch(e => { if (!cancelled && epoch === epochRef.current) setError(message(e)); });
    api.fixtures().then(list => { if (!cancelled) { setFixtures(list); setFixtureId(list[0]?.id ?? ''); } }).catch(e => { if (!cancelled) setError(message(e)); });
    return () => { cancelled = true; };
  }, [initialState.session]);

  async function run<T>(name: string, operation: () => Promise<T>, success: (value: T, epoch: number) => void) {
    const epoch = epochRef.current, action = ++actionRef.current;
    setBusy(name); setError('');
    try { const result = await operation(); if (epoch === epochRef.current && action === actionRef.current) success(result, epoch); }
    catch (e) { if (epoch === epochRef.current && action === actionRef.current) setError(message(e)); }
    finally { if (epoch === epochRef.current && action === actionRef.current) setBusy(''); }
  }

  function invalidate() {
    epochRef.current += 1; actionRef.current += 1;
    dispatch({ type: 'invalidate' }); setBusy(''); setError('');
  }
  function editScenario() {
    epochRef.current += 1; actionRef.current += 1;
    dispatch({ type: 'scenario-edit' }); setBusy(''); setError('');
  }

  function extract(event: FormEvent) {
    event.preventDefault();
    if (!state.session) return;
    if (!file && !fixtureId) { setError('Choose a synthetic quote or upload one of the listed documents.'); return; }
    invalidate();
    run('extract', () => file ? api.upload(state.session!.csrf_token, file, mode) : api.extract(state.session!.csrf_token, { fixture_id: fixtureId, mode }), (result, epoch) => {
      dispatch({ type: 'offer', offer: result.offer, epoch, cached: result.served_from_cache }); setFile(null);
    });
  }
  function correct(name: string, value: unknown) {
    if (!offer || !state.session) return;
    const current = offer;
    invalidate();
    run('correct', () => api.correct(state.session!.csrf_token, current.offer_id, name, value, current.version), (result, epoch) => {
      dispatch({ type: 'offer', offer: result.offer, epoch }); setSelected(ids => ids.filter(id => id !== current.offer_id));
    });
  }
  function save() {
    if (!offer || !state.session) return;
    const current = offer;
    invalidate();
    run('save', () => api.save(state.session!.csrf_token, current.offer_id, current.version), (result, epoch) => {
      dispatch({ type: 'offer', offer: result.offer, epoch }); setSelected(ids => ids.includes(current.offer_id) ? ids : [...ids, current.offer_id]);
      if (!inputs.material) setInputs(old => ({ ...old, material: String(current.items[0]?.material?.value ?? '') }));
    });
  }
  function compare(event: FormEvent) {
    event.preventDefault();
    if (!state.session || !chosen.length) return;
    const request: PlanRequest = { ...inputs, offer_ids: chosen.map(o => o.offer_id), expected_versions: Object.fromEntries(chosen.map(o => [o.offer_id, o.version])) };
    invalidate();
    run('plan', () => api.plan(state.session!.csrf_token, request), (plan, epoch) => {
      dispatch({ type: 'plan', plan, request, epoch });
      const controls = delayControlsForPlan(plan, request);
      setDelayOfferId(controls.offerId); setDelayDays(controls.days);
    });
  }
  const baseline = state.recovery?.baseline ?? state.plan;
  const delayOffers = baseline ? accepted.filter(o => baseline.offer_versions[o.offer_id] === o.version) : [];
  const selectedDelayOffer = delayOffers.some(o => o.offer_id === delayOfferId) ? delayOfferId : delayOffers[0]?.offer_id ?? '';
  function compareDelay(days: Record<string, number>) {
    if (!state.session || !baseline) return;
    const expected_versions = baseline.offer_versions;
    editScenario();
    run('recovery', () => api.recovery(state.session!.csrf_token, { baseline_plan_id: baseline.plan_id, expected_versions, delay_days_by_offer: days }), (recovery, epoch) => {
      dispatch({ type: 'recovery', recovery, epoch });
      const controls = delayControlsForRecovery(recovery, selectedDelayOffer);
      setDelayOfferId(controls.offerId); setDelayDays(controls.days);
    });
  }
  function prepareRfq() {
    if (state.dirty || !state.session || !state.plan || !state.reviewed || !state.plan.selected_offer_id || /^0(?:\.0+)?$/.test(state.plan.required_purchase_kg)) return;
    const plan = state.plan;
    run('rfq', () => api.rfq(state.session!.csrf_token, { plan_id: plan.plan_id, expected_versions: plan.offer_versions, reviewed: true }), (rfq, epoch) => dispatch({ type: 'rfq', rfq, epoch }));
  }
  function downloadRfq() {
    if (state.dirty || !state.rfq) return;
    run('download-rfq', () => api.downloadRfq(state.rfq!.draft_id), blob => {
      const url = URL.createObjectURL(blob), anchor = document.createElement('a');
      anchor.href = url; anchor.download = 'synthetic-rfq-draft.txt'; anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    });
  }
  function reset() {
    if (!state.session) return;
    const csrf = state.session.csrf_token;
    dispatch({ type: 'reset-start' }); epochRef.current += 1; actionRef.current += 1;
    setBusy('reset'); setError(''); setSelected([]); setFile(null); setInputs(defaultInputs()); setDelayOfferId(''); setDelayDays('0');
    const epoch = epochRef.current;
    api.reset(csrf).then(session => { if (epoch === epochRef.current) restoreSession(session, epoch); })
      .catch(async e => { if (epoch !== epochRef.current) return; try { const session = await api.session(); if (epoch === epochRef.current) restoreSession(session, epoch); } catch { if (epoch === epochRef.current) setError(`${message(e)} Refresh the page to recover the session.`); } })
      .finally(() => { if (epoch === epochRef.current) setBusy(''); });
  }
  function exportReport() {
    if (state.dirty) return;
    run('export', () => api.exportReport(), blob => {
      const url = URL.createObjectURL(blob), anchor = document.createElement('a');
      anchor.href = url; anchor.download = 'closed-source-demo-report.json'; anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    });
  }

  return <div className="demo-page"><header className="demo-masthead"><a className="demo-brand" href="/demo">CLOSED<span>SOURCE</span><sup> / DEMO</sup></a><span>QUOTE → FEASIBLE PLAN</span></header><main>
    <section className="demo-hero"><div><p className="demo-kicker">A working procurement walkthrough</p><h1>From supplier quote<br /><em>to buying decision.</em></h1></div><p>Review a source-backed quote, correct what the document leaves unclear, then compare which supplier can actually deliver within your limits.</p></section>
    <div className="demo-boundary"><strong>Synthetic documents</strong><span>Quotes and buyer inputs are for this demonstration. Your work stays in a temporary session and may disappear when the service restarts. No order or supplier message is sent.</span></div>
    {error && <div className="demo-error" role="alert"><strong>Could not complete that step.</strong> {error} {state.session && <button type="button" className="demo-link" onClick={() => run('refresh', () => api.session(), restoreSession)}>Refresh session</button>}</div>}
    <section className="demo-start"><div className="demo-heading"><div><span className="demo-kicker">01 / Choose a document</span><h2>Start with a supplier quote</h2></div><span className="demo-session">{state.session ? `Session expires ${new Date(state.session.expires_at).toLocaleString()}` : 'Opening session…'}</span></div>
      <form onSubmit={extract} className="demo-extract-form"><label><span>Sample quote</span><select value={fixtureId} onChange={e => { setFixtureId(e.target.value); setFile(null); }}><option value="">Choose a document</option>{fixtures.map(f => <option key={f.id} value={f.id}>{f.name}</option>)}</select></label>
        <label><span>Or upload one of these sample files</span><input type="file" accept=".pdf,.csv,application/pdf,text/csv" onChange={e => setFile(e.target.files?.[0] ?? null)} /><small>Only the exact listed sample documents are accepted. Maximum 500 KB.</small></label>
        <label><span>Extraction method</span><select value={mode} onChange={e => setMode(e.target.value as 'gemini' | 'rules')}><option value="gemini">Gemini · live extraction</option><option value="rules">Rules · comparison fallback</option></select></label>
        <button className="demo-button" disabled={!state.session || !!busy}>{busy === 'extract' ? 'Reading quote…' : 'Read quote'}</button></form>
      {fixtures.length > 0 && <div className="demo-fixtures"><span>Sample documents</span>{fixtures.map(f => <a key={f.id} href={f.download_url} download>{f.filename} ↗</a>)}</div>}
      {state.session && !state.session.provider.available && <p className="demo-provider">Gemini is currently unavailable. Rule extraction can still be tried and will be labelled separately.</p>}
    </section>
    {state.offers.length > 0 && <div className="demo-offer-tabs" role="group" aria-label="Quotes in this session">{state.offers.map(o => <button key={o.offer_id} type="button" className={o.offer_id === state.focusedId ? 'active' : ''} onClick={() => dispatch({ type: 'focus', offerId: o.offer_id })}>{o.document.filename}<small>{words(o.status)}</small></button>)}</div>}
    {offer && <><p className="demo-source-line">{offer.extractor === 'gemini' && offer.extractor_detail?.call === 'real' ? offer.served_from_cache ? 'This is a cached response from an earlier real Gemini extraction.' : 'Gemini processed this synthetic document.' : offer.extractor === 'rules' ? 'Rule extraction is shown separately from Gemini.' : 'The extraction source is unavailable.'}</p><OfferReview key={offer.offer_id} offer={offer} busy={!!busy} cached={!!offer.served_from_cache} onCorrect={correct} onSave={save} /></>}
    <section className="demo-planning"><div className="demo-heading"><div><span className="demo-kicker">03 / Test a purchase</span><h2>What can arrive in time?</h2></div><span className="demo-session">Single-supplier options · deterministic comparison</span></div>
      <p className="demo-muted">Choose accepted quotes. Enter the quantity, date, budget and any extra cost you want included. This is a single-order sourcing comparison; it checks each supplier independently.</p>
      <div className="demo-accepted"><strong>Accepted quotes</strong>{accepted.length ? accepted.map(o => <label key={o.offer_id}><input type="checkbox" checked={selected.includes(o.offer_id)} onChange={e => { invalidate(); setSelected(ids => e.target.checked ? [...ids, o.offer_id] : ids.filter(id => id !== o.offer_id)); }} />{o.document.filename}<small>v{o.version}</small></label>) : <p>Accept at least one reviewed quote to run a comparison.</p>}</div>
      <div className="demo-sample"><button type="button" className="demo-link" disabled={!state.session} onClick={() => { if (!state.session) return; invalidate(); setInputs(sampleBuyerInputs()); }}>Use sample buyer inputs</button><span>Optional fictional scenario: 10,000 kg copper need, 2,000 kg in stock, needed by 5 Nov 2026, USD 100,000 budget. The sample decision date is 14 Oct 2026; quote acceptance still checks current validity. Edit any input below.</span></div>
      <form onSubmit={compare} className="demo-plan-form"><div className="demo-plan-grid">{([['material', 'Material'], ['quantity_kg', 'Needed quantity (kg)'], ['current_stock_kg', 'Current stock (kg)'], ['need_by', 'Needed by'], ['decision_date', 'Decision date'], ['reporting_currency', 'Reporting currency'], ['budget', 'Maximum budget'], ['additional_cost_per_kg', 'Extra cost per kg']] as const).map(([name, title]) => <label key={name}><span>{title}</span><input type={name === 'need_by' || name === 'decision_date' ? 'date' : 'text'} required disabled={!state.session} value={inputs[name]} onChange={e => { if (!state.session) return; invalidate(); setInputs(old => ({ ...old, [name]: e.target.value })); }} inputMode={['quantity_kg', 'current_stock_kg', 'budget', 'additional_cost_per_kg'].includes(name) ? 'decimal' : undefined} /></label>)}</div>
        <p className="demo-assumption">Extra cost is your synthetic per-kg assumption; total cost includes it but does not prove full freight or tariffs. Scenario FX reference when conversion applies: EUR 1 = USD 1.25, dated 2026-10-14 (synthetic). Budget and price are decimal amounts in the reporting currency.</p><button className="demo-button" disabled={!state.session || !chosen.length || !!busy}>{busy === 'plan' ? 'Comparing…' : 'Run comparison'}</button></form>
    </section>
    {state.plan && !state.recovery && <PlanResults plan={state.plan} offers={state.offers} />}
    {baseline && <section className="demo-planning demo-delay"><div className="demo-heading"><div><span className="demo-kicker">05 / Test a supplier delay</span><h2>What if a quote arrives late?</h2></div><span className="demo-session">Synthetic what-if · no confirmed shipment event</span></div>
      <p className="demo-muted">Keep the original buyer inputs and accepted quote versions. A delay changes only the selected supplier arrival assumption.</p>
      <div className="demo-delay-controls"><label><span>Accepted supplier</span><select value={selectedDelayOffer} onChange={e => { editScenario(); setDelayOfferId(e.target.value); setDelayDays(String(state.recovery?.scenario_request.delay_days_by_offer?.[e.target.value] ?? 0)); }}>{delayOffers.map(o => <option key={o.offer_id} value={o.offer_id}>{o.document.filename}</option>)}</select></label><label><span>Hypothetical delay (whole days)</span><input type="number" min="0" max="3650" step="1" value={delayDays} onChange={e => { editScenario(); setDelayDays(e.target.value); }} /></label></div>
      <p className="demo-assumption">Scenario: {selectedDelayOffer ? `${delayOffers.find(o => o.offer_id === selectedDelayOffer)?.document.filename} delayed ${delayDays || '0'} days` : 'Choose an accepted supplier'}{state.recovery && Object.entries(state.recovery.scenario_request.delay_days_by_offer ?? {}).length ? ` · Current comparison: ${Object.entries(state.recovery.scenario_request.delay_days_by_offer ?? {}).map(([id, days]) => `${delayOffers.find(o => o.offer_id === id)?.document.filename ?? id} +${days} days`).join(', ')}` : ''}; the original quote and evidence remain unchanged.</p>
      <div className="demo-actions"><button type="button" className="demo-button" disabled={!selectedDelayOffer || !/^\d+$/.test(delayDays) || Number(delayDays) > 3650 || !!busy} onClick={() => compareDelay(delayRequest(state.recovery?.scenario_request.delay_days_by_offer ?? {}, selectedDelayOffer, delayDays))}>{busy === 'recovery' ? 'Comparing delay…' : 'Compare hypothetical delay'}</button><button type="button" className="demo-link" disabled={!!busy} onClick={() => compareDelay({})}>Remove delay</button></div>
    </section>}
    {state.recovery && <RecoveryResults recovery={state.recovery} offers={state.offers} />}
    <section className="demo-planning demo-rfq"><div className="demo-heading"><div><span className="demo-kicker">06 / Prepare an RFQ draft</span><h2>Review before you download</h2></div><span className="demo-session">Synthetic · unsent · no order commitment</span></div>
      <p className="demo-muted">The draft uses the current selected plan and quote. The buyer total is a scenario comparison, not the supplier's commercial quote.</p>
      {state.dirty && <p className="demo-alert">Inputs changed. Run a new comparison before reviewing, preparing or downloading an RFQ draft.</p>}
      <label className="demo-review-check"><input type="checkbox" checked={state.reviewed} disabled={state.dirty || !state.plan?.selected_offer_id || /^0(?:\.0+)?$/.test(state.plan?.required_purchase_kg ?? '0')} onChange={e => dispatch({ type: 'reviewed', value: e.target.checked })} />I reviewed this plan and understand the RFQ is an unsent draft</label>
      <div className="demo-actions"><button type="button" className="demo-button" disabled={state.dirty || !state.reviewed || !state.plan?.selected_offer_id || !!busy} onClick={prepareRfq}>{busy === 'rfq' ? 'Preparing draft…' : 'Prepare unsent RFQ'}</button></div>
      {state.rfq && <div className="demo-draft"><p><strong>Draft for {state.rfq.supplier}</strong> · {state.rfq.status.replace('_', ' ')} · current reviewed comparison</p><pre>{state.rfq.text}</pre><button type="button" className="demo-button" disabled={state.dirty || !!busy} onClick={downloadRfq}>Download RFQ draft</button></div>}
    </section>
    <section className="demo-end"><div><span className="demo-kicker">Your session</span><h2>Keep the evidence.</h2><p>Download the quote, corrections, assumptions and comparison as JSON. Starting over clears this session only.</p>{state.dirty && <p>Run a new comparison before exporting the report for these inputs.</p>}</div><div className="demo-end-actions"><button className="demo-button" type="button" disabled={!state.session || state.dirty || !!busy} onClick={exportReport}>Download JSON report</button><button className="demo-link" type="button" disabled={!state.session || busy === 'reset'} onClick={reset}>Start a new session</button></div></section>
  </main><footer className="demo-footer"><span>Closed Source / AI Builder Cup</span><span>Synthetic decisions · No forecast recommendation · No customer savings claim</span></footer></div>;
}
