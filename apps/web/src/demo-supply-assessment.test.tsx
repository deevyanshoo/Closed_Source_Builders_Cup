import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import type { DemoSession, PlanView, SupplyCoverage } from './demo-api';
import { applyDemoResult, DemoPage, DraftsWorkspace, initialDemoState, PlanResults, RecoveryResults } from './demo-page';
import { WorkspaceOverview } from './zoorq-workspace';
import { SupplyAssessment, bridgeEligibility, prepareBridgeDraft } from './demo-supply-assessment';

const coverage: SupplyCoverage = {
  schema: 1, company_id: 'solara-cables', plan_id: 'p1', material_id: 'copper', quantity_unit: 'kg',
  decision_date: '2026-10-14', horizon_end: '2026-10-30', inventory_as_of: '2026-10-14', consumption_as_of: '2026-10-14',
  stock: '2000', consumption_per_day: '333.33', basis: 'constant_daily_consumption', status: 'gap',
  order_deadline_met: true, continuous_supply_covered: false, stock_cover_days: '6.00006',
  stock_depletion_date: '2026-10-20', selected_arrival_date: '2026-10-24', uncovered_days: '4',
  uncovered_quantity: '1333.30', bridge_quantity: '1333.30', bridge_needed_by: '2026-10-20',
  assumptions: [], missing_inputs: [], evidence_ids: [], alternatives: [],
};
const plan: PlanView = {
  plan_id: 'p1', synthetic: true, decision_date: '2026-10-14', need_by: '2026-10-30', material: 'copper',
  quantity: '8000', current_stock: '2000', required_purchase: '6000', quantity_unit: 'kg', reporting_currency: 'USD',
  assumptions: {}, alternatives: [{ offer_id: 'q1', supplier: 'Swift', feasible: true, reasons: [], arrival_date: '2026-10-24', total_cost: '86000' }],
  selected_offer_id: 'q1', total_cost: '86000', explanation: 'Swift is feasible.', forecast_action: 'ABSTAIN', customer_savings: null, offer_versions: { q1: 1 },
};
const currentOffer = { offer_id: 'q1', version: 1, status: 'saved', document: { document_id: 'd1', filename: 'SYN-offer.pdf', media_type: 'application/pdf', byte_size: 1 }, fields: {}, items: [], review_flags: [], normalized: {}, blocking_reasons: [], extractor: 'rules', extractor_detail: {}, as_of: '2026-10-14', synthetic: true as const };
const currentCompany = { company_id: 'solara-cables', name: 'Solara Cables', sector: 'Manufacturing', size: 'Mid-size', synthetic: true, as_of: '2026-10-14', description: 'Sample', locations: [], reporting_currency: 'USD', materials: [{ material_id: 'copper', name: 'Copper', quantity_unit: 'kg', stock: '2000', consumption_per_day: '333.33', monthly_requirement: '10000', criticality: 'high', keywords: [] }], suppliers: [], orders: [], routes: [], currencies: ['USD'], news_keywords: [], quote_coverage: { materials: ['copper'], fixture_ids: [], note: 'Sample' }, buyer_defaults: null } as never;
const bridgeBody = 'Supplier: Swift\nMaterial: copper\nRequested bridge quantity: 1333.30 kg\nCalculated bridge quantity: 1333.30 kg\nEarlier delivery needed by: 2026-10-20\n';

describe('reviewed supply assessment', () => {
  it('separates a met order deadline from a production gap with dated quantities', () => {
    const html = renderToStaticMarkup(<PlanResults plan={{ ...plan, supply_coverage: coverage }} offers={[]} />);
    expect(html).toContain('Order deadline met');
    expect(html).toContain('Production supply gap');
    expect(html).toContain('4 days');
    expect(html).toContain('1,333.3 kg');
    expect(html).toContain('Inventory as of 2026-10-14');
    expect(html).toContain('Consumption as of 2026-10-14');
    expect(html).not.toContain('Swift is feasible</h2>');
  });

  it('shows covered and unknown as distinct states and never greens an old plan', () => {
    const covered = renderToStaticMarkup(<SupplyAssessment coverage={{ ...coverage, status: 'covered', continuous_supply_covered: true, uncovered_days: '0', uncovered_quantity: '0' }} />);
    expect(covered).toContain('Production supply covered');
    const unknown = renderToStaticMarkup(<SupplyAssessment coverage={{ ...coverage, status: 'unknown', continuous_supply_covered: null, order_deadline_met: null, missing_inputs: ['current accepted quote'] }} />);
    expect(unknown).toContain('Check supply coverage');
    expect(unknown).toContain('current accepted quote');
    const old = renderToStaticMarkup(<PlanResults plan={plan} offers={[]} />);
    expect(old).toContain('Check supply coverage');
    expect(old).not.toContain('Stock covers this need');
  });

  it('shows baseline and scenario production impact independently', () => {
    const recovery = { comparison_id: 'r1', synthetic: true as const, baseline: { ...plan, supply_coverage: { ...coverage, status: 'covered' as const, continuous_supply_covered: true, uncovered_days: '0', uncovered_quantity: '0' } }, scenario: { ...plan, plan_id: 'p2', supply_coverage: { ...coverage, plan_id: 'p2', uncovered_days: '8', uncovered_quantity: '2666.62' } }, baseline_request: {} as never, scenario_request: {} as never, cost_delta: '100', selection_changed: false, outcome: 'changed' as const, replayed: false };
    const html = renderToStaticMarkup(<RecoveryResults recovery={recovery} offers={[]} />);
    expect(html).toContain('Original baseline');
    expect(html).toContain('Hypothetical scenario');
    expect(html).toContain('Production supply covered');
    expect(html).toContain('8 days');
    expect(html).toContain('2,666.62 kg');
  });

  it('uses top-level recovery coverage and shows the reviewed status in overview', () => {
    const gap = { ...coverage, uncovered_days: '6', uncovered_quantity: '1999.98' };
    const recovery = { comparison_id: 'r1', synthetic: true as const, baseline: plan, scenario: { ...plan, plan_id: 'p2' }, baseline_request: {} as never, scenario_request: {} as never, cost_delta: '100', selection_changed: false, outcome: 'changed' as const, replayed: false, baseline_supply_coverage: coverage, scenario_supply_coverage: { ...gap, plan_id: 'p2' } };
    const html = renderToStaticMarkup(<RecoveryResults recovery={recovery} offers={[]} />);
    expect(html).toContain('6 days');
    expect(html).toContain('1,999.98 kg');
    const company = { company_id: 'solara-cables', name: 'Solara Cables', sector: 'Manufacturing', materials: [{ material_id: 'copper', name: 'Copper', quantity_unit: 'kg', stock: '2000', consumption_per_day: '333.33', criticality: 'high' }], suppliers: [] } as never;
    const overview = renderToStaticMarkup(<WorkspaceOverview company={company} news={null} quoteCount={1} currentComparison plan={{ ...plan, supply_coverage: coverage }} onView={() => {}} />);
    expect(overview).toContain('Production supply gap');
    expect(overview).toContain('Inventory as of 2026-10-14');
  });

  it('shows concise repeating decimals without hiding a tiny positive gap', () => {
    const repeating = renderToStaticMarkup(<SupplyAssessment coverage={{ ...coverage, uncovered_days: '4.000000000000001', uncovered_quantity: '1333.333333333333333333' }} />);
    expect(repeating).toContain('about 4 days');
    expect(repeating).toContain('about 1,333.33 kg');
    expect(repeating).not.toContain('333333333333');
    const tiny = renderToStaticMarkup(<SupplyAssessment coverage={{ ...coverage, uncovered_days: '0.000000001', uncovered_quantity: '0.000000001' }} />);
    expect(tiny).toContain('&lt;0.01 days');
    expect(tiny).toContain('&lt;0.01 kg');
    expect(tiny).not.toContain('Uncovered: 0 days');
  });

  it('makes the bridge action visible and keeps the saved RFQ downloadable and unsent', () => {
    const company = { company_id: 'solara-cables', name: 'Solara Cables', sector: 'Manufacturing', size: 'Mid-size', synthetic: true, as_of: '2026-10-14', description: 'Sample', locations: [], reporting_currency: 'USD', materials: [{ material_id: 'copper', name: 'Copper', quantity_unit: 'kg', stock: '2000', consumption_per_day: '333.33', monthly_requirement: '10000', criticality: 'high', keywords: [] }], suppliers: [], orders: [], routes: [], currencies: ['USD'], news_keywords: [], quote_coverage: { materials: ['copper'], fixture_ids: [], note: 'Sample' }, buyer_defaults: null } as never;
    const draft = { draft_id: 'bridge-1', kind: 'rfq' as const, status: 'draft_unsent' as const, company_id: 'solara-cables', material_id: 'copper', recipient_label: 'Swift', subject: 'Earlier delivery', body: 'Please quote.', created_at: '2026-10-14' };
    const session = { session_id: 's1', csrf_token: 'csrf', context_version: 3, company, offers: [currentOffer], plan: { ...plan, supply_coverage: coverage }, plan_request: { material: 'copper' }, drafts: [draft], persistence: 'ephemeral', provider: { model: 'test', available: false } } as unknown as DemoSession;
    const loaded = applyDemoResult(initialDemoState, { type: 'session', session, epoch: 0 });
    const comparison = renderToStaticMarkup(<DemoPage initialState={loaded} initialView="procurement" initialStage="compare" />);
    expect(comparison).toContain('Prepare earlier-delivery / bridge RFQ');
    const saved = renderToStaticMarkup(<DraftsWorkspace company={company} drafts={[draft]} rfq={null} messages={[]} dirty={false} onDownloadRfq={() => {}} />);
    expect(saved).toContain('Unsent draft');
    expect(saved).toContain('/api/demo/rfq/bridge-1/download');
  });

  it('puts a saved gap decision and bridge action before the buyer editor and ordinary RFQ', () => {
    const session = { session_id: 's1', csrf_token: 'csrf', context_version: 3, company: currentCompany, offers: [currentOffer], plan: { ...plan, supply_coverage: coverage }, plan_request: { material: 'copper' }, persistence: 'ephemeral', provider: { model: 'test', available: false } } as unknown as DemoSession;
    const loaded = applyDemoResult(initialDemoState, { type: 'session', session, epoch: 0 });
    const html = renderToStaticMarkup(<DemoPage initialState={loaded} initialView="procurement" initialStage="compare" />);
    expect(html.indexOf('Production supply gap')).toBeLessThan(html.indexOf('Edit buyer inputs'));
    expect(html.indexOf('Prepare earlier-delivery / bridge RFQ')).toBeLessThan(html.indexOf('Review regular RFQ draft'));
    expect(html.indexOf('Edit buyer inputs')).toBeLessThan(html.indexOf('Use company buyer inputs'));
    expect(html).toContain('<details class="demo-buyer-editor"');
    expect(html).not.toContain('Review its unsent RFQ draft next.');
  });

  it('asks for missing coverage input and recommends the ordinary RFQ only when covered', () => {
    const base = { session_id: 's1', csrf_token: 'csrf', context_version: 3, company: currentCompany, offers: [currentOffer], plan_request: { material: 'copper' }, persistence: 'ephemeral', provider: { model: 'test', available: false } } as unknown as DemoSession;
    const render = (status: SupplyCoverage['status']) => renderToStaticMarkup(<DemoPage initialState={applyDemoResult(initialDemoState, { type: 'session', session: { ...base, plan: { ...plan, supply_coverage: { ...coverage, status, continuous_supply_covered: status === 'covered' ? true : null, missing_inputs: status === 'unknown' ? ['daily consumption'] : [] } } }, epoch: 0 })} initialView="procurement" initialStage="compare" />);
    const unknown = render('unknown');
    expect(unknown).toContain('Review needed input');
    expect(unknown).not.toContain('Prepare earlier-delivery / bridge RFQ');
    expect(unknown).not.toContain('Review RFQ draft</button>');
    const covered = render('covered');
    expect(covered).toContain('Review RFQ draft</button>');
    expect(covered).not.toContain('Prepare earlier-delivery / bridge RFQ');
  });

  it('keeps a saved recovery gap and its action ahead of delay controls and buyer edits', () => {
    const scenario = { ...plan, plan_id: 'p2', supply_coverage: { ...coverage, plan_id: 'p2' } };
    const recovery = { comparison_id: 'r1', synthetic: true as const, baseline: plan, scenario, baseline_request: { material: 'copper' } as never, scenario_request: { delay_days_by_offer: { q1: 4 } } as never, cost_delta: '100', selection_changed: false, outcome: 'changed' as const, replayed: false };
    const session = { session_id: 's1', csrf_token: 'csrf', context_version: 3, company: currentCompany, offers: [currentOffer], plan, plan_request: { material: 'copper' }, recovery, persistence: 'ephemeral', provider: { model: 'test', available: false } } as unknown as DemoSession;
    const html = renderToStaticMarkup(<DemoPage initialState={applyDemoResult(initialDemoState, { type: 'session', session, epoch: 0 })} initialView="procurement" initialStage="compare" />);
    expect(html.indexOf('Original and changed plan')).toBeLessThan(html.indexOf('What if a quote arrives late?'));
    expect(html.indexOf('Prepare earlier-delivery / bridge RFQ')).toBeLessThan(html.indexOf('Edit buyer inputs'));
  });

  it.each(['comparison', 'recovery'] as const)('does not offer a supplier bridge for a missing or stale selected quote in %s', view => {
    const base = { session_id: 's1', csrf_token: 'csrf', context_version: 3, company: currentCompany, offers: [currentOffer], plan_request: { material: 'copper' }, persistence: 'ephemeral', provider: { model: 'test', available: false } } as unknown as DemoSession;
    for (const selected of [null, 'missing', 'stale'] as const) {
      const candidate: PlanView = { ...plan, plan_id: view === 'recovery' ? 'p2' : 'p1', selected_offer_id: selected === 'missing' ? 'q2' : selected === 'stale' ? 'q1' : null, offer_versions: selected === 'stale' ? { q1: 2 } : selected === 'missing' ? { q2: 1 } : { q1: 1 } };
      const assessed = { ...coverage, plan_id: candidate.plan_id };
      const recovery = view === 'recovery' ? { comparison_id: 'r1', synthetic: true as const, baseline: plan, scenario: candidate, baseline_request: {} as never, scenario_request: {} as never, cost_delta: null, selection_changed: false, outcome: 'infeasible' as const, replayed: false, scenario_supply_coverage: assessed } : null;
      const loaded = applyDemoResult(initialDemoState, { type: 'session', session: { ...base, plan: view === 'recovery' ? plan : { ...candidate, supply_coverage: assessed }, recovery }, epoch: 0 });
      const html = renderToStaticMarkup(<DemoPage initialState={loaded} initialView="procurement" initialStage="compare" />);
      expect(html, `${view} ${selected}`).not.toContain('Prepare earlier-delivery / bridge RFQ');
      expect(html).toContain('Select a current accepted supplier');
    }
  });

  it.each(['fresh comparison', 'new draft', 'existing draft'])('opens the canonical draft from this operator turn: %s', async source => {
    const session = { session_id: 's1', csrf_token: 'csrf', context_version: 3, company: { company_id: 'solara-cables' }, offers: [{ offer_id: 'q1', version: 1, status: 'saved' }], plan, drafts: [] } as unknown as DemoSession;
    const draft = { draft_id: 'bridge-1', kind: 'rfq' as const, status: 'draft_unsent' as const, company_id: 'solara-cables', material_id: 'copper', recipient_label: 'Swift', subject: 'Earlier delivery', body: bridgeBody, created_at: '2026-10-14', plan_id: 'p1', offer_versions: { q1: 1 }, selected_offer_id: 'q1' };
    const saved = { ...session, rfq: { draft_id: 'bridge-1', status: 'draft_unsent', bridge: true, company_id: 'solara-cables', plan_id: 'p1', offer_versions: { q1: 1 }, supplier: 'Swift', material: 'copper', quantity: '1333.30', quantity_unit: 'kg', need_by: '2026-10-20', arithmetic_bridge_quantity: '1333.30', text: bridgeBody, total_cost: null }, drafts: [draft] } as unknown as DemoSession;
    const operatorTurn = vi.fn().mockResolvedValue({ status: 'completed', company_id: 'solara-cables', context_version: 3, action_result: { tool: 'prepare_rfq' }, session: saved, workspace: { drafts: [draft] } });
    const refresh = vi.fn().mockResolvedValue(saved);
    const onSaved = vi.fn();
    let current = source === 'existing draft' ? saved : session;
    if (source === 'fresh comparison') {
      const loaded = applyDemoResult(initialDemoState, { type: 'session', session: { ...session, plan: null }, epoch: 0 });
      current = applyDemoResult(loaded, { type: 'plan', plan: { ...plan, supply_coverage: coverage }, epoch: 0 }).session!;
    }
    const result = await prepareBridgeDraft({ operatorTurn, session: refresh }, current, { ...plan, supply_coverage: coverage }, onSaved);
    expect(operatorTurn).toHaveBeenCalledWith('csrf', expect.stringContaining('Prepare an unsent earlier-delivery or bridge RFQ'), 3);
    expect(refresh).toHaveBeenCalledOnce();
    expect(result.draft_id).toBe('bridge-1');
    expect(onSaved).toHaveBeenCalledWith(saved, draft);
  });

  it.each([
    ['wrong supplier', { supplier: 'Other' }, {}],
    ['wrong plan', { plan_id: 'other' }, {}],
    ['ordinary RFQ', { bridge: false }, {}],
    ['wrong quantity', { quantity: '1' }, {}],
    ['unrelated ledger draft', {}, { draft_id: 'other' }],
    ['unrelated body', {}, { body: 'Different bytes' }],
  ])('rejects %s without opening drafts', async (_case, rfqChange, draftChange) => {
    const session = { session_id: 's1', csrf_token: 'csrf', context_version: 3, company: { company_id: 'solara-cables' }, offers: [{ offer_id: 'q1', version: 1, status: 'saved' }], plan, drafts: [] } as unknown as DemoSession;
    const draft = { draft_id: 'bridge-1', kind: 'rfq' as const, status: 'draft_unsent' as const, company_id: 'solara-cables', material_id: 'copper', recipient_label: 'Swift', body: bridgeBody, plan_id: 'p1', offer_versions: { q1: 1 }, selected_offer_id: 'q1', ...draftChange };
    const refreshed = { ...session, rfq: { draft_id: 'bridge-1', status: 'draft_unsent', bridge: true, company_id: 'solara-cables', plan_id: 'p1', offer_versions: { q1: 1 }, supplier: 'Swift', material: 'copper', quantity: '1333.30', quantity_unit: 'kg', need_by: '2026-10-20', arithmetic_bridge_quantity: '1333.30', text: bridgeBody, total_cost: null, ...rfqChange }, drafts: [draft] } as unknown as DemoSession;
    const onSaved = vi.fn();
    await expect(prepareBridgeDraft({ operatorTurn: vi.fn().mockResolvedValue({ status: 'completed', company_id: 'solara-cables', context_version: 3, action_result: { tool: 'prepare_rfq' }, session: refreshed, workspace: { drafts: [draft] } }), session: vi.fn().mockResolvedValue(refreshed) }, session, { ...plan, supply_coverage: coverage }, onSaved)).rejects.toThrow();
    expect(onSaved).not.toHaveBeenCalled();
  });

  it('rejects a concurrent bridge that was not produced by this operator turn', async () => {
    const session = { session_id: 's1', csrf_token: 'csrf', context_version: 3, company: { company_id: 'solara-cables' }, offers: [currentOffer], plan, drafts: [] } as unknown as DemoSession;
    const draft = { draft_id: 'bridge-1', kind: 'rfq' as const, status: 'draft_unsent' as const, company_id: 'solara-cables', material_id: 'copper', recipient_label: 'Swift', body: bridgeBody, plan_id: 'p1', offer_versions: { q1: 1 }, selected_offer_id: 'q1' };
    const refreshed = { ...session, rfq: { draft_id: 'bridge-1', status: 'draft_unsent', bridge: true, company_id: 'solara-cables', plan_id: 'p1', offer_versions: { q1: 1 }, supplier: 'Swift', material: 'copper', quantity: '1333.30', quantity_unit: 'kg', need_by: '2026-10-20', arithmetic_bridge_quantity: '1333.30', text: bridgeBody, total_cost: null }, drafts: [draft] } as unknown as DemoSession;
    const onSaved = vi.fn();
    await expect(prepareBridgeDraft({ operatorTurn: vi.fn().mockResolvedValue({ status: 'needs_clarification', session, workspace: { drafts: [] } }), session: vi.fn().mockResolvedValue(refreshed) }, session, { ...plan, supply_coverage: coverage }, onSaved)).rejects.toThrow();
    expect(onSaved).not.toHaveBeenCalled();
  });
});


it('keeps fresh comparisons, recovery and offer edits coherent without reload and rejects stale epochs', () => {
  const session = { session_id: 's1', csrf_token: 'csrf', context_version: 3, company: currentCompany, offers: [currentOffer], plan: null, recovery: null } as unknown as DemoSession;
  let state = applyDemoResult(initialDemoState, { type: 'session', session, epoch: 0 });
  const first = { ...plan, supply_coverage: coverage };
  state = applyDemoResult(state, { type: 'plan', plan: first, request: { material: 'copper' } as never, epoch: 0 });
  expect(bridgeEligibility(state.session, state.plan, state.plan?.supply_coverage)).toBe(true);
  expect(state.session?.plan).toEqual(first);
  const revised = { ...first, plan_id: 'p2', supply_coverage: { ...coverage, plan_id: 'p2' } };
  state = applyDemoResult(state, { type: 'plan', plan: revised, epoch: 0 });
  expect(bridgeEligibility(state.session, revised, revised.supply_coverage)).toBe(true);
  expect(bridgeEligibility(state.session, first, coverage)).toBe(false);
  const scenario = { ...revised, plan_id: 'p3', supply_coverage: { ...coverage, plan_id: 'p3' } };
  const recovery = { comparison_id: 'r1', baseline: revised, scenario, scenario_supply_coverage: scenario.supply_coverage } as never;
  state = applyDemoResult(state, { type: 'recovery', recovery, epoch: 0 });
  expect(bridgeEligibility(state.session, state.plan, state.plan?.supply_coverage)).toBe(true);
  expect(state.session?.recovery).toEqual(recovery);
  state = applyDemoResult(state, { type: 'offer', offer: { ...currentOffer, version: 2 }, epoch: 0 });
  expect(state.session?.offers[0].version).toBe(2);
  expect(state.session?.plan).toBeNull();
  expect(state.session?.recovery).toBeNull();
  expect(bridgeEligibility(state.session, scenario, scenario.supply_coverage)).toBe(false);
  state = applyDemoResult(state, { type: 'invalidate' });
  expect(applyDemoResult(state, { type: 'plan', plan: revised, epoch: 0 })).toBe(state);
});
