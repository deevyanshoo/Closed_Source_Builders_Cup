import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { CountryPricingPanel, requestCountryPricing } from './demo-country-pricing';
import type { CountryPricing } from './demo-country-pricing';

const view = {
  company_id: 'solara-cables', reporting_currency: 'USD', currencies: ['USD', 'AED', 'EUR'], snapshot_version: 'commodity-benchmarks-v1', benchmarks_synthetic: false, synthetic_quotes: true,
  source: { publisher: 'World Bank', title: 'Commodity Price Data (The Pink Sheet)', page_url: 'https://www.worldbank.org/en/research/commodity-markets', licence: 'Creative Commons Attribution 4.0 (CC BY 4.0)', retrieved_at: '2026-10-08T16:31:57+00:00', source_updated: '2026-10-02' },
  benchmarks: [
    { material_id: 'copper', name: 'Copper cathode', unit: 'kg', status: 'benchmark', series: 'Copper', month: '2026-09', value: '14474', currency: 'USD', per_unit: 'tonne', note: 'Benchmark is LME grade A copper cathode.', age_days: 15, stale: false,
      per_company_unit: { value: '14.474', unit: 'kg', formula: '14474 USD per tonne / 1000 kg per tonne' },
      conversions: [{ currency: 'USD', status: 'same_currency', rate: '1', amount: '14474', per_unit: 'tonne', observation_date: null, provider: null, formula: null },
        { currency: 'AED', status: 'fx_unavailable', rate: null, amount: null, per_unit: 'tonne', observation_date: null, provider: null, formula: null },
        { currency: 'EUR', status: 'converted', rate: '0.8', amount: '11579.2', per_unit: 'tonne', observation_date: '2026-10-14', provider: 'ECB', formula: 'USD->EUR = 1 / EUR->USD 1.25 (ECB reference rate, 2026-10-14)' }] },
    { material_id: 'aluminium', name: 'Aluminium conductor', unit: 'kg', status: 'benchmark', series: 'Aluminum', month: '2026-06', value: '3000', currency: 'USD', per_unit: 'tonne', note: 'Ingot.', age_days: 107, stale: true, conversions: [] },
    { material_id: 'polymer', name: 'Cable insulation polymer', unit: 'kg', status: 'no_public_benchmark', message: 'no public benchmark' }],
  quotes: [{ offer_id: 'q1', supplier: 'Swift Copper Trading Ltd [SYNTHETIC]', origin_country_code: 'IN', material_id: 'copper', unit_price: '10.50', currency: 'USD', per_unit: 'kg', as_of: '2026-10-14', synthetic: true, document: 'SYN-express-copper.pdf' }],
  fx_rates_used: [['2026-10-14', 'ECB']], limitations: ['Benchmarks are monthly averages.'],
} as unknown as CountryPricing;

describe('country-wise commodity pricing (#110)', () => {
  it('shows each dated benchmark with source, conversions and explicit gaps', () => {
    const html = renderToStaticMarkup(<CountryPricingPanel view={view} onLoad={() => {}} />);
    expect(html).toContain('Copper · 2026-09');
    expect(html).toContain('14474 USD per tonne');
    expect(html).toContain('14.474 USD per kg');
    expect(html).toContain('11579.2 EUR per tonne');
    expect(html).toContain('USD-&gt;EUR = 1 / EUR-&gt;USD 1.25 (ECB reference rate, 2026-10-14)');
    expect(html).toContain('AED: FX unavailable');
    expect(html).toContain('no public benchmark');
    expect(html).toContain('Stale: 107 days after the benchmark month');
    expect(html).toContain('href="https://www.worldbank.org/en/research/commodity-markets"');
    expect(html).toContain('Creative Commons Attribution 4.0 (CC BY 4.0)');
  });

  it('keeps accepted synthetic quotes in a separate table from public benchmarks', () => {
    const html = renderToStaticMarkup(<CountryPricingPanel view={view} onLoad={() => {}} />);
    const benchmarkAt = html.indexOf('Public benchmarks');
    const quotesAt = html.indexOf('Accepted quotes by origin (synthetic)');
    expect(benchmarkAt).toBeGreaterThan(-1);
    expect(quotesAt).toBeGreaterThan(benchmarkAt);
    expect(html).toContain('Swift Copper Trading Ltd');
    expect(html).toContain('IN');
    expect(html).toContain('10.50 USD per kg');
  });

  it('reads the route with the session cookie', async () => {
    const send = vi.fn(async () => new Response(JSON.stringify(view), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    const result = await requestCountryPricing(send as unknown as typeof fetch);
    expect(result.company_id).toBe('solara-cables');
    expect(send).toHaveBeenCalledWith('/api/demo/country-pricing', { credentials: 'same-origin' });
  });
});
