import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { DemoDispatchPanel, dispatchReviewAction, orderedDispatchRows } from './demo-dispatch';
import type { DispatchShipment, DispatchView } from './demo-api';

const expected: DispatchShipment = { shipment_id: 'expected-1', company_id: 'company-a', material_id: 'copper', material_name: 'Copper', order_id: 'ORDER-1', quantity: '100', quantity_unit: 'kg', need_by: '2026-11-05', status: 'expected_order_scenario', expected_arrival: null, offer_id: null, offer_version: null, plan_id: null, recovery_available: false, recovery_reason: 'expected_order_only', delay_days: null, timeline: [], source: 'fictional_company_profile_expected_order' };
const planned: DispatchShipment = { ...expected, shipment_id: 'planned-1', order_id: null, demand_reference: 'plan-demand:plan-a', quantity: '80', order_demand_quantity: '100', status: 'planned_inbound_scenario', expected_arrival: '2026-11-02', original_expected_arrival: '2026-11-02', offer_id: 'offer-a', offer_version: 2, plan_id: 'plan-a', baseline_plan_id: 'plan-a', baseline_offer_id: 'offer-a', baseline_offer_version: 2, baseline_supplier: 'Supplier A', recovery_available: true, recovery_reason: null, delay_days: 0, source: 'synthetic_plan_demand_scenario', arrival_basis: 'plan_decision_plus_reviewed_quote_lead_and_assumed_delay', timeline: [{ date: '2026-10-16', label: 'Plan decision', source: 'synthetic_plan_assumption' }, { date: '2026-11-02', label: 'Planned arrival', source: 'synthetic_plan_assumption' }] };
const view: DispatchView = { company_id: 'company-a', context_version: 4, plan_id: 'plan-a', synthetic: true, source: 'fictional_company_profile_and_plan', as_of: '2026-10-16', limitations: ['No live shipment tracking.'], shipments: [expected, planned] };

describe('dispatch review panel', () => {
  it('puts the current plan-demand row first and keeps the other orders stable', () => {
    const another = { ...expected, shipment_id: 'expected-2', order_id: 'ORDER-2' };
    const mixed = { ...view, shipments: [expected, another, planned] };
    expect(orderedDispatchRows(mixed).map(row => row.shipment_id)).toEqual(['planned-1', 'expected-1', 'expected-2']);
    const html = renderToStaticMarkup(<DemoDispatchPanel view={mixed} busy={false} onRefresh={() => {}} onReviewDelay={() => {}} />);
    expect(html.indexOf('plan-demand:plan-a')).toBeLessThan(html.indexOf('ORDER-1'));
    expect(html).toContain('aria-current="true"');
  });
  it('permits only one current plan-linked row and an explicit whole-day input', () => {
    expect(dispatchReviewAction(view, 'planned-1', '12')).toEqual({ shipmentId: 'planned-1', days: 12 });
    for (const bad of ['-1', '1.5', '3651', '', '1e2']) expect(dispatchReviewAction(view, 'planned-1', bad)).toBeNull();
    expect(dispatchReviewAction(view, 'expected-1', '12')).toBeNull();
    expect(dispatchReviewAction({ ...view, plan_id: 'new-plan' }, 'planned-1', '12')).toBeNull();
    expect(dispatchReviewAction({ ...view, shipments: [planned, planned] }, 'planned-1', '12')).toBeNull();
    expect(dispatchReviewAction({ ...view, shipments: [{ ...planned, scenario_plan_id: 'reviewed' }] }, 'planned-1', '12')).toBeNull();
    expect(dispatchReviewAction({ ...view, shipments: [{ ...planned, status: 'reviewed_infeasible_scenario' }] }, 'planned-1', '12')).toBeNull();
    expect(dispatchReviewAction(view, 'planned-1', '12', true)).toBeNull();
  });

  it('shows the linked planning scenario separately from expected orders and discloses sources', () => {
    const html = renderToStaticMarkup(<DemoDispatchPanel view={view} busy={false} onRefresh={() => {}} onReviewDelay={() => {}} />);
    expect(html).toContain('ORDER-1');
    expect(html).toContain('plan-demand:plan-a');
    expect(html).toContain('80 kg');
    expect(html).toContain('100 kg');
    expect(html).toContain('2026-11-02');
    expect(html).toContain('Plan decision');
    expect(html).toContain('synthetic_plan_assumption');
    expect(html).toContain('No live shipment tracking.');
    expect(html).toContain('Review recovery');
    const onlyExpected = renderToStaticMarkup(<DemoDispatchPanel view={{ ...view, plan_id: null, shipments: [expected] }} busy={false} onRefresh={() => {}} onReviewDelay={() => {}} />);
    expect(onlyExpected).toContain('Review sourcing before testing a delay');
    expect(onlyExpected).not.toContain('Hypothetical delay, whole days');
  });

  it('keeps baseline and reviewed scenario identities and arrivals distinct', () => {
    const reviewed = { ...planned, recovery_available: false, recovery_reason: 'recovery_already_reviewed', plan_id: 'scenario-plan', original_expected_arrival: '2026-11-02', scenario_expected_arrival: '2026-11-14', baseline_plan_id: 'plan-a', baseline_supplier: 'Supplier A', scenario_plan_id: 'scenario-plan', scenario_supplier: 'Supplier B', scenario_status: 'changed', timeline: [{ date: '2026-11-02', label: 'Baseline planned arrival', phase: 'baseline', source: 'synthetic_plan_assumption' }, { date: '2026-11-14', label: 'Scenario planned arrival', phase: 'scenario', source: 'synthetic_plan_assumption' }] };
    const html = renderToStaticMarkup(<DemoDispatchPanel view={{ ...view, plan_id: 'scenario-plan', shipments: [reviewed] }} busy={false} onRefresh={() => {}} onReviewDelay={() => {}} />);
    expect(html).toContain('Original baseline');
    expect(html).toContain('Reviewed scenario');
    expect(html).toContain('Supplier A');
    expect(html).toContain('Supplier B');
    expect(html).toContain('2026-11-02');
    expect(html).toContain('2026-11-14');
    expect(html).toContain('Already reviewed');
  });

  it('renders clear loading, empty, error and disabled states', () => {
    const base = { view: null, busy: false, onRefresh: () => {}, onReviewDelay: () => {} };
    expect(renderToStaticMarkup(<DemoDispatchPanel {...base} busy />)).toContain('Loading shipment scenarios');
    expect(renderToStaticMarkup(<DemoDispatchPanel {...base} error="Read failed" />)).toContain('Read failed');
    expect(renderToStaticMarkup(<DemoDispatchPanel {...base} view={{ ...view, shipments: [] }} />)).toContain('No shipment scenarios');
    const disabled = renderToStaticMarkup(<DemoDispatchPanel {...base} view={view} disabled />);
    expect(disabled).toContain('disabled=""');
  });
});
