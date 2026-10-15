import { readFileSync } from 'node:fs';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { OfferView, PlanView } from './demo-api';
import { CompanyOverview } from './demo-company-news';
import { applyDemoTitle, DEMO_TITLE, roundDecimalText } from './demo-display';
import { formatOperatorAttention } from './demo-operator';
import { PlanResults } from './demo-page';

const materials = [{ material_id: 'copper', name: 'Copper' }];

describe('hosted demo polish (#107)', () => {
  it('rounds fractional stock cover to one decimal for display only, keeping whole days as stated', () => {
    const line = formatOperatorAttention({ material_id: 'copper', synthetic: true, stock_cover_days: '6.0000600006000060000600006', minimum_lead_time_days: 7 }, materials);
    expect(line.detail).toBe('Stock cover: 6.0 days · shortest listed supplier lead: 7 days');
    expect(formatOperatorAttention({ material_id: 'copper', synthetic: true, stock_cover_days: '12', minimum_lead_time_days: 15 }, materials).detail).toContain('Stock cover: 12 days');
  });

  it('rounds decimal text half up without floating point', () => {
    expect(roundDecimalText('5.25', 1)).toBe('5.3');
    expect(roundDecimalText('9.96', 1)).toBe('10.0');
    expect(roundDecimalText('0.04', 1)).toBe('0.0');
    expect(roundDecimalText('123456789012345678.95', 1)).toBe('123456789012345679.0');
    expect(roundDecimalText('not a number', 1)).toBe('not a number');
  });

  it('gives /demo its own browser tab title', () => {
    const doc = { title: 'Closed Source | Planning' };
    applyDemoTitle(doc);
    expect(DEMO_TITLE).toBe('Closed Source | Hosted demo');
    expect(doc.title).toBe(DEMO_TITLE);
  });

  it('labels the quoted unit price and says the total includes the extra cost', () => {
    const offer = { offer_id: 'q1', document: { filename: 'quote.csv' } } as unknown as OfferView;
    const plan = { plan_id: 'p1', synthetic: true, decision_date: '2026-10-14', need_by: '2026-10-30', material: 'copper', quantity_kg: '8000', current_stock_kg: '0', required_purchase_kg: '8000', reporting_currency: 'USD', assumptions: { additional_cost_per_kg: '0.25', fx_source: 'synthetic' }, alternatives: [{ offer_id: 'q1', supplier: 'Swift [SYNTHETIC]', feasible: true, reasons: [], arrival_date: '2026-10-20', unit_price_per_kg: '10', total_cost: '82000', quantity_kg: '8000' }], selected_offer_id: 'q1', total_cost: '82000', explanation: 'Swift is cheapest.', forecast_action: 'ABSTAIN', customer_savings: null, offer_versions: { q1: 1 } } as unknown as PlanView;
    const html = renderToStaticMarkup(<PlanResults plan={plan} offers={[offer]} />);
    expect(html).toContain('<th>Quoted unit price</th>');
    expect(html).toContain('<th>Total<small>includes extra cost</small></th>');
    expect(html).not.toContain('<th>Unit price</th>');
  });

  it('keeps the orders date column whole by wrapping long order ids instead', () => {
    const company = { company_id: 'meridian-pharma', name: 'Meridian Pharma', sector: 'Pharma', size: 'Enterprise', synthetic: true, as_of: '2026-10-14', description: 'Fictional.', locations: [{ country: 'Switzerland', city: 'Basel' }], reporting_currency: 'CHF', materials: [], suppliers: [], routes: [], currencies: [], news_keywords: [], quote_coverage: { materials: [], fixture_ids: [], note: '' },
      orders: [{ order_id: 'meridian-pharma-vials-scenario-order', material_id: 'vials', quantity: '220000', unit: 'unit', need_by: '2026-11-05', status: 'open' }] } as never;
    const html = renderToStaticMarkup(<CompanyOverview company={company} />);
    expect(html).toContain('<table class="demo-orders-table">');
    expect(html).toContain('<td class="demo-nowrap">2026-11-05</td>');
    const css = readFileSync(new URL('./demo.css', import.meta.url), 'utf8');
    expect(css).toContain('.demo-company-grid table.demo-orders-table{min-width:0}');
    expect(css).toContain('.demo-orders-table td:first-child{overflow-wrap:anywhere');
    expect(css).toContain('.demo-nowrap{white-space:nowrap}');
  });
});
