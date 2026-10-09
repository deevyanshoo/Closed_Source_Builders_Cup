import { describe, expect, it } from 'vitest';
import { createDispatchFixtureAdapter, DispatchError, dispatchFixtureInfo, explainChange, formatInstant, formatLeadTime, formatQuantities, freshness, replayReason, statusLabel } from './dispatch';
import type { LeadTime, ShipmentStatus } from './dispatch';

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

describe('port-delay replay (local adapter)', () => {
  const adapter = createDispatchFixtureAdapter();

  it('offers labelled synthetic sample events', async () => {
    const samples = await adapter.replaySamples();
    expect(samples.notice).toContain('SYNTHETIC');
    expect(samples.samples.map(s => s.sample_id)).toEqual(['innsa-hold-new-eta', 'innsa-release', 'inmaa-hold-no-eta', 'eta-after-cutoff']);
    expect(samples.samples.every(s => s.synthetic)).toBe(true);
  });

  it('returns before and after with the injected event labelled', async () => {
    const r = await adapter.replay('innsa-release');
    expect(r.status).toBe('ok');
    expect(r.injected).toEqual({ event_id: 'rpl-innsa-release', shipment_id: 'SHP-SYN-002', event_type: 'port_released', synthetic: true, label: 'Synthetic replay event, not observed data' });
    expect(r.changes.map(c => [c.shipment_id, c.before?.status, c.after?.status])).toEqual([['SHP-SYN-002', 'on_hold', 'delayed']]);
    expect(r.timeline?.after.filter(e => e.replay_injected).map(e => e.event_id)).toEqual(['rpl-innsa-release']);
    expect(r.recovery_link).toBe('/shipment-recovery');
  });

  it('keeps the material screen from the propagation projection', async () => {
    const r = await adapter.replay('innsa-hold-new-eta');
    const screen = r.material_screen;
    if (screen?.status !== 'screened') throw new Error('expected a screened material result');
    expect(screen.arrival_changes[0]).toMatchObject({ line_id: 'LINE-002-A', quantity: '3000', unit: 'kg', changed_at: '2026-10-16T02:30:00+00:00' });
    expect((await adapter.replay('inmaa-hold-no-eta')).material_screen).toEqual({ status: 'not_run', reason: 'hold_without_new_eta', event_id: 'rpl-inmaa-hold' });
  });

  it('shows the cutoff rejection as an error, and rejects unknown samples', async () => {
    const r = await adapter.replay('eta-after-cutoff');
    expect([r.status, r.reasons]).toEqual(['error', ['event_after_as_of']]);
    await expect(adapter.replay('nope')).rejects.toBeInstanceOf(DispatchError);
  });

  it('explains what changed and why', async () => {
    const release = (await adapter.replay('innsa-release')).changes[0];
    expect(explainChange(release)).toEqual([
      'Status: On hold → Delayed',
      'Signals added: none · removed: on hold',
      'On hold: yes → no',
      'Last milestone: port hold → port released',
    ]);
    const hold = (await adapter.replay('innsa-hold-new-eta')).changes[0];
    expect(explainChange(hold)).toEqual(['Expected arrival: 2026-10-12 02:30 UTC (stated ETA) → 2026-10-16 02:30 UTC (stated ETA)']);
  });

  it('describes reasons in plain words', () => {
    expect(replayReason('event_after_as_of')).toMatch(/after the data cutoff/);
    expect(replayReason('hold_without_new_eta')).toMatch(/not a measured delay/);
    expect(replayReason('something_new')).toBe('something new');
  });
});

describe('working-day lead time', () => {
  const adapter = createDispatchFixtureAdapter();

  it('carries the Python lead time for each timeline', async () => {
    const t = await adapter.timeline('SHP-SYN-004');
    expect(t.lead_time).toMatchObject({ status: 'ok', basis: 'cutoff_to_expected_arrival', country: 'IN', working_days: 4, synthetic: true });
    expect(formatLeadTime(t.lead_time!)).toEqual({
      headline: '4 working days',
      detail: 'Cutoff 2026-10-08 to expected arrival 2026-10-15 in IN (lane destination: INMAA): 7 calendar days, 2 weekend days, 1 holiday (Synthetic closure day IN-A).',
      source: 'Synthetic holiday table: invented dates, not real public holidays.',
    });
    expect(formatLeadTime((await adapter.timeline('SHP-SYN-001')).lead_time!).detail).toMatch(/^Departure 2026-09-20 to arrival 2026-10-06 in IN/);
  });

  it('says plainly when no lead time can be given', async () => {
    const none = (await adapter.timeline('SHP-SYN-003')).lead_time!;
    expect(formatLeadTime(none).headline).toBe('Not available');
    expect(formatLeadTime(none).detail).toMatch(/No departure and arrival, or cutoff and ETA/);
    const base = (await adapter.timeline('SHP-SYN-004')).lead_time!;
    const states: [LeadTime['status'], string][] = [
      ['unsupported_country', 'No approved holiday calendar for this country'],
      ['unsupported_year', 'No holiday calendar for that year'],
      ['source_unavailable', 'The holiday calendar source is unavailable'],
    ];
    for (const [status, text] of states) {
      const view = formatLeadTime({ ...base, status, reason: 'x', working_days: null });
      expect(view.headline).toBe('Not available');
      expect(view.detail).toContain(text);
    }
  });
});
