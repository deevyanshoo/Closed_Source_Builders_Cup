import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { applyDemoResult, canonicalBuyerState, delayControlsForPlan, delayControlsForRecovery, delayRequest, DemoPage, initialDemoState, PlanResults, sampleBuyerInputs } from './demo-page';
import type { DemoSession, OfferView, PlanRequest, PlanView, RecoveryView, RfqView } from './demo-api';

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

describe('demo response state', () => {
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
    expect(canonicalBuyerState(canonical)).toMatchObject({ selected: ['q2'], inputs: { material: 'aluminium', quantity_kg: '8000', budget: '10000' } });
    const restored = applyDemoResult(initialDemoState, { type: 'session', session: canonical, epoch: 0 });
    const html = renderToStaticMarkup(<DemoPage initialState={restored} />);
    expect(html).toContain('value="aluminium"');
    expect(html).toContain('value="8000"');
    expect(html).toMatch(/type="checkbox" checked=""\/>second\.pdf/);
  });

  it('disables buyer edits and sample values until the first session is ready', () => {
    const html = renderToStaticMarkup(<DemoPage />);
    expect(html).toMatch(/disabled=""[^>]*>Use sample buyer inputs/);
    expect(html).toMatch(/<input type="text" required="" disabled="" value=""/);
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
    expect(sampleBuyerInputs()).toEqual({ material: 'copper', quantity_kg: '10000', current_stock_kg: '2000', need_by: '2026-11-05', decision_date: '2026-10-14', reporting_currency: 'USD', budget: '100000', additional_cost_per_kg: '0.25' });
    const html = renderToStaticMarkup(<DemoPage initialState={applyDemoResult(initialDemoState, { type: 'session', session, epoch: 0 })} />);
    expect(html).toContain('Use sample buyer inputs');
    expect(html).toContain('EUR 1 = USD 1.25');
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
    expect(html).toContain('Synthetic documents');
    expect(html).toContain('temporary session');
    expect(html).toContain('Run comparison');
    expect(html).toMatch(/disabled=""[^>]*>Run comparison|disabled=""[^>]*>[^<]*Run comparison/);
  });
});
