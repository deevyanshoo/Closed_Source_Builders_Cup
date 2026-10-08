import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { AffectedPanel, ReplayResult, ReplaySamplePicker, RouteEvidencePanel, ShipmentDetail, ShipmentList, StateNotice } from './dispatch-page';
import { createDispatchFixtureAdapter } from './dispatch';

const adapter = createDispatchFixtureAdapter();

describe('dispatch workspace components', () => {
  it('shows each shipment with a status label and its signals', async () => {
    const list = await adapter.listShipments();
    const html = renderToStaticMarkup(<ShipmentList rows={list.shipments} selected="SHP-SYN-004" onSelect={() => {}} />);
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
