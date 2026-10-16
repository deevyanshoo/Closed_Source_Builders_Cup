import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { DispatchPage, RecoveryLink } from './dispatch-page';
import { HostedDemoContext, readCapabilities } from './dispatch-hosted';
import { DemoPage } from './demo-page';

describe('dispatch on the hosted demo (#112)', () => {
  const hosted = { hosted: true, served_pages: ['/demo', '/dispatch'], recovery_link: null };

  it('uses server capabilities, so bare /dispatch and /dispatch?from=demo render the same hosted header', () => {
    const bare = renderToStaticMarkup(<MemoryRouter initialEntries={['/dispatch']}><DispatchPage capabilities={hosted} /></MemoryRouter>);
    const query = renderToStaticMarkup(<MemoryRouter initialEntries={['/dispatch?from=demo']}><DispatchPage capabilities={hosted} /></MemoryRouter>);
    expect(bare).toBe(query);
    expect(bare).toMatch(/href="\/demo"[^>]*>Back to the demo<\/a>/);
    expect(bare).not.toContain('href="/planning"');
    expect(bare).toContain('SYNTHETIC FIXTURE DATA · LOCAL ADAPTER · NOT CONNECTED TO THE GATEWAY');
  });

  it('keeps the full local navigation without server capabilities, whatever the query says', () => {
    for (const entry of ['/dispatch', '/dispatch?from=demo']) {
      const html = renderToStaticMarkup(<MemoryRouter initialEntries={[entry]}><DispatchPage capabilities={null} /></MemoryRouter>);
      expect(html).toContain('href="/planning"');
      expect(html).not.toContain('Back to the demo');
    }
  });

  it('reads capabilities only from a JSON server answer', async () => {
    const json = vi.fn(async () => new Response(JSON.stringify(hosted), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    expect(await readCapabilities(json as unknown as typeof fetch)).toEqual(hosted);
    expect(json).toHaveBeenCalledWith('/api/demo/capabilities', { credentials: 'same-origin' });
    const spa = vi.fn(async () => new Response('<!doctype html>', { status: 200, headers: { 'Content-Type': 'text/html' } }));
    expect(await readCapabilities(spa as unknown as typeof fetch)).toBeNull();
    const missing = vi.fn(async () => new Response('{}', { status: 404, headers: { 'Content-Type': 'application/json' } }));
    expect(await readCapabilities(missing as unknown as typeof fetch)).toBeNull();
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
