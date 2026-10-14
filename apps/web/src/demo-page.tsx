import { useEffect, useReducer, useRef, useState } from 'react';
import type { FormEvent, KeyboardEvent } from 'react';
import { createDemoApi, DemoApiError } from './demo-api';
import type { BuyerDraft, CompanyProfile, DemoFixture, DemoSession, FieldResult, NewsView, OfferView, OperatorTurn, PlanRequest, PlanView, RecoveryView, RfqView } from './demo-api';
import { CompanyOverview, NewsWorkspace, OutboxWorkspace } from './demo-company-news';
import { OperatorPanel } from './demo-operator';
import './demo.css';

export type DemoState = { epoch: number; session: DemoSession | null; offers: OfferView[]; plan: PlanView | null; planRequest: PlanRequest | null; recovery: RecoveryView | null; rfq: RfqView | null; news: NewsView | null; reviewed: boolean; dirty: boolean; focusedId: string | null };
export const initialDemoState: DemoState = { epoch: 0, session: null, offers: [], plan: null, planRequest: null, recovery: null, rfq: null, news: null, reviewed: false, dirty: false, focusedId: null };
export type DemoAction = { type: 'session'; session: DemoSession; epoch: number } | { type: 'reset-start' } | { type: 'company-start' } |
  { type: 'offer'; offer: OfferView; epoch: number; cached?: boolean } | { type: 'plan'; plan: PlanView; request?: PlanRequest; epoch: number } |
  { type: 'recovery'; recovery: RecoveryView; epoch: number } | { type: 'rfq'; rfq: RfqView; epoch: number } |
  { type: 'news'; news: NewsView; sessionId: string; epoch: number } |
  { type: 'reviewed'; value: boolean } | { type: 'invalidate' } | { type: 'scenario-edit' } | { type: 'focus'; offerId: string };

const currentVersions = (offers: OfferView[], versions: Record<string, number>) => Object.entries(versions).every(([id, version]) => offers.some(o => o.offer_id === id && o.version === version && acceptedStatus(o.status)));

export function applyDemoResult(state: DemoState, action: DemoAction): DemoState {
  if (action.type === 'reset-start' || action.type === 'company-start') return { ...initialDemoState, epoch: state.epoch + 1 };
  if (action.type === 'invalidate') return { ...state, epoch: state.epoch + 1, plan: null, planRequest: null, recovery: null, rfq: null, reviewed: false, dirty: true };
  if (action.type === 'scenario-edit') return { ...state, epoch: state.epoch + 1, rfq: null, reviewed: false, dirty: true };
  if (action.type === 'focus') return { ...state, focusedId: action.offerId };
  if (action.type === 'reviewed') return state.dirty ? state : { ...state, reviewed: action.value };
  if (action.type === 'news') {
    if (action.epoch !== state.epoch || action.sessionId !== state.session?.session_id ||
      action.news.company_id !== state.session?.company?.company_id || action.news.context_version !== state.session?.context_version) return state;
    return { ...state, news: action.news };
  }
  if (action.epoch !== state.epoch) return state;
  if (action.type === 'session') {
    const computed = action.session.plan_request ?? action.session.recovery?.baseline_request ?? null;
    const draft = action.session.buyer_draft;
    const request = computed && (!draft || sameBuyerFields(draft, computed)) &&
      (!action.session.selected_material_id || action.session.selected_material_id === computed.material) ? computed : null;
    const recovery = request ? action.session.recovery ?? null : null;
    const plan = request ? recovery?.scenario ?? action.session.plan ?? null : null;
    const sameContext = state.session?.session_id === action.session.session_id && state.session?.company?.company_id === action.session.company?.company_id && state.session?.context_version === action.session.context_version;
    return { ...state, session: action.session, offers: action.session.offers, news: sameContext ? state.news : null, dirty: false,
      plan, planRequest: request, recovery, rfq: state.dirty || !plan ? null : action.session.rfq ?? null, reviewed: false,
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
export async function selectCompanyWithRecovery(select: () => Promise<DemoSession>, readSession: () => Promise<DemoSession>): Promise<DemoSession> {
  try { return await select(); }
  catch { return readSession(); }
}
const today = () => {
  const date = new Date();
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
};
const defaultInputs = () => ({ material: '', quantity: '', current_stock: '0', quantity_unit: 'kg', need_by: '', decision_date: today(), reporting_currency: 'USD', budget: '', additional_cost_per_unit: '0' });
const genericInputs = (request: PlanRequest | BuyerDraft) => ({ material: request.material, quantity: request.quantity ?? ('quantity_kg' in request ? request.quantity_kg : undefined) ?? '', current_stock: request.current_stock ?? ('current_stock_kg' in request ? request.current_stock_kg : undefined) ?? '0', quantity_unit: request.quantity_unit ?? 'kg', need_by: request.need_by, decision_date: request.decision_date, reporting_currency: request.reporting_currency, budget: request.budget, additional_cost_per_unit: request.additional_cost_per_unit ?? ('additional_cost_per_kg' in request ? request.additional_cost_per_kg : undefined) ?? '0' });
const sameBuyerFields = (a: PlanRequest | BuyerDraft, b: PlanRequest | BuyerDraft) => JSON.stringify(genericInputs(a)) === JSON.stringify(genericInputs(b));
export function needsBuyerDraftSync(session: DemoSession, inputs: BuyerDraft, dirty: boolean): boolean {
  if (dirty || (session.selected_material_id && session.selected_material_id !== inputs.material)) return true;
  const canonical = session.buyer_draft ?? session.plan_request ?? session.recovery?.baseline_request;
  return !canonical || !sameBuyerFields(canonical, inputs);
}
export function companyScenarios(company: CompanyProfile) {
  if (company.quote_coverage.scenarios?.length) return company.quote_coverage.scenarios;
  return company.buyer_defaults ? [{ material_id: company.buyer_defaults.material, fixture_ids: company.quote_coverage.fixture_ids, buyer_defaults: company.buyer_defaults }] : [];
}
export function materialInputs(company: CompanyProfile, materialId: string) {
  const scenario = companyScenarios(company).find(item => item.material_id === materialId);
  return scenario ? { ...genericInputs(scenario.buyer_defaults), material: scenario.material_id } : null;
}
export function fixturesForMaterial(fixtures: DemoFixture[], company: CompanyProfile | undefined, materialId: string) {
  if (!company) return fixtures;
  const scenario = companyScenarios(company).find(item => item.material_id === materialId);
  return scenario ? fixtures.filter(fixture => scenario.fixture_ids.includes(fixture.id)) : [];
}
export function offersForMaterial(offers: OfferView[], fixtures: DemoFixture[], company: CompanyProfile | undefined, materialId: string) {
  if (!company) return offers;
  const scenario = companyScenarios(company).find(item => item.material_id === materialId);
  if (!scenario) return [];
  return offers.filter(offer => {
    if (offer.fixture_id) return scenario.fixture_ids.includes(offer.fixture_id);
    const fixture = fixtures.find(item => item.filename === offer.document.filename);
    if (fixture) return scenario.fixture_ids.includes(fixture.id);
    return !company.quote_coverage.scenarios?.length && companyScenarios(company).length === 1;
  });
}
export const requiredPurchase = (plan: PlanView): string => plan.required_purchase ?? plan.required_purchase_kg ?? '0';
export const planUnit = (plan: PlanView) => plan.quantity_unit ?? 'kg';
export function canonicalBuyerState(session: DemoSession, preferredMaterial?: string) {
  const serverRequest = session.plan_request ?? session.recovery?.baseline_request ?? null;
  const primaryMaterial = session.company?.buyer_defaults?.material ?? (session.company ? companyScenarios(session.company)[0]?.material_id : undefined);
  const validMaterial = (value: string | null | undefined) => !!value && (!session.company || companyScenarios(session.company).some(item => item.material_id === value));
  const materialId = [session.buyer_draft?.material, session.selected_material_id, serverRequest?.material, preferredMaterial, primaryMaterial].find(validMaterial) ?? '';
  const request = session.buyer_draft ?? (serverRequest?.material === materialId ? serverRequest : null) ?? (session.company ? companyScenarios(session.company).find(item => item.material_id === materialId)?.buyer_defaults ?? null : null);
  const computedSelection = serverRequest?.material === materialId ? serverRequest : null;
  const computedCurrent = !!serverRequest && serverRequest.material === materialId && (!session.buyer_draft || sameBuyerFields(session.buyer_draft, serverRequest));
  const delayControls = computedCurrent && session.recovery ? delayControlsForRecovery(session.recovery) : computedCurrent && session.plan && serverRequest ? delayControlsForPlan(session.plan, serverRequest) : { offerId: '', days: '0' };
  return {
    materialId,
    selected: computedSelection?.offer_ids.filter(id => offersForMaterial(session.offers, [], session.company, materialId).some(o => o.offer_id === id && acceptedStatus(o.status))) ?? [],
    inputs: request ? genericInputs(request) : defaultInputs(),
    delayControls,
  };
}
export const sampleBuyerInputs = () => ({ material: 'copper', quantity: '10000', current_stock: '2000', quantity_unit: 'kg', need_by: '2026-11-05', decision_date: '2026-10-14', reporting_currency: 'USD', budget: '100000', additional_cost_per_unit: '0.25' });
const words = (s: string) => s.replace(/^items\[(\d+)\]\./, (_, i) => `Item ${Number(i) + 1} · `).replaceAll('_', ' ');
const message = (error: unknown) => error instanceof DemoApiError ? error.message : 'Something went wrong. Please try again.';
export const isSessionExpired = (error: unknown) => error instanceof DemoApiError && (error.code === 'session_expired' || [401, 403, 410].includes(error.status));
export const workspaceChanged = (previous: DemoSession | null, current: DemoSession) => !previous ||
  previous.session_id !== current.session_id || previous.csrf_token !== current.csrf_token ||
  previous.company?.company_id !== current.company?.company_id || previous.context_version !== current.context_version;
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

export function reviewSuggestionFor(offer: OfferView, fixtures: DemoFixture[], fieldName: string): unknown {
  const fixture = offer.fixture_id ? fixtures.find(item => item.id === offer.fixture_id) : fixtures.find(item => item.filename === offer.document.filename);
  return fixture?.review_values?.[fieldName];
}
const needsReviewerInput = (field: FieldResult) => ['missing', 'unverified', 'unsupported', 'conflict', 'invalid', 'unknown'].includes(field.status);
function editDefaults(field: FieldResult, suggestion?: unknown): Record<string, string> {
  const value = needsReviewerInput(field) && suggestion !== undefined ? suggestion : field.value;
  const v = value && typeof value === 'object' ? value as Record<string, unknown> : {};
  const price = v.unit_price && typeof v.unit_price === 'object' ? v.unit_price as Record<string, unknown> : {};
  return { value: value == null ? '' : String(value), amount: String(price.amount ?? v.amount ?? ''), currency: String(price.currency ?? ''), unit: String(v.per_unit ?? v.unit ?? 'tonne'), code: String(v.code ?? 'FOB'), place: String(v.place ?? ''), period: String(v.period ?? '') };
}

export function correctionValue(field: FieldResult, edit: Record<string, string>): unknown {
  if (field.kind === 'integer') return /^\d+$/.test(edit.value.trim()) ? Number(edit.value.trim()) : edit.value.trim();
  if (field.kind === 'quantity') return { amount: edit.amount.trim(), unit: edit.unit, ...(field.name === 'capacity' && edit.period ? { period: edit.period } : {}) };
  if (field.kind === 'pricing') return { type: 'fixed', unit_price: { amount: edit.amount.trim(), currency: edit.currency.trim().toUpperCase() }, per_unit: edit.unit };
  if (field.kind === 'incoterm') return { code: edit.code, place: edit.place.trim() || null };
  return field.kind === 'currency' ? edit.value.trim().toUpperCase() : edit.value.trim();
}

export function CorrectionEditor({ field, busy, units = [], materialIds = [], suggestion, onApply, onCancel }: { field: FieldResult; busy: boolean; units?: string[]; materialIds?: string[]; suggestion?: unknown; onApply: (value: unknown) => void; onCancel: () => void }) {
  const [edit, setEdit] = useState(() => editDefaults(field, suggestion));
  const set = (name: string, value: string) => setEdit(old => ({ ...old, [name]: value }));
  const input = (name: string, title: string, type = 'text') => <label><span>{title}</span><input type={type} value={edit[name] ?? ''} onChange={e => set(name, e.target.value)} required={name !== 'place' && name !== 'period'} /></label>;
  const availableUnits = [...new Set([...(units.length ? units : ['kg', 'tonne', 'lb', 'barrel', 'bale', 'unit', 'litre', 'roll']), edit.unit].filter(Boolean))];
  const unit = <label><span>Unit</span><select value={edit.unit} onChange={e => set('unit', e.target.value)}>{availableUnits.map(value => <option key={value} value={value}>{value}</option>)}</select></label>;
  return <form className="demo-editor" onSubmit={e => { e.preventDefault(); onApply(correctionValue(field, edit)); }}><div className="demo-editor-grid">
    {field.kind === 'quantity' ? <>{input('amount', 'Amount')}{unit}{field.name === 'capacity' && input('period', 'Period, if stated')}</> :
      field.kind === 'pricing' ? <>{input('amount', 'Fixed unit price')}{input('currency', 'Currency')}{unit}</> :
        field.kind === 'incoterm' ? <><label><span>Incoterm</span><select value={edit.code} onChange={e => set('code', e.target.value)}>{['EXW', 'FCA', 'FAS', 'FOB', 'CFR', 'CIF', 'CPT', 'CIP', 'DAP', 'DPU', 'DDP'].map(x => <option key={x}>{x}</option>)}</select></label>{input('place', 'Named place, if stated')}</> :
        field.name === 'material' && materialIds.length ? <label><span>Covered material ID</span><input list="demo-covered-materials" required value={edit.value} onChange={e => set('value', e.target.value)} /><datalist id="demo-covered-materials">{materialIds.map(id => <option key={id} value={id} />)}</datalist></label> :
          input('value', field.kind === 'date' ? 'Date' : field.kind === 'currency' ? 'Currency' : 'Correct value', field.kind === 'date' ? 'date' : 'text')}
  </div>{needsReviewerInput(field) && suggestion !== undefined && <p className="demo-alert">Suggested synthetic fixture value. Check the downloaded document before applying this reviewer correction; it is not extracted evidence.</p>}<p>Reviewer entries stay separate from document evidence. The service checks the value again before acceptance.</p><div className="demo-actions"><button className="demo-button" disabled={busy}>Apply correction</button><button className="demo-link" type="button" onClick={onCancel}>Cancel</button></div></form>;
}

function SourceBadge({ offer, cached }: { offer: OfferView; cached: boolean }) {
  const real = offer.extractor === 'gemini' && offer.extractor_detail?.call === 'real';
  return <span className="demo-badge">{real ? cached ? 'Cached real Gemini extraction' : 'Real Gemini extraction' : offer.extractor === 'rules' ? 'Rule-based extraction' : 'Extraction source unavailable'}</span>;
}

function OfferReview({ offer, company, fixtures, busy, cached, onCorrect, onSave }: { offer: OfferView; company?: CompanyProfile; fixtures: DemoFixture[]; busy: boolean; cached: boolean; onCorrect: (field: string, value: unknown) => void; onSave: () => void }) {
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
      {editing === name && <CorrectionEditor key={`${name}-${offer.version}`} field={field} busy={busy} units={company?.materials.map(item => item.quantity_unit)} materialIds={company?.quote_coverage.materials} suggestion={reviewSuggestionFor(offer, fixtures, name)} onApply={value => { onCorrect(name, value); setEditing(null); }} onCancel={() => setEditing(null)} />}
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
  const stockCoversNeed = /^0(?:\.0+)?$/.test(requiredPurchase(plan));
  return <section className="demo-results" aria-label="Comparison result"><span className="demo-kicker">04 / Comparison</span><h2>{stockCoversNeed ? 'Stock covers this need' : selected ? `${selected.supplier} is feasible` : 'No feasible quote for these inputs'}</h2><p>{plan.explanation}</p>
    <div className="demo-result-numbers"><div><span>Purchase needed after stock</span><strong>{requiredPurchase(plan)} {planUnit(plan)}</strong></div><div><span>Selected total</span><strong>{plan.total_cost == null ? 'No selection' : `${plan.total_cost} ${plan.reporting_currency}`}</strong></div></div>
    {plan.alternatives.length > 0 && <div className="demo-table-wrap"><table><thead><tr><th>Supplier</th><th>Arrival</th><th>Unit price</th><th>Total</th><th>Decision</th></tr></thead><tbody>{plan.alternatives.map(a => <tr key={a.offer_id}><td>{a.supplier}<small>{offers.find(o => o.offer_id === a.offer_id)?.document.filename}</small></td><td>{a.arrival_date ?? 'Unknown'}</td><td>{(a.unit_price_per_unit ?? a.unit_price_per_kg) == null ? 'Unavailable' : `${a.unit_price_per_unit ?? a.unit_price_per_kg} ${plan.reporting_currency}/${planUnit(plan)}`}</td><td>{a.total_cost == null ? 'Unavailable' : `${a.total_cost} ${plan.reporting_currency}`}</td><td>{a.feasible ? a.offer_id === plan.selected_offer_id ? 'Selected' : 'Feasible' : a.reasons.map(reasonText).join('; ') || 'Infeasible'}</td></tr>)}</tbody></table></div>}
    <p className="demo-muted">Total includes your extra cost per {planUnit(plan)}. It is not proof of full freight, tariffs or landed cost. Currency rates are synthetic assumptions in the export. Forecast action: {plan.forecast_action.toLowerCase()}. No customer savings are claimed.</p>
  </section>;
}

function PlanSnapshot({ title, plan, offers }: { title: string; plan: PlanView; offers: OfferView[] }) {
  const choice = plan.alternatives.find(a => a.offer_id === plan.selected_offer_id);
  const stock = /^0(?:\.0+)?$/.test(requiredPurchase(plan));
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
  const [companies, setCompanies] = useState<CompanyProfile[]>([]);
  const [companyError, setCompanyError] = useState('');
  const [newsError, setNewsError] = useState('');
  const [newsBusy, setNewsBusy] = useState(false);
  const [workspaceTab, setWorkspaceTab] = useState<'company' | 'news' | 'outbox'>('company');
  const [highlightFields, setHighlightFields] = useState<string[]>([]);
  const [operatorOpen, setOperatorOpen] = useState(false);
  const operatorDockRef = useRef<HTMLElement | null>(null);
  const operatorToggleRef = useRef<HTMLButtonElement | null>(null);
  const [fixtureId, setFixtureId] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [mode, setMode] = useState<'gemini' | 'rules'>('gemini');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [selected, setSelected] = useState<string[]>(() => initialState.session ? canonicalBuyerState(initialState.session).selected : []);
  const [inputs, setInputs] = useState(() => initialState.session ? canonicalBuyerState(initialState.session).inputs : defaultInputs());
  const [selectedMaterialId, setSelectedMaterialId] = useState(() => initialState.session ? canonicalBuyerState(initialState.session).materialId : '');
  const [delayOfferId, setDelayOfferId] = useState(() => initialState.session ? canonicalBuyerState(initialState.session).delayControls.offerId : '');
  const [delayDays, setDelayDays] = useState(() => initialState.session ? canonicalBuyerState(initialState.session).delayControls.days : '0');
  const epochRef = useRef(initialState.epoch);
  const actionRef = useRef(0);
  const newsRequestRef = useRef(0);
  const draftSaveRef = useRef<{ epoch: number; promise: Promise<DemoSession> } | null>(null);
  const newsAutoRecoveryUsedRef = useRef(false);
  const selectedMaterialRef = useRef(selectedMaterialId);
  const materialScopeRef = useRef(initialState.session ? `${initialState.session.session_id}:${initialState.session.company?.company_id ?? ''}:${initialState.session.context_version ?? ''}` : '');
  const company = state.session?.company;
  const visibleOffers = offersForMaterial(state.offers, fixtures, company, selectedMaterialId);
  const offer = visibleOffers.find(o => o.offer_id === state.focusedId) ?? visibleOffers.at(-1) ?? null;
  const accepted = visibleOffers.filter(o => acceptedStatus(o.status));
  const chosen = accepted.filter(o => selected.includes(o.offer_id));
  const supportedFixtures = fixturesForMaterial(fixtures, company, selectedMaterialId);
  const selectedFixtureId = supportedFixtures.some(fixture => fixture.id === fixtureId) ? fixtureId : supportedFixtures[0]?.id ?? '';
  const coveredMaterials = company?.materials.filter(item => companyScenarios(company).some(scenario => scenario.material_id === item.material_id)) ?? [];
  const coveredMaterial = !company || coveredMaterials.some(item => item.material_id === selectedMaterialId && item.material_id === inputs.material && item.quantity_unit === inputs.quantity_unit);
  const currentDefaults = company ? materialInputs(company, selectedMaterialId) : null;
  const companyOptions = companies.length ? companies : company ? [company] : [];
  useEffect(() => { if (operatorOpen) operatorDockRef.current?.focus(); }, [operatorOpen]);
  function closeOperator() { setOperatorOpen(false); operatorToggleRef.current?.focus(); }
  function operatorKeys(event: KeyboardEvent<HTMLElement>) {
    if (!operatorOpen) return;
    if (event.key === 'Escape') { closeOperator(); return; }
    if (event.key !== 'Tab') return;
    const focusable = [...(operatorDockRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href]') ?? [])].filter(item => item.offsetParent !== null);
    if (!focusable.length) { event.preventDefault(); return; }
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (event.shiftKey && (document.activeElement === first || document.activeElement === operatorDockRef.current)) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }

  function restoreSession(session: DemoSession, epoch: number) {
    const scope = `${session.session_id}:${session.company?.company_id ?? ''}:${session.context_version ?? ''}`;
    const buyer = canonicalBuyerState(session, scope === materialScopeRef.current ? selectedMaterialRef.current : undefined);
    materialScopeRef.current = scope; selectedMaterialRef.current = buyer.materialId;
    dispatch({ type: 'session', session, epoch });
    setSelectedMaterialId(buyer.materialId); setSelected(buyer.selected); setInputs(buyer.inputs);
    setDelayOfferId(buyer.delayControls.offerId);
    setDelayDays(buyer.delayControls.days);
  }
  function restoreOperatorSession(session: DemoSession, action?: OperatorTurn['action_result']) {
    const changed = workspaceChanged(state.session, session);
    actionRef.current += 1;
    restoreSession(session, epochRef.current);
    setHighlightFields(action?.fields_changed ?? []);
    if (changed) {
      // The operator remounts on a session change, so its local voice notice
      // would disappear. Keep recovery guidance in the persistent page.
      setOperatorOpen(false);
      setError('The session expired or changed. The current workspace was restored. Review the company and buyer inputs, then start conversation again.');
    }
  }
  async function beforeOperatorTurn(): Promise<DemoSession> {
    const current = state.session;
    if (!current || !current.company || current.context_version === undefined) throw new DemoApiError('Open a company session before asking the operator.');
    if (!needsBuyerDraftSync(current, inputs, state.dirty)) return current;
    const epoch = epochRef.current;
    if (draftSaveRef.current?.epoch === epoch) return draftSaveRef.current.promise;
    const buyer_draft: BuyerDraft = { ...inputs };
    const promise = api.buyerDraft(current.csrf_token, buyer_draft, current.context_version).then(session => {
      if (epoch !== epochRef.current) throw new DemoApiError('Buyer inputs changed while the draft was saving. Ask again with the current inputs.');
      if (workspaceChanged(current, session)) {
        restoreOperatorSession(session);
        throw new DemoApiError('The workspace changed while buyer inputs were saving. Review the restored session and ask again.');
      }
      restoreSession(session, epoch);
      return session;
    }).finally(() => { if (draftSaveRef.current?.epoch === epoch) draftSaveRef.current = null; });
    draftSaveRef.current = { epoch, promise };
    return promise;
  }
  function navigateOperator(view: NonNullable<NonNullable<OperatorTurn['action_result']>['view']>) {
    if (view === 'news' || view === 'company' || view === 'outbox') setWorkspaceTab(view);
    if (view === 'procurement') document.getElementById('demo-procurement')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  function clearUnknownOperatorSession() {
    epochRef.current += 1; actionRef.current += 1; newsRequestRef.current += 1;
    dispatch({ type: 'company-start' });
    selectedMaterialRef.current = ''; materialScopeRef.current = ''; setSelectedMaterialId('');
    setSelected([]); setInputs(defaultInputs()); setFile(null); setDelayOfferId(''); setDelayDays('0');
    setBusy(''); setError('The current workspace could not be confirmed. Refresh session before continuing.');
  }

  async function recoverExpiredNews(epoch: number, requestId: number) {
    if (epoch !== epochRef.current || requestId !== newsRequestRef.current) return;
    const attemptRecovery = !newsAutoRecoveryUsedRef.current;
    newsAutoRecoveryUsedRef.current = true;
    epochRef.current += 1; actionRef.current += 1; newsRequestRef.current += 1;
    dispatch({ type: 'company-start' });
    selectedMaterialRef.current = ''; materialScopeRef.current = ''; setSelectedMaterialId('');
    setSelected([]); setInputs(defaultInputs()); setFile(null); setDelayOfferId(''); setDelayDays('0');
    setNewsBusy(false); setNewsError('');
    setError('The session expired. The previous company, news, comparison and review are cleared. Refresh session to continue.');
    if (!attemptRecovery) { setBusy(''); return; }
    const recoveryEpoch = epochRef.current;
    setBusy('session');
    try {
      const session = await api.session();
      if (recoveryEpoch !== epochRef.current) return;
      restoreSession(session, recoveryEpoch);
      setError('The session expired and a new canonical workspace was opened. Review the company and buyer inputs before continuing.');
    } catch {
      if (recoveryEpoch === epochRef.current) setError('The session expired and the current workspace could not be confirmed. Refresh session to continue.');
    } finally { if (recoveryEpoch === epochRef.current) setBusy(''); }
  }

  useEffect(() => {
    let cancelled = false;
    const epoch = epochRef.current;
    if (!initialState.session) api.session().then(session => { if (!cancelled && epoch === epochRef.current) restoreSession(session, epoch); }).catch(e => { if (!cancelled && epoch === epochRef.current) setError(message(e)); });
    api.fixtures().then(list => { if (!cancelled) { setFixtures(list); setFixtureId(list[0]?.id ?? ''); } }).catch(e => { if (!cancelled) setError(message(e)); });
    api.companies().then(list => { if (!cancelled) setCompanies(list); }).catch(e => { if (!cancelled) setCompanyError(message(e)); });
    return () => { cancelled = true; };
  }, [initialState.session]);

  useEffect(() => {
    if (!state.session?.company || state.session.context_version === undefined || state.news) return;
    let cancelled = false;
    const epoch = state.epoch;
    const sessionId = state.session.session_id;
    const requestId = ++newsRequestRef.current;
    setNewsBusy(true); setNewsError('');
    const timer = setTimeout(() => {
      api.news().then(news => { if (!cancelled && requestId === newsRequestRef.current) { setNewsBusy(false); dispatch({ type: 'news', news, sessionId, epoch }); } })
        .catch(e => { if (!cancelled && requestId === newsRequestRef.current && epoch === epochRef.current) {
          if (isSessionExpired(e)) void recoverExpiredNews(epoch, requestId);
          else setNewsError(message(e));
        } })
        .finally(() => { if (!cancelled && requestId === newsRequestRef.current) setNewsBusy(false); });
    }, 150);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [state.session?.session_id, state.session?.company?.company_id, state.session?.context_version, state.epoch]);

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
  function selectMaterial(materialId: string) {
    if (!company || materialId === selectedMaterialRef.current || !companyScenarios(company).some(item => item.material_id === materialId)) return;
    const next = materialInputs(company, materialId);
    if (!next) return;
    invalidate();
    selectedMaterialRef.current = materialId;
    setSelectedMaterialId(materialId); setInputs(next); setSelected([]); setFile(null);
    setFixtureId(companyScenarios(company).find(item => item.material_id === materialId)?.fixture_ids[0] ?? '');
    setDelayOfferId(''); setDelayDays('0');
  }
  function editScenario() {
    epochRef.current += 1; actionRef.current += 1;
    dispatch({ type: 'scenario-edit' }); setBusy(''); setError('');
  }
  async function switchCompany(companyId: string) {
    if (!state.session?.company || state.session.context_version === undefined || companyId === state.session.company.company_id) return;
    const prior = state.session;
    const contextVersion = state.session.context_version;
    epochRef.current += 1; actionRef.current += 1; newsRequestRef.current += 1;
    dispatch({ type: 'company-start' });
    selectedMaterialRef.current = ''; materialScopeRef.current = ''; setSelectedMaterialId('');
    setSelected([]); setInputs(defaultInputs()); setFile(null); setDelayOfferId(''); setDelayDays('0');
    setNewsBusy(false); setNewsError(''); setError(''); setBusy('company'); setWorkspaceTab('company');
    const epoch = epochRef.current;
    try {
      const session = await selectCompanyWithRecovery(async () => {
        const selected = await api.company(prior.csrf_token, companyId, contextVersion);
        if (selected.company?.company_id !== companyId || selected.context_version === undefined) throw new DemoApiError('The selected company did not match the returned session.');
        return selected;
      }, () => api.session());
      if (epoch !== epochRef.current) return;
      restoreSession(session, epoch);
    } catch (e) {
      if (epoch === epochRef.current) setError(`Could not confirm the current company. The workspace is cleared until the session can be reloaded. ${message(e)}`);
    } finally { if (epoch === epochRef.current) setBusy(''); }
  }
  async function refreshNews() {
    if (!state.session?.company || state.session.context_version === undefined || newsBusy) return;
    const epoch = epochRef.current, sessionId = state.session.session_id;
    const requestId = ++newsRequestRef.current;
    setNewsBusy(true); setNewsError('');
    try {
      const news = await api.refreshNews(state.session.csrf_token, state.session.context_version);
      if (requestId === newsRequestRef.current && epoch === epochRef.current) dispatch({ type: 'news', news, sessionId, epoch });
    } catch (e) { if (requestId === newsRequestRef.current && epoch === epochRef.current) {
      if (isSessionExpired(e)) void recoverExpiredNews(epoch, requestId);
      else setNewsError(message(e));
    } }
    finally { if (requestId === newsRequestRef.current) setNewsBusy(false); }
  }

  function extract(event: FormEvent) {
    event.preventDefault();
    if (!state.session) return;
    if (!file && !selectedFixtureId) { setError('Choose a synthetic quote or upload one of the listed documents.'); return; }
    if (company && (!supportedFixtures.length || (file && !supportedFixtures.some(fixture => fixture.filename === file.name)))) { setError('This company has no matching quote fixture for that document.'); return; }
    invalidate();
    run('extract', () => file ? api.upload(state.session!.csrf_token, file, mode, state.session!.context_version) : api.extract(state.session!.csrf_token, { fixture_id: selectedFixtureId, mode }, state.session!.context_version), (result, epoch) => {
      dispatch({ type: 'offer', offer: result.offer, epoch, cached: result.served_from_cache }); setFile(null);
    });
  }
  function correct(name: string, value: unknown) {
    if (!offer || !state.session) return;
    const current = offer;
    invalidate();
    run('correct', () => api.correct(state.session!.csrf_token, current.offer_id, name, value, current.version, state.session!.context_version), (result, epoch) => {
      dispatch({ type: 'offer', offer: result.offer, epoch }); setSelected(ids => ids.filter(id => id !== current.offer_id));
    });
  }
  function save() {
    if (!offer || !state.session) return;
    const current = offer;
    invalidate();
    run('save', () => api.save(state.session!.csrf_token, current.offer_id, current.version, state.session!.context_version), (result, epoch) => {
      dispatch({ type: 'offer', offer: result.offer, epoch }); setSelected(ids => ids.includes(current.offer_id) ? ids : [...ids, current.offer_id]);
      if (!inputs.material) setInputs(old => ({ ...old, material: String(current.items[0]?.material?.value ?? '') }));
    });
  }
  function compare(event: FormEvent) {
    event.preventDefault();
    if (!state.session || !chosen.length || !coveredMaterial) return;
    const request: PlanRequest = { ...inputs, offer_ids: chosen.map(o => o.offer_id), expected_versions: Object.fromEntries(chosen.map(o => [o.offer_id, o.version])) };
    invalidate();
    run('plan', () => api.plan(state.session!.csrf_token, request, state.session!.context_version), (plan, epoch) => {
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
    run('recovery', () => api.recovery(state.session!.csrf_token, { baseline_plan_id: baseline.plan_id, expected_versions, delay_days_by_offer: days }, state.session!.context_version), (recovery, epoch) => {
      dispatch({ type: 'recovery', recovery, epoch });
      const controls = delayControlsForRecovery(recovery, selectedDelayOffer);
      setDelayOfferId(controls.offerId); setDelayDays(controls.days);
    });
  }
  function prepareRfq() {
    if (state.dirty || !state.session || !state.plan || !state.reviewed || !state.plan.selected_offer_id || /^0(?:\.0+)?$/.test(requiredPurchase(state.plan))) return;
    const plan = state.plan;
    run('rfq', () => api.rfq(state.session!.csrf_token, { plan_id: plan.plan_id, expected_versions: plan.offer_versions, reviewed: true }, state.session!.context_version), (rfq, epoch) => dispatch({ type: 'rfq', rfq, epoch }));
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
    dispatch({ type: 'reset-start' }); epochRef.current += 1; actionRef.current += 1; newsRequestRef.current += 1;
    selectedMaterialRef.current = ''; materialScopeRef.current = ''; setSelectedMaterialId('');
    setBusy('reset'); setError(''); setSelected([]); setFile(null); setInputs(defaultInputs()); setDelayOfferId(''); setDelayDays('0');
    const epoch = epochRef.current;
    api.reset(csrf, state.session.context_version).then(session => { if (epoch === epochRef.current) restoreSession(session, epoch); })
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

  return <div className="demo-page"><header className="demo-masthead"><a className="demo-brand" href="/demo">CLOSED<span>SOURCE</span><sup> / DEMO</sup></a><span>COMPANY → NEWS → REVIEWED ACTIONS</span></header><main>
    <section className="demo-hero"><div><p className="demo-kicker">Company and supply risk workspace</p><h1>Supply risks.<br /><em>Reviewed actions.</em></h1></div><p>Turn supply risks into reviewed actions. Switch between fictional companies, inspect dated public news, review supplier quotes where sample coverage exists, and ask the operator to prepare changes for your approval.</p></section>
    <div className="demo-boundary"><strong>Demo boundaries</strong><span>Company profiles, quotes and buyer inputs are synthetic; news is observed public data. Your work stays in a temporary session and may disappear when the service restarts. No order or supplier message is sent.</span></div>
    {company && <section className="demo-company-shell"><div className="demo-company-picker"><label><span>Company workspace</span><select value={company.company_id} disabled={busy === 'company'} onChange={event => switchCompany(event.target.value)}>{companyOptions.map(option => <option key={option.company_id} value={option.company_id}>{option.name} · {option.sector}</option>)}</select></label><p>Fictional company profile · changes kept separately for this visit</p></div>
      {companyError && <p className="demo-alert" role="alert">Company catalogue unavailable: {companyError}. The current workspace remains visible.</p>}
      <div className="demo-material-picker"><label><span>Procurement material</span><select value={selectedMaterialId} disabled={!state.session || !coveredMaterials.length} onChange={event => selectMaterial(event.target.value)}>{!coveredMaterials.length && <option value="">No runnable material</option>}{coveredMaterials.map(item => <option key={item.material_id} value={item.material_id}>{item.name} · {item.quantity_unit}</option>)}</select></label><p>{currentDefaults ? `Synthetic scenario: ${currentDefaults.quantity} ${currentDefaults.quantity_unit} needed, ${currentDefaults.current_stock} ${currentDefaults.quantity_unit} in stock · ${currentDefaults.reporting_currency} ${currentDefaults.budget} budget. Select this material's quotes below.` : 'Choose a covered material to review its synthetic quote scenario.'}</p></div>
      <div className="demo-workspace-tabs" role="group" aria-label="Company sections"><button type="button" className={workspaceTab === 'company' ? 'active' : ''} aria-pressed={workspaceTab === 'company'} onClick={() => setWorkspaceTab('company')}>Company overview</button><button type="button" className={workspaceTab === 'news' ? 'active' : ''} aria-pressed={workspaceTab === 'news'} onClick={() => setWorkspaceTab('news')}>News workspace</button><button type="button" className={workspaceTab === 'outbox' ? 'active' : ''} aria-pressed={workspaceTab === 'outbox'} onClick={() => setWorkspaceTab('outbox')}>Simulated outbox</button></div>
      {workspaceTab === 'company' ? <CompanyOverview company={company} /> : workspaceTab === 'news' ? <NewsWorkspace key={company.company_id} company={company} view={state.news} loading={newsBusy} error={newsError} onRefresh={refreshNews} /> : <OutboxWorkspace company={company} messages={state.session?.simulated_outbox ?? []} />}
    </section>}
    {error && <div className="demo-error" role="alert"><strong>Workspace update.</strong> {error} <button type="button" className="demo-link" disabled={!!busy} onClick={() => run('refresh', () => api.session(), (session, epoch) => { newsAutoRecoveryUsedRef.current = false; restoreSession(session, epoch); })}>Refresh session</button></div>}
    <section className="demo-start"><div className="demo-heading"><div><span className="demo-kicker">01 / Choose a document</span><h2>Start with a supplier quote</h2></div><span className="demo-session">{state.session ? `Session expires ${new Date(state.session.expires_at).toLocaleString()}` : error ? 'Session unavailable' : busy === 'company' ? 'Switching company…' : 'Opening session…'}</span></div>
      <form onSubmit={extract} className="demo-extract-form"><label><span>Sample quote</span><select value={selectedFixtureId} disabled={!!company && !supportedFixtures.length} onChange={e => { setFixtureId(e.target.value); setFile(null); }}><option value="">Choose a document</option>{supportedFixtures.map(f => <option key={f.id} value={f.id}>{f.name}</option>)}</select></label>
        <label><span>Or upload one of these sample files</span><input type="file" accept=".pdf,.csv,application/pdf,text/csv" disabled={!!company && !supportedFixtures.length} onChange={e => setFile(e.target.files?.[0] ?? null)} /><small>Only the exact listed sample documents are accepted. Maximum 500 KB.</small></label>
        <label><span>Extraction method</span><select value={mode} onChange={e => setMode(e.target.value as 'gemini' | 'rules')}><option value="gemini">Gemini · live extraction</option><option value="rules">Rules · comparison fallback</option></select></label>
        <button className="demo-button" disabled={!state.session || !!busy || (!!company && !supportedFixtures.length)}>{busy === 'extract' ? 'Reading quote…' : 'Read quote'}</button></form>
      {company && !supportedFixtures.length && <p className="demo-alert">This company has no runnable quote fixture. Explore its fictional operations and observed news; quote comparison is unavailable here.</p>}
      {supportedFixtures.length > 0 && <div className="demo-fixtures"><span>Sample documents</span>{supportedFixtures.map(f => <a key={f.id} href={f.download_url} download>{f.filename} ↗</a>)}</div>}
      {state.session && !state.session.provider.available && <p className="demo-provider">Gemini is currently unavailable. Rule extraction can still be tried and will be labelled separately.</p>}
    </section>
    {visibleOffers.length > 0 && <div className="demo-offer-tabs" role="group" aria-label="Quotes for selected material">{visibleOffers.map(o => <button key={o.offer_id} type="button" className={o.offer_id === offer?.offer_id ? 'active' : ''} onClick={() => dispatch({ type: 'focus', offerId: o.offer_id })}>{o.document.filename}<small>{words(o.status)}</small></button>)}</div>}
    {offer && <><p className="demo-source-line">{offer.extractor === 'gemini' && offer.extractor_detail?.call === 'real' ? offer.served_from_cache ? 'This is a cached response from an earlier real Gemini extraction.' : 'Gemini processed this synthetic document.' : offer.extractor === 'rules' ? 'Rule extraction is shown separately from Gemini.' : 'The extraction source is unavailable.'}</p><OfferReview key={offer.offer_id} offer={offer} company={company} fixtures={supportedFixtures} busy={!!busy} cached={!!offer.served_from_cache} onCorrect={correct} onSave={save} /></>}
    <section id="demo-procurement" className="demo-planning"><div className="demo-heading"><div><span className="demo-kicker">03 / Test a purchase</span><h2>What can arrive in time?</h2></div><span className="demo-session">Single-supplier options · deterministic comparison</span></div>
      <p className="demo-muted">Choose accepted quotes. Enter the quantity, date, budget and any extra cost you want included. This is a single-order sourcing comparison; it checks each supplier independently.</p>
      <div className="demo-accepted"><strong>Accepted quotes</strong>{accepted.length ? accepted.map(o => <label key={o.offer_id}><input type="checkbox" checked={selected.includes(o.offer_id)} onChange={e => { invalidate(); setSelected(ids => e.target.checked ? [...ids, o.offer_id] : ids.filter(id => id !== o.offer_id)); }} />{o.document.filename}<small>v{o.version}</small></label>) : <p>Accept at least one reviewed quote to run a comparison.</p>}</div>
      <div className="demo-sample"><button type="button" className="demo-link" disabled={!state.session || (!!company && !currentDefaults)} onClick={() => { if (!state.session) return; invalidate(); setInputs(currentDefaults ?? sampleBuyerInputs()); }}>{company ? 'Use company buyer inputs' : 'Use sample buyer inputs'}</button><span>{company ? currentDefaults ? 'Dated synthetic buyer assumptions for the selected material. Edit any input below; quantities use its listed unit.' : 'No buyer defaults or runnable quote scenario are available for this material.' : 'Optional fictional scenario: 10,000 kg copper need, 2,000 kg in stock, needed by 5 Nov 2026, USD 100,000 budget. The sample decision date is 14 Oct 2026; quote acceptance still checks current validity. Edit any input below.'}</span></div>
      <form onSubmit={compare} className="demo-plan-form"><div className="demo-plan-grid">{company ? <label className={highlightFields.includes('material') ? 'demo-agent-filled' : ''}><span>Material</span><select required disabled={!state.session || !coveredMaterials.length} value={selectedMaterialId} onChange={e => selectMaterial(e.target.value)}>{coveredMaterials.map(item => <option key={item.material_id} value={item.material_id}>{item.name} · {item.quantity_unit}</option>)}</select></label> : <label><span>Material</span><input required disabled={!state.session} value={inputs.material} onChange={e => { invalidate(); setInputs(old => ({ ...old, material: e.target.value })); }} /></label>}{([['quantity', `Needed quantity (${inputs.quantity_unit})`], ['current_stock', `Current stock (${inputs.quantity_unit})`], ['need_by', 'Needed by'], ['decision_date', 'Decision date'], ['reporting_currency', 'Reporting currency'], ['budget', 'Maximum budget'], ['additional_cost_per_unit', `Extra cost per ${inputs.quantity_unit}`]] as const).map(([name, title]) => <label key={name} className={highlightFields.includes(name) ? 'demo-agent-filled' : ''}><span>{title}</span><input type={name === 'need_by' || name === 'decision_date' ? 'date' : 'text'} required disabled={!state.session} value={inputs[name]} onChange={e => { if (!state.session) return; setHighlightFields([]); invalidate(); setInputs(old => ({ ...old, [name]: e.target.value })); }} inputMode={['quantity', 'current_stock', 'budget', 'additional_cost_per_unit'].includes(name) ? 'decimal' : undefined} /></label>)}</div>
        <p className="demo-assumption">Extra cost is your synthetic per-{inputs.quantity_unit} assumption; total cost includes it but does not prove full freight or tariffs. Dated synthetic FX rates, when needed, are recorded in the comparison and JSON export. Budget and price are decimal amounts in the reporting currency. Quantities use the selected material unit; no unit conversions are inferred.</p>{company && inputs.material && !coveredMaterial && <p className="demo-alert">{inputs.material} has no matching runnable quote coverage for {company.name}. {company.quote_coverage.note}</p>}<button className="demo-button" disabled={!state.session || !chosen.length || !coveredMaterial || !!busy}>{busy === 'plan' ? 'Comparing…' : 'Run comparison'}</button></form>
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
      <label className="demo-review-check"><input type="checkbox" checked={state.reviewed} disabled={state.dirty || !state.plan?.selected_offer_id || /^0(?:\.0+)?$/.test(state.plan ? requiredPurchase(state.plan) : '0')} onChange={e => dispatch({ type: 'reviewed', value: e.target.checked })} />I reviewed this plan and understand the RFQ is an unsent draft</label>
      <div className="demo-actions"><button type="button" className="demo-button" disabled={state.dirty || !state.reviewed || !state.plan?.selected_offer_id || !!busy} onClick={prepareRfq}>{busy === 'rfq' ? 'Preparing draft…' : 'Prepare unsent RFQ'}</button></div>
      {state.rfq && <div className="demo-draft"><p><strong>Draft for {state.rfq.supplier}</strong> · {state.rfq.status.replace('_', ' ')} · {(state.rfq.quantity ?? state.rfq.quantity_kg) && `${state.rfq.quantity ?? state.rfq.quantity_kg} ${state.rfq.quantity_unit ?? 'kg'} · `}current reviewed comparison</p><pre>{state.rfq.text}</pre><button type="button" className="demo-button" disabled={state.dirty || !!busy} onClick={downloadRfq}>Download RFQ draft</button></div>}
    </section>
    <section className="demo-end"><div><span className="demo-kicker">Your session</span><h2>Keep the evidence.</h2><p>Download the quote, corrections, assumptions and comparison as JSON. Starting over clears this session only.</p>{state.dirty && <p>Run a new comparison before exporting the report for these inputs.</p>}</div><div className="demo-end-actions"><button className="demo-button" type="button" disabled={!state.session || state.dirty || !!busy} onClick={exportReport}>Download JSON report</button><button className="demo-link" type="button" disabled={!state.session || busy === 'reset'} onClick={reset}>Start a new session</button></div></section>
  </main>{company && state.session && state.session.context_version !== undefined && <><button ref={operatorToggleRef} type="button" className="demo-operator-toggle" aria-expanded={operatorOpen} aria-controls="demo-operator-dock" onClick={() => operatorOpen ? closeOperator() : setOperatorOpen(true)}>{operatorOpen ? 'Close operator' : 'Open operator chat'}</button>{operatorOpen && <button type="button" className="demo-operator-scrim" aria-label="Close operator chat" onClick={closeOperator} />}<aside ref={operatorDockRef} id="demo-operator-dock" className={`demo-operator-dock ${operatorOpen ? 'is-open' : ''}`} role={operatorOpen ? 'dialog' : 'complementary'} aria-modal={operatorOpen ? true : undefined} aria-label="Operator chat" tabIndex={-1} onKeyDown={operatorKeys}><OperatorPanel key={`${state.session.session_id}:${company.company_id}:${state.session.context_version}`} scope={{ sessionId: state.session.session_id, companyId: company.company_id, contextVersion: state.session.context_version, epoch: state.epoch }} csrf={state.session.csrf_token} news={state.news} offers={state.offers} plan={state.plan} materials={company.materials} dirty={state.dirty} blocked={!!busy} beforeTurn={beforeOperatorTurn} onSession={restoreOperatorSession} onNavigate={navigateOperator} onUnknown={clearUnknownOperatorSession} /></aside></> }<footer className="demo-footer"><span>Closed Source / AI Builder Cup</span><span>Synthetic decisions · No forecast recommendation · No customer savings claim</span></footer></div>;
}
