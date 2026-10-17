import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { EditButton, Editor, FieldName, FieldStatus, OffersPage, ScenarioForm, ScenarioView, ServiceOfferReview } from './offers-page';
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

  it('marks this page as current and explains the local sample boundary once', () => {
    expect(nav).toMatch(/aria-current="page"[^>]*href="\/offers"|href="\/offers"[^>]*aria-current="page"/);
    expect(nav.match(/aria-current="page"/g)?.length).toBe(1);
    expect(html).toContain('Sample quote data · local adapter · not connected to the gateway');
    expect(html).not.toContain('Synthetic · local adapter');
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

  it('shows a before-shipment deadline beside its computed date without reversing the direction', async () => {
    const result = await adapter.scenario('fixture-q01', { decision_date: '2026-10-10', dates: { ...dates, shipment: '2026-11-30' }, rate_percent: null, alternative_offer_id: null });
    result.schedule.lines[0] = { ...result.schedule.lines[0], anchor: 'shipment', timing: 'on_or_before', days: 30,
      due_date: '2026-10-31', days_from_decision: 21, clause: '100% TT 30 days before shipment' };
    const html = renderToStaticMarkup(<ScenarioView state={{ kind: 'result', result }} offers={offers} />);
    expect(html).toContain('by 30 days before shipment');
    expect(html).toContain('2026-10-31');
    expect(html).not.toContain('shipment + 30 days');
    result.schedule.lines[0] = { ...result.schedule.lines[0], days: 0, due_date: '2026-11-30', days_from_decision: 51 };
    const zeroDay = renderToStaticMarkup(<ScenarioView state={{ kind: 'result', result }} offers={offers} />);
    expect(zeroDay).toContain('on or before shipment');
    expect(zeroDay).toContain('2026-11-30');
  });

  it('says a missing validity is not stated and is not treated as open', async () => {
    const result = await adapter.scenario('fixture-q17', { decision_date: '2026-10-10', dates, rate_percent: null, alternative_offer_id: null });
    expect(renderToStaticMarkup(<ScenarioView state={{ kind: 'result', result }} offers={offers} />)).toContain('Validity not stated in the document');
  });
});

describe('service quote mode', () => {
  it('offers an explicit read-only shipment check for exact saved and used-in-plan candidates, including viewers', () => {
    const document_id = `sha256:${'a'.repeat(64)}`;
    const offer = { offer_id: 'synthetic_10', version: 3, status: 'saved', synthetic: true, extraction_id: 'ext-10',
      document: { document_id, filename: 'SYN-Q10.pdf', media_type: 'application/pdf', byte_size: 100 },
      fields: {}, items: [{}], blocking_reasons: [], review_flags: [], normalized: {}, allowed_actions: [], segments: [] } as any;
    const candidate = { candidate_id: 'synthetic_10@v3#item0', offer_id: offer.offer_id, offer_version: 3,
      document_id, item_index: 0, material: 'copper', quantity: { kg: '250000' },
      price: { type: 'index_linked', premium_per_kg: { amount: '0.095', currency: 'USD' } },
      landed_price: { status: 'index_linked_final_price_unknown', per_kg: null } };
    const render = (status: string, changed = candidate) => renderToStaticMarkup(<ServiceOfferReview offer={{ ...offer, status }}
      sourcing={{ as_of: '2026-10-10', synthetic: true, excluded: [], candidates: [changed] } as any}
      selected={null} onSelect={() => {}} onEdit={() => {}} onSave={() => {}} onBridge={() => {}} canEdit={false} busy={false} />);
    for (const status of ['saved', 'used_in_plan']) {
      expect(render(status)).toContain('Check host-configured shipment context for this candidate');
      expect(render(status)).toContain('final price unknown');
    }
    expect(render('extracted')).not.toContain('Check host-configured shipment context');
    expect(render('saved', { ...candidate, offer_version: 2 })).not.toContain('Check host-configured shipment context');
    expect(render('saved', { ...candidate, candidate_id: 'wrong' })).not.toContain('Check host-configured shipment context');
  });
  it('keeps fixture and service controls separate and labels service as local unavailable until selected', () => {
    const html = renderToStaticMarkup(<MemoryRouter initialEntries={['/offers']}><OffersPage /></MemoryRouter>);
    expect(html).toContain('Sample quotes');
    expect(html).toContain('Local quote service');
    expect(html).toContain('Sample quote data · local adapter');
  });
  it('keeps reviewer assertions separate and an index-linked final price unknown', () => {
    const userField = { name: 'settlement_currency', kind: 'currency', status: 'corrected', value: 'USD', critical: true,
      source: 'user', reasons: [], evidence: [], candidates: [], evidence_status: 'user_entered_without_document_evidence' };
    const offer = { offer_id: 'synthetic-10', version: 3, status: 'saved', synthetic: true, extraction_id: 'ext-10',
      document: { document_id: 'sha256:synthetic', filename: 'SYN-Q10.pdf', media_type: 'application/pdf', byte_size: 100 },
      fields: { settlement_currency: userField }, items: [{}], blocking_reasons: [], review_flags: [], normalized: {},
      allowed_actions: [], segments: [] } as any;
    const sourcing = { as_of: '2026-10-10', synthetic: true, excluded: [], candidates: [{ offer_id: 'synthetic-10', offer_version: 3,
      document_id: 'sha256:synthetic', item_index: 0, material: 'copper',
      quantity: { kg: '250000' }, price: { type: 'index_linked', premium_per_kg: { amount: '0.095', currency: 'USD' } },
      landed_price: { status: 'index_linked_final_price_unknown', per_kg: null } }] } as any;
    const html = renderToStaticMarkup(<ServiceOfferReview offer={offer} sourcing={sourcing} selected={null} onSelect={() => {}}
      onEdit={() => {}} onSave={() => {}} canEdit={false} busy={false} />);
    expect(html).toContain('Reviewer assertion; no document evidence');
    expect(html).toContain('premium 0.095 USD per kg; final price unknown');
    expect(html).toContain('disabled="">Save offer →</button>');
    expect(html).not.toContain('fixture-q10');
    const plannedHtml = renderToStaticMarkup(<ServiceOfferReview offer={{ ...offer, status: 'used_in_plan' }} sourcing={sourcing}
      selected={null} onSelect={() => {}} onEdit={() => {}} onSave={() => {}} canEdit={false} busy={false} />);
    expect(plannedHtml).toContain('premium 0.095 USD per kg; final price unknown');
    expect(plannedHtml).toContain('used in plan');
    for (const stale of [
      { offer_version: 2 }, { document_id: 'sha256:other' }, { item_index: 1 }, { offer_id: 'other' },
    ]) {
      const staleSourcing = { ...sourcing, candidates: [{ ...sourcing.candidates[0], ...stale }] };
      const staleHtml = renderToStaticMarkup(<ServiceOfferReview offer={offer} sourcing={staleSourcing} selected={null} onSelect={() => {}}
        onEdit={() => {}} onSave={() => {}} canEdit={false} busy={false} />);
      expect(staleHtml).not.toContain('premium 0.095 USD');
      const stalePlannedHtml = renderToStaticMarkup(<ServiceOfferReview offer={{ ...offer, status: 'used_in_plan' }} sourcing={staleSourcing}
        selected={null} onSelect={() => {}} onEdit={() => {}} onSave={() => {}} canEdit={false} busy={false} />);
      expect(stalePlannedHtml).not.toContain('premium 0.095 USD');
    }
  });
});
