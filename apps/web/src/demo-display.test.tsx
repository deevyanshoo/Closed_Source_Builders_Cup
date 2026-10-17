import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { CompanyOverview, OutboxWorkspace } from './demo-company-news';
import { displaySupplierMentions, supplierDisplayName } from './demo-display';
import type { CompanyProfile, SimulatedOutboxMessage } from './demo-api';

const raw = 'Europa Copper SA [SYNTHETIC]';
const company = {
  company_id: 'solara-cables', name: 'Solara Cables', synthetic: true, as_of: '2026-10-15',
  description: 'Fictional company', sector: 'Manufacturing', size: 'Mid-size', reporting_currency: 'USD',
  locations: [{ city: 'Pune', country: 'India' }], materials: [], orders: [], routes: [],
  currencies: ['USD'], news_keywords: [], buyer_defaults: null,
  suppliers: [{ supplier_id: 'supplier-3', name: raw, country: 'Germany', materials: ['copper'], lead_time_days: 10, settlement_currency: 'EUR' }],
  quote_coverage: { materials: [], fixture_ids: [], note: '' },
} satisfies CompanyProfile;

describe('presentation-only supplier labels', () => {
  it('strips only a trailing fixture annotation from human-facing names', () => {
    expect(supplierDisplayName(raw)).toBe('Europa Copper SA');
    expect(supplierDisplayName('Europa [SYNTHETIC] extra')).toBe('Europa [SYNTHETIC] extra');
    expect(supplierDisplayName('Europa Copper SA')).toBe('Europa Copper SA');
  });
  it('cleans a known supplier mention in an answer without removing quantities or other evidence', () => {
    expect(displaySupplierMentions(`Ask ${raw} about 2000 kg by 10 days.`, [raw]))
      .toBe('Ask Europa Copper SA about 2000 kg by 10 days.');
    expect(displaySupplierMentions('Public headline [SYNTHETIC] has no matched supplier.', [raw]))
      .toBe('Public headline [SYNTHETIC] has no matched supplier.');
  });
  it('renders a clean profile and simulated outbox while leaving canonical recipients intact', () => {
    const message = { message_id: 'm1', company_id: company.company_id, material_id: 'copper',
      recipient_label: raw, subject: 'Copper availability', body: 'Please confirm capacity.',
      status: 'simulated_sent', simulated: true, external_delivery: false, created_at: '2026-10-15T00:00:00Z' } as SimulatedOutboxMessage;
    const profile = renderToStaticMarkup(<CompanyOverview company={company} />);
    const outbox = renderToStaticMarkup(<OutboxWorkspace company={company} messages={[message]} />);
    expect(profile).toContain('Europa Copper SA · Germany');
    expect(outbox).toContain('To: Europa Copper SA · Material: copper');
    expect(profile + outbox).not.toContain('[SYNTHETIC]');
    expect(outbox).toContain('No external delivery');
    expect(message.recipient_label).toBe(raw);
    expect(company.suppliers[0].supplier_id).toBe('supplier-3');
  });
});
