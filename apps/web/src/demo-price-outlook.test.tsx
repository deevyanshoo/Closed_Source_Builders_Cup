import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { acceptBudget, acceptOutlook, PriceOutlookView, priceOutlookExport, requestBudgetAtRisk, requestFxOutlook, requestPriceOutlook } from './demo-price-outlook';
import type { BudgetAtRisk, FxOutlook, PriceOutlook } from './demo-price-outlook';

const backtest = (o: Partial<Record<string, number | string | null>> = {}) => ({ split: 'test', selected_model: 'naive', n_origins: 24, mape: null, naive_mape: 0.041, mape_ratio: null, directional_acc: null, coverage: null, naive_coverage: 0.792, skill: null, bootstrap_ci_lower: null, bootstrap_ci_upper: null, ...o });
const series = {
  series_id: 'commodity:copper', kind: 'commodity', unit: 'USD per mt', source_series: 'Copper', data_date: '2026-08', latest_value: 14326, issue_month: '2026-09', freshness_state: 'HISTORICAL_NOT_CURRENT', age_days: 46, stale: true, stale_label: 'Data is older than 45 days: latest month 2026-08.',
  source: { publisher: 'World Bank', url: 'https://www.worldbank.org/en/research/commodity-markets', licence: 'Creative Commons Attribution 4.0 (CC BY 4.0)', attribution: 'Source: World Bank.', retrieved_at: '2026-10-08T16:31:57Z' },
  selection: { selected_model: 'naive', best_challenger: 'ets', beats_naive_on_validation: false },
  horizons: [
    { horizon: 1, target_month: '2026-10', status: 'RANGE_ONLY', label: 'range based on past volatility, not a directional forecast', model: 'naive', p10: 13500.5, p50: 14326, p90: 15100.25, naive_value: 14326, backtest: backtest() },
    { horizon: 3, target_month: '2026-12', status: 'PREDICTION', label: 'Projection with uncertainty, not a recommendation', model: 'ets', p10: 13000, p50: 14500, p90: 16000, naive_value: 14326, backtest: backtest({ selected_model: 'ets', mape: 0.05, naive_mape: 0.06, mape_ratio: 0.833, coverage: 0.79, skill: 0.12, bootstrap_ci_lower: 10, bootstrap_ci_upper: 90 }) },
    { horizon: 6, target_month: '2027-03', status: 'NO_RELIABLE_FORECAST', label: 'naive baseline, not a prediction', model: 'naive', p10: null, p50: null, p90: null, naive_value: 14326, backtest: backtest({ naive_coverage: 0.6 }) },
  ],
};
const price = { kind: 'price', company_id: 'solara-cables', context_version: 3, material_id: 'copper', label: 'Projection with uncertainty, not a recommendation', status: 'available', match: 'exact', note: 'Copper cathode.', series } as unknown as PriceOutlook;
const fx = { kind: 'fx', company_id: 'solara-cables', context_version: 3, label: 'Projection with uncertainty, not a recommendation', status: 'available', reporting_currency: 'USD',
  currencies: [{ currency: 'EUR', status: 'available', series: { ...series, series_id: 'fx:EUR', unit: 'EUR per USD', source_series: 'WS_XRU M.DE.EUR.A', stale: false, stale_label: null, source: { ...series.source, publisher: 'Bank for International Settlements (BIS)' } } },
    { currency: 'TZS', status: 'no history available', reason: 'BIS series not eligible; values are not interpolated.' }] } as unknown as FxOutlook;

describe('price outlook panel (#111)', () => {
  it('shows each horizon only as its status allows, with the scorecard, source and stale label', () => {
    const html = renderToStaticMarkup(<PriceOutlookView price={price} fx={fx} />);
    expect(html).toContain('Projection with uncertainty, not a recommendation');
    expect(html).toContain('Range 13,501 to 15,100 USD per mt');  // display rounds to 5 significant figures
    expect(html).toContain('range based on past volatility, not a directional forecast');
    expect(html).toContain('P10 13,000 · P50 14,500 · P90 16,000 USD per mt');
    expect(html).toContain('No reliable forecast: the model does not beat a simple baseline on past data');
    expect(html).toContain('Naive baseline, not a prediction: 14,326 USD per mt');
    expect(html).toContain('ets MAPE 5.0% vs naive 6.0% · coverage 79.0% · skill 0.12 · 24 test origins');
    expect(html).toContain('Data is older than 45 days: latest month 2026-08.');
    expect(html).toContain('href="https://www.worldbank.org/en/research/commodity-markets"');
    expect(html).toContain('Creative Commons Attribution 4.0 (CC BY 4.0)');
    expect(html).toContain('Issued 2026-09 from data through 2026-08 · historical, not current');
    expect(html).toContain('TZS: no history available');
    expect(html).not.toMatch(/\b(buy|wait|should purchase|saving)\b/i);
  });

  it('never prints a number for a no-reliable horizon other than the labelled naive value', () => {
    const only = { ...price, series: { ...series, horizons: [series.horizons[2]] } } as unknown as PriceOutlook;
    const html = renderToStaticMarkup(<PriceOutlookView price={only} fx={null} />);
    expect(html).not.toContain('P10');
    expect(html).not.toContain('Range');
  });

  it('accepts a reply only for the same session, company, context and generation (A to B to A)', () => {
    const owner = { sessionId: 's1', companyId: 'solara-cables', contextVersion: 3, generation: 1 };
    expect(acceptOutlook(owner, owner, price)).toBe(true);
    expect(acceptOutlook(owner, { ...owner, companyId: 'harvest-foods', contextVersion: 4, generation: 2 }, price)).toBe(false);
    // back to A with a new context: the old A reply is still refused
    expect(acceptOutlook(owner, { ...owner, contextVersion: 5, generation: 3 }, price)).toBe(false);
    expect(acceptOutlook(owner, { ...owner, sessionId: 's2' }, price)).toBe(false);
    expect(acceptOutlook(owner, owner, { ...price, company_id: 'harvest-foods' })).toBe(false);
  });

  it('reads both routes with the session cookie and builds the export entry', async () => {
    const send = vi.fn(async (url: string) => new Response(JSON.stringify(url.includes('fx') ? fx : price), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    expect((await requestPriceOutlook(send as unknown as typeof fetch, 'copper')).company_id).toBe('solara-cables');
    expect(send).toHaveBeenCalledWith('/api/demo/price-outlook?material_id=copper', { credentials: 'same-origin' });
    await requestFxOutlook(send as unknown as typeof fetch);
    expect(send).toHaveBeenCalledWith('/api/demo/fx-outlook', { credentials: 'same-origin' });
    const exported = priceOutlookExport(price, fx);
    expect(exported).toMatchObject({ schema: 'price-outlook-export.v1', company_id: 'solara-cables', context_version: 3, label: 'Projection with uncertainty, not a recommendation' });
    expect(() => priceOutlookExport(price, { ...fx, company_id: 'terrapack' })).toThrow('scope');
  });
});

const money = (amount: string, display: string, change?: string, changeDisplay?: string, pct?: string) => ({ amount, display, ...(change ? { change, change_display: changeDisplay, change_percent: pct } : {}) });
const budget = {
  kind: 'budget', company_id: 'solara-cables', context_version: 3, plan_id: 'plan-1', material_id: 'copper', status: 'available', currency: 'USD',
  label: 'benchmark cost exposure, not a supplier quote, landed cost or recommendation', match: 'proxy', note: 'Copper rod: cathode benchmark, rod premium not included.',
  quantity: { value: '8000', unit: 'kg', benchmark_quantity: '8', benchmark_unit: 'mt' }, benchmark_unit: 'USD per mt', data_date: '2026-08', issue_month: '2026-09', freshness_state: 'HISTORICAL_NOT_CURRENT',
  source: series.source, fx: null, fx_note: null, today: money('114608.0', '114,608.00'),
  horizons: [
    { horizon: 1, target_month: '2026-10', status: 'RANGE_ONLY', label: 'range based on past volatility, not a directional forecast', low: money('105762.38128', '105,762.38', '-8845.61872', '-8,845.62', '-7.72'), mid: money('114608.0', '114,608.00', '0.0', '0.00', '0.00'), high: money('124494.764', '124,494.76', '9886.764', '9,886.76', '8.63'), hit_rate: { value: '0.7916666666666666', basis: 'naive band coverage', test_origins: 24 } },
    { horizon: 6, target_month: '2027-02', status: 'NO_RELIABLE_FORECAST', label: 'naive baseline, not a prediction', reason: 'no tested range for this horizon' },
  ],
} as unknown as BudgetAtRisk;

describe('budget at risk (#130)', () => {
  it('shows today, each tested horizon in money with its hit rate, and the reason otherwise', () => {
    const html = renderToStaticMarkup(<PriceOutlookView price={price} fx={fx} budget={budget} />);
    expect(html).toContain('Budget at risk for this purchase');
    expect(html).toContain('Benchmark cost today for 8000 kg copper: <strong>USD 114,608.00</strong>');
    expect(html).toContain('USD 105,762.38 to USD 124,494.76');
    expect(html).toContain('middle USD 114,608.00');
    expect(html).toContain('-8,845.62 (-7.72%) to +9,886.76 (+8.63%)');
    expect(html).toContain('range held 79% of the time on 24 test months (naive band coverage)');
    expect(html).toContain('no tested range for this horizon');
    expect(html).toContain('benchmark cost exposure, not a supplier quote, landed cost or recommendation');
    expect(html).toContain('Proxy benchmark: Copper rod: cathode benchmark, rod premium not included.');
    expect(html.indexOf('Budget at risk')).toBeLessThan(html.indexOf('Benchmark price: copper'));
    const block = renderToStaticMarkup(<PriceOutlookView price={null} fx={null} budget={budget} />).replace('Projection with uncertainty, not a recommendation', '');
    expect(block.replace(budget.label ?? '', '')).not.toMatch(/\b(buy|wait|saving|recommend)/i);
  });

  it('shows the FX month and note for a converted currency and the reason when no figure is possible', () => {
    const inr = { ...budget, currency: 'INR', fx: { rate: '95.446733', quote: 'INR per USD', month: '2026-08', series_id: 'fx:INR', source: 'Source: BIS bilateral exchange rate statistics.', publisher: 'Bank for International Settlements (BIS)', url: 'https://data.bis.org/topics/XRU' }, fx_note: 'FX held at 2026-08 rate; see the FX outlook for currency uncertainty.' } as unknown as BudgetAtRisk;
    const html = renderToStaticMarkup(<PriceOutlookView price={null} fx={null} budget={inr} />);
    expect(html).toContain('95.446733 INR per USD (2026-08, Bank for International Settlements (BIS))');
    expect(html).toContain('FX held at 2026-08 rate; see the FX outlook for currency uncertainty.');
    const litre = { ...budget, status: 'unit_basis_required', reason: 'The plan quantity is in litre and the benchmark is USD per mt; a stated mass per litre is needed (for example oil per litre needs a stated density). No mass is assumed.', horizons: [], today: undefined } as unknown as BudgetAtRisk;
    const reason = renderToStaticMarkup(<PriceOutlookView price={null} fx={null} budget={litre} />);
    expect(reason).toContain('needs a stated density');
    expect(reason).not.toContain('Benchmark cost today');
    const none = { ...litre, status: 'no_current_plan', reason: 'Run a comparison first.', plan_id: null } as unknown as BudgetAtRisk;
    expect(renderToStaticMarkup(<PriceOutlookView price={null} fx={null} budget={none} />)).toContain('Run a comparison first.');
  });

  it('accepts a budget reply only for the same plan id as well as session, company, context and generation', () => {
    const owner = { sessionId: 's1', companyId: 'solara-cables', contextVersion: 3, generation: 1, planId: 'plan-1' };
    expect(acceptBudget(owner, owner, budget)).toBe(true);
    expect(acceptBudget(owner, { ...owner, planId: 'plan-2', generation: 2 }, budget)).toBe(false);
    expect(acceptBudget(owner, owner, { ...budget, plan_id: 'plan-0' })).toBe(false);
    expect(acceptBudget(owner, { ...owner, companyId: 'harvest-foods', contextVersion: 4, generation: 2 }, budget)).toBe(false);
    expect(acceptBudget(owner, { ...owner, contextVersion: 5, generation: 3 }, budget)).toBe(false);
    const none = { ...owner, planId: null };
    expect(acceptBudget(none, none, { ...budget, status: 'no_current_plan', plan_id: null })).toBe(true);
  });

  it('requests the plan-bound route and exports the budget with the outlook', async () => {
    const send = vi.fn(async () => new Response(JSON.stringify(budget), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    await requestBudgetAtRisk(send as unknown as typeof fetch, 'copper', 'plan 1');
    expect(send).toHaveBeenCalledWith('/api/demo/budget-at-risk?material_id=copper&plan_id=plan%201', { credentials: 'same-origin' });
    await requestBudgetAtRisk(send as unknown as typeof fetch, 'copper', null);
    expect(send).toHaveBeenCalledWith('/api/demo/budget-at-risk?material_id=copper', { credentials: 'same-origin' });
    expect(priceOutlookExport(price, fx, budget).budget).toEqual(budget);
    expect(priceOutlookExport(price, fx).budget).toBeNull();
    expect(() => priceOutlookExport(price, fx, { ...budget, context_version: 9 })).toThrow('scope');
  });
});
