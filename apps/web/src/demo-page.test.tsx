import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { applyDemoResult, DemoPage, initialDemoState, PlanResults, sampleBuyerInputs } from './demo-page';
import type { DemoSession, OfferView, PlanView } from './demo-api';

const offer = (version: number, status = 'corrected'): OfferView => ({
  offer_id: 'q1', version, status, synthetic: true,
  document: { document_id: 'd1', filename: 'synthetic.pdf', media_type: 'application/pdf', byte_size: 200, warnings: [] },
  extractor: 'gemini', extractor_detail: { requested: 'gemini', call: 'real', model: 'gemini-test' }, as_of: '2026-10-14',
  fields: {}, items: [], review_flags: [], normalized: {}, blocking_reasons: [],
});
const session: DemoSession = { session_id: 's1', csrf_token: 'csrf1', expires_at: '2026-10-14T12:00:00Z', synthetic: true, persistence: 'ephemeral', offers: [offer(1)], provider: { model: 'gemini-test', available: true } };
const plan = { plan_id: 'p1', synthetic: true, decision_date: '2026-10-14', need_by: '2026-10-30', material: 'copper', quantity_kg: '1000', current_stock_kg: '0', required_purchase_kg: '1000', reporting_currency: 'USD', assumptions: { additional_cost_per_kg: '0', fx_source: 'synthetic' }, alternatives: [], selected_offer_id: null, total_cost: null, explanation: 'No feasible offer.', forecast_action: 'ABSTAIN', customer_savings: null, offer_versions: { q1: 1 } } satisfies PlanView;

describe('demo response state', () => {
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
