import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { applyDemoResult, DemoPage, initialDemoState, PlanResults } from './demo-page';
import { acceptScopedResult, eligibleShipmentDelay, currentCarbonAssessment, currentCarbonResult, linkedDelayRequest } from './demo-taher-workspace';
import type { CarbonAssessment, DemoSession, DispatchView, OfferView, PlanView } from './demo-api';
import { readSupplyLocation, workspaceSearch } from './zoorq-workspace';

const plan: PlanView = { plan_id: 'plan-a', synthetic: true, decision_date: '2026-10-16', need_by: '2026-11-05', material: 'copper', quantity: '100', current_stock: '20', required_purchase: '80', quantity_unit: 'kg', reporting_currency: 'USD', assumptions: {}, alternatives: [{ offer_id: 'offer-a', supplier: 'Supplier A', feasible: true, reasons: [], arrival_date: '2026-11-02', unit_price_per_unit: '10', total_cost: '800' }], selected_offer_id: 'offer-a', total_cost: '800', explanation: 'Selected.', forecast_action: 'ABSTAIN', customer_savings: null, offer_versions: { 'offer-a': 2 } };
const offer: OfferView = { offer_id: 'offer-a', version: 2, status: 'saved', synthetic: true, document: { document_id: 'doc-a', filename: 'quote.pdf', media_type: 'application/pdf', byte_size: 100 }, extractor: 'rules', extractor_detail: {}, as_of: '2026-10-16', fields: {}, items: [], review_flags: [], normalized: {}, blocking_reasons: [] };
const company = { company_id: 'company-a', name: 'Company A', sector: 'Manufacturing', size: 'Small', synthetic: true as const, as_of: '2026-10-16', description: '', locations: [], reporting_currency: 'USD', materials: [{ material_id: 'copper', name: 'Copper', quantity_unit: 'kg', stock: '20', consumption_per_day: '1', monthly_requirement: '30', criticality: 'high', keywords: [] }], suppliers: [], orders: [], routes: [], currencies: ['USD'], news_keywords: [], quote_coverage: { materials: ['copper'], fixture_ids: ['fixture-a'], note: '' }, buyer_defaults: null };
const session: DemoSession = { session_id: 'session-a', csrf_token: 'csrf', expires_at: '2026-11-05T00:00:00Z', synthetic: true, persistence: 'ephemeral', offers: [offer], provider: { model: '', available: false }, company, context_version: 5, plan };
const scope = { sessionId: 'session-a', companyId: 'company-a', contextVersion: 5, epoch: 0, planId: 'plan-a' };
const assessment: CarbonAssessment = { schema: 1, company_id: 'company-a', context_version: 5, plan_id: 'plan-a', offer_versions: { 'offer-a': 2 }, material_id: 'copper', registry_version: 'v1', calculator_version: 'v1', inputs: {}, created_at: '2026-10-16T10:00:00Z', result: { company_id: 'company-a', context_version: 5, plan_id: 'plan-a', synthetic_inputs: true, registry_version: 'v1', label: 'Estimate', scope: 'Material and freight', material_id: 'copper', quantity_unit: 'kg', suppliers: [] } };
const dispatch: DispatchView = { company_id: 'company-a', context_version: 5, plan_id: 'plan-a', baseline_plan_id: 'plan-a', synthetic: true, source: 'fictional_company_profile_and_plan', as_of: '2026-10-16', limitations: [], shipments: [{ shipment_id: 'shipment-a', company_id: 'company-a', material_id: 'copper', material_name: 'Copper', order_id: null, quantity: '80', quantity_unit: 'kg', need_by: '2026-11-05', status: 'planned_inbound_scenario', expected_arrival: '2026-11-02', offer_id: 'offer-a', offer_version: 2, plan_id: 'plan-a', recovery_available: true, recovery_reason: null, delay_days: 0, timeline: [], source: 'synthetic_plan_assumption' }] };

describe('Taher workspace integration', () => {
  it('restores only current carbon evidence for the exact plan and quote version', () => {
    expect(currentCarbonAssessment({ ...session, carbon_assessment: assessment }, plan, false)?.result).toEqual(assessment.result);
    expect(currentCarbonAssessment({ ...session, carbon_assessment: assessment }, { ...plan, offer_versions: { 'offer-a': 3 } }, false)).toBeNull();
    expect(currentCarbonAssessment({ ...session, context_version: 6, carbon_assessment: assessment }, plan, false)?.result).toEqual(assessment.result);
    expect(currentCarbonAssessment({ ...session, company: { ...company, company_id: 'company-b' }, carbon_assessment: assessment }, plan, false)).toBeNull();
    expect(currentCarbonAssessment({ ...session, carbon_assessment: assessment }, plan, true)).toBeNull();
  });
  it('holds an edited carbon result hidden across a Cost tab visit and a late restore response', () => {
    const saved = { ...session, carbon_assessment: assessment };
    expect(currentCarbonResult(saved, plan, false, scope, null, null)).toEqual(assessment.result);
    expect(currentCarbonResult(saved, plan, false, scope, { scope, result: null }, scope)).toBeNull();
    expect(currentCarbonResult(saved, plan, false, scope, null, scope)).toBeNull();
    expect(acceptScopedResult(scope, scope, 1, 2)).toBe(false);
    expect(currentCarbonResult(saved, plan, false, { ...scope, epoch: 2 }, null, scope)).toEqual(assessment.result);
  });
  it('keeps a shipment ID when a reviewer edits only its delay', () => {
    const handoff = { shipmentId: 'shipment-a', offerId: 'offer-a', days: 12 };
    expect(linkedDelayRequest(handoff, 'offer-a', '15')).toEqual({ shipmentId: 'shipment-a', daysByOffer: { 'offer-a': 15 } });
    expect(linkedDelayRequest(handoff, 'offer-b', '15')).toBeNull();
    expect(linkedDelayRequest(handoff, 'offer-a', '15.5')).toBeNull();
    expect(linkedDelayRequest(handoff, 'offer-a', '3651')).toBeNull();
  });
  it('rejects a late A response after A to B to A, or a new same-company session', () => {
    expect(acceptScopedResult(scope, { ...scope, epoch: 2 }, 1, 1)).toBe(false);
    expect(acceptScopedResult(scope, { ...scope, sessionId: 'session-new' }, 1, 1)).toBe(false);
    expect(acceptScopedResult(scope, scope, 1, 2)).toBe(false);
    expect(acceptScopedResult(scope, scope, 2, 2)).toBe(true);
  });
  it('maps only an eligible exact current shipment to one reviewed delay input', () => {
    expect(eligibleShipmentDelay(dispatch, 'shipment-a', 12, scope, plan, [offer], false)).toEqual({ shipmentId: 'shipment-a', offerId: 'offer-a', days: 12 });
    expect(eligibleShipmentDelay(dispatch, 'shipment-a', 12, { ...scope, contextVersion: 6 }, plan, [offer], false)).toBeNull();
    expect(eligibleShipmentDelay(dispatch, 'shipment-a', 12, scope, plan, [offer], true)).toBeNull();
    expect(eligibleShipmentDelay(dispatch, 'shipment-a', 12, scope, plan, [{ ...offer, version: 3 }], false)).toBeNull();
    expect(eligibleShipmentDelay({ ...dispatch, shipments: [{ ...dispatch.shipments[0], recovery_available: false }] }, 'shipment-a', 12, scope, plan, [offer], false)).toBeNull();
    expect(eligibleShipmentDelay({ ...dispatch, shipments: [dispatch.shipments[0], dispatch.shipments[0]] }, 'shipment-a', 12, scope, plan, [offer], false)).toBeNull();
    expect(eligibleShipmentDelay({ ...dispatch, shipments: [{ ...dispatch.shipments[0], plan_id: 'older-plan' }] }, 'shipment-a', 12, scope, plan, [offer], false)).toBeNull();
    expect(eligibleShipmentDelay(dispatch, 'shipment-a', 12.5, scope, plan, [offer], false)).toBeNull();
  });
  it('shows Supply and Compare tabs and accurately labels normalized comparison price', () => {
    const loaded = applyDemoResult(initialDemoState, { type: 'session', session, epoch: 0 });
    const compare = renderToStaticMarkup(<DemoPage initialState={loaded} initialView="procurement" initialStage="compare" />);
    const supply = renderToStaticMarkup(<DemoPage initialState={loaded} initialView="inventory" />);
    expect(compare).toContain('Emissions');
    expect(compare).toContain('Cost');
    expect(supply).toContain('Shipments');
    expect(supply).toContain('Inventory');
    expect(renderToStaticMarkup(<PlanResults plan={plan} offers={[offer]} />)).toContain('Comparison unit price');
  });
  it('keeps the Shipments destination in the shareable supply URL', () => {
    expect(readSupplyLocation('?view=inventory&supply=shipments')).toBe('shipments');
    expect(workspaceSearch('?company=company-a&view=inventory&supply=shipments', 'inventory', 'add', 'shipments')).toBe('?view=inventory&supply=shipments');
    expect(workspaceSearch('?view=inventory&supply=shipments', 'procurement', 'compare')).toBe('?view=procurement&stage=compare');
  });
});
