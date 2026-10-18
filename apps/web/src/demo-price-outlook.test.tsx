import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { acceptOutlook, PriceOutlookView, priceOutlookExport, requestFxOutlook, requestPriceOutlook } from './demo-price-outlook';
import type { FxOutlook, PriceOutlook } from './demo-price-outlook';

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
