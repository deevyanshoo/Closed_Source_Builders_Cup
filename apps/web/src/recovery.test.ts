import { afterEach, describe, expect, it, vi } from 'vitest';
import { recoveryExamples } from '../../../contracts/generated/recovery-examples';
import { buildComparisonRequest, createExample, requestComparison, validateComparisonResponse } from './recovery';

const response = () => structuredClone((recoveryExamples as Record<string, unknown>).recovery);

describe('recovery comparison boundary', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('binds only the three workspace fields to the authenticated session', () => {
    const request = createExample('recovery_precision', 'team-west');
    expect(request.snapshot.workspace_id).toBe('team-west');
    expect(request.scenario.workspace_id).toBe('team-west');
    expect(request.recovery.workspace_id).toBe('team-west');
    expect(request.snapshot.inventory[0].starting_stock).toBe('1000000000000000000.000000000000000001');
    expect(recoveryExamples.recovery_precision.snapshot.workspace_id).toBe('W');
  });

  it('preserves exact strings and permits a no-change comparison', () => {
    const base = createExample('recovery_precision', 'team-west');
    const request = buildComparisonRequest(base, { 'r-C': '2026-10-10T09:00:00Z' }, { 'O-A': 'P1', 'O-B': 'P2', 'O-U': 'P1' });
    expect(request.scenario.arrival_replacements).toEqual([]);
    expect(request.snapshot.orders[0].output_quantity).toBe('0.2');
    expect(request.snapshot.inventory[0].starting_stock).toBe('1000000000000000000.000000000000000001');
  });

  it('rejects ambiguous dates and unknown order periods before a request', () => {
    const base = createExample('recovery', 'team-west');
    expect(() => buildComparisonRequest(base, { 'r-C': '2026-10-10T10:00' }, {})).toThrow(/UTC/);
    expect(() => buildComparisonRequest(base, {}, { 'O-A': 'missing' })).toThrow(/period/);
  });

  it('rejects nested malformed output and duplicate unique modeled checks', async () => {
    const base = createExample('recovery', 'team-west');
    const good = await import('../../../contracts/examples/recovery_response.json');
    const body = structuredClone(good.default);
    body.comparison.workspace_id = 'team-west';
    expect(validateComparisonResponse(body, base).comparison.workspace_id).toBe('team-west');
    const malformed = structuredClone(body);
    malformed.comparison.recovery.orders[0].demands[0].quantity = 2 as unknown as string;
    expect(() => validateComparisonResponse(malformed, base)).toThrow(/invalid/i);
    const duplicate = structuredClone(body);
    duplicate.comparison.modeled_checks[1] = duplicate.comparison.modeled_checks[0];
    expect(() => validateComparisonResponse(duplicate, base)).toThrow(/invalid/i);
  });

  it('posts the exact request, handles unsupported separately, and fails closed on bad success', async () => {
    const base = createExample('recovery', 'team-west');
    const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ contract_version: '0.2.0', status: 'unsupported', reason: 'stale_receipt', details: [] }), { status: 422, headers: { 'Content-Type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify(response()), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetcher);
    const first = await requestComparison(base, new AbortController().signal);
    expect(first.kind).toBe('unsupported');
    expect(fetcher.mock.calls[0][0]).toBe('/v2/production/receipt-recovery/comparisons');
    expect(JSON.parse(fetcher.mock.calls[0][1].body).snapshot.workspace_id).toBe('team-west');
    await expect(requestComparison(base, new AbortController().signal)).rejects.toThrow(/invalid/i);
  });

  it('marks an expired or changed gateway session for refresh', async () => {
    const base = createExample('recovery', 'team-west');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}', { status: 401, headers: { 'Content-Type': 'application/json' } })));
    await expect(requestComparison(base, new AbortController().signal)).rejects.toMatchObject({ kind: 'session' });
  });
});
