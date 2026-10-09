import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { EditButton, Editor, FieldName, FieldStatus, OffersPage } from './offers-page';
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
