import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { requestSchemes, SchemesPanel } from './demo-schemes';
import { currentSchemes } from './demo-schemes';
import type { SchemeResult } from './demo-schemes';

const base = { country: 'India', publisher: 'Ministry of MSME', checked_on: '2026-10-16', who: 'MSMEs with Udyam Registration.', eligibility: 'possibly eligible, check conditions', matched_on: ['company location in India'], numbers_used: ['Women-owned MSMEs: free ZED certification.'] };
const zed = { ...base, scheme_id: 'in-msme-zed', official_name: 'MSME Sustainable (ZED) Certification Scheme', official_url: 'https://zed.msme.gov.in/subsidy-on-cost-of-certification', offers: 'subsidy', offers_text: 'A subsidy on the official certification cost.',
  benefit_status: 'unavailable', benefit_reason: 'No current official ZED fee schedule is archived here, so no amount is calculated.', benefit_label: null, benefit_formula: null, input_fields: [], missing_inputs: [], benefit: null };
const sidbi = { ...base, scheme_id: 'in-sidbi-green-finance', official_name: 'SIDBI Green Finance Scheme (GFS)', official_url: 'https://www.sidbi.in/en/green-finance', offers: 'green_financing', offers_text: 'Green project financing may be available.',
  benefit_status: 'available', benefit_reason: null, benefit_label: 'first-year simple interest difference, not an offer', benefit_formula: 'loan amount x (current rate - offered rate)',
  input_fields: [{ name: 'loan_amount', label: 'Loan amount', unit: 'INR' }, { name: 'current_rate_percent', label: 'Your current borrowing rate', unit: '% per year' }, { name: 'offered_rate_percent', label: 'Rate in an offer you actually received', unit: '% per year' }],
  missing_inputs: [], benefit: { kind: 'first_year_simple_interest_difference', amount: '-200000', currency: 'INR', direction: 'extra_cost', label: 'first-year simple interest difference, not an offer', formula: 'f', arithmetic: '20000000 INR x (9% - 10%) for the first year = -200000 INR', inputs: [] } };
const offset = { ...base, scheme_id: 'in-ccts-offset', official_name: 'Carbon Credit Trading Scheme (CCTS), offset mechanism', official_url: 'https://beeindia.gov.in/', offers: 'certificates', offers_text: 'Certificates for registered projects.',
  benefit_status: 'available', benefit_reason: null, benefit_label: 'hypothetical gross certificate value, not issued credit or a sale', benefit_formula: 'issued certificates x price',
  input_fields: [{ name: 'issued_certificates', label: 'Carbon Credit Certificates actually issued to you', unit: 'certificates' }, { name: 'price_per_certificate', label: 'Certificate price you assume', unit: 'INR per certificate' }],
  missing_inputs: ['issued_certificates', 'price_per_certificate'], benefit: null };
const result = { company_id: 'terrapack', registry_version: 'carbon-schemes-v1', checked_on: '2026-10-16', label: 'indicative, not financial or legal advice', matches: [zed, sidbi, offset], not_verified_countries: {}, no_match_reason: null } as unknown as SchemeResult;

describe('carbon programmes and green finance (#109, review fixes)', () => {
  it('shows an unavailable benefit as a reason with no inputs and no number', () => {
    const html = renderToStaticMarkup(<SchemesPanel csrf="c" contextVersion={1} companyName="TerraPack Films" result={result} onResult={() => {}} />);
    expect(html).toContain('Benefit amount unavailable: No current official ZED fee schedule is archived here, so no amount is calculated.');
    expect(html).not.toContain('MSME category');
    expect(html).toContain('href="https://zed.msme.gov.in/subsidy-on-cost-of-certification"');
  });

  it('labels the interest difference and shows extra cost distinctly', () => {
    const html = renderToStaticMarkup(<SchemesPanel csrf="c" contextVersion={1} companyName="TerraPack Films" result={result} onResult={() => {}} />);
    expect(html).toContain('First-year simple interest difference, not an offer');
    expect(html).toContain('Extra cost: 20000000 INR x (9% - 10%) for the first year = -200000 INR');
    expect(html).toContain('Green financing');
    expect(html).not.toMatch(/saving|cheaper loan|tradeable credit|is eligible/i);
  });

  it('lists missing inputs before any number is shown', () => {
    const html = renderToStaticMarkup(<SchemesPanel csrf="c" contextVersion={1} companyName="TerraPack Films" result={result} onResult={() => {}} />);
    expect(html).toContain('Enter carbon credit certificates actually issued to you and certificate price you assume to see the hypothetical gross certificate value, not issued credit or a sale.');
  });

  it('says plainly when nothing matches, including countries not verified', () => {
    const empty = { ...result, matches: [], not_verified_countries: { Mexico: 'page unreadable' }, no_match_reason: 'No scheme in registry carbon-schemes-v1 matches Mexico. Absence here is not evidence that no scheme exists. Official sources for Mexico could not be verified in this pass.' } as unknown as SchemeResult;
    const html = renderToStaticMarkup(<SchemesPanel csrf="c" contextVersion={1} companyName="ForgeLine" result={empty} onResult={() => {}} />);
    expect(html).toContain('Absence here is not evidence that no scheme exists.');
    expect(html).toContain('Mexico: page unreadable');
  });

  it('posts only the entered values with the CSRF token and context version', async () => {
    const send = vi.fn(async () => new Response(JSON.stringify(result), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    await requestSchemes(send as unknown as typeof fetch, 'csrf1', { 'in-sidbi-green-finance': { loan_amount: '100', current_rate_percent: '', offered_rate_percent: '9' } }, 4);
    const [url, init] = send.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/demo/carbon/schemes');
    expect((init.headers as Record<string, string>)['X-CSRF-Token']).toBe('csrf1');
    expect(JSON.parse(init.body as string)).toEqual({ inputs_by_scheme: { 'in-sidbi-green-finance': { loan_amount: '100', offered_rate_percent: '9' } }, expected_context_version: 4 });
  });
});

describe('scheme result scope (#115 review)', () => {
  it('shows a stored result only for the same company and context version', () => {
    const stored = { ...result, context_version: 1 } as unknown as SchemeResult;
    expect(currentSchemes(stored, 'terrapack', 1)).toBe(stored);
    expect(currentSchemes(stored, 'terrapack', 3)).toBeNull(); // A to B to A: same company, newer context
    expect(currentSchemes(stored, 'solara-cables', 1)).toBeNull();
    expect(currentSchemes(result, 'terrapack', 1)).toBeNull(); // a reply without a context version is not trusted
    expect(currentSchemes(null, 'terrapack', 1)).toBeNull();
  });
});
