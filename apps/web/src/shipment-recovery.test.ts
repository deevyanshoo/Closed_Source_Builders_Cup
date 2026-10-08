import { afterEach, describe, expect, it, vi } from 'vitest';
import { execFileSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import type { ShipmentRecoveryContext } from '../../../contracts/generated/types';
import { compareShipmentContext, listShipmentContexts, parseStrictOutcome, ShipmentRecoveryError } from './shipment-recovery';

const context: ShipmentRecoveryContext = {
  context_id: 'a'.repeat(32), revision: 1, workspace_id: 'W', plan_version: 'v7',
  propagation_snapshot_id: 'graph-1', production_snapshot_id: 's1', as_of: '2026-10-09T00:00:00Z',
  synthetic: true, source: 'host_configured_synthetic', persistence: 'ephemeral',
  source_authenticity: 'not_verified', request_sha256: 'b'.repeat(64),
};
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const outcome = { adapter_version: '1', status: 'no_eligible_scenario', synthetic: true,
  binding: { adapter_version: '1', synthetic: true, workspace_id: 'W', plan_version: 'v7',
    propagation_snapshot_id: 'graph-1', production_snapshot_id: 's1', as_of: '2026-10-09T00:00:00Z',
    raw_sha256: 'c'.repeat(64), propagation_sha256: 'd'.repeat(64), production_sha256: 'e'.repeat(64),
    joins_sha256: 'f'.repeat(64), assignments_sha256: '0'.repeat(64), binding_sha256: '1'.repeat(64) },
  evidence: { scenarios: [], excluded: [{ event_id: 'E', shipment_id: 'SH', reason: 'hold_without_new_eta' }], normalization_rejected: [{ record_index: 0, event_id: 'E', reason: 'invalid_quantity', details: ['1000000000000000000.000000000000000001'] }], normalization_duplicates: [] }, comparisons: [] };
const localPython = resolve(process.cwd(), '../../../Closed_Source_Builders_Cup/procurement-engine/.venv/bin/python');
let evaluatedRaw: string | undefined;
function localEvaluatedOutcome(): string {
  if (evaluatedRaw) return evaluatedRaw;
  const repo = resolve(process.cwd(), '../..');
  const script = 'import json,runpy; m=runpy.run_path("procurement-engine/tests/test_shipment_production_recovery.py"); request,binding=m["case"](); from procure.production.shipment_recovery import compare_shipment_production_recovery; print(json.dumps(compare_shipment_production_recovery(request,expected_binding=binding),separators=(",",":")))';
  evaluatedRaw = execFileSync(localPython, ['-c', script], { cwd: repo, env: { PATH: '/usr/bin:/bin', PYTHONPATH: resolve(repo, 'procurement-engine/src'), PYTHONDONTWRITEBYTECODE: '1', PYTHONNOUSERSITE: '1' }, encoding: 'utf8', timeout: 10000 }).trim();
  return evaluatedRaw;
}

describe('shipment recovery client boundary', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('lists metadata only in the current encoded workspace and validates scope', async () => {
    const fetcher = vi.fn().mockResolvedValue(json({ contract_version: '0.2.0', contexts: [context] }));
    vi.stubGlobal('fetch', fetcher);
    expect(await listShipmentContexts('W', new AbortController().signal)).toEqual([context]);
    expect(fetcher.mock.calls[0][0]).toBe('/v2/workspaces/W/shipment-recovery/contexts');
    expect(fetcher.mock.calls[0][1].body).toBeUndefined();
    fetcher.mockResolvedValue(json({ contract_version: '0.2.0', contexts: [{ ...context, workspace_id: 'other' }] }));
    await expect(listShipmentContexts('W', new AbortController().signal)).rejects.toMatchObject({ kind: 'invalid_response' });
  });

  it('posts only the selected reference and preserves full decimal outcome text', async () => {
    const raw = JSON.stringify(outcome);
    const fetcher = vi.fn().mockResolvedValue(json({ contract_version: '0.2.0', context, outcome_json: raw }));
    vi.stubGlobal('fetch', fetcher);
    const result = await compareShipmentContext('W', context, new AbortController().signal);
    expect(fetcher.mock.calls[0][0]).toBe('/v2/workspaces/W/shipment-recovery/comparisons');
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ contract_version: '0.2.0', context_id: context.context_id, expected_revision: 1 });
    expect(result.raw).toBe(raw);
    expect(result.raw).toContain('1000000000000000000.000000000000000001');
    expect(result.kind).toBe('no_eligible_scenario');
  });

  it('rejects duplicate JSON keys, numeric amounts, and changed context binding', async () => {
    expect(() => parseStrictOutcome('{"status":"a","status":"b"}')).toThrow(/invalid/i);
    expect(() => parseStrictOutcome('{"amount":0.1}')).toThrow(/invalid/i);
    const fetcher = vi.fn().mockResolvedValue(json({ contract_version: '0.2.0', context: { ...context, revision: 2 }, outcome_json: JSON.stringify(outcome) }));
    vi.stubGlobal('fetch', fetcher);
    await expect(compareShipmentContext('W', context, new AbortController().signal)).rejects.toMatchObject({ kind: 'invalid_response' });
  });

  it('keeps complete adapter rejection distinct from fixed state errors', async () => {
    const rejected = { adapter_version: '1', status: 'rejected', synthetic: true, stage: 'join', reason: 'invalid_join', details: [], evidence: outcome.evidence, comparisons: [] };
    const fetcher = vi.fn().mockResolvedValueOnce(json({ contract_version: '0.2.0', context, outcome_json: JSON.stringify(rejected) }, 422))
      .mockResolvedValueOnce(json({ contract_version: '0.2.0', error: { code: 'shipment_context_stale', message: 'Stale context.' } }, 409));
    vi.stubGlobal('fetch', fetcher);
    expect((await compareShipmentContext('W', context, new AbortController().signal)).kind).toBe('rejected');
    await expect(compareShipmentContext('W', context, new AbortController().signal)).rejects.toMatchObject({ kind: 'stale' });
  });

  it('classifies auth loss and unavailable service without retaining outcome data', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(json({ contract_version: '0.2.0', error: { code: 'unauthorized', message: 'Unauthorized.' } }, 401))
      .mockResolvedValueOnce(json({ contract_version: '0.2.0', error: { code: 'shipment_recovery_unavailable', message: 'Unavailable.' } }, 503));
    vi.stubGlobal('fetch', fetcher);
    await expect(listShipmentContexts('W', new AbortController().signal)).rejects.toMatchObject({ kind: 'session' });
    await expect(listShipmentContexts('W', new AbortController().signal)).rejects.toMatchObject({ kind: 'unavailable' });
    expect(ShipmentRecoveryError).toBeDefined();
  });

  it.skipIf(!existsSync(localPython))('accepts the complete local evaluated adapter result', async () => {
    const raw = localEvaluatedOutcome();
    const fetcher = vi.fn().mockResolvedValue(json({ contract_version: '0.2.0', context, outcome_json: raw }));
    vi.stubGlobal('fetch', fetcher);
    const result = await compareShipmentContext('W', context, new AbortController().signal);
    expect(result.kind).toBe('evaluated');
    expect(result.outcome.comparisons).toHaveLength(1);
    expect(result.raw).toBe(raw);
  });

  it.each([
    ['foreign source workspace', (v: any) => { v.evidence.scenarios[0].scenario.workspace_id = 'OTHER'; }],
    ['false source synthetic flag', (v: any) => { v.evidence.scenarios[0].scenario.synthetic = false; }],
    ['empty source arrival changes', (v: any) => { v.evidence.scenarios[0].scenario.changes = []; }],
    ['empty projection component balances', (v: any) => { v.comparisons[0].projection.component_balances = []; }],
    ['unbound projection component', (v: any) => { v.comparisons[0].projection.component_balances[0].component_id = 'not-in-source'; }],
    ['fabricated unsupported reason', (v: any) => { v.comparisons[0].result = { status: 'unsupported', reason: 'fabricated_unknown_reason', details: [] }; }],
  ])('rejects evaluated outcome with %s', async (_name, mutate) => {
    if (!existsSync(localPython)) return;
    const changed = JSON.parse(localEvaluatedOutcome());
    mutate(changed);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ contract_version: '0.2.0', context, outcome_json: JSON.stringify(changed) })));
    const invalid = await compareShipmentContext('W', context, new AbortController().signal).then(() => false, error => error instanceof ShipmentRecoveryError && error.kind === 'invalid_response');
    expect(invalid).toBe(true);
  });

  it.skipIf(!existsSync(localPython))('rejects a one-microsecond projected arrival mismatch', async () => {
    const changed = JSON.parse(localEvaluatedOutcome());
    changed.comparisons[0].projection.arrival_changes[0].changed_at = '2026-10-10T10:00:00.000001Z';
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ contract_version: '0.2.0', context, outcome_json: JSON.stringify(changed) })));
    const invalid = await compareShipmentContext('W', context, new AbortController().signal).then(() => false, error => error instanceof ShipmentRecoveryError && error.kind === 'invalid_response');
    expect(invalid).toBe(true);
  });

  it.skipIf(!existsSync(localPython))('accepts the same source arrival in a valid offset with full microsecond precision', async () => {
    const changed = JSON.parse(localEvaluatedOutcome());
    changed.evidence.scenarios[0].scenario.changes[0].arrival_at = '2026-10-10T15:30:00+05:30';
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ contract_version: '0.2.0', context, outcome_json: JSON.stringify(changed) })));
    expect((await compareShipmentContext('W', context, new AbortController().signal)).kind).toBe('evaluated');
  });

  it.skipIf(!existsSync(localPython))('rejects a calendar-invalid source and projection instant even when they agree', async () => {
    const changed = JSON.parse(localEvaluatedOutcome());
    changed.evidence.scenarios[0].arrival_at = '2026-02-30T10:00:00Z';
    changed.evidence.scenarios[0].scenario.changes[0].arrival_at = '2026-02-30T10:00:00Z';
    changed.comparisons[0].projection.arrival_changes[0].changed_at = '2026-02-30T10:00:00Z';
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ contract_version: '0.2.0', context, outcome_json: JSON.stringify(changed) })));
    const invalid = await compareShipmentContext('W', context, new AbortController().signal).then(() => false, error => error instanceof ShipmentRecoveryError && error.kind === 'invalid_response');
    expect(invalid).toBe(true);
  });

  it.skipIf(!existsSync(localPython))('rejects an offset that would move a valid local date outside Python UTC year range', async () => {
    const changed = JSON.parse(localEvaluatedOutcome());
    const overflow = '0001-01-01T00:00:00+23:59';
    changed.evidence.scenarios[0].arrival_at = overflow;
    changed.evidence.scenarios[0].scenario.changes[0].arrival_at = overflow;
    changed.comparisons[0].projection.arrival_changes[0].changed_at = overflow;
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ contract_version: '0.2.0', context, outcome_json: JSON.stringify(changed) })));
    const invalid = await compareShipmentContext('W', context, new AbortController().signal).then(() => false, error => error instanceof ShipmentRecoveryError && error.kind === 'invalid_response');
    expect(invalid).toBe(true);
  });
});
