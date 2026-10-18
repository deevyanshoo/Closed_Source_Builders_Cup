import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { acceptPricing, CountryPricingPanel, requestCountryPricing } from './demo-country-pricing';
import type { CountryPricing } from './demo-country-pricing';

const view = {
  title: 'Market reference: global benchmarks and synthetic origin quotes', context_version: 2, fx_basis: 'BIS monthly average from the committed snapshot (latest month shown on each rate), not a live rate',
  coverage: { state: 'partial', benchmarked: ['aluminium', 'copper'], without_benchmark: ['polymer'], note: 'Only materials with an exact series mapping are benchmarked; others are not guessed.' },
  company_id: 'solara-cables', reporting_currency: 'USD', currencies: ['USD', 'AED', 'EUR'], snapshot_version: 'commodity-benchmarks-v1', benchmarks_synthetic: false, synthetic_quotes: true,
  source: { publisher: 'World Bank', title: 'Commodity Price Data (The Pink Sheet)', page_url: 'https://www.worldbank.org/en/research/commodity-markets', licence: 'Creative Commons Attribution 4.0 (CC BY 4.0)', retrieved_at: '2026-10-08T16:31:57+00:00', source_updated: '2026-10-02' },
  benchmarks: [
    { material_id: 'copper', name: 'Copper cathode', unit: 'kg', status: 'benchmark', series: 'Copper', month: '2026-09', value: '14474', currency: 'USD', per_unit: 'tonne', note: 'Benchmark is LME grade A copper cathode.', age_days: 15, stale: false,
      per_company_unit: { value: '14.474', unit: 'kg', formula: '14474 USD per tonne / 1000 kg per tonne' },
      conversions: [{ currency: 'USD', status: 'same_currency', rate: '1', amount: '14474', per_unit: 'tonne', observation_date: null, provider: null, formula: null },
        { currency: 'AED', status: 'converted', rate: '3.6725', amount: '53155.7', per_unit: 'tonne', observation_date: '2026-08', provider: 'BIS', basis: 'snapshot', formula: 'USD->AED = 3.6725 (BIS monthly average 2026-08, WS_XRU M.AE.AED.A; snapshot, not a live rate)' },
        { currency: 'EUR', status: 'converted', rate: '0.8', amount: '11579.2', per_unit: 'tonne', observation_date: '2026-10-14', provider: 'ECB', formula: 'USD->EUR = 1 / EUR->USD 1.25 (ECB reference rate, 2026-10-14)' }] },
    { material_id: 'aluminium', name: 'Aluminium conductor', unit: 'kg', status: 'benchmark', series: 'Aluminum', month: '2026-06', value: '3000', currency: 'USD', per_unit: 'tonne', note: 'Ingot.', age_days: 107, stale: true, conversions: [] },
    { material_id: 'polymer', name: 'Cable insulation polymer', unit: 'kg', status: 'no_public_benchmark', message: 'no public benchmark' }],
  quote_note: 'Demo quotes are synthetic and not calibrated to market prices',
  quotes: [{ offer_id: 'q1', item_index: 0, supplier: 'Swift Copper Trading Ltd [SYNTHETIC]', origin_country_code: 'IN', material_id: 'copper', unit_price: '10.50', currency: 'USD', per_unit: 'kg', as_of: '2026-10-14', synthetic: true, document: 'SYN-express-copper.pdf',
      comparable: { amount: '10.5', currency: 'USD', per_unit: 'kg' }, vs_benchmark: { status: 'compared', difference_percent: '-27.5', benchmark_per_unit: '14.474', currency: 'USD', per_unit: 'kg', benchmark_month: '2026-09', series: 'Copper', label: 'synthetic quote compared with a public benchmark', formula: '(10.5 - 14.474) / 14.474 x 100, rounded to 0.1' } },
    { offer_id: 'q2', item_index: 0, supplier: 'Atacama Red Metals SpA [SYNTHETIC]', origin_country_code: 'CL', material_id: 'copper', unit_price: '9412.50', currency: 'USD', per_unit: 'tonne', as_of: '2026-10-14', synthetic: true, document: 'SYN-late-copper.pdf',
      comparable: { amount: '9.4125', currency: 'USD', per_unit: 'kg' }, vs_benchmark: { status: 'compared', difference_percent: '-35.0', benchmark_per_unit: '14.474', currency: 'USD', per_unit: 'kg', benchmark_month: '2026-09', series: 'Copper', label: 'synthetic quote compared with a public benchmark', formula: 'f' } },
    { offer_id: 'q3', item_index: 0, supplier: 'Europa Copper SA [SYNTHETIC]', origin_country_code: 'DE', material_id: 'copper', unit_price: '8.00', currency: 'EUR', per_unit: 'kg', as_of: '2026-10-14', synthetic: true, document: 'SYN-eu-copper.csv',
      comparable: { amount: '8', currency: 'EUR', per_unit: 'kg' }, vs_benchmark: { status: 'fx_unavailable', difference_percent: null, label: 'synthetic quote compared with a public benchmark', reason: 'No same-day ECB reference rate for USD to EUR is loaded.' } }],
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
    expect(html).toContain('USD-&gt;AED = 3.6725 (BIS monthly average 2026-08, WS_XRU M.AE.AED.A; snapshot, not a live rate)');
    expect(html).toContain('no public benchmark');
    expect(html).toContain('Stale: 107 days after the benchmark month');
    expect(html).toContain('href="https://www.worldbank.org/en/research/commodity-markets"');
    expect(html).toContain('Creative Commons Attribution 4.0 (CC BY 4.0)');
  });

  it('keeps accepted synthetic quotes in a separate table from public benchmarks', () => {
    const html = renderToStaticMarkup(<CountryPricingPanel view={view} onLoad={() => {}} />);
    const benchmarkAt = html.indexOf('Global benchmarks');
    const quotesAt = html.indexOf('Synthetic supplier quotes by origin');
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

  it('says quotes are not calibrated and shows each one in one unit with its labelled difference', () => {
    const html = renderToStaticMarkup(<CountryPricingPanel view={view} onLoad={() => {}} />);
    expect(html).toContain('Demo quotes are synthetic and not calibrated to market prices');
    expect(html).toContain('<th>Comparable price</th>');
    expect(html).toContain('9412.50 USD per tonne');
    expect(html).toContain('9.4125 USD per kg');
    expect(html).toContain('-35.0% vs Copper 2026-09 (synthetic quote)');
    expect(html).toContain('-27.5% vs Copper 2026-09 (synthetic quote)');
    expect(html).toContain('Not compared: No same-day ECB reference rate for USD to EUR is loaded.');
  });

  it('is titled as a market reference with separate global and synthetic sections and explicit coverage', () => {
    const html = renderToStaticMarkup(<CountryPricingPanel view={view} onLoad={() => {}} scope={{ sessionId: 's1', companyId: 'solara-cables', contextVersion: 2 }} />);
    expect(html).toContain('<h2>Market reference: global benchmarks and synthetic origin quotes</h2>');
    expect(html).toContain('Global benchmarks (one world price per material, not a country price)');
    expect(html).toContain('Synthetic supplier quotes by origin');
    expect(html).toContain('Coverage: 2 of 3 materials benchmarked (aluminium, copper). Only materials with an exact series mapping are benchmarked; others are not guessed.');
    expect(html).toContain('FX basis: BIS monthly average from the committed snapshot');
  });

  it('hides a view that belongs to another company or context (A to B to A)', () => {
    const other = renderToStaticMarkup(<CountryPricingPanel view={view} onLoad={() => {}} scope={{ sessionId: 's1', companyId: 'harvest-foods', contextVersion: 3 }} />);
    expect(other).not.toContain('Copper');
    const back = renderToStaticMarkup(<CountryPricingPanel view={view} onLoad={() => {}} scope={{ sessionId: 's1', companyId: 'solara-cables', contextVersion: 4 }} />);
    expect(back).not.toContain('Copper');
    const owner = { sessionId: 's1', companyId: 'solara-cables', contextVersion: 2, generation: 1 };
    expect(acceptPricing(owner, owner, view)).toBe(true);
    expect(acceptPricing(owner, { ...owner, generation: 2 }, view)).toBe(false);
    expect(acceptPricing(owner, { ...owner, contextVersion: 4, generation: 3 }, view)).toBe(false);
    expect(acceptPricing(owner, owner, { ...view, context_version: 9 })).toBe(false);
  });
});
