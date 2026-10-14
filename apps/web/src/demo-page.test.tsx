import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { applyDemoResult, canonicalBuyerState, companyScenarios, CorrectionEditor, delayControlsForPlan, delayControlsForRecovery, delayRequest, DemoPage, fixturesForMaterial, initialDemoState, isSessionExpired, materialInputs, needsBuyerDraftSync, offersForMaterial, PlanResults, reviewSuggestionFor, sampleBuyerInputs, selectCompanyWithRecovery, workspaceChanged } from './demo-page';
import { DemoApiError } from './demo-api';
import type { DemoFixture, DemoSession, FieldResult, OfferView, PlanRequest, PlanView, RecoveryView, RfqView } from './demo-api';

const offer = (version: number, status = 'corrected'): OfferView => ({
  offer_id: 'q1', version, status, synthetic: true,
  document: { document_id: 'd1', filename: 'synthetic.pdf', media_type: 'application/pdf', byte_size: 200, warnings: [] },
  extractor: 'gemini', extractor_detail: { requested: 'gemini', call: 'real', model: 'gemini-test' }, as_of: '2026-10-14',
  fields: {}, items: [], review_flags: [], normalized: {}, blocking_reasons: [],
});
const session: DemoSession = { session_id: 's1', csrf_token: 'csrf1', expires_at: '2026-10-14T12:00:00Z', synthetic: true, persistence: 'ephemeral', offers: [offer(1)], provider: { model: 'gemini-test', available: true } };
const plan = { plan_id: 'p1', synthetic: true, decision_date: '2026-10-14', need_by: '2026-10-30', material: 'copper', quantity_kg: '1000', current_stock_kg: '0', required_purchase_kg: '1000', reporting_currency: 'USD', assumptions: { additional_cost_per_kg: '0', fx_source: 'synthetic' }, alternatives: [], selected_offer_id: null, total_cost: null, explanation: 'No feasible offer.', forecast_action: 'ABSTAIN', customer_savings: null, offer_versions: { q1: 1 } } satisfies PlanView;
const request: PlanRequest = { offer_ids: ['q1'], expected_versions: { q1: 1 }, material: 'copper', quantity_kg: '1000', current_stock_kg: '0', need_by: '2026-10-30', decision_date: '2026-10-14', reporting_currency: 'USD', budget: '10000', additional_cost_per_kg: '0' };
const selectedPlan: PlanView = { ...plan, selected_offer_id: 'q1', total_cost: '86000', required_purchase_kg: '1000', alternatives: [{ offer_id: 'q1', supplier: 'Swift', feasible: true, reasons: [], arrival_date: '2026-10-29', unit_price_per_kg: '84', total_cost: '86000', quantity_kg: '1000' }] };
const recovery: RecoveryView = { comparison_id: 'c1', synthetic: true, baseline: { ...selectedPlan, plan_id: 'p1', total_cost: '82000' }, scenario: { ...selectedPlan, plan_id: 'p2' }, baseline_request: request, scenario_request: { ...request, delay_days_by_offer: { q1: 20 } }, cost_delta: '4000', selection_changed: true, outcome: 'changed', replayed: false };
const rfq: RfqView = { draft_id: 'd1', plan_id: 'p2', offer_versions: { q1: 1 }, synthetic: true, status: 'draft_unsent', supplier: 'Swift', material: 'copper', quantity_kg: '1000', need_by: '2026-10-30', reporting_currency: 'USD', total_cost: '86000', created_at: '2026-10-14T10:00:00Z', text: 'Synthetic unsent request for quotation' };
const company = { company_id: 'solara-cables', name: 'Solara Cables', sector: 'Electrical manufacturing', size: 'Mid-size', synthetic: true as const, as_of: '2026-10-14', description: 'Fictional cables producer.', locations: [{ country: 'India', city: 'Pune' }], reporting_currency: 'USD', materials: [{ material_id: 'copper', name: 'Copper', quantity_unit: 'kg', stock: '2000', consumption_per_day: '100', monthly_requirement: '3000', criticality: 'high', keywords: ['copper'] }], suppliers: [], orders: [{ order_id: 'order-1', material_id: 'copper', quantity: '1000', unit: 'kg', need_by: '2026-11-05', status: 'planned' }], routes: [], currencies: ['USD', 'EUR'], news_keywords: ['copper'], quote_coverage: { materials: ['copper'], fixture_ids: ['quote-a'], note: 'Synthetic copper quote only.' }, buyer_defaults: request };
const companySession = { ...session, offers: [offer(1, 'saved')], company, context_version: 1, plan: selectedPlan, plan_request: request, rfq };
const emptyCompany = { ...company, company_id: 'loomworks', name: 'Loomworks', materials: [{ ...company.materials[0], material_id: 'cotton', name: 'Cotton' }], quote_coverage: { materials: [], fixture_ids: [], note: 'No runnable quote coverage.' }, buyer_defaults: null };
const naphthaRequest: PlanRequest = { ...request, material: 'naphtha', quantity: '240', current_stock: '40', quantity_unit: 'barrel', budget: '6500', reporting_currency: 'EUR', additional_cost_per_unit: '3' };
const multiCompany = { ...company, materials: [...company.materials, { ...company.materials[0], material_id: 'naphtha', name: 'Naphtha', quantity_unit: 'barrel', stock: '40' }], quote_coverage: { materials: ['copper', 'naphtha'], fixture_ids: ['quote-a', 'naphtha-a'], note: 'Two runnable materials.', scenarios: [{ material_id: 'copper', fixture_ids: ['quote-a'], buyer_defaults: request }, { material_id: 'naphtha', fixture_ids: ['naphtha-a'], buyer_defaults: naphthaRequest }] }, buyer_defaults: request };
const materialFixtures: DemoFixture[] = [{ id: 'quote-a', name: 'Copper quote', filename: 'copper.csv', description: '', download_url: '/api/demo/fixtures/quote-a' }, { id: 'naphtha-a', name: 'Naphtha quote', filename: 'naphtha.csv', description: '', download_url: '/api/demo/fixtures/naphtha-a' }];
const naphthaOffer: OfferView = { ...offer(1, 'saved'), offer_id: 'q2', fixture_id: 'naphtha-a', document: { ...offer(1).document, filename: 'naphtha.csv' } };

describe('demo response state', () => {
  it('retains a recovery notice only when canonical voice preflight changes workspace identity', () => {
    expect(workspaceChanged(session, { ...session, offers: [] })).toBe(false);
    expect(workspaceChanged(session, { ...session, session_id: 'renewed' })).toBe(true);
    expect(workspaceChanged(session, { ...session, csrf_token: 'rotated' })).toBe(true);
    expect(workspaceChanged(companySession, { ...companySession, context_version: 2 })).toBe(true);
    expect(workspaceChanged(companySession, { ...companySession, company: emptyCompany })).toBe(true);
  });
  it('uses the selected catalogue scenario and shows only its quote evidence before the quote form', () => {
    expect(companyScenarios(multiCompany).map(item => item.material_id)).toEqual(['copper', 'naphtha']);
    expect(fixturesForMaterial(materialFixtures, multiCompany, 'naphtha').map(item => item.id)).toEqual(['naphtha-a']);
    expect(offersForMaterial([{ ...offer(1, 'saved'), fixture_id: 'quote-a' }, naphthaOffer], materialFixtures, multiCompany, 'naphtha').map(item => item.offer_id)).toEqual(['q2']);
    expect(materialInputs(multiCompany, 'naphtha')).toMatchObject({ material: 'naphtha', quantity: '240', current_stock: '40', quantity_unit: 'barrel', reporting_currency: 'EUR', budget: '6500' });
    const canonical = { ...companySession, company: multiCompany, offers: [{ ...offer(1, 'saved'), fixture_id: 'quote-a' }, naphthaOffer], plan: null, plan_request: null, rfq: null };
    expect(canonicalBuyerState(canonical, 'naphtha').inputs).toMatchObject({ material: 'naphtha', quantity: '240', quantity_unit: 'barrel' });
    const restored = applyDemoResult(initialDemoState, { type: 'session', session: { ...canonical, plan_request: naphthaRequest }, epoch: 0 });
    const html = renderToStaticMarkup(<DemoPage initialState={restored} />);
    expect(html.indexOf('Procurement material')).toBeLessThan(html.indexOf('Sample quote'));
    expect(html).toContain('Naphtha · barrel');
    expect(html).toContain('naphtha.csv');
    expect(html).not.toContain('synthetic.pdf');
  });
  it('restores the server buyer draft over an old comparison and shows server-confirmed simulated messages', () => {
    const draft = { material: 'naphtha', quantity: '410', current_stock: '40', quantity_unit: 'barrel', need_by: '2026-10-30', decision_date: '2026-10-14', reporting_currency: 'EUR', budget: '12000', additional_cost_per_unit: '3' };
    const canonical = { ...companySession, company: multiCompany, buyer_draft: draft, selected_material_id: 'naphtha', plan: null, plan_request: null, rfq: null, simulated_outbox: [{ message_id: 'm1', company_id: 'solara-cables', material_id: 'naphtha', recipient_label: 'Supplier desk', subject: 'Review availability', body: 'Please review 410 barrels.', status: 'simulated_sent' as const, simulated: true as const, external_delivery: false as const, created_at: '2026-10-14T10:00:00Z' }] };
    expect(canonicalBuyerState(canonical).inputs).toMatchObject({ material: 'naphtha', quantity: '410', budget: '12000' });
    expect(canonicalBuyerState(canonical).materialId).toBe('naphtha');
    const stalePlan = applyDemoResult(initialDemoState, { type: 'session', session: { ...canonical, plan: selectedPlan, plan_request: request, rfq }, epoch: 0 });
    expect(stalePlan.plan).toBeNull();
    expect(stalePlan.rfq).toBeNull();
    const html = renderToStaticMarkup(<DemoPage initialState={applyDemoResult(initialDemoState, { type: 'session', session: canonical, epoch: 0 })} />);
    expect(html).toContain('Simulated outbox');
    expect(html).toContain('>Simulated outbox</button>');
  });
  it('syncs initially displayed defaults before agent turns but preserves a current computed comparison', () => {
    const defaults = materialInputs(multiCompany, 'naphtha')!;
    const fresh = { ...companySession, company: multiCompany, plan: null, plan_request: null, buyer_draft: null, selected_material_id: null };
    expect(needsBuyerDraftSync(fresh, defaults, false)).toBe(true);
    const computed = { ...companySession, buyer_draft: null };
    expect(needsBuyerDraftSync(computed, canonicalBuyerState(computed).inputs, false)).toBe(false);
    expect(needsBuyerDraftSync(computed, { ...canonicalBuyerState(computed).inputs, quantity: '1500' }, true)).toBe(true);
  });

  it('invalidates a pending material comparison without losing other saved offers', () => {
    const loaded = applyDemoResult(initialDemoState, { type: 'session', session: { ...companySession, company: multiCompany, offers: [{ ...offer(1, 'saved'), fixture_id: 'quote-a' }, naphthaOffer] }, epoch: 0 });
    const switched = applyDemoResult(loaded, { type: 'invalidate' });
    expect(switched).toMatchObject({ epoch: 1, dirty: true, plan: null, rfq: null, reviewed: false });
    expect(switched.offers.map(item => item.offer_id)).toEqual(['q1', 'q2']);
    expect(applyDemoResult(switched, { type: 'plan', plan: selectedPlan, request, epoch: 0 }).plan).toBeNull();
    expect(applyDemoResult(switched, { type: 'rfq', rfq, epoch: 0 }).rfq).toBeNull();
  });
  it('recognizes expired news sessions and clears stale evidence before accepting a new one', () => {
    expect(isSessionExpired(new DemoApiError('Session expired', 403, 'session_expired'))).toBe(true);
    expect(isSessionExpired(new DemoApiError('Session expired', 409, 'session_expired'))).toBe(true);
    expect(isSessionExpired(new DemoApiError('Source rate limited', 429, 'rate_limited'))).toBe(false);
    const oldNews = { company_id: 'solara-cables', context_version: 1, checked_at: '2026-10-14T10:00:00Z', cache_state: 'stale', sources: [], items: [], fx: [], limitations: [] };
    const old = applyDemoResult(applyDemoResult(initialDemoState, { type: 'session', session: companySession, epoch: 0 }), { type: 'news', news: oldNews, sessionId: 's1', epoch: 0 });
    const cleared = applyDemoResult(old, { type: 'company-start' });
    expect(cleared).toMatchObject({ session: null, news: null, plan: null, rfq: null });
    const fresh = applyDemoResult(cleared, { type: 'session', session: { ...companySession, session_id: 's2', offers: [], plan: null, plan_request: null, rfq: null }, epoch: 1 });
    expect(fresh.news).toBeNull();
    expect(applyDemoResult(fresh, { type: 'news', news: oldNews, sessionId: 's1', epoch: 0 }).news).toBeNull();
  });
  it('clears prior company state and ignores late session/news responses after a switch', () => {
    const loaded = applyDemoResult(initialDemoState, { type: 'session', session: companySession, epoch: 0 });
    const switching = applyDemoResult(loaded, { type: 'company-start' });
    expect(switching).toMatchObject({ session: null, offers: [], plan: null, rfq: null, news: null });
    const next = applyDemoResult(switching, { type: 'session', session: { ...companySession, company: emptyCompany, context_version: 2, offers: [], plan: null, plan_request: null, rfq: null }, epoch: 1 });
    expect(applyDemoResult(next, { type: 'session', session: companySession, epoch: 0 }).session?.company?.company_id).toBe('loomworks');
    const oldNews = { company_id: 'solara-cables', context_version: 1, checked_at: '2026-10-14T10:00:00Z', cache_state: 'fresh', sources: [], items: [], fx: [], limitations: [] };
    expect(applyDemoResult(next, { type: 'news', news: oldNews, sessionId: 's1', epoch: 0 }).news).toBeNull();
    expect(applyDemoResult(next, { type: 'news', news: oldNews, sessionId: 's1', epoch: 1 }).news).toBeNull();
    const currentNews = { ...oldNews, company_id: 'loomworks', context_version: 2 };
    expect(applyDemoResult(next, { type: 'news', news: currentNews, sessionId: 's1', epoch: 1 }).news?.company_id).toBe('loomworks');
    const switchingBack = applyDemoResult(next, { type: 'company-start' });
    const restored = applyDemoResult(switchingBack, { type: 'session', session: { ...companySession, context_version: 3 }, epoch: 2 });
    expect(restored.offers.map(item => item.offer_id)).toEqual(['q1']);
    expect(restored.plan?.plan_id).toBe('p1');
    expect(restored.rfq?.draft_id).toBe('d1');
  });

  it('recovers the authoritative session after a lost company-switch response and leaves failure to the caller when recovery fails', async () => {
    const serverSession = { ...companySession, session_id: 's2', company: emptyCompany, context_version: 2, offers: [], plan: null, plan_request: null, rfq: null };
    const recovered = await selectCompanyWithRecovery(
      async () => { throw new Error('response lost'); },
      async () => serverSession,
    );
    expect(recovered).toEqual(serverSession);
    await expect(selectCompanyWithRecovery(
      async () => { throw new Error('response lost'); },
      async () => { throw new Error('session unavailable'); },
    )).rejects.toThrow('session unavailable');
    expect(applyDemoResult(applyDemoResult(initialDemoState, { type: 'session', session: companySession, epoch: 0 }), { type: 'company-start' }))
      .toMatchObject({ session: null, offers: [], plan: null, rfq: null, news: null });
  });

  it('binds news to session identity and captured epoch, including a buyer edit during a pending read', () => {
    const news = { company_id: 'solara-cables', context_version: 1, checked_at: '2026-10-14T10:00:00Z', cache_state: 'fresh', sources: [], items: [], fx: [], limitations: [] };
    const s1 = applyDemoResult(initialDemoState, { type: 'session', session: companySession, epoch: 0 });
    const withNews = applyDemoResult(s1, { type: 'news', news, sessionId: 's1', epoch: 0 });
    expect(withNews.news).toEqual(news);
    const s2 = applyDemoResult(withNews, { type: 'session', session: { ...companySession, session_id: 's2' }, epoch: 0 });
    expect(s2.news).toBeNull();
    expect(applyDemoResult(s2, { type: 'news', news, sessionId: 's1', epoch: 0 }).news).toBeNull();
    expect(applyDemoResult(s2, { type: 'news', news, sessionId: 's2', epoch: -1 }).news).toBeNull();
    expect(applyDemoResult(s2, { type: 'news', news, sessionId: 's2', epoch: 0 }).news).toEqual(news);
    const edited = applyDemoResult(s1, { type: 'invalidate' });
    expect(applyDemoResult(edited, { type: 'news', news, sessionId: 's1', epoch: 0 }).news).toBeNull();
    expect(applyDemoResult(edited, { type: 'news', news, sessionId: 's1', epoch: 1 }).news).toEqual(news);
  });

  it('shows company inventory/orders and blocks unsupported quote planning', () => {
    const loaded = applyDemoResult(initialDemoState, { type: 'session', session: { ...companySession, company: emptyCompany, plan: null, plan_request: null, rfq: null }, epoch: 0 });
    const html = renderToStaticMarkup(<DemoPage initialState={loaded} />);
    expect(html).toContain('Loomworks');
    expect(html).toContain('Cotton');
    expect(html).toContain('order-1');
    expect(html).toContain('No runnable quote coverage.');
    expect(html).toMatch(/disabled=""[^>]*>Run comparison/);
    expect(html).toContain('News workspace');
  });
  it('uses generic units and covered company defaults for a runnable tonne workflow', () => {
    const generic = { ...request, quantity: '20', current_stock: '3', additional_cost_per_unit: '2', quantity_unit: 'tonne' };
    const tonneCompany = { ...company, materials: [{ ...company.materials[0], quantity_unit: 'tonne' }], buyer_defaults: generic };
    const tonnePlan = { ...selectedPlan, quantity: '20', current_stock: '3', required_purchase: '17', quantity_unit: 'tonne', alternatives: [{ ...selectedPlan.alternatives[0], quantity: '17', unit_price_per_unit: '84' }] };
    const loaded = applyDemoResult(initialDemoState, { type: 'session', session: { ...companySession, company: tonneCompany, plan: tonnePlan, plan_request: generic }, epoch: 0 });
    const html = renderToStaticMarkup(<DemoPage initialState={loaded} />);
    expect(canonicalBuyerState({ ...companySession, company: tonneCompany, plan_request: generic }).inputs).toMatchObject({ quantity: '20', current_stock: '3', quantity_unit: 'tonne' });
    expect(html).toContain('Needed quantity (tonne)');
    expect(html).toContain('17 tonne');
    expect(html).toContain('USD/tonne');
    expect(html).not.toContain('kg-only');
  });
  it('renders quote corrections for barrel, bale and unit and suggests covered material IDs', () => {
    for (const unit of ['barrel', 'bale', 'unit']) {
      const field: FieldResult = { name: 'capacity', kind: 'quantity', status: 'extracted', value: { amount: '10', unit }, source: 'document', evidence: [], reasons: [] };
      const html = renderToStaticMarkup(<CorrectionEditor field={field} busy={false} units={[unit]} materialIds={['crude-oil']} onApply={() => {}} onCancel={() => {}} />);
      expect(html).toContain(`<option value="${unit}" selected="">${unit}</option>`);
    }
    const material: FieldResult = { name: 'material', kind: 'text', status: 'missing', value: null, source: 'document', evidence: [], reasons: [] };
    const html = renderToStaticMarkup(<CorrectionEditor field={material} busy={false} units={['barrel']} materialIds={['crude-oil', 'cotton-bales', 'electronic-components']} onApply={() => {}} onCancel={() => {}} />);
    expect(html).toContain('value="cotton-bales"');
    expect(html).toContain('value="electronic-components"');
  });
  it('labels a generic RFQ draft with its material quantity and unit', () => {
    const generic = { ...request, material: 'crude-oil', quantity: '20', current_stock: '3', quantity_unit: 'barrel', additional_cost_per_unit: '2' };
    const oilCompany = { ...company, materials: [{ ...company.materials[0], material_id: 'crude-oil', name: 'Crude oil', quantity_unit: 'barrel' }], quote_coverage: { ...company.quote_coverage, materials: ['crude-oil'] }, buyer_defaults: generic };
    const oilPlan = { ...selectedPlan, material: 'crude-oil', quantity: '20', current_stock: '3', required_purchase: '17', quantity_unit: 'barrel' };
    const oilRfq = { ...rfq, material: 'crude-oil', quantity: '17', quantity_unit: 'barrel' };
    const restored = applyDemoResult(initialDemoState, { type: 'session', session: { ...companySession, company: oilCompany, plan: oilPlan, plan_request: generic, rfq: oilRfq }, epoch: 0 });
    expect(renderToStaticMarkup(<DemoPage initialState={restored} />)).toMatch(/Draft for Swift<\/strong>[^<]*17 barrel/);
  });
  it('prefills only unverified quote terms from the matching synthetic fixture for explicit reviewer apply', () => {
    const fixture = { id: 'atlas-quote', name: 'Atlas', filename: 'atlas.csv', description: '', download_url: '/api/demo/fixtures/atlas-quote', review_values: { 'items[0].material': 'naphtha', 'items[0].quantity': { amount: '40', unit: 'barrel' } } };
    const atlas = { ...offer(1), fixture_id: fixture.id, document: { ...offer(1).document, filename: fixture.filename } };
    expect(reviewSuggestionFor(atlas, [fixture], 'items[0].material')).toBe('naphtha');
    expect(reviewSuggestionFor(atlas, [fixture], 'items[0].quantity')).toEqual({ amount: '40', unit: 'barrel' });
    expect(reviewSuggestionFor({ ...atlas, fixture_id: 'other' }, [fixture], 'items[0].material')).toBeUndefined();
    const missing: FieldResult = { name: 'material', kind: 'text', status: 'missing', value: null, source: 'document', evidence: [], reasons: [] };
    const html = renderToStaticMarkup(<CorrectionEditor field={missing} suggestion="naphtha" busy={false} materialIds={['naphtha']} onApply={() => {}} onCancel={() => {}} />);
    expect(html).toContain('value="naphtha"');
    expect(html).toContain('Suggested synthetic fixture value');
    const verified = renderToStaticMarkup(<CorrectionEditor field={{ ...missing, status: 'extracted', value: 'copper' }} suggestion="naphtha" busy={false} onApply={() => {}} onCancel={() => {}} />);
    expect(verified).toContain('value="copper"');
    expect(verified).not.toContain('Suggested synthetic fixture value');
  });
  it('exposes the scoped operator tab beside company and news without removing procurement results', () => {
    const loaded = applyDemoResult(initialDemoState, { type: 'session', session: companySession, epoch: 0 });
    const html = renderToStaticMarkup(<DemoPage initialState={loaded} />);
    expect(html).toContain('Company overview');
    expect(html).toContain('News workspace');
    expect(html).toContain('Operator');
    expect(html).toContain('current reviewed comparison');
    expect(html).toContain('Turn supply risks into reviewed actions');
    expect(html).toContain('Fictional company profile · changes kept separately for this visit');
    expect(html).not.toContain('context v1');
    expect(html).toContain('demo-operator-dock');
    expect(html).toContain('Open operator chat');
    expect(html).toContain('Start conversation');
    expect(html).not.toContain('Stop and transcribe');
  });
  it('adds a second supplier delay without losing the first and can remove all delays', () => {
    expect(delayRequest({ q1: 20 }, 'q2', '10')).toEqual({ q1: 20, q2: 10 });
    expect(delayRequest({ q1: 20, q2: 10 }, 'q2', '0')).toEqual({ q1: 20, q2: 0 });
    expect(delayRequest({ q1: 20 }, '', '')).toEqual({});
  });
  it('resets the delay editor from the returned comparison and starts a fresh plan at its selected supplier', () => {
    const twoOffers = { ...request, offer_ids: ['q2', 'q1'], expected_versions: { q1: 1, q2: 1 } };
    expect(delayControlsForPlan(selectedPlan, twoOffers)).toEqual({ offerId: 'q1', days: '0' });
    const twoDelays = { ...recovery, scenario_request: { ...request, delay_days_by_offer: { q1: 20, q2: 10 } } };
    expect(delayControlsForRecovery(twoDelays, 'q2')).toEqual({ offerId: 'q2', days: '10' });
    const removed = { ...recovery, scenario_request: { ...request, delay_days_by_offer: {} }, cost_delta: '0' };
    expect(delayControlsForRecovery(removed, 'q2')).toEqual({ offerId: 'q1', days: '0' });
    const restored = applyDemoResult(initialDemoState, { type: 'session', session: { ...session, offers: [offer(1, 'saved')], plan: selectedPlan, plan_request: request, recovery: removed, rfq }, epoch: 0 });
    const html = renderToStaticMarkup(<DemoPage initialState={restored} />);
    expect(html).toContain('Scenario: synthetic.pdf delayed 0 days');
    expect(html).not.toContain('Scenario: synthetic.pdf delayed 20 days');
  });
  it('describes the RFQ as the current reviewed comparison without exposing a plan ID in preview copy', () => {
    const restored = applyDemoResult(initialDemoState, { type: 'session', session: { ...session, offers: [offer(1, 'saved')], plan: selectedPlan, plan_request: request, rfq }, epoch: 0 });
    const html = renderToStaticMarkup(<DemoPage initialState={restored} />);
    expect(html).toContain('current reviewed comparison');
    expect(html).not.toContain('current plan p2');
  });
  it('restores paired comparison and current draft from the ephemeral session', () => {
    const restored = applyDemoResult(initialDemoState, { type: 'session', session: { ...session, plan: selectedPlan, plan_request: request, recovery, rfq }, epoch: 0 });
    expect(restored.plan?.plan_id).toBe('p2');
    expect(restored.recovery?.baseline.plan_id).toBe('p1');
    const html = renderToStaticMarkup(<DemoPage initialState={restored} />);
    expect(html).toContain('4000 USD');
    expect(html).toContain('Synthetic unsent request for quotation');
    expect(html).toContain('Download RFQ draft');
  });

  it('clears recovery and draft on buyer edit and ignores the old async response', () => {
    const loaded = applyDemoResult(initialDemoState, { type: 'session', session: { ...session, plan: selectedPlan, plan_request: request, recovery, rfq }, epoch: 0 });
    const edited = applyDemoResult(loaded, { type: 'invalidate' });
    expect(edited).toMatchObject({ plan: null, recovery: null, rfq: null, reviewed: false });
    expect(applyDemoResult(edited, { type: 'recovery', recovery, epoch: 0 }).recovery).toBeNull();
  });

  it('keeps the paired plans but clears the draft when delay controls change', () => {
    const loaded = applyDemoResult(initialDemoState, { type: 'session', session: { ...session, offers: [offer(1, 'saved')], plan: selectedPlan, plan_request: request, recovery, rfq }, epoch: 0 });
    const changed = applyDemoResult(loaded, { type: 'scenario-edit' });
    expect(changed.recovery?.baseline.plan_id).toBe('p1');
    expect(changed.plan?.plan_id).toBe('p2');
    expect(changed.rfq).toBeNull();
    expect(changed.reviewed).toBe(false);
    expect(applyDemoResult(changed, { type: 'rfq', rfq, epoch: 0 }).rfq).toBeNull();
  });

  it('blocks RFQ review and export while edits are pending, then re-enables them after comparison', () => {
    const loaded = applyDemoResult(initialDemoState, { type: 'session', session: { ...session, offers: [offer(1, 'saved')], plan: selectedPlan, plan_request: request, recovery, rfq }, epoch: 0 });
    const edited = applyDemoResult(loaded, { type: 'scenario-edit' });
    expect(edited.recovery?.baseline.plan_id).toBe('p1');
    const dirtyHtml = renderToStaticMarkup(<DemoPage initialState={edited} />);
    expect(dirtyHtml).toMatch(/type="checkbox" disabled=""\/>I reviewed this plan/);
    expect(dirtyHtml).toMatch(/disabled=""[^>]*>Prepare unsent RFQ/);
    expect(dirtyHtml).toMatch(/disabled=""[^>]*>Download JSON report/);
    expect(renderToStaticMarkup(<DemoPage initialState={{ ...edited, rfq }} />)).toMatch(/disabled=""[^>]*>Download RFQ draft/);
    expect(applyDemoResult(edited, { type: 'reviewed', value: true }).reviewed).toBe(false);
    const buyerEdited = applyDemoResult(loaded, { type: 'invalidate' });
    expect(renderToStaticMarkup(<DemoPage initialState={buyerEdited} />)).toMatch(/disabled=""[^>]*>Download JSON report/);
    const compared = applyDemoResult(edited, { type: 'recovery', recovery: { ...recovery, comparison_id: 'c2' }, epoch: 1 });
    expect(compared.dirty).toBe(false);
    const cleanHtml = renderToStaticMarkup(<DemoPage initialState={compared} />);
    expect(cleanHtml).toMatch(/type="checkbox"\/>I reviewed this plan/);
    expect(cleanHtml).not.toMatch(/disabled=""[^>]*>Download JSON report/);
    const reviewed = applyDemoResult(compared, { type: 'reviewed', value: true });
    expect(renderToStaticMarkup(<DemoPage initialState={reviewed} />)).not.toMatch(/disabled=""[^>]*>Prepare unsent RFQ/);
    const replanned = applyDemoResult(buyerEdited, { type: 'plan', plan: selectedPlan, request, epoch: 1 });
    expect(replanned.dirty).toBe(false);
    expect(renderToStaticMarkup(<DemoPage initialState={replanned} />)).not.toMatch(/disabled=""[^>]*>Download JSON report/);
  });

  it('rejects pending recovery and restores the canonical plan without reviving an old draft', () => {
    const loaded = applyDemoResult(initialDemoState, { type: 'session', session: { ...session, offers: [offer(1, 'saved')], plan: selectedPlan, plan_request: request, recovery, rfq }, epoch: 0 });
    const edited = applyDemoResult(loaded, { type: 'scenario-edit' });
    const late = applyDemoResult(edited, { type: 'recovery', recovery: { ...recovery, comparison_id: 'late' }, epoch: 0 });
    expect(late.recovery?.comparison_id).toBe('c1');
    expect(late.recovery?.baseline.plan_id).toBe('p1');
    const refreshed = applyDemoResult(late, { type: 'session', session: { ...session, offers: [offer(1, 'saved')], plan: selectedPlan, plan_request: request, recovery, rfq }, epoch: 1 });
    expect(refreshed.rfq).toBeNull();
    expect(refreshed.recovery?.baseline.plan_id).toBe('p1');
    expect(refreshed.plan?.plan_id).toBe('p2');
    expect(refreshed.dirty).toBe(false);
  });

  it('refreshes canonical buyer inputs and selected quote IDs with the server plan', () => {
    const q2 = { ...offer(1, 'saved'), offer_id: 'q2', document: { ...offer(1, 'saved').document, filename: 'second.pdf' } };
    const q2Plan = { ...selectedPlan, selected_offer_id: 'q2', offer_versions: { q2: 1 } };
    const canonical = { ...session, offers: [offer(1, 'saved'), q2], plan: q2Plan, plan_request: { ...request, offer_ids: ['q2'], expected_versions: { q2: 1 }, material: 'aluminium', quantity_kg: '8000' } };
    expect(canonicalBuyerState(canonical)).toMatchObject({ selected: ['q2'], inputs: { material: 'aluminium', quantity: '8000', budget: '10000' } });
    const restored = applyDemoResult(initialDemoState, { type: 'session', session: canonical, epoch: 0 });
    const html = renderToStaticMarkup(<DemoPage initialState={restored} />);
    expect(html).toContain('value="aluminium"');
    expect(html).toContain('value="8000"');
    expect(html).toMatch(/type="checkbox" checked=""\/>second\.pdf/);
  });

  it('disables buyer edits and sample values until the first session is ready', () => {
    const html = renderToStaticMarkup(<DemoPage />);
    expect(html).toMatch(/disabled=""[^>]*>Use sample buyer inputs/);
    expect(html).toMatch(/<input required="" disabled="" value=""/);
  });

  it('requires an active selected purchase and explicit review before showing draft action as enabled', () => {
    const loaded = applyDemoResult(initialDemoState, { type: 'session', session, epoch: 0 });
    expect(renderToStaticMarkup(<DemoPage initialState={loaded} />)).toMatch(/disabled=""[^>]*>Prepare unsent RFQ/);
    const withPlan = applyDemoResult(loaded, { type: 'plan', plan: selectedPlan, request, epoch: 0 });
    expect(renderToStaticMarkup(<DemoPage initialState={withPlan} />)).toContain('I reviewed this plan and understand the RFQ is an unsent draft');
  });
  it('ignores a late extraction after reset moves to a new session', () => {
    const before = applyDemoResult(initialDemoState, { type: 'session', session, epoch: 0 });
    const resetting = applyDemoResult(before, { type: 'reset-start' });
    const after = applyDemoResult(resetting, { type: 'session', session: { ...session, session_id: 's2', offers: [] }, epoch: 1 });
    expect(applyDemoResult(after, { type: 'offer', offer: offer(2), epoch: 0 }).offers).toEqual([]);
  });

  it('ignores an older correction and clears a plan when the current offer changes', () => {
    const loaded = applyDemoResult(initialDemoState, { type: 'session', session, epoch: 0 });
    const planned = applyDemoResult(loaded, { type: 'plan', plan, epoch: 0 });
    const corrected = applyDemoResult(planned, { type: 'offer', offer: offer(2), epoch: 0 });
    expect(corrected.plan).toBeNull();
    expect(applyDemoResult(corrected, { type: 'offer', offer: offer(1), epoch: 0 }).offers[0].version).toBe(2);
  });

  it('keeps a cached label attached to the extracted quote when another quote is selected', () => {
    const loaded = applyDemoResult(initialDemoState, { type: 'session', session: { ...session, offers: [offer(1), { ...offer(1), offer_id: 'q2' }] }, epoch: 0 });
    const extracted = applyDemoResult(loaded, { type: 'offer', offer: offer(2), epoch: 0, cached: true });
    const other = applyDemoResult(extracted, { type: 'focus', offerId: 'q2' });
    const html = renderToStaticMarkup(<DemoPage initialState={other} />);
    expect(html).not.toContain('Cached real Gemini extraction');
  });

  it('restores a cached real-extraction label from a quote returned in a new session read', () => {
    const loaded = applyDemoResult(initialDemoState, { type: 'session', session: { ...session, offers: [{ ...offer(1), served_from_cache: true }] }, epoch: 0 });
    expect(renderToStaticMarkup(<DemoPage initialState={loaded} />)).toContain('Cached real Gemini extraction');
  });

  it('offers optional fictional buyer inputs with the dated synthetic currency assumption', () => {
    expect(sampleBuyerInputs()).toEqual({ material: 'copper', quantity: '10000', current_stock: '2000', quantity_unit: 'kg', need_by: '2026-11-05', decision_date: '2026-10-14', reporting_currency: 'USD', budget: '100000', additional_cost_per_unit: '0.25' });
    const html = renderToStaticMarkup(<DemoPage initialState={applyDemoResult(initialDemoState, { type: 'session', session, epoch: 0 })} />);
    expect(html).toContain('Use sample buyer inputs');
    expect(html).toContain('Dated synthetic FX rates, when needed, are recorded in the comparison and JSON export.');
    expect(html).not.toContain('EUR 1 = USD 1.25');
    expect(html).toContain('2026-10-14');
    expect(html).toContain('fictional');
  });

  it('explains infeasible reasons and the buyer-added total in plain language', () => {
    const result = { ...plan, required_purchase_kg: '8000', total_cost: null, alternatives: [{ offer_id: 'q1', supplier: 'Late supplier', feasible: false, reasons: ['arrives_after_need_by', 'over_budget', 'fx_unavailable', 'capacity_period_unsupported', 'payment_terms_unknown'], arrival_date: '2026-11-10', unit_price_per_kg: null, total_cost: null, quantity_kg: '8000' }] };
    const html = renderToStaticMarkup(<PlanResults plan={result} offers={[offer(1)]} />);
    expect(html).toContain('Arrives after the needed date');
    expect(html).toContain('Exceeds the budget');
    expect(html).toContain('Currency rate unavailable');
    expect(html).toContain('Capacity period cannot be compared');
    expect(html).toContain('Payment terms are unknown');
    expect(html).not.toContain('arrives_after_need_by');
    expect(html).toContain('Total includes your extra cost per kg');
  });

  it('shows stock-covered demand as zero purchase rather than an infeasible comparison', () => {
    const result = { ...plan, required_purchase_kg: '0', total_cost: '0', explanation: 'Opening stock covers the stated need; no purchase is required.' };
    const html = renderToStaticMarkup(<PlanResults plan={result} offers={[offer(1)]} />);
    expect(html).toContain('Stock covers this need');
    expect(html).toContain('0 USD');
    expect(html).not.toContain('No feasible quote');
  });

  it('renders honest demo limits and disabled planning before an accepted quote', () => {
    const html = renderToStaticMarkup(<DemoPage initialState={applyDemoResult(initialDemoState, { type: 'session', session, epoch: 0 })} />);
    expect(html).toContain('Company profiles, quotes and buyer inputs are synthetic');
    expect(html).toContain('news is observed public data');
    expect(html).toContain('temporary session');
    expect(html).toContain('Run comparison');
    expect(html).toMatch(/disabled=""[^>]*>Run comparison|disabled=""[^>]*>[^<]*Run comparison/);
  });
});
