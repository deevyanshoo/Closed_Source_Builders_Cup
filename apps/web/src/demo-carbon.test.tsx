import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import type { OfferView, PlanView } from './demo-api';
import { CarbonPanel, carbonCell, requestCarbonEstimate, CarbonRequestOwner, carbonResponseMatchesScope, invalidateCarbonInputs } from './demo-carbon';
import type { CarbonComparison } from './demo-carbon';
import { PlanResults } from './demo-page';

const factorSource = { publisher: 'Department for Energy Security and Net Zero (UK government)', source_url: 'https://www.gov.uk/government/publications/greenhouse-gas-reporting-conversion-factors-2026', licence: 'Open Government Licence v3.0', factor_year: 2026, checked_on: '2026-10-15' };
const estimated = {
  label: 'estimate from published average factors', scope: 'Cradle-to-gate primary material production plus entered legs.', registry_version: 'carbon-factors-v1', material_id: 'pet-resin', quantity: '8000', unit: 'kg',
  material: { status: 'estimated', kg_co2e: '30892.66008', mass_tonnes: '8', formula: '8 t x 3861.58251 kg CO2e/t', factor: { ...factorSource, category: 'Plastics: PET (incl. forming)', factor: '3861.58251', unit: 'kg CO2e per tonne', note: 'Closest published category.', system_boundary: 'Cradle-to-gate primary material production.', product_match: 'proxy', geography: 'United Kingdom (DESNZ UK company-reporting averages)' }, product_match: 'proxy' },
  transport: { status: 'estimated', kg_co2e: '128.96', legs: [{ mode: 'sea_container', distance_km: '1000', status: 'estimated', tonne_km: '8000', kg_co2e: '128.96', formula: '8 t x 1000 km x 0.01612 kg CO2e/tonne.km', factor: { ...factorSource, label: 'Sea, average container ship', factor: '0.01612', unit: 'kg CO2e per tonne.km' } }] },
  total_kg_co2e: null, combined_status: 'components_shown_separately', combined_reason: 'The material factor is a proxy for this product, so a combined figure would overstate precision.', mass_basis: null, complete: false, factor_versions: { 'carbon-factors-v1': { 'desnz-2026-full-set': { published_on: '2026-06-11', checked_on: '2026-10-15', factor_year: 2026, file_sha256: 'abc' } } },
};
const unavailable = { ...estimated, material_id: 'copper', material: { status: 'factor_unavailable', kg_co2e: null, factor: null, mass_tonnes: '8', reason: 'No published factor is registered for copper.' }, combined_status: 'incomplete', combined_reason: 'A component is missing.', transport: { status: 'not_entered', kg_co2e: null, legs: [] }, total_kg_co2e: null, complete: false };
const scope = { sessionId: 'session-a', companyId: 'solara-cables', contextVersion: 2 };
const comparison: CarbonComparison = { company_id: 'solara-cables', context_version: 2, plan_id: 'p1', synthetic_inputs: true, registry_version: 'carbon-factors-v1', label: 'estimate from published average factors', scope: estimated.scope, material_id: 'pet-resin', quantity_unit: 'kg',
  suppliers: [{ offer_id: 'q1', supplier: 'Swift [SYNTHETIC]', estimate: estimated }, { offer_id: 'q2', supplier: 'Late [SYNTHETIC]', estimate: unavailable }] } as CarbonComparison;
const plan = { plan_id: 'p1', synthetic: true, decision_date: '2026-10-14', need_by: '2026-10-30', material: 'pet-resin', quantity: '8000', quantity_unit: 'kg', reporting_currency: 'USD', assumptions: {}, selected_offer_id: 'q1', total_cost: '82000', explanation: 'Swift is cheapest.', forecast_action: 'ABSTAIN', customer_savings: null, offer_versions: { q1: 1, q2: 1 },
  alternatives: [{ offer_id: 'q1', supplier: 'Swift [SYNTHETIC]', feasible: true, reasons: [], arrival_date: '2026-10-20', unit_price_per_unit: '10', total_cost: '82000', quantity: '8000' }, { offer_id: 'q2', supplier: 'Late [SYNTHETIC]', feasible: false, reasons: ['arrives_after_need_by'], arrival_date: '2026-11-20', unit_price_per_unit: '9', total_cost: '74000', quantity: '8000' }] } as unknown as PlanView;
const offers = [{ offer_id: 'q1', document: { filename: 'a.csv' } }, { offer_id: 'q2', document: { filename: 'b.csv' } }] as unknown as OfferView[];

describe('carbon footprint estimate (#108)', () => {
  it('summarises each supplier as an estimate and never invents a missing factor', () => {
    expect(carbonCell(comparison, 'q1')).toBe('Material about 30,892.7 kg CO2e · freight about 129.0 kg CO2e (components shown separately)');
    expect(carbonCell(comparison, 'q2')).toBe('Production factor unavailable · transport not entered');
    expect(carbonCell(comparison, 'missing')).toBe('Not estimated');
    const partial = { ...comparison, suppliers: [{ offer_id: 'q3', supplier: 'X', estimate: { ...unavailable, transport: estimated.transport } }] } as CarbonComparison;
    expect(carbonCell(partial, 'q3')).toBe('Production factor unavailable · transport about 129.0 kg CO2e');
  });

  it('adds an emissions column to the comparison only for the matching plan', () => {
    const html = renderToStaticMarkup(<PlanResults plan={plan} offers={offers} carbon={comparison} />);
    expect(html).toContain('<th>Emissions<small>estimate, not certified</small></th>');
    expect(html).toContain('Material about 30,892.7 kg CO2e · freight about 129.0 kg CO2e (components shown separately)');
    expect(renderToStaticMarkup(<PlanResults plan={plan} offers={offers} />)).not.toContain('Emissions');
    expect(renderToStaticMarkup(<PlanResults plan={plan} offers={offers} carbon={{ ...comparison, plan_id: 'old' }} />)).not.toContain('Emissions');
  });

  it('shows the inputs, the label, the scope and every factor with its source and formula', () => {
    const html = renderToStaticMarkup(<CarbonPanel scope={scope} plan={plan} csrf="c" contextVersion={2} result={comparison} dirty={false} onResult={() => {}} />);
    expect(html).toContain('Estimate from published average factors');
    expect(html).not.toContain('certified');
    expect(html).toContain(estimated.scope);
    expect(html).toContain('aria-label="Transport mode for Swift"');
    expect(html).toContain('aria-label="Distance in km for Swift"');
    expect(html).toContain('8 t x 3861.58251 kg CO2e/t');
    expect(html).toContain('8 t x 1000 km x 0.01612 kg CO2e/tonne.km');
    expect(html).toContain('Plastics: PET (incl. forming)');
    expect(html).toContain('href="https://www.gov.uk/government/publications/greenhouse-gas-reporting-conversion-factors-2026"');
    expect(html).toContain('Open Government Licence v3.0');
    expect(html).toContain('Boundary: Cradle-to-gate primary material production.');
    expect(html).toContain('Product match: proxy · Geography: United Kingdom (DESNZ UK company-reporting averages)');
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

  it('shows overlapping or proxy components separately with no combined figure', () => {
    const separate = { ...comparison, suppliers: [{ offer_id: 'q1', supplier: 'Swift', estimate: { ...estimated, total_kg_co2e: null, combined_status: 'components_shown_separately', combined_reason: 'The material factor already includes transport to the point of sale.', complete: false } }] } as CarbonComparison;
    expect(carbonCell(separate, 'q1')).toBe('Material about 30,892.7 kg CO2e · freight about 129.0 kg CO2e (components shown separately)');
    const html = renderToStaticMarkup(<CarbonPanel scope={scope} plan={plan} csrf="c" contextVersion={2} result={separate} dirty={false} onResult={() => {}} />);
    expect(html).toContain('The material factor already includes transport to the point of sale.');
    expect(html).not.toContain('About 31,021.6');
  });

  it('blocks estimation on edited buyer inputs and labels the earlier result stale', () => {
    const html = renderToStaticMarkup(<CarbonPanel scope={scope} plan={plan} csrf="c" contextVersion={2} result={comparison} dirty onResult={() => {}} />);
    expect(html).toContain('Buyer inputs changed. Run the comparison again before estimating emissions.');
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Estimate emissions<\/button>/);
    expect(html).not.toContain('Material about 30,892.7 kg CO2e');
  });

  it('asks for a shipping mass and its basis when the quantity is not a mass', () => {
    const counted = { ...plan, quantity_unit: 'unit' } as PlanView;
    const html = renderToStaticMarkup(<CarbonPanel scope={scope} plan={counted} csrf="c" contextVersion={2} result={null} dirty={false} onResult={() => {}} />);
    expect(html).toContain('aria-label="Shipping mass in kg for Swift"');
    expect(html).toContain('aria-label="Mass basis for Swift"');
    expect(html).toContain('used for freight only and never inferred');
    expect(renderToStaticMarkup(<CarbonPanel scope={scope} plan={plan} csrf="c" contextVersion={2} result={null} dirty={false} onResult={() => {}} />)).not.toContain('Shipping mass');
  });

  it('shows a parent-validated saved assessment after a company revisit', () => {
    const revisited = { ...scope, contextVersion: 9 };
    const html = renderToStaticMarkup(<CarbonPanel scope={revisited} plan={plan} csrf="c" contextVersion={9} result={comparison} dirty={false} onResult={() => {}} />);
    expect(html).toContain('Material about 30,892.7 kg CO2e');
  });

  it('requires a fresh estimate response to match the active company, context and plan', () => {
    expect(carbonResponseMatchesScope(comparison, scope, 'p1')).toBe(true);
    expect(carbonResponseMatchesScope(comparison, { ...scope, contextVersion: 9 }, 'p1')).toBe(false);
    expect(carbonResponseMatchesScope(comparison, { ...scope, companyId: 'another-company' }, 'p1')).toBe(false);
    expect(carbonResponseMatchesScope(comparison, scope, 'another-plan')).toBe(false);
    expect(carbonResponseMatchesScope(null, scope, 'p1')).toBe(false);
  });

  it('sends an entered shipping mass with its basis', async () => {
    const send = vi.fn(async () => new Response(JSON.stringify(comparison), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    await requestCarbonEstimate(send as unknown as typeof fetch, 'c', 'p1', { q1: [{ mode: 'road_hgv', distance_km: '10' }] }, 1, { q1: { mass_kg: '2500', basis: 'packing list' } });
    const [, init] = send.mock.calls[0] as unknown as [string, RequestInit];
    expect(JSON.parse(init.body as string).mass_by_offer).toEqual({ q1: { mass_kg: '2500', basis: 'packing list' } });
  });
});


describe('carbon request ownership', () => {
  it('reactivates after Strict Mode effect replay without publishing the old mount response', async () => {
    const owner = new CarbonRequestOwner('session-a:company-a:1:p1');
    owner.activate();
    let resolveOld!: (value: CarbonComparison) => void;
    const oldPublish = vi.fn();
    const old = owner.run(() => new Promise<CarbonComparison>(done => { resolveOld = done; }), oldPublish);
    owner.dispose();
    owner.activate();
    const newPublish = vi.fn();
    expect(await owner.run(async () => comparison, newPublish)).toBe(true);
    expect(newPublish).toHaveBeenCalledWith(comparison);
    resolveOld(comparison);
    expect(await old).toBe(false);
    expect(oldPublish).not.toHaveBeenCalled();
  });

  it('notifies the parent of an edit while invalidating the pending response', async () => {
    const owner = new CarbonRequestOwner('session-a:company-a:1:p1');
    let resolve!: (value: CarbonComparison) => void;
    const publish = vi.fn();
    const hideResult = vi.fn();
    const onInputsChange = vi.fn();
    const pending = owner.run(() => new Promise<CarbonComparison>(done => { resolve = done; }), publish);
    invalidateCarbonInputs(owner, hideResult, onInputsChange);
    resolve(comparison);
    expect(await pending).toBe(false);
    expect(publish).not.toHaveBeenCalled();
    expect(hideResult).toHaveBeenCalledOnce();
    expect(onInputsChange).toHaveBeenCalledOnce();
    expect(hideResult.mock.invocationCallOrder[0]).toBeLessThan(onInputsChange.mock.invocationCallOrder[0]);
  });

  it('ignores delayed responses after edits, scope changes or disposal', async () => {
    for (const invalidate of ['edit', 'scope', 'dispose']) {
      const owner = new CarbonRequestOwner('session-a:company-a:1:p1');
      let resolve!: (value: CarbonComparison) => void;
      const pending = new Promise<CarbonComparison>(done => { resolve = done; });
      const publish = vi.fn();
      const task = owner.run(() => pending, publish);
      if (invalidate === 'edit') owner.invalidate();
      if (invalidate === 'scope') owner.setScope('session-b:company-b:2:p2');
      if (invalidate === 'dispose') owner.dispose();
      resolve(comparison);
      expect(await task).toBe(false);
      expect(publish).not.toHaveBeenCalled();
    }
  });
  it('only publishes the newest request and cannot resurrect A after A to B to A', async () => {
    const owner = new CarbonRequestOwner('a');
    let resolve!: (value: CarbonComparison) => void;
    const oldPublish = vi.fn();
    const old = owner.run(() => new Promise<CarbonComparison>(done => { resolve = done; }), oldPublish);
    owner.setScope('b'); owner.setScope('a');
    const publish = vi.fn();
    expect(await owner.run(async () => comparison, publish)).toBe(true);
    expect(publish).toHaveBeenCalledWith(comparison);
    resolve(comparison); expect(await old).toBe(false);
    expect(oldPublish).not.toHaveBeenCalled();
  });
});
