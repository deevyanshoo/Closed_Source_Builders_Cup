import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import type { CompanyProfile, RiskReport } from './demo-api';
import { delayForMaterial, InventoryOrders, InventoryWorkspace, materialGapLabel, materialHasRunnableSourcing, materialRiskSummary } from './zoorq-inventory';
import { assessMaterialSupply } from './zoorq-supply-timeline';

const company: CompanyProfile = {
  company_id: 'solara', name: 'Solara Cables', sector: 'Electrical manufacturing', size: 'Mid-size', synthetic: true, as_of: '2026-10-14', description: 'Synthetic profile', locations: [], reporting_currency: 'USD',
  materials: [
    { material_id: 'copper', name: 'Copper', quantity_unit: 'kg', stock: '600', consumption_per_day: '100', monthly_requirement: '3000', criticality: 'high', keywords: [] },
    { material_id: 'polymer', name: 'Polymer', quantity_unit: 'rolls', stock: '500', consumption_per_day: '50', monthly_requirement: '1500', criticality: 'low', keywords: [] },
  ],
  suppliers: [{ supplier_id: 'copper', name: 'Copper Works', country: 'India', settlement_currency: 'USD', materials: ['copper'], lead_time_days: 10 }],
  orders: [{ order_id: 'order-1', material_id: 'copper', quantity: '200', unit: 'kg', need_by: '2026-10-25', status: 'open' }],
  routes: [{ route_id: 'r1', origin: 'India', destination: 'India', via: [], materials: ['copper'], direction: 'import', currency: 'USD' }, { route_id: 'r1', origin: 'India', destination: 'India', via: [], materials: ['copper'], direction: 'import', currency: 'USD' }],
  currencies: ['USD'], news_keywords: [], quote_coverage: { materials: [], fixture_ids: [], note: '' }, buyer_defaults: null,
};

const report: RiskReport = {
  company_id: 'solara', context_version: 1, checked_at: '2026-10-16', monitoring: 'while_workspace_open', news_refresh_seconds: 600,
  companies: [{ company_id: 'solara', company_name: 'Solara Cables', sector: company.sector, checked_at: '2026-10-16', alerts: [
    { alert_id: 'a', company_id: 'solara', category: 'inventory', severity: 'high', title: 'A', summary: '', impact: '', material_ids: ['copper'], provenance: 'synthetic_profile', confidence: { level: 'high', reason: '' }, evidence: [], metrics: {}, recovery_actions: [] },
    { alert_id: 'b', company_id: 'solara', category: 'supplier_delay', severity: 'medium', title: 'B', summary: '', impact: '', material_ids: ['copper'], provenance: 'synthetic_profile', confidence: { level: 'high', reason: '' }, evidence: [], metrics: {}, recovery_actions: [] },
  ], coverage: { materials_evaluated: 2, news_state: 'unavailable', news_items_evaluated: 0, sources: [] }, data_gaps: [] }],
  summary: { high: 1, medium: 1, low: 0, companies_with_alerts: 1 }, limitations: [],
};

describe('InventoryWorkspace', () => {
  it('prioritizes the uncovered material and keeps unit-bearing stock and demand separate', () => {
    const html = renderToStaticMarkup(<InventoryWorkspace company={company} />);
    expect(html.indexOf('Copper</')).toBeLessThan(html.indexOf('Polymer</'));
    expect(html).toContain('600 kg');
    expect(html).toContain('500 rolls');
    expect(html).toContain('200 kg');
    expect(html).not.toContain('1100');
    expect(html).toContain('4 days');
  });

  it('counts two alerts for one affected material and ignores a stale company report', () => {
    expect(materialRiskSummary(company, report)).toEqual({ alerts: 2, affectedMaterials: 1 });
    expect(materialRiskSummary({ ...company, company_id: 'other' }, report)).toEqual({ alerts: 0, affectedMaterials: 0 });
    const html = renderToStaticMarkup(<InventoryWorkspace company={{ ...company, company_id: 'other', name: 'Other company' }} report={report} />);
    expect(html).toContain('Other company');
    expect(html).not.toContain('Solara Cables');
    expect(html).not.toContain('2 alerts');
  });

  it('scopes a what-if delay to one material in one company', () => {
    const scenario = { companyId: 'solara', materialId: 'copper', days: 5 };
    expect(delayForMaterial(scenario, 'solara', 'copper')).toBe(5);
    expect(delayForMaterial(scenario, 'solara', 'polymer')).toBe(0);
    expect(delayForMaterial(scenario, 'other', 'copper')).toBe(0);
  });

  it('presents unknown coverage when a matching lead or valid demand is unavailable', () => {
    const noLead = renderToStaticMarkup(<InventoryWorkspace company={{ ...company, suppliers: [] }} />);
    expect(noLead).toContain('Unknown coverage');
    expect(noLead).toContain('Gap unknown until stock, daily demand, and a matching supplier lead are available.');
    const noDemand = renderToStaticMarkup(<InventoryWorkspace company={{ ...company, materials: [{ ...company.materials[0], consumption_per_day: '0' }] }} />);
    expect(noDemand).toContain('Unknown coverage');
    expect(noDemand).not.toContain('approximately 4 days');
  });

  it('keeps unmatched company orders reachable in the Orders view without mixing companies', () => {
    const unmatched = { order_id: 'unmatched-order', material_id: 'unknown-material', quantity: '8', unit: 'spools', need_by: '2026-11-02', status: 'open' };
    const withUnmatched = { ...company, orders: [...company.orders, unmatched] };
    const html = renderToStaticMarkup(<InventoryOrders company={withUnmatched} />);
    expect(html).toContain('order-1');
    expect(html).toContain('unmatched-order');
    expect(html).toContain('8 spools');
    expect(html).toContain('Not listed in material profile');
    const other = renderToStaticMarkup(<InventoryOrders company={{ ...company, company_id: 'other', orders: [] }} />);
    expect(other).not.toContain('unmatched-order');
    const workspace = renderToStaticMarkup(<InventoryWorkspace company={withUnmatched} />);
    expect(workspace).toContain('role="tablist"');
    expect(workspace).toContain('Orders <span>2</span>');
    expect(workspace).toContain('unmatched-order');
  });

  it('offers sourcing only when the selected material has runnable quote coverage', () => {
    expect(materialHasRunnableSourcing(company, 'copper')).toBe(false);
    const covered = { ...company, quote_coverage: { ...company.quote_coverage, materials: ['copper'], note: 'Copper fixture only.' } };
    expect(materialHasRunnableSourcing(covered, 'copper')).toBe(true);
    expect(materialHasRunnableSourcing(covered, 'polymer')).toBe(false);
    const html = renderToStaticMarkup(<InventoryWorkspace company={covered} onSource={() => {}} />);
    expect(html).toContain('No runnable quote scenario for Polymer');
    expect(html).toContain('Copper fixture only.');
    expect(html).toMatch(/Review sourcing for Copper[^<]*<svg/);
  });

  it('keeps a covered material neutral when it has a news watch signal', () => {
    const covered = { ...company, suppliers: [...company.suppliers, { ...company.suppliers[0], supplier_id: 'polymer', name: 'Polymer Works', materials: ['polymer'], lead_time_days: 4 }] };
    const newsAlert = { ...report.companies[0].alerts[0], alert_id: 'polymer-news', category: 'news' as const, material_ids: ['polymer'], title: 'Polymer route watch', impact: 'Possible transport disruption; no stock shortage established.', recovery_actions: [{ action_id: 'review', label: 'Check route evidence', message: 'Explain the Polymer route signal.', requires_review: true }] };
    const withNews: RiskReport = { ...report, companies: [{ ...report.companies[0], alerts: [newsAlert] }] };
    const html = renderToStaticMarkup(<InventoryWorkspace company={covered} report={withNews} onAsk={() => {}} />);
    expect(html).toContain('Stock gaps</dt><dd>1</dd>');
    expect(html).toContain('Stock gaps <span>1</span>');
    expect(html).toMatch(/class="zoorq-material "><summary><span class="zoorq-material-identity"><strong>Polymer<\/strong><small>low criticality · No gap indicated · 1 watch signal/);
    expect(html).toContain('Polymer route watch');
    expect(html).toContain('Possible transport disruption; no stock shortage established.');
    expect(html).toContain('Check route evidence');
    expect(html).toContain('1 watch signal');
    const noGap = { ...covered, suppliers: covered.suppliers.map(supplier => supplier.supplier_id === 'copper' ? { ...supplier, lead_time_days: 4 } : supplier) };
    const allCovered = renderToStaticMarkup(<InventoryWorkspace company={noGap} report={withNews} />);
    expect(allCovered).toContain('No measured stock gap');
    expect(allCovered).toContain('Stock gaps</dt><dd>0</dd>');
    expect(allCovered).toContain('Monitor alerts</dt><dd>1');
    expect(allCovered).not.toContain('zoorq-material-risk');
  });

  it('labels the changing local gap as a scenario and cleans only supplier display names', () => {
    const baseline = assessMaterialSupply(company.materials[0], company.suppliers);
    const delayed = assessMaterialSupply(company.materials[0], company.suppliers, 14);
    expect(materialGapLabel(baseline.gapDays, 0)).toBe('Profile: 4 days gap');
    expect(materialGapLabel(delayed.gapDays, 14)).toBe('Scenario: 18 days gap');
    const tagged = { ...company, suppliers: [{ ...company.suppliers[0], name: 'Copper Works [SYNTHETIC]' }] };
    const html = renderToStaticMarkup(<InventoryWorkspace company={tagged} />);
    expect(html).toContain('Copper Works · India');
    expect(html).not.toContain('Copper Works [SYNTHETIC]');
  });
});
