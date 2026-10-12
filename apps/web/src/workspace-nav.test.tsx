import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import type { ReactElement } from 'react';
import { PlanningPage } from './page';
import { OffersPage } from './offers-page';
import { DispatchPage } from './dispatch-page';
import { RecoveryPage } from './recovery-page';
import { ShipmentRecoveryPage } from './shipment-recovery-page';
import { VoicePage } from './voice-page';
import { sessionStatus } from './workspace-nav';

const LINKS = [['/planning', 'Planning'], ['/offers', 'Offers'], ['/dispatch', 'Dispatch'], ['/recovery', 'Recovery'],
  ['/shipment-recovery', 'Shipment recovery'], ['/scenarios', 'Scenarios']];
const PAGES: [string, ReactElement][] = [['/planning', <PlanningPage />], ['/offers', <OffersPage />], ['/dispatch', <DispatchPage />],
  ['/recovery', <RecoveryPage />], ['/shipment-recovery', <ShipmentRecoveryPage />], ['/scenarios', <VoicePage />]];

const render = (path: string, page: ReactElement) => renderToStaticMarkup(<MemoryRouter initialEntries={[path]}>{page}</MemoryRouter>);
const headerOf = (html: string) => html.slice(html.indexOf('<header'), html.indexOf('</header>') + 9);

describe('one shared workspace header (#80)', () => {
  it.each(PAGES)('%s shows the same six short links and marks itself current', (path, page) => {
    const header = headerOf(render(path, page));
    const nav = header.slice(header.indexOf('<nav'), header.indexOf('</nav>'));
    expect(nav).toContain('aria-label="Workspace pages"');
    const links = [...nav.matchAll(/<a [^>]*href="([^"]+)"[^>]*>([^<]+)<\/a>/g)].map(m => [m[1], m[2]]);
    expect(links).toEqual(LINKS);
    const current = [...nav.matchAll(/<a [^>]*aria-current="page"[^>]*href="([^"]+)"|<a [^>]*href="([^"]+)"[^>]*aria-current="page"/g)];
    expect(current.map(m => m[1] ?? m[2])).toEqual([path]);
  });

  it.each(PAGES)('%s keeps a visible status in the header', (path, page) => {
    const header = headerOf(render(path, page));
    const meta = /<div class="masthead-meta">([\s\S]*?)<\/div>/.exec(header)?.[1].replace(/<[^>]+>/g, '').replace('●', '').trim();
    expect(meta).toBeTruthy();
    if (path !== '/planning') expect(meta).toMatch(/^Synthetic/);
    expect(header).toContain('<span class="red-dot" aria-hidden="true">');
  });

  it('describes the session without inventing one', () => {
    expect(sessionStatus(null)).toBe('Synthetic · session not confirmed');
    expect(sessionStatus({ contract_version: '0.2.0', principal_id: 'p', workspace_id: 'W', role: 'editor', development_only: true }))
      .toBe('Synthetic · local development · editor · workspace W');
    expect(sessionStatus({ contract_version: '0.2.0', principal_id: 'p', workspace_id: 'W', role: 'viewer', development_only: false, identity_mode: 'private_machine' }))
      .toBe('Synthetic · private machine · viewer · workspace W');
  });
});
