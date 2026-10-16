import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import type { OfferView, PlanView } from './demo-api';
import { CarbonPanel, carbonCell, requestCarbonEstimate } from './demo-carbon';
import type { CarbonComparison } from './demo-carbon';
import { PlanResults } from './demo-page';

const factorSource = { publisher: 'Department for Energy Security and Net Zero (UK government)', source_url: 'https://www.gov.uk/government/publications/greenhouse-gas-reporting-conversion-factors-2026', licence: 'Open Government Licence v3.0', factor_year: 2026, checked_on: '2026-10-15' };
const estimated = {
  label: 'estimate, not a certified footprint', scope: 'Cradle-to-gate primary material production plus entered legs.', registry_version: 'carbon-factors-v1', material_id: 'pet-resin', quantity: '8000', unit: 'kg',
  material: { status: 'estimated', kg_co2e: '30892.66008', mass_tonnes: '8', formula: '8 t x 3861.58251 kg CO2e/t', factor: { ...factorSource, category: 'Plastics: PET (incl. forming)', factor: '3861.58251', unit: 'kg CO2e per tonne', note: 'Closest published category.', system_boundary: 'Cradle-to-gate primary material production.' } },
  transport: { status: 'estimated', kg_co2e: '128.96', legs: [{ mode: 'sea_container', distance_km: '1000', status: 'estimated', tonne_km: '8000', kg_co2e: '128.96', formula: '8 t x 1000 km x 0.01612 kg CO2e/tonne.km', factor: { ...factorSource, label: 'Sea, average container ship', factor: '0.01612', unit: 'kg CO2e per tonne.km' } }] },
  total_kg_co2e: '31021.62008', complete: true, factor_versions: { 'carbon-factors-v1': { 'desnz-2026-full-set': { published_on: '2026-06-11', checked_on: '2026-10-15', factor_year: 2026, file_sha256: 'abc' } } },
};
const unavailable = { ...estimated, material_id: 'copper', material: { status: 'factor_unavailable', kg_co2e: null, factor: null, mass_tonnes: '8', reason: 'No published factor is registered for copper.' }, transport: { status: 'not_entered', kg_co2e: null, legs: [] }, total_kg_co2e: null, complete: false };
const comparison: CarbonComparison = { plan_id: 'p1', synthetic_inputs: true, registry_version: 'carbon-factors-v1', label: 'estimate, not a certified footprint', scope: estimated.scope, material_id: 'pet-resin', quantity_unit: 'kg',
  suppliers: [{ offer_id: 'q1', supplier: 'Swift [SYNTHETIC]', estimate: estimated }, { offer_id: 'q2', supplier: 'Late [SYNTHETIC]', estimate: unavailable }] } as CarbonComparison;
const plan = { plan_id: 'p1', synthetic: true, decision_date: '2026-10-14', need_by: '2026-10-30', material: 'pet-resin', quantity: '8000', quantity_unit: 'kg', reporting_currency: 'USD', assumptions: {}, selected_offer_id: 'q1', total_cost: '82000', explanation: 'Swift is cheapest.', forecast_action: 'ABSTAIN', customer_savings: null, offer_versions: { q1: 1, q2: 1 },
  alternatives: [{ offer_id: 'q1', supplier: 'Swift [SYNTHETIC]', feasible: true, reasons: [], arrival_date: '2026-10-20', unit_price_per_unit: '10', total_cost: '82000', quantity: '8000' }, { offer_id: 'q2', supplier: 'Late [SYNTHETIC]', feasible: false, reasons: ['arrives_after_need_by'], arrival_date: '2026-11-20', unit_price_per_unit: '9', total_cost: '74000', quantity: '8000' }] } as unknown as PlanView;
const offers = [{ offer_id: 'q1', document: { filename: 'a.csv' } }, { offer_id: 'q2', document: { filename: 'b.csv' } }] as unknown as OfferView[];

describe('carbon footprint estimate (#108)', () => {
  it('summarises each supplier as an estimate and never invents a missing factor', () => {
    expect(carbonCell(comparison, 'q1')).toBe('About 31,021.6 kg CO2e');
    expect(carbonCell(comparison, 'q2')).toBe('Production factor unavailable · transport not entered');
    expect(carbonCell(comparison, 'missing')).toBe('Not estimated');
    const partial = { ...comparison, suppliers: [{ offer_id: 'q3', supplier: 'X', estimate: { ...unavailable, transport: estimated.transport } }] } as CarbonComparison;
    expect(carbonCell(partial, 'q3')).toBe('Production factor unavailable · transport about 129.0 kg CO2e');
  });

  it('adds an emissions column to the comparison only for the matching plan', () => {
    const html = renderToStaticMarkup(<PlanResults plan={plan} offers={offers} carbon={comparison} />);
    expect(html).toContain('<th>Emissions<small>estimate, not certified</small></th>');
    expect(html).toContain('About 31,021.6 kg CO2e');
    expect(renderToStaticMarkup(<PlanResults plan={plan} offers={offers} />)).not.toContain('Emissions');
    expect(renderToStaticMarkup(<PlanResults plan={plan} offers={offers} carbon={{ ...comparison, plan_id: 'old' }} />)).not.toContain('Emissions');
  });

  it('shows the inputs, the label, the scope and every factor with its source and formula', () => {
    const html = renderToStaticMarkup(<CarbonPanel plan={plan} csrf="c" contextVersion={2} result={comparison} onResult={() => {}} />);
    expect(html).toContain('Estimate, not a certified footprint');
    expect(html).toContain(estimated.scope);
    expect(html).toContain('aria-label="Transport mode for Swift"');
    expect(html).toContain('aria-label="Distance in km for Swift"');
    expect(html).toContain('8 t x 3861.58251 kg CO2e/t');
    expect(html).toContain('8 t x 1000 km x 0.01612 kg CO2e/tonne.km');
    expect(html).toContain('Plastics: PET (incl. forming)');
    expect(html).toContain('href="https://www.gov.uk/government/publications/greenhouse-gas-reporting-conversion-factors-2026"');
    expect(html).toContain('Open Government Licence v3.0');
    expect(html).toContain('Boundary: Cradle-to-gate primary material production.');
    expect(html).toContain('No published factor is registered for copper.');
    expect(html).toContain('Distances are what you enter; nothing is inferred from the route.');
    expect(html).not.toMatch(/\bis certified\b|\bverified footprint\b|\bcarbon neutral\b/i);
  });

  it('posts the active plan id and the entered legs with the CSRF token', async () => {
    const send = vi.fn(async () => new Response(JSON.stringify(comparison), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    const result = await requestCarbonEstimate(send as unknown as typeof fetch, 'csrf1', 'p1', { q1: [{ mode: 'sea_container', distance_km: '1000' }] }, 3);
    expect(result.plan_id).toBe('p1');
    const [url, init] = send.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/demo/carbon/estimate');
    expect(init.method).toBe('POST');
    expect((init.headers as Record<string, string>)['X-CSRF-Token']).toBe('csrf1');
    expect(JSON.parse(init.body as string)).toEqual({ plan_id: 'p1', legs_by_offer: { q1: [{ mode: 'sea_container', distance_km: '1000' }] }, expected_context_version: 3 });
  });

  it('reports a refused estimate with the service message', async () => {
    const send = vi.fn(async () => new Response(JSON.stringify({ error: { code: 'stale_plan', message: 'The comparison changed.' } }), { status: 409, headers: { 'Content-Type': 'application/json' } }));
    await expect(requestCarbonEstimate(send as unknown as typeof fetch, 'c', 'p1', {}, 1)).rejects.toThrow('The comparison changed.');
  });
});
