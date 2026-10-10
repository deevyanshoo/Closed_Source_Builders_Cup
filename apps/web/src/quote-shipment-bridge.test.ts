import { describe, expect, it } from 'vitest';
import type { QuoteShipmentBridge, QuoteShipmentBridgeResponse, ShipmentRecoveryContext } from '../../../contracts/generated/types';
import type { QuoteSession } from './offers-service';
import { claimHandoff, getQuoteShipmentBridge, handoffUrl, parseHandoff, selectHandoffContext, stageHandoff } from './quote-shipment-bridge';

const session: QuoteSession = { principal_id: 'P', workspace_id: 'W', role: 'viewer', identity_mode: 'development', development_only: true };
const candidate = { candidate_id: 'offer_1@v2#item0', offer_id: 'offer_1', offer_version: 2, item_index: 0,
  document_id: `sha256:${'a'.repeat(64)}` };
const triple = { context_id: 'b'.repeat(32), revision: 3, request_sha256: 'c'.repeat(64) };
const bridge: QuoteShipmentBridge = { candidate, shipment_context: { ...triple, plan_version: 'plan-3' }, host_binding: 'host_configured_synthetic',
  source_authenticity: 'not_verified', context_as_of: '2026-10-09T00:00:00Z', source_as_of: '2026-10-10',
  source_checked_at: '2026-10-10T12:00:00Z', evidence_refs: [{ field: 'shipments', locator: 'line:1',
    text_sha256: 'd'.repeat(64), document_id: candidate.document_id }] };
const envelope: QuoteShipmentBridgeResponse = { contract_version: '0.2.0', context: { principal_id: 'P', workspace_id: 'W', role: 'viewer', identity_mode: 'development' }, bridge };
const context = { context_id: triple.context_id, revision: triple.revision, request_sha256: triple.request_sha256,
  workspace_id: 'W', plan_version: 'plan-3', propagation_snapshot_id: 'graph-1', production_snapshot_id: 's1',
  as_of: bridge.context_as_of, synthetic: true, source: 'host_configured_synthetic', persistence: 'ephemeral',
  source_authenticity: 'not_verified' } as ShipmentRecoveryContext;
const response = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });

describe('quote to shipment read-only bridge', () => {
  it('sends one exact candidate GET with no client identity, as-of, body or auth headers', async () => {
    let calls = 0;
    const result = await getQuoteShipmentBridge(session, candidate, () => true, new AbortController().signal,
      (async (url, init) => {
        calls++;
        const parsed = new URL(String(url), 'http://local');
        expect(parsed.pathname).toBe('/v2/workspaces/W/shipment-recovery/quote-bridge');
        expect([...parsed.searchParams.entries()]).toEqual(Object.entries({ candidate_id: candidate.candidate_id,
          offer_id: candidate.offer_id, offer_version: '2', item_index: '0', document_id: candidate.document_id }));
        expect(init?.method).toBe('GET');
        expect(init?.cache).toBe('no-store');
        expect(init?.body).toBeUndefined();
        expect(init?.headers).toBeUndefined();
        return response(envelope);
      }) as typeof fetch);
    expect(calls).toBe(1);
    expect(result.bridge.shipment_context.request_sha256).toBe(triple.request_sha256);
    const nano = { ...envelope, bridge: { ...bridge, source_checked_at: '2026-10-10T12:00:00.123456789Z' } };
    expect((await getQuoteShipmentBridge(session, candidate, () => true, new AbortController().signal,
      (async () => response(nano)) as typeof fetch)).bridge.source_checked_at).toBe(nano.bridge.source_checked_at);
  });

  it('rejects identity, candidate, provenance, oversized, and late responses without retaining data', async () => {
    const bad = [
      { ...envelope, context: { ...envelope.context, role: 'editor' } },
      { ...envelope, bridge: { ...bridge, candidate: { ...candidate, offer_version: 3 } } },
      { ...envelope, bridge: { ...bridge, source_authenticity: 'verified' } },
      { ...envelope, bridge: { ...bridge, evidence_refs: [{ ...bridge.evidence_refs[0], document_id: `sha256:${'e'.repeat(64)}` }] } },
    ];
    for (const body of bad) await expect(getQuoteShipmentBridge(session, candidate, () => true,
      new AbortController().signal, (async () => response(body)) as typeof fetch)).rejects.toThrow('unavailable');
    await expect(getQuoteShipmentBridge(session, candidate, () => true, new AbortController().signal,
      (async () => response({ filler: 'x'.repeat(65536) })) as typeof fetch)).rejects.toThrow('unavailable');
    let current = true;
    await expect(getQuoteShipmentBridge(session, candidate, () => current, new AbortController().signal,
      (async () => { current = false; return response(envelope); }) as typeof fetch)).rejects.toThrow('unavailable');
  });

  it('requires canonical URL, same-session ephemeral state, and one exact owned context', () => {
    const url = handoffUrl(triple);
    expect(parseHandoff(new URL(url, 'http://local').search)).toEqual(triple);
    const highRevision = { ...triple, revision: 2147483648 };
    expect(parseHandoff(new URL(handoffUrl(highRevision), 'http://local').search)).toEqual(highRevision);
    for (const search of ['', '?context_id=' + triple.context_id, `${url.slice(url.indexOf('?'))}&extra=1`,
      `${url.slice(url.indexOf('?'))}&revision=3`, `?revision=03&context_id=${triple.context_id}&request_sha256=${triple.request_sha256}`,
      `?context_id=${triple.context_id}&request_sha256=${triple.request_sha256}&revision=3`])
      expect(parseHandoff(search)).toBeNull();
    const state = stageHandoff(envelope, session);
    expect(claimHandoff({ ...state, token: 'forged' }, new URL(url, 'http://local').search)).toBeNull();
    expect(claimHandoff(state, new URL(url, 'http://local').search)).toBeNull();
    const next = stageHandoff(envelope, session);
    const claimed = claimHandoff(next, new URL(url, 'http://local').search);
    expect(claimed).not.toBeNull();
    expect(claimHandoff(next, new URL(url, 'http://local').search)).toBeNull();
    expect(selectHandoffContext([context], claimed, triple, session)).toEqual(context);
    expect(selectHandoffContext([context, context], claimed, triple, session)).toBeNull();
    expect(selectHandoffContext([{ ...context, workspace_id: 'foreign' }], claimed, triple, session)).toBeNull();
    expect(selectHandoffContext([{ ...context, revision: 4 }], claimed, triple, session)).toBeNull();
    expect(selectHandoffContext([{ ...context, request_sha256: 'e'.repeat(64) }], claimed, triple, session)).toBeNull();
    expect(selectHandoffContext([context], claimed, triple, { ...session, principal_id: 'other' })).toBeNull();
    expect(selectHandoffContext([context], claimed, triple, { ...session, role: 'editor' })).toBeNull();
    expect(selectHandoffContext([context], claimed, triple, { ...session, workspace_id: 'other' })).toBeNull();
    expect(selectHandoffContext([context], claimed, triple, { ...session, identity_mode: 'private_machine' as never })).toBeNull();
    expect(selectHandoffContext([context], claimed, { ...triple, request_sha256: 'e'.repeat(64) }, session)).toBeNull();
  });
});
