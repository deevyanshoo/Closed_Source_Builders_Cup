import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { DispatchPage, RecoveryLink } from './dispatch-page';
import { HostedDemoContext } from './dispatch-hosted';
import { DemoPage } from './demo-page';

describe('dispatch on the hosted demo (#112)', () => {
  it('opened from the demo, links back to /demo and hides pages the hosted site does not serve', () => {
    const html = renderToStaticMarkup(<MemoryRouter initialEntries={['/dispatch?from=demo']}><DispatchPage /></MemoryRouter>);
    expect(html).toMatch(/href="\/demo"[^>]*>Back to the demo<\/a>/);
    expect(html).not.toContain('href="/planning"');
    expect(html).not.toContain('href="/scenarios"');
    expect(html).toContain('SYNTHETIC FIXTURE DATA · LOCAL ADAPTER · NOT CONNECTED TO THE GATEWAY');
  });

  it('keeps the full local workspace navigation otherwise', () => {
    const html = renderToStaticMarkup(<MemoryRouter initialEntries={['/dispatch']}><DispatchPage /></MemoryRouter>);
    expect(html).toContain('href="/planning"');
    expect(html).not.toContain('Back to the demo');
  });

  it('replaces the recovery link with a plain note on the hosted demo', () => {
    const local = renderToStaticMarkup(<MemoryRouter><RecoveryLink to="/shipment-recovery" note="Opens comparisons." /></MemoryRouter>);
    expect(local).toContain('href="/shipment-recovery"');
    const hosted = renderToStaticMarkup(<MemoryRouter><HostedDemoContext.Provider value={true}><RecoveryLink to="/shipment-recovery" note="Opens comparisons." /></HostedDemoContext.Provider></MemoryRouter>);
    expect(hosted).not.toContain('href="/shipment-recovery"');
    expect(hosted).toContain('Recovery comparisons run in the local workspace and are not part of the hosted demo.');
  });

  it('links from the demo header to the hosted dispatch workspace', () => {
    const html = renderToStaticMarkup(<DemoPage />);
    expect(html).toMatch(/<a[^>]*href="\/dispatch\?from=demo"[^>]*>Dispatch<\/a>/);
  });
});
