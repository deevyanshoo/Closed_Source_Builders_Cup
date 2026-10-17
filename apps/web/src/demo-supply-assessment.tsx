import type { DemoSession, PlanView, PreparedDraft, SupplyCoverage, createDemoApi } from './demo-api';

function displayCoverageDecimal(raw: string | null): string {
  if (raw === null) return 'unknown';
  if (!/^(?:0|[1-9]\d*)(?:\.\d+)?$/.test(raw)) return 'unknown';
  const [whole, fraction = ''] = raw.split('.');
  const cents = BigInt(whole) * 100n + BigInt((fraction + '00').slice(0, 2)) + (Number(fraction[2] ?? '0') >= 5 ? 1n : 0n);
  if (cents === 0n && /[1-9]/.test(raw)) return '<0.01';
  const roundedWhole = (cents / 100n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  const decimal = (cents % 100n).toString().padStart(2, '0').replace(/0+$/, '');
  const concise = decimal ? `${roundedWhole}.${decimal}` : roundedWhole;
  return /[1-9]/.test(fraction.slice(2)) ? `about ${concise}` : concise;
}

export function SupplyAssessment({ coverage, compact = false }: { coverage?: SupplyCoverage | null; compact?: boolean }) {
  const known = coverage?.status !== 'unknown' && !!coverage;
  const state = !known ? 'unknown' : coverage.status;
  const deadline = !known || coverage.order_deadline_met === null ? 'Check order deadline' : coverage.order_deadline_met ? 'Order deadline met' : 'Order deadline missed';
  const supply = state === 'gap' ? 'Production supply gap' : state === 'covered' && coverage?.continuous_supply_covered === true ? 'Production supply covered' : 'Check supply coverage';
  return <div className={`demo-supply-assessment demo-supply-assessment--${state}`} aria-label="Supply coverage assessment" data-coverage-status={state}>
    <div className="demo-supply-assessment-head"><span>{deadline}</span><strong>{supply}</strong></div>
    {state === 'gap' && coverage && <p>Uncovered: {displayCoverageDecimal(coverage.uncovered_days)} days · {displayCoverageDecimal(coverage.uncovered_quantity)} {coverage.quantity_unit}{coverage.bridge_needed_by && ` · bridge needed by ${coverage.bridge_needed_by}`}. {coverage.selected_arrival_date && `Selected arrival ${coverage.selected_arrival_date}.`}</p>}
    {state === 'unknown' && <p>{coverage?.missing_inputs?.length ? `Check ${coverage.missing_inputs.join(', ')} before relying on production cover.` : 'This saved comparison has no current production coverage assessment.'}</p>}
    {!compact && coverage && <p className="demo-supply-basis">Inventory as of {coverage.inventory_as_of ?? 'unknown'} · Consumption as of {coverage.consumption_as_of ?? 'unknown'} · Decision {coverage.decision_date} to {coverage.horizon_end}. Constant daily consumption is a planning assumption.</p>}
  </div>;
}

type BridgeApi = Pick<ReturnType<typeof createDemoApi>, 'operatorTurn' | 'session'>;

function sameVersions(a: Record<string, number> | undefined, b: Record<string, number> | undefined): boolean {
  return !!a && !!b && Object.keys(a).length === Object.keys(b).length && Object.entries(a).every(([id, version]) => b[id] === version);
}

function positiveQuantity(value: string | null | undefined): boolean {
  return typeof value === 'string' && /^(?:0|[1-9]\d*)(?:\.\d+)?$/.test(value) && /[1-9]/.test(value);
}

export function bridgeEligibility(session: DemoSession | null, plan: PlanView | null, coverage: SupplyCoverage | null | undefined): boolean {
  if (!session?.company || session.context_version === undefined || !plan || !coverage || coverage.status !== 'gap' ||
    coverage.company_id !== session.company.company_id || coverage.plan_id !== plan.plan_id || coverage.material_id !== plan.material ||
    coverage.quantity_unit !== plan.quantity_unit || !positiveQuantity(coverage.bridge_quantity) ||
    !/^\d{4}-\d{2}-\d{2}$/.test(coverage.bridge_needed_by ?? '') || coverage.missing_inputs.length > 0 || !plan.selected_offer_id ||
    (session.recovery?.scenario ?? session.plan)?.plan_id !== plan.plan_id) return false;
  const activePlan = session.recovery?.scenario ?? session.plan;
  if (activePlan?.selected_offer_id !== plan.selected_offer_id || activePlan.material !== plan.material ||
    activePlan.quantity_unit !== plan.quantity_unit) return false;
  const selected = session.offers.find(offer => offer.offer_id === plan.selected_offer_id);
  return !!selected && selected.status === 'saved' && selected.version === plan.offer_versions[selected.offer_id] &&
    Object.entries(plan.offer_versions).every(([id, version]) => session.offers.some(offer => offer.offer_id === id && offer.version === version && offer.status === 'saved')) &&
    sameVersions(activePlan.offer_versions, plan.offer_versions) &&
    plan.alternatives.some(item => item.offer_id === selected.offer_id && item.supplier.trim() !== '');
}

export async function prepareBridgeDraft(api: BridgeApi, session: DemoSession, plan: PlanView, onSaved: (session: DemoSession, draft: PreparedDraft) => void): Promise<PreparedDraft> {
  if (!bridgeEligibility(session, plan, plan.supply_coverage)) throw new Error('Select a current accepted supplier for this production gap before preparing a bridge RFQ.');
  const coverage = plan.supply_coverage!;
  const selectedId = plan.selected_offer_id!;
  const selectedSupplier = plan.alternatives.find(item => item.offer_id === selectedId)!.supplier;
  const companyId = session.company!.company_id;
  const contextVersion = session.context_version!;
  const turn = await api.operatorTurn(session.csrf_token,
    `Prepare an unsent earlier-delivery or bridge RFQ for the production supply gap in current reviewed plan ${plan.plan_id}. Ask the selected supplier for new capacity, price and terms. Save the draft; do not send it.`, contextVersion);
  const refreshed = await api.session();
  if (refreshed.session_id !== session.session_id || refreshed.company?.company_id !== companyId ||
    refreshed.context_version !== contextVersion) throw new Error('The workspace changed. Review the current company before preparing another draft.');
  if (!bridgeEligibility(refreshed, plan, coverage)) throw new Error('The selected plan or accepted quote changed. Review the current comparison before drafting.');
  const rfq = refreshed.rfq;
  const draft = refreshed.drafts?.find(item => item.draft_id === rfq?.draft_id);
  if (!rfq || turn.status !== 'completed' || turn.action_result?.tool !== 'prepare_rfq' ||
    turn.company_id !== companyId || turn.context_version !== contextVersion ||
    turn.session?.rfq?.draft_id !== rfq.draft_id || turn.session.rfq?.text !== rfq.text ||
    !turn.workspace?.drafts?.some(item => item.draft_id === rfq.draft_id && item.body === rfq.text) ||
    rfq.bridge !== true || rfq.status !== 'draft_unsent' || rfq.company_id !== companyId || rfq.total_cost !== null ||
    rfq.plan_id !== plan.plan_id || !sameVersions(rfq.offer_versions, plan.offer_versions) ||
    rfq.supplier !== selectedSupplier || rfq.material !== plan.material || rfq.quantity_unit !== plan.quantity_unit ||
    !positiveQuantity(rfq.quantity) || rfq.need_by !== coverage.bridge_needed_by ||
    rfq.arithmetic_bridge_quantity !== coverage.bridge_quantity ||
    !rfq.text.includes(`Supplier: ${selectedSupplier}`) ||
    !rfq.text.includes(`Material: ${plan.material}`) ||
    !rfq.text.includes(`Requested bridge quantity: ${rfq.quantity} ${rfq.quantity_unit}`) ||
    !rfq.text.includes(`Calculated bridge quantity: ${coverage.bridge_quantity} ${coverage.quantity_unit}`) ||
    !rfq.text.includes(`Earlier delivery needed by: ${coverage.bridge_needed_by}`) ||
    !draft || draft.kind !== 'rfq' || draft.status !== 'draft_unsent' ||
    draft.company_id !== companyId || draft.material_id !== plan.material ||
    draft.plan_id !== plan.plan_id || !sameVersions(draft.offer_versions, plan.offer_versions) ||
    draft.selected_offer_id !== selectedId || draft.recipient_label !== selectedSupplier || draft.body !== rfq.text)
    throw new Error('No matching unsent bridge RFQ was saved for this selected plan. Review the operator response.');
  onSaved(refreshed, draft);
  return draft;
}
