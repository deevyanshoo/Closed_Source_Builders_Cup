import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { acceptExports, ExportOpportunitiesView, orderMarkets, requestExportMargin, requestExportOpportunities } from './demo-export-opportunities';
import type { ExportMargin, ExportOpportunities } from './demo-export-opportunities';

const LABEL = 'observed historical trade, not buyer demand, a confirmed buyer or a price quote';
const MARGIN_LABEL = 'scenario from your inputs and historical customs unit values, not a buyer price, quote or forecast';

const market = (code: number, name: string, o: Record<string, unknown> = {}) => ({
  code, name, years_reported: [2020, 2021, 2022, 2023, 2024], years_not_reported: [], existing_export_market: false,
  import_value: { status: 'available', value_usd: '294016133.147', year: 2024, basis: 'CIF', source: 'reported by importer' },
  unit_value: { status: 'available', usd_per_kg: '9.236253773289553798445969341', year: 2024, basis: 'CIF', net_weight_kg: '31832834.0', label: 'historical customs unit value' },
  growth: { status: 'available', cagr: '0.132164141425', from_year: 2020, to_year: 2024, years_used: [2020, 2024] },
  home_share: { status: 'available', share: '0.0161724599851', year: 2024, basis: 'reported by importer' },
  mirror: null, margin: null, ...o });

const view = {
  kind: 'export_opportunities', company_id: 'loomworks', context_version: 3, status: 'available', label: LABEL, sort: 'market_size', sorts: ['market_size', 'growth', 'home_share', 'margin'],
  home_country: 'Bangladesh', reporting_currency: 'BDT', candidate_discovery: 'mirror_importers',
  product: { company_id: 'loomworks', product: 'Woven cotton fabric', hs_heading: '5208', hs_description: 'Woven fabrics of cotton, containing 85% or more by weight of cotton', note: 'illustrative mapping for a fictional company', reason: 'Fabric mill' },
  markets: [
    market(380, 'Italy'),
    market(504, 'Morocco', { unit_value: { status: 'unit value suppressed', reason: 'net weight estimated by UN Comtrade; no reliability measure is published with it', year: 2024 }, growth: { status: 'growth unavailable', reason: 'needs at least 3 reported years; 2 reported' }, existing_export_market: true }),
    market(800, 'Uganda', { import_value: { status: 'not reported', reason: 'Uganda did not report imports of this heading in 2020 to 2024.' }, years_reported: [], years_not_reported: [2020, 2021, 2022, 2023, 2024],
      unit_value: { status: 'unit value suppressed', reason: 'imports not reported', year: null }, growth: { status: 'growth unavailable', reason: 'needs at least 3 reported years; 0 reported' },
      home_share: { status: 'unavailable', reason: 'no year with both total imports and imports from the home country' }, mirror: { value_usd: '1200.5', year: 2023, label: 'mirror (reported by exporter)', meaning: 'Bangladesh reported exports to Uganda' } }),
  ],
  source: { provider: 'UN Comtrade, United Nations Statistics Division', attribution: 'Source: UN Comtrade (United Nations Statistics Division), retrieved via the public preview API.', use_policy: 'https://uncomtrade.org/docs/faqs-on-use-and-re-dissemination/', terms_summary: 'Transformed extract; raw payloads are not committed.', endpoint: 'https://comtradeapi.un.org/public/v1/preview/C/A/HS', years: [2020, 2021, 2022, 2023, 2024], built_on: '2026-10-17', hs_revision: 'HS 2022 (UN Comtrade classification H6)' },
} as unknown as ExportOpportunities;

const inputs = [['unit_cost_per_kg', 'unit production cost (BDT per kg)', '600'], ['freight_insurance_per_kg', 'freight and insurance to this market (BDT per kg)', '45'], ['tariff_rate_percent', 'tariff rate on the customs value (percent)', '0'],
  ['capacity_kg_per_year', 'available export capacity (kg per year)', '200000'], ['target_share_percent', "target share of this market's import volume (percent)", '1']].map(([name, label, value]) => ({ name, label, value, source: 'user supplied' }));
const done = { company_id: 'loomworks', context_version: 3, market_code: 380, market_name: 'Italy', margin: { status: 'available', label: MARGIN_LABEL, currency: 'BDT', basis: 'CIF', inputs, missing: [], notes: [],
  fx: { rate: '123.100769', quote: 'BDT per USD', month: '2026-08', publisher: 'Bank for International Settlements (BIS)', url: 'https://data.bis.org/topics/XRU' },
  margin_per_kg: '491.989942171095732255569831', volume_kg: '200000', contribution: '98397988.4342191464511139662', formula: 'margin per kg = market unit value (CIF, converted) - unit cost - freight and insurance - tariff on the customs value; contribution = margin per kg x min(capacity, target share x market import volume)',
  arithmetic: ['unit value: 9.2363 USD/kg x 123.100769 BDT per USD (2026-08) = 1,136.99 BDT/kg (CIF, 2024)', 'margin per kg: 1,136.99 - 600.00 (unit cost) - 45.00 (freight and insurance) - 0.00 (tariff) = 491.99 BDT/kg', 'contribution: 491.99 x 200,000 = 98,397,988.43 BDT'] } } as unknown as ExportMargin;
const missing = { ...done, market_code: 504, market_name: 'Morocco', margin: { status: 'unavailable', label: MARGIN_LABEL, currency: 'BDT', inputs: inputs.map(i => ({ ...i, value: null })), notes: ['tariff unavailable, check the official schedule'],
  missing: ['unit_cost_per_kg', 'tariff_rate_percent', 'historical customs unit value (net weight estimated by UN Comtrade; no reliability measure is published with it)'], fx: null } } as unknown as ExportMargin;

const render = (margins: Record<number, ExportMargin> = {}, selected = 380) => renderToStaticMarkup(
  <ExportOpportunitiesView view={view} margins={margins} selected={selected} values={{}} busy={false} error="" onSort={() => {}} onSelect={() => {}} onValue={() => {}} onSubmit={() => {}} />);

describe('export opportunities panel (#131)', () => {
  it('shows the product, heading, mapping note, label and the numbers behind each market', () => {
    const html = render();
    expect(html).toContain('Woven cotton fabric');
    expect(html).toContain('HS 5208');
    expect(html).toContain('illustrative mapping for a fictional company');
    expect(html).toContain(LABEL);
    expect(html).toContain('USD 294,016,133');
    expect(html).toContain('2024 · CIF · reported by importer');
    expect(html).toContain('13.2% a year, 2020 to 2024');
    expect(html).toContain('1.62% (2024, reported by importer)');
    expect(html).toContain('9.2363 USD/kg (CIF, 2024)');
    expect(html).toContain('Unit value suppressed: net weight estimated by UN Comtrade');
    expect(html).toContain('needs at least 3 reported years; 2 reported');
    expect(html).toContain('Existing export route');
  });

  it('keeps missing distinct from zero and labels mirror data', () => {
    const html = render();
    expect(html).toContain('Not reported');
    expect(html).toContain('mirror (reported by exporter): Bangladesh reported exports to Uganda, USD 1,201 (2023)');
    expect(html).toContain('not reported: 2020, 2021, 2022, 2023, 2024');
    expect(html).not.toContain('USD 0');
  });

  it('offers the four sorts and the margin form with every input tagged as yours', () => {
    const html = render();
    for (const label of ['Market size', 'Growth', 'Home-country share', 'Margin scenario']) expect(html).toContain(`>${label}</option>`);
    expect(html).toContain('unit production cost (BDT per kg)');
    expect(html).toContain("target share of this market&#x27;s import volume (percent)");
    expect(html).toContain('All inputs are yours; nothing is filled in.');
  });

  it('shows the formula, arithmetic and result, or the exact missing list', () => {
    const html = render({ 380: done });
    expect(html).toContain('Contribution: BDT 98,397,988.43');
    expect(html).toContain('Margin per kg: BDT 491.99');
    expect(html).toContain('margin per kg: 1,136.99 - 600.00 (unit cost) - 45.00 (freight and insurance) - 0.00 (tariff) = 491.99 BDT/kg');
    expect(html).toContain('FX 123.100769 BDT per USD (2026-08, Bank for International Settlements (BIS))');
    expect(html).toContain(MARGIN_LABEL);
    const gap = render({ 504: missing }, 504);
    expect(gap).toContain('No margin figure. Missing: unit_cost_per_kg, tariff_rate_percent, historical customs unit value');
    expect(gap).toContain('tariff unavailable, check the official schedule');
    expect(gap).not.toContain('Contribution:');
  });

  it('carries the source and terms footnote and avoids forbidden wording', () => {
    const html = render({ 380: done });
    expect(html).toContain('Source: UN Comtrade (United Nations Statistics Division)');
    expect(html).toContain('href="https://uncomtrade.org/docs/faqs-on-use-and-re-dissemination/"');
    expect(html).toContain('HS 2022 (UN Comtrade classification H6) · annual data 2020 to 2024 · snapshot built 2026-10-17');
    const text = html.replaceAll(LABEL, '').replaceAll(MARGIN_LABEL, '');
    expect(text).not.toMatch(/buyer demand|confirmed buyer|\bquote\b|saving|recommend/i);
  });

  it('orders by margin contribution locally, missing last, ties by name', () => {
    const margins = { 504: { ...done, market_code: 504, margin: { ...done.margin, contribution: '5' } }, 380: { ...done, margin: { ...done.margin, contribution: '9' } } } as unknown as Record<number, ExportMargin>;
    expect(orderMarkets(view.markets, 'margin', margins).map(m => m.name)).toEqual(['Italy', 'Morocco', 'Uganda']);
    expect(orderMarkets(view.markets, 'market_size', margins).map(m => m.name)).toEqual(['Italy', 'Morocco', 'Uganda']);
  });

  it('accepts replies only for the same session, company, context and generation (A to B to A)', () => {
    const owner = { sessionId: 's1', companyId: 'loomworks', contextVersion: 3, generation: 1 };
    expect(acceptExports(owner, owner, view)).toBe(true);
    expect(acceptExports(owner, { ...owner, companyId: 'solara-cables', contextVersion: 4, generation: 2 }, view)).toBe(false);
    expect(acceptExports(owner, { ...owner, contextVersion: 5, generation: 3 }, view)).toBe(false);
    expect(acceptExports(owner, owner, { ...view, company_id: 'solara-cables' })).toBe(false);
    expect(acceptExports(owner, owner, done)).toBe(true);
  });

  it('reads the ranking with the cookie and posts the margin with CSRF and context', async () => {
    const send = vi.fn(async () => new Response(JSON.stringify(view), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    await requestExportOpportunities(send as unknown as typeof fetch, 'growth');
    expect(send).toHaveBeenCalledWith('/api/demo/export-opportunities?sort=growth', { credentials: 'same-origin' });
    await requestExportMargin(send as unknown as typeof fetch, 'csrf1', { market_code: 380, inputs: { unit_cost_per_kg: '600' }, expected_context_version: 3 });
    const [url, init] = send.mock.calls[1] as unknown as [string, RequestInit];
    expect(url).toBe('/api/demo/export-opportunities/margin');
    expect(init.method).toBe('POST');
    expect((init.headers as Record<string, string>)['X-CSRF-Token']).toBe('csrf1');
    expect(JSON.parse(init.body as string)).toEqual({ market_code: 380, inputs: { unit_cost_per_kg: '600' }, expected_context_version: 3 });
    const failing = vi.fn(async () => new Response(JSON.stringify({ error: { code: 'invalid_request', message: 'target_share_percent must be between 0 and 100.' } }), { status: 422 }));
    await expect(requestExportMargin(failing as unknown as typeof fetch, 'csrf1', { market_code: 380, inputs: {}, expected_context_version: 3 })).rejects.toThrow('target_share_percent must be between 0 and 100.');
  });
});
