import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { AffectedPanel, DispatchPage, HazardPanel, ReplayResult, ReplaySamplePicker, RouteEvidencePanel, ShipmentDetail, ShipmentList, StateNotice } from './dispatch-page';
import { createDispatchFixtureAdapter } from './dispatch';

const adapter = createDispatchFixtureAdapter();

describe('dispatch workspace components', () => {
  it('shows each shipment with a status label and its signals', async () => {
    const list = await adapter.listShipments();
    const html = renderToStaticMarkup(<ShipmentList response={list} selected="SHP-SYN-004" onSelect={() => {}} />);
    for (const label of ['On hold', 'Delayed', 'On time', 'Unknown']) expect(html).toContain(label);
    expect(html).toContain('eta after baseline');
    expect(html).toContain('aria-pressed="true"');
  });

  it('shows the selected shipment timeline, current ETA, exact quantities and the recovery link', async () => {
    const list = await adapter.listShipments();
    const row = list.shipments.find(s => s.shipment_id === 'SHP-SYN-003')!;
    const html = renderToStaticMarkup(<MemoryRouter><ShipmentDetail row={row} timeline={await adapter.timeline('SHP-SYN-003')} onShowAffected={() => {}} /></MemoryRouter>);
    expect(html).toContain('20.000 tonne');
    expect(html).toContain('partially delivered');
    expect(html).toContain('href="/shipment-recovery"');
    expect(html).toContain('View recovery options');
    expect(html).toContain('does not preselect');
  });

  it('shows the working-day lead time with its synthetic calendar, or why it is not available', async () => {
    const list = await adapter.listShipments();
    const render = async (id: string) => renderToStaticMarkup(<MemoryRouter><ShipmentDetail row={list.shipments.find(s => s.shipment_id === id)!} timeline={await adapter.timeline(id)} onShowAffected={() => {}} /></MemoryRouter>);
    const ok = await render('SHP-SYN-004');
    expect(ok).toContain('Working-day lead time');
    expect(ok).toContain('4 working days');
    expect(ok).toContain('Synthetic holiday table');
    const none = await render('SHP-SYN-003');
    expect(none).toContain('Not available');
  });

  it('labels route evidence as confirmed or exposure with source, times and expiry', async () => {
    const html = renderToStaticMarkup(<RouteEvidencePanel evidence={await adapter.routeEvidence('SHP-SYN-002')} />);
    expect(html).toContain('Confirmed operating status');
    expect(html).toContain('restricted');
    expect(html).toContain('synthetic-port-authority');
    expect(html).toContain('Retrieved');
    expect(html).toContain('Observed');
    expect(html).toContain('Expiry');
    const exposure = renderToStaticMarkup(<RouteEvidencePanel evidence={await adapter.routeEvidence('SHP-SYN-004')} />);
    expect(exposure).toContain('Exposure only');
    expect(exposure).toContain('synthetic-weather-service');
  });

  it('labels affected shipments as exposure, not confirmed delay', async () => {
    const html = renderToStaticMarkup(<AffectedPanel affected={await adapter.affected('evt-002-hold')} />);
    expect(html).toContain('Exposure only');
    expect(html.match(/>exposure</g)?.length).toBe(2);
  });

  it('renders loading, empty, error and stale states', () => {
    expect(renderToStaticMarkup(<StateNotice kind="loading" />)).toContain('Loading');
    expect(renderToStaticMarkup(<StateNotice kind="empty" message="No accepted milestones" />)).toContain('No accepted milestones');
    expect(renderToStaticMarkup(<StateNotice kind="error" message="Not found" />)).toContain('role="alert"');
    expect(renderToStaticMarkup(<StateNotice kind="stale" message="Data cutoff is 50 hours old" />)).toContain('Data cutoff is 50 hours old');
  });

  it('shows read-model errors as errors rather than empty evidence or actions', async () => {
    const route = await adapter.routeEvidence('SHP-SYN-002');
    const routeHtml = renderToStaticMarkup(<RouteEvidencePanel evidence={{ ...route, status: 'error', reasons: ['invalid_route_evidence'], confirmed: [], exposure: [] }} />);
    expect(routeHtml).toContain('role="alert"');
    expect(routeHtml).not.toContain('No route evidence');

    const affected = await adapter.affected('evt-002-hold');
    const affectedHtml = renderToStaticMarkup(<AffectedPanel affected={{ ...affected, status: 'error', reasons: ['invalid_event'], affected: [] }} />);
    expect(affectedHtml).toContain('role="alert"');
    expect(affectedHtml).not.toContain('No other shipments');

    const list = await adapter.listShipments();
    const timeline = await adapter.timeline('SHP-SYN-003');
    const row = list.shipments.find(s => s.shipment_id === 'SHP-SYN-003')!;
    const detailHtml = renderToStaticMarkup(<MemoryRouter><ShipmentDetail row={row} timeline={{ ...timeline, status: 'error', reasons: ['invalid_timeline'], events: [] }} onShowAffected={() => {}} /></MemoryRouter>);
    expect(detailHtml).toContain('role="alert"');
    expect(detailHtml).not.toContain('No accepted milestones');
  });

  it('distinguishes error, genuine empty, and stale shipment lists', async () => {
    const list = await adapter.listShipments();
    const render = (response: typeof list) => renderToStaticMarkup(<ShipmentList response={response} selected={null} onSelect={() => {}} />);
    const error = render({ ...list, status: 'error', reasons: ['invalid_snapshot'], shipments: [] });
    expect(error).toContain('role="alert"');
    expect(error).not.toContain('No shipments');
    const empty = render({ ...list, status: 'empty', reasons: ['no_accepted_events'], shipments: [] });
    expect(empty).toContain('No shipments');
    expect(empty).not.toContain('role="alert"');
    const stale = render({ ...list, status: 'stale', reasons: ['data_older_than_max_age'] });
    expect(stale).toContain('Old data');
    expect(stale).toContain('2026-10-08');
    expect(stale).toContain('SHP-SYN-004');
  });
});

describe('replay a sample delay', () => {
  const render = async (id: string) => renderToStaticMarkup(<MemoryRouter><ReplayResult result={await adapter.replay(id)} /></MemoryRouter>);

  it('lists the samples as synthetic', async () => {
    const html = renderToStaticMarkup(<ReplaySamplePicker samples={(await adapter.replaySamples()).samples} selected="innsa-release" onSelect={() => {}} />);
    expect(html).toContain('Port hold at INNSA with a new ETA');
    expect(html.match(/>synthetic</g)?.length).toBe(4);
    expect(html).toContain('aria-pressed="true"');
  });

  it('compares before and after and says why', async () => {
    const html = await render('innsa-release');
    expect(html).toContain('Synthetic replay event, not observed data');
    expect(html).toContain('Status: On hold → Delayed');
    expect(html).toContain('removed: on hold');
    expect(html).toContain('Injected (synthetic)');
    expect(html).toContain('This event states no new arrival time');
    expect(html).toContain('href="/shipment-recovery"');
  });

  it('keeps affected shipments as exposure and route evidence unchanged', async () => {
    const html = await render('innsa-hold-new-eta');
    expect(html).toContain('Exposure only');
    expect(html).toContain('rev-port-002');
    expect(html).toContain('Unchanged by the replay');
    expect(html).toContain('LINE-002-A');
    expect(html).toContain('3000 kg');
  });

  it('shows a rejected event as an error with the reason', async () => {
    const html = await render('eta-after-cutoff');
    expect(html).toContain('role="alert"');
    expect(html).toContain('Event rejected');
    expect(html).not.toContain('Could not load');
    expect(html).toContain('after the data cutoff');
    expect(html).not.toContain('What changed');
  });
});

describe('top navigation', () => {
  const html = renderToStaticMarkup(<MemoryRouter initialEntries={['/dispatch']}><DispatchPage /></MemoryRouter>);
  const nav = html.slice(html.indexOf('<nav'), html.indexOf('</nav>'));

  it('links to every workspace page like the planning header', () => {
    for (const path of ['/planning', '/offers', '/dispatch', '/shipment-recovery', '/recovery', '/scenarios'])
      expect(nav).toContain(`href="${path}"`);
    expect(nav).toContain('aria-label="Workspace pages"');
  });

  it('marks this page as current and keeps the synthetic local-adapter labels', () => {
    expect(nav).toMatch(/aria-current="page"[^>]*href="\/dispatch"|href="\/dispatch"[^>]*aria-current="page"/);
    expect(nav.match(/aria-current="page"/g)?.length).toBe(1);
    expect(html).toContain('Synthetic · local adapter');
    expect(html).toContain('SYNTHETIC FIXTURE DATA · LOCAL ADAPTER · NOT CONNECTED TO THE GATEWAY');
  });
});

describe('hazard and weather exposure panel', () => {
  const render = async (id: string) => renderToStaticMarkup(<HazardPanel hazards={await adapter.hazards(id)} />);

  it('labels exposure as not confirmed with provider, retrieval, period, threshold and expiry', async () => {
    const html = await render('SHP-SYN-008');
    expect(html).toContain('Exposure, not confirmed');
    expect(html).toContain('gdacs');
    expect(html).toContain('121 km');
    expect(html).toContain('Orange or Red within 300 km');
    for (const label of ['Retrieved', 'Period', 'Threshold', 'Valid until']) expect(html).toContain(label);
    expect(html).toContain('Stale: retrieved too long before the cutoff. Not usable for planning.');
    expect(html).toContain('Provider failure');
    const failure = html.slice(html.indexOf('data-state="provider_failure"'));
    expect(failure.slice(0, failure.indexOf('</li>'))).toContain('Not available');
  });

  it('shows measured values with units against the rule', async () => {
    const html = await render('SHP-SYN-002');
    expect(html).toContain('76.8 mm');
    expect(html).toContain('64.5 mm');
    expect(html).toContain('No exposure found (not a safety check)');
  });

  it('shows unsupported locations and the empty state', async () => {
    expect(await render('SHP-SYN-003')).toContain('Location not in registry');
    expect(await render('SHP-NOPE')).toContain('No hazard or weather assessments for this shipment.');
  });
});

describe('accessibility', () => {
  it('gives status chips and signals screen-reader context', async () => {
    const html = renderToStaticMarkup(<ShipmentList response={await adapter.listShipments()} selected={null} onSelect={() => {}} />);
    expect(html.match(/<span class="sr-only">Status: <\/span>/g)?.length).toBe(6);
    expect(html).toContain('<span class="sr-only">Signals: </span>');
  });

  it('labels evidence chips and hides the decorative dot', async () => {
    expect(renderToStaticMarkup(<RouteEvidencePanel evidence={await adapter.routeEvidence('SHP-SYN-002')} />))
      .toContain('<span class="sr-only">Evidence status: </span>');
    expect(renderToStaticMarkup(<HazardPanel hazards={await adapter.hazards('SHP-SYN-008')} />))
      .toContain('<span class="sr-only">Assessment: </span>');
    const page = renderToStaticMarkup(<MemoryRouter initialEntries={['/dispatch']}><DispatchPage /></MemoryRouter>);
    expect(page).toContain('<span class="red-dot" aria-hidden="true">');
  });
});
