import { describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import type { CompanyProfile, RiskReport } from './demo-api';
import { CompanyHub, filterWorkspaceCompanies, WorkspaceHeader, WorkspaceNavigation, WorkspaceOverview } from './zoorq-workspace';

const company: CompanyProfile = {
  company_id: 'solara', name: 'Solara Cables', sector: 'Electrical manufacturing', size: 'Mid-size', synthetic: true, as_of: '2026-10-14', description: 'Synthetic profile', locations: [{ country: 'India', city: 'Pune' }], reporting_currency: 'USD',
  materials: [{ material_id: 'copper', name: 'Copper', quantity_unit: 'kg', stock: '600', consumption_per_day: '100', monthly_requirement: '3000', criticality: 'high', keywords: ['copper'] }, { material_id: 'polymer', name: 'Polymer', quantity_unit: 'kg', stock: '500', consumption_per_day: '50', monthly_requirement: '1500', criticality: 'low', keywords: [] }],
  suppliers: [{ supplier_id: 'wrong', name: 'Polymer Works', country: 'India', settlement_currency: 'USD', materials: ['polymer'], lead_time_days: 2 }, { supplier_id: 'copper-a', name: 'Copper Works', country: 'India', settlement_currency: 'USD', materials: ['copper'], lead_time_days: 10 }],
  orders: [], routes: [], currencies: ['USD'], news_keywords: ['copper'], quote_coverage: { materials: ['copper'], fixture_ids: [], note: 'Synthetic' }, buyer_defaults: null,
};

describe('ZOORQ workspace shell', () => {
  it('searches company names and sectors without case sensitivity and gives a useful empty state', () => {
    const second = { ...company, company_id: 'loom', name: 'Loomworks', sector: 'Textiles' };
    expect(filterWorkspaceCompanies([company, second], '  TEXTILES ')).toEqual([second]);
    expect(filterWorkspaceCompanies([company, second], 'solara')).toEqual([company]);
    expect(renderToStaticMarkup(<CompanyHub companies={[]} busy={false} onOpen={() => {}} />)).toContain('No companies');
  });

  it('renders five reachable destinations, current state, and disables navigation during switching', () => {
    const html = renderToStaticMarkup(<WorkspaceNavigation company={company} view="news" disabled onView={() => {}} />);
    expect((html.match(/class="zoorq-nav-link /g) ?? []).length).toBe(5);
    expect(html).toContain('aria-current="page"');
    expect(html).toContain('News');
    expect((html.match(/disabled=""/g) ?? []).length).toBeGreaterThanOrEqual(6);
    expect(html).toContain('aria-label="Inventory &amp; supply"');
    expect(html).toContain('>Supply</span>');
    expect(html).toContain('>Procure</span>');
    expect(html).not.toContain('®');
    expect(html).not.toContain('▥');
  });

  it('provides distinct Talk and Type actions in the compact header', () => {
    const onTalk = vi.fn(), onChat = vi.fn();
    const element = WorkspaceHeader({ company, view: 'overview', onTalk, onChat });
    const buttons = element.props.children.filter((child: { props?: { onClick?: () => void } }) => child?.props?.onClick);
    buttons[0].props.onClick();
    buttons[1].props.onClick();
    expect(onTalk).toHaveBeenCalledOnce();
    expect(onChat).toHaveBeenCalledOnce();
    const html = renderToStaticMarkup(element);
    expect(html).toContain('Talk to ZOORQ');
    expect(html).toContain('Type to ZOORQ');
    expect(html).toContain('id="zoorq-talk-live"');
    expect(html).toContain('>Talk</span>');
    expect(html).toContain('>Type</span>');
  });

  it('keeps a working All companies control in the header for mobile', () => {
    const onHub = vi.fn();
    const element = WorkspaceHeader({ company, view: 'overview', onTalk: () => {}, onChat: () => {}, onHub });
    element.props.children[0].props.onClick();
    expect(onHub).toHaveBeenCalledOnce();
    expect(renderToStaticMarkup(element)).toContain('aria-label="All companies"');
  });

  it('compares stock cover with the lead for the same material and labels the estimate', () => {
    const html = renderToStaticMarkup(<WorkspaceOverview company={company} news={null} quoteCount={0} onView={() => {}} />);
    expect(html).toContain('6 days of stock cover');
    expect(html).toContain('10 days');
    expect(html).toContain('approximately 4 days');
    expect(html).toContain('profile lead');
    expect(html).not.toContain('accepted quote lead');
  });

  it('does not turn a different material supplier or invalid consumption into a false gap', () => {
    const noCopperSupplier = { ...company, suppliers: company.suppliers.slice(0, 1) };
    const noLead = renderToStaticMarkup(<WorkspaceOverview company={noCopperSupplier} news={null} quoteCount={0} onView={() => {}} />);
    expect(noLead).toContain('Profile lead unavailable');
    expect(noLead).not.toContain('approximately 4 days');
    const noConsumption = { ...company, materials: [{ ...company.materials[0], consumption_per_day: '0' }] };
    const noCover = renderToStaticMarkup(<WorkspaceOverview company={noConsumption} news={null} quoteCount={0} onView={() => {}} />);
    expect(noCover).toContain('Stock cover unavailable');
    expect(noCover).not.toContain('approximately');
  });

  it('brings an uncovered material ahead of a covered high-criticality material', () => {
    const changed = { ...company, suppliers: [{ ...company.suppliers[0], lead_time_days: 15 }, { ...company.suppliers[1], lead_time_days: 4 }] };
    const html = renderToStaticMarkup(<WorkspaceOverview company={changed} news={null} quoteCount={0} onView={() => {}} />);
    expect(html).toContain('Polymer cover needs review');
    expect(html).toContain('approximately 5 days');
  });

  it('shows only alerts for the current company beside the shared timeline', () => {
    const report = { company_id: 'solara', companies: [{ company_id: 'solara', alerts: [{ company_id: 'solara' }] }, { company_id: 'other', alerts: [{ company_id: 'other' }, { company_id: 'other' }] }] } as RiskReport;
    const html = renderToStaticMarkup(<WorkspaceOverview company={company} report={report} news={null} quoteCount={0} onView={() => {}} />);
    expect(html).toContain('1 current-company monitor alert');
    expect(html).toContain('role="img"');
    const stale = renderToStaticMarkup(<WorkspaceOverview company={{ ...company, company_id: 'another' }} report={report} news={null} quoteCount={0} onView={() => {}} />);
    expect(stale).not.toContain('current-company monitor alert');
  });
});
