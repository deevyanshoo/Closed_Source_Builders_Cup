import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { EditButton, Editor, FieldName, FieldStatus, OffersPage, ScenarioForm, ScenarioView } from './offers-page';
import { createFixtureAdapter } from './offers';

describe('offer review editor', () => {
  it('renders existing Q14 averaging and FX basis for a reviewer to preserve or correct', () => {
    const q14 = createFixtureAdapter(() => new Date('2026-10-07T12:00:00Z')).list().find(o => o.fixture_id === 'Q14')!;
    const html = renderToStaticMarkup(<Editor field={q14.items[0].pricing} onSubmit={() => {}} onCancel={() => {}} busy={false} />);
    expect(html).toContain('Averaging period');
    expect(html).toContain('average of the month of delivery');
    expect(html).toContain('FX basis');
    expect(html).toContain('converted to EUR at the ECB reference rate on the invoice date');
    expect(html).toContain('Quotational period');
  });
});

describe('top navigation', () => {
  const html = renderToStaticMarkup(<MemoryRouter initialEntries={['/offers']}><OffersPage /></MemoryRouter>);
  const nav = html.slice(html.indexOf('<nav'), html.indexOf('</nav>'));

  it('links to every workspace page like the planning header', () => {
    for (const path of ['/planning', '/offers', '/dispatch', '/shipment-recovery', '/recovery', '/scenarios'])
      expect(nav).toContain(`href="${path}"`);
    expect(nav).toContain('aria-label="Workspace pages"');
  });

  it('marks this page as current and keeps the synthetic local-adapter labels', () => {
    expect(nav).toMatch(/aria-current="page"[^>]*href="\/offers"|href="\/offers"[^>]*aria-current="page"/);
    expect(nav.match(/aria-current="page"/g)?.length).toBe(1);
    expect(html).toContain('Synthetic · local adapter');
    expect(html).toContain('SYNTHETIC FIXTURE DATA · LOCAL ADAPTER · NOT CONNECTED TO THE GATEWAY');
  });
});

describe('accessibility', () => {
  const offer = createFixtureAdapter().list()[0];
  const field = offer.fields.supplier_name;

  it('lets keyboard users select a field to highlight its source', () => {
    const html = renderToStaticMarkup(<FieldName name="supplier_name" field={field} selected onSelect={() => {}} />);
    expect(html).toMatch(/<button type="button" class="offers-field-button" aria-pressed="true"/);
    expect(html).toContain('supplier name');
    expect(html).toContain('<span class="sr-only">Show source for </span>');
  });

  it('names each edit button after its field and gives status chips context', () => {
    expect(renderToStaticMarkup(<EditButton name="supplier_name" onEdit={() => {}} />)).toContain('aria-label="Edit supplier name"');
    const chip = renderToStaticMarkup(<FieldStatus field={field} />);
    expect(chip).toContain('<span class="sr-only">Field status: </span>');
  });

  it('hides the decorative dot from screen readers', () => {
    const html = renderToStaticMarkup(<MemoryRouter initialEntries={['/offers']}><OffersPage /></MemoryRouter>);
    expect(html).toContain('<span class="red-dot" aria-hidden="true">');
  });
});

describe('decision date and payment timing panel', () => {
  const adapter = createFixtureAdapter(() => new Date('2026-10-10T12:00:00Z'));
  const offers = adapter.list();
  const q05 = offers.find(o => o.fixture_id === 'Q05')!;
  const dates = { shipment: '2026-11-05', bill_of_lading: '2026-11-06', documents: '2026-11-12', invoice: '2026-11-06', delivery: null, arrival: null };

  it('labels every input and lists the other quotes as alternative terms', () => {
    const html = renderToStaticMarkup(<ScenarioForm offer={q05} offers={offers} defaultDate="2026-10-10" busy={false} onRun={() => {}} />);
    for (const label of ['Decision date', 'Shipment date', 'Bill of lading date', 'Documents presented', 'Invoice date', 'Delivery date', 'Arrival date', 'Compare with the payment terms of', 'Annual rate (%, optional)'])
      expect(html).toContain(label);
    expect(html).toContain('Q03 · LC 90 days from bill of lading date');
    expect(html).not.toContain('>Q05 ·');
    expect(html).toContain('No rate is assumed');
  });

  it('has empty, loading and error states', () => {
    expect(renderToStaticMarkup(<ScenarioView state={{ kind: 'empty' }} offers={offers} />)).toContain('No scenario yet');
    expect(renderToStaticMarkup(<ScenarioView state={{ kind: 'loading' }} offers={offers} />)).toContain('aria-busy="true"');
    const error = renderToStaticMarkup(<ScenarioView state={{ kind: 'error', message: 'Enter the annual rate as a decimal from 0 to 100, such as 8 or 7.25.' }} offers={offers} />);
    expect(error).toContain('role="alert"');
    expect(error).toContain('Could not run the scenario');
  });

  it('shows expiry at the decision date, what leaves the comparison, the schedule with evidence and the terms comparison', async () => {
    const result = await adapter.scenario('fixture-q05', { decision_date: '2026-10-21', dates, rate_percent: '8', alternative_offer_id: 'fixture-q03' });
    const html = renderToStaticMarkup(<ScenarioView state={{ kind: 'result', result }} offers={offers} />);
    expect(html).toContain('Expired: valid until 2026-10-18');
    expect(html).toContain('stays listed but is excluded from comparison');
    expect(html).toContain('Leaves the comparison after the decision date moves');
    expect(html).toMatch(/Q03[^<]*valid until 2026-10-20/);
    expect(html).toContain('Quote!B13');
    expect(html).toContain('50% TT advance, 50% before shipment');
    expect(html).toContain('Order value not available');
    expect(html).toContain('Payment moves 98.5 days later on average.');
    expect(html).toContain('Not valued: amount unavailable.');
    expect(html).toContain('No rate is assumed');
  });

  it('values later payment only at the entered rate', async () => {
    const result = await adapter.scenario('fixture-q01', { decision_date: '2026-10-10', dates, rate_percent: '8', alternative_offer_id: 'fixture-q03' });
    const html = renderToStaticMarkup(<ScenarioView state={{ kind: 'result', result }} offers={offers} />);
    expect(html).toContain('705937.5 USD');
    expect(html).toContain('98.1 days later');
    expect(html).toContain('50595.41 USD');
    const noRate = await adapter.scenario('fixture-q01', { decision_date: '2026-10-10', dates, rate_percent: null, alternative_offer_id: 'fixture-q03' });
    expect(renderToStaticMarkup(<ScenarioView state={{ kind: 'result', result: noRate }} offers={offers} />)).toContain('Enter an annual rate to value this.');
  });

  it('says a missing validity is not stated and is not treated as open', async () => {
    const result = await adapter.scenario('fixture-q17', { decision_date: '2026-10-10', dates, rate_percent: null, alternative_offer_id: null });
    expect(renderToStaticMarkup(<ScenarioView state={{ kind: 'result', result }} offers={offers} />)).toContain('Validity not stated in the document');
  });
});
