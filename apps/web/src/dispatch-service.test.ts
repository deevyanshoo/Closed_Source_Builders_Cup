import { describe, expect, it } from 'vitest';
import raw from './dispatch-fixtures.json?raw';
import { DispatchError } from './dispatch';
import { createDispatchServiceAdapter } from './dispatch-service';
import type { SessionResponse } from '../../../contracts/generated/types';

const fixture = JSON.parse(raw);
const session: SessionResponse = { contract_version: '0.2.0', principal_id: 'p', workspace_id: 'W', role: 'viewer', development_only: true };
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
const wrap = (data: unknown, workspace = 'W') => ({ contract_version: '0.2.0', workspace_id: workspace, synthetic: true, source: 'local_python_service', data });

function service(answer: (path: string, query: URLSearchParams) => Response) {
  const urls: string[] = [];
  const adapter = createDispatchServiceAdapter({
    loadSession: async () => session,
    transport: async input => { const url = new URL(String(input), 'http://x'); urls.push(url.pathname + url.search); return answer(url.pathname, url.searchParams); },
  });
  return { adapter, urls };
}

const byPath = (path: string, query: URLSearchParams): Response => {
  const tail = path.replace('/v2/workspaces/W/dispatch/', '');
  if (tail === 'shipments') return json(wrap({ ...fixture.shipments, synthetic: true }));
  if (tail.startsWith('shipments/')) return json(wrap({ ...fixture.timelines[tail.slice(10)], synthetic: true }));
  if (tail === 'hazards') return json(wrap({ ...fixture.hazards['SHP-SYN-002'], synthetic: true, seen: query.getAll('location') }));
  if (tail === 'route-evidence') return json(wrap({ ...fixture.route_evidence['SHP-SYN-002'], synthetic: true }));
  if (tail === 'replay/samples') return json(wrap({ status: 'ok', reasons: [], synthetic: true, notice: fixture.replay.notice, samples: fixture.replay.samples }));
  if (tail === 'replay' && query.get('sample') === 'eta-after-cutoff')
    return json({ contract_version: '0.2.0', error: { code: 'invalid_request', message: 'Invalid dispatch request: event_after_as_of' },
      data: fixture.replay.results['eta-after-cutoff'] }, 422);
  if (tail === 'replay') return json(wrap(fixture.replay.results[query.get('sample') ?? '']));
  return json({ contract_version: '0.2.0', error: { code: 'not_found', message: 'Resource not found' } }, 404);
};

describe('local dispatch service adapter (#77)', () => {
  it('reads the session workspace routes at the fixture cutoff and returns the read models', async () => {
    const { adapter, urls } = service(byPath);
    expect(adapter.source).toBe('local_dispatch_service');
    const list = await adapter.listShipments();
    expect(list.shipments.map(s => [s.shipment_id, s.status])).toEqual(fixture.shipments.shipments.map((s: { shipment_id: string; status: string }) => [s.shipment_id, s.status]));
    const cutoff = encodeURIComponent(fixture.as_of);
    expect(urls[0]).toBe(`/v2/workspaces/W/dispatch/shipments?as_of=${cutoff}&now=${cutoff}`);
    const timeline = await adapter.timeline('SHP-SYN-002');
    expect(timeline.shipment_id).toBe('SHP-SYN-002');
    expect(urls[1]).toBe(`/v2/workspaces/W/dispatch/shipments/SHP-SYN-002?as_of=${cutoff}&now=${cutoff}`);
  });

  it('asks for hazards and route evidence at the shipment’s own locations', async () => {
    const { adapter, urls } = service(byPath);
    await adapter.listShipments();
    await adapter.hazards('SHP-SYN-002');
    await adapter.routeEvidence('SHP-SYN-002');
    const keys = fixture.shipments.shipments.find((s: { shipment_id: string }) => s.shipment_id === 'SHP-SYN-002').location_keys as string[];
    expect(urls[1]).toContain('/dispatch/hazards?');
    expect(new URL(urls[1], 'http://x').searchParams.getAll('location')).toEqual(keys);
    expect(new URL(urls[2], 'http://x').searchParams.getAll('location')).toEqual(keys);
  });

  it('shows the service’s own after-cutoff rejection as the readable replay result', async () => {
    const { adapter } = service(byPath);
    const rejected = await adapter.replay('eta-after-cutoff');
    expect(rejected.status).toBe('error');
    expect(rejected.reasons).toEqual(['event_after_as_of']);
    const released = await adapter.replay('innsa-release');
    expect(released.status).toBe('ok');
    expect((await adapter.replaySamples()).samples.length).toBe(fixture.replay.samples.length);
  });

  it.each([
    ['another workspace', () => json(wrap({ ...fixture.shipments, synthetic: true }, 'other'))],
    ['an unlabelled answer', () => json({ contract_version: '0.2.0', workspace_id: 'W', synthetic: false, source: 'local_python_service', data: fixture.shipments })],
    ['unlabelled data', () => json(wrap({ ...fixture.shipments, synthetic: false }))],
    ['an unknown source', () => json({ ...wrap({ ...fixture.shipments, synthetic: true }), source: 'elsewhere' })],
    ['extra wrapper keys', () => json({ ...wrap({ ...fixture.shipments, synthetic: true }), extra: 1 })],
  ])('refuses %s', async (_name, answer) => {
    const { adapter } = service(answer);
    await expect(adapter.listShipments()).rejects.toBeInstanceOf(DispatchError);
  });

  it('says plainly when the gateway has no dispatch connector or no session', async () => {
    const off = service(() => json({ contract_version: '0.2.0', error: { code: 'service_unavailable', message: 'Dispatch workspace unavailable' } }, 503));
    await expect(off.adapter.listShipments()).rejects.toThrow('The local dispatch service is not enabled on this gateway.');
    const anonymous = service(() => json({ contract_version: '0.2.0', error: { code: 'unauthorized', message: 'Valid identity required' } }, 401));
    await expect(anonymous.adapter.listShipments()).rejects.toThrow('The local session is unavailable.');
    const missing = service(byPath);
    await expect(missing.adapter.affected('no-such-event')).rejects.toMatchObject({ kind: 'not_found' });
  });
});
