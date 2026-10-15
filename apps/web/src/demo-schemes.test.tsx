import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { requestSchemes, SchemesPanel } from './demo-schemes';
import type { SchemeResult } from './demo-schemes';

const zed = { scheme_id: 'in-msme-zed', country: 'India', official_name: 'MSME Sustainable (ZED) Certification Scheme', publisher: 'Ministry of Micro, Small and Medium Enterprises, Government of India', official_url: 'https://zed.msme.gov.in/subsidy-on-cost-of-certification', checked_on: '2026-10-15', offers: 'subsidy', offers_text: 'A subsidy on the cost of ZED certification.', who: 'MSMEs with Udyam Registration.', numbers_used: ['Subsidy on cost of certification: micro 80%, small 60%, medium 50%.'],
  eligibility: 'possibly eligible, check conditions', matched_on: ['company location in India'],
  input_fields: [{ name: 'enterprise_category', label: 'MSME category (micro, small or medium)', unit: 'category' }, { name: 'certification_cost', label: 'Certification cost you were quoted', unit: 'INR' }],
  saving: { status: 'calculated', amount: '150000', currency: 'INR', wording: 'approximate, based on these assumptions', formula: 'certification cost x subsidy rate for your enterprise category', arithmetic: '250000 INR x 60% (small) = 150000 INR', inputs: [{ name: 'enterprise_category', value: 'small', unit: 'category', source: 'entered by you' }, { name: 'certification_cost', value: '250000', unit: 'INR', source: 'entered by you' }] } };
const cbam = { ...zed, scheme_id: 'eu-cbam', official_name: 'Carbon Border Adjustment Mechanism (CBAM), definitive regime', offers: 'avoided_cost', matched_on: ['export route A to Hamburg, Germany carries steel'],
  input_fields: [{ name: 'tonnes_reduced', label: 'Embedded emissions avoided', unit: 'tCO2e' }, { name: 'price_per_tonne', label: 'CBAM certificate price', unit: 'EUR per tCO2e', default: '82.32', default_source: 'European Commission, Price of CBAM certificates, Q3 2026, published 2026-10-05' }],
  saving: { status: 'missing_inputs', amount: null, missing: ['tonnes_reduced'], currency: 'EUR', wording: 'approximate, based on these assumptions', formula: 'embedded tonnes avoided x CBAM certificate price', inputs: [] } };
const result: SchemeResult = { company_id: 'terrapack', registry_version: 'carbon-schemes-v1', checked_on: '2026-10-15', label: 'indicative, not financial or legal advice', matches: [zed, cbam], no_match_reason: null } as unknown as SchemeResult;

describe('carbon schemes and green finance (#109)', () => {
  it('lists possible schemes with cautious wording, sources and the saving arithmetic', () => {
    const html = renderToStaticMarkup(<SchemesPanel csrf="c" contextVersion={1} companyName="TerraPack Films" result={result} onResult={() => {}} />);
    expect(html).toContain('Indicative, not financial or legal advice');
    expect(html).toContain('Possibly eligible, check conditions');
    expect(html).toContain('href="https://zed.msme.gov.in/subsidy-on-cost-of-certification"');
    expect(html).toContain('checked 2026-10-15');
    expect(html).toContain('250000 INR x 60% (small) = 150000 INR');
    expect(html).toContain('Approximate, based on these assumptions');
    expect(html).toContain('Subsidy on cost of certification: micro 80%, small 60%, medium 50%.');
    expect(html).toContain('Matched on: company location in India');
    expect(html).toContain('Default 82.32: European Commission, Price of CBAM certificates, Q3 2026, published 2026-10-05');
    expect(html).toContain('Enter embedded emissions avoided to calculate.');
    expect(html).not.toMatch(/\bis eligible\b|\bwill save\b|\bguaranteed\b/i);
  });

  it('says plainly when nothing matches and does not claim nothing exists', () => {
    const empty = { ...result, matches: [], no_match_reason: 'No scheme in registry carbon-schemes-v1 matches Mexico or this company\'s export routes. Absence here is not evidence that no scheme exists.' } as SchemeResult;
    const html = renderToStaticMarkup(<SchemesPanel csrf="c" contextVersion={1} companyName="ForgeLine" result={empty} onResult={() => {}} />);
    expect(html).toContain('Absence here is not evidence that no scheme exists.');
  });

  it('offers only the three MSME categories for the category input', () => {
    const html = renderToStaticMarkup(<SchemesPanel csrf="c" contextVersion={1} companyName="TerraPack Films" result={result} onResult={() => {}} />);
    expect(html).toMatch(/<option value="micro">Micro<\/option><option value="small">Small<\/option><option value="medium">Medium<\/option>/);
  });

  it('posts only the entered values with the CSRF token and context version', async () => {
    const send = vi.fn(async () => new Response(JSON.stringify(result), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    await requestSchemes(send as unknown as typeof fetch, 'csrf1', { 'in-msme-zed': { enterprise_category: 'small', certification_cost: '250000', unused: '' } }, 4);
    const [url, init] = send.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/demo/carbon/schemes');
    expect((init.headers as Record<string, string>)['X-CSRF-Token']).toBe('csrf1');
    expect(JSON.parse(init.body as string)).toEqual({ inputs_by_scheme: { 'in-msme-zed': { enterprise_category: 'small', certification_cost: '250000' } }, expected_context_version: 4 });
  });
});
