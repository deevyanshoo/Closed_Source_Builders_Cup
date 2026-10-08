import { describe, expect, it } from 'vitest';
import { createDispatchFixtureAdapter, DispatchError, dispatchFixtureInfo, formatInstant, formatQuantities, freshness, statusLabel } from './dispatch';
import type { ShipmentStatus } from './dispatch';

describe('local dispatch fixture adapter', () => {
  const adapter = createDispatchFixtureAdapter();

  it('labels itself synthetic and local', () => {
    expect(adapter.source).toBe('local_fixture_adapter');
    expect(adapter.synthetic).toBe(true);
    expect(dispatchFixtureInfo.notice).toContain('Synthetic');
  });

  it('lists shipments with evidence-based statuses from the Python read model', async () => {
    const list = await adapter.listShipments();
    expect(list.status).toBe('ok');
    expect(list.shipments.map(s => [s.shipment_id, s.status])).toEqual([
      ['SHP-SYN-001', 'unknown'], ['SHP-SYN-002', 'on_hold'], ['SHP-SYN-003', 'unknown'],
      ['SHP-SYN-004', 'delayed'], ['SHP-SYN-005', 'on_time'], ['SHP-SYN-008', 'unknown']]);
  });

  it('returns a timeline with the open ETA and exact delivered quantity', async () => {
    const hold = await adapter.timeline('SHP-SYN-002');
    expect(hold.current?.open_eta?.eta).toBe('2026-10-12T02:30:00+00:00');
    expect(hold.current?.on_hold).toBe(true);
    const partial = await adapter.timeline('SHP-SYN-003');
    expect(partial.current?.delivered_quantity).toEqual({ tonne: '20.000' });
  });

  it('answers an unknown shipment with an empty timeline, not an invented one', async () => {
    const unknown = await adapter.timeline('SHP-NOT-THERE');
    expect(unknown.status).toBe('empty');
    expect(unknown.events).toEqual([]);
  });

  it('lists affected shipments as exposure only', async () => {
    const affected = await adapter.affected('evt-002-hold');
    expect(affected.affected.map(a => [a.shipment_id, a.status])).toEqual([['SHP-SYN-001', 'exposure'], ['SHP-SYN-002', 'exposure']]);
    expect(affected.note).toMatch(/^Exposure only/);
    await expect(adapter.affected('evt-nope')).rejects.toBeInstanceOf(DispatchError);
  });

  it('does not return an expired trigger as a successful affected-shipment result', async () => {
    await expect(adapter.affected('evt-008-eta')).rejects.toMatchObject({ kind: 'unavailable' });
  });

  it('separates confirmed and exposure route evidence', async () => {
    const port = await adapter.routeEvidence('SHP-SYN-002');
    expect(port.confirmed.map(e => [e.evidence_id, e.operating_status])).toEqual([['rev-port-002', 'restricted']]);
    const hazard = await adapter.routeEvidence('SHP-SYN-004');
    expect(hazard.exposure.map(e => e.evidence_id)).toEqual(['rev-haz-001']);
    expect((await adapter.routeEvidence('SHP-SYN-003')).status).toBe('empty');
  });
});

describe('display helpers', () => {
  it('labels every status', () => {
    expect((['on_time', 'at_risk', 'delayed', 'delivered', 'on_hold', 'unknown'] as ShipmentStatus[]).map(s => statusLabel(s)))
      .toEqual(['On time', 'At risk', 'Delayed', 'Delivered', 'On hold', 'Unknown']);
  });

  it('shows UTC and the facility local time', () => {
    expect(formatInstant('2026-10-05T04:30:00+00:00', '+05:30')).toBe('2026-10-05 04:30 UTC (10:00 local, +05:30)');
    expect(formatInstant('2026-09-20T17:00:00+00:00', '-03:00')).toBe('2026-09-20 17:00 UTC (14:00 local, -03:00)');
    expect(formatInstant(null)).toBe('Not stated');
  });

  it('keeps delivered quantities as exact decimal text', () => {
    expect(formatQuantities({ tonne: '20.000', kg: '0.125' })).toBe('20.000 tonne, 0.125 kg');
    expect(formatQuantities({})).toBe('None delivered');
  });

  it('marks data older than a day as stale', () => {
    expect(freshness('2026-10-08T12:00:00+00:00', new Date('2026-10-08T13:00:00Z'))).toEqual({ ageSeconds: 3600, stale: false });
    expect(freshness('2026-10-08T12:00:00+00:00', new Date('2026-10-10T12:00:01Z')).stale).toBe(true);
  });
});
