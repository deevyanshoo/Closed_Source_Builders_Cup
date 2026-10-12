import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { PlanningPage } from './page';
import { ShipmentRecoveryPage } from './shipment-recovery-page';

describe('dispatch workspace discoverability', () => {
  it('offers labelled dispatch navigation from planning while retaining recovery navigation', () => {
    const html = renderToStaticMarkup(<MemoryRouter><PlanningPage /></MemoryRouter>);
    expect(html).toMatch(/href="\/dispatch"[^>]*>Dispatch<\/a>/);
    expect(html).toContain('href="/shipment-recovery"');
    expect(html).toContain('href="/recovery"');
  });

  it('offers labelled dispatch navigation from shipment recovery while retaining existing exits', () => {
    const html = renderToStaticMarkup(<MemoryRouter><ShipmentRecoveryPage /></MemoryRouter>);
    expect(html).toMatch(/href="\/dispatch"[^>]*>Dispatch<\/a>/);
    expect(html).toContain('href="/planning"');
    expect(html).toContain('href="/recovery"');
  });
});
