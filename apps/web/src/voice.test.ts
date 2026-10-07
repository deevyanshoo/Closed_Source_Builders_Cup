import { describe, expect, it, vi } from 'vitest';
import type { SessionResponse } from '../../../contracts/generated/types';
import { VoiceController, voiceMatches, voiceExample } from './voice';
import resultRaw from '../../../contracts/examples/voice_http_recovery_result.json?raw';
import precisionRaw from '../../../contracts/examples/voice_http_recovery_precision_result.json?raw';

const identity: SessionResponse = { contract_version: '0.2.0', principal_id: 'p', workspace_id: 'W', role: 'viewer', development_only: true };
const id = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
const version = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';
const fingerprint = 'c'.repeat(64);
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
const created = () => ({ contract_version: '0.2.0', session_id: id, context_version: version, context_fingerprint: fingerprint, read_only: true, synthetic: true, snapshot_id: 's1', plan_version: 'v7', scenario_id: 'scenario-1', status: 'created' });
const accepted = () => ({ contract_version: '0.2.0', session_id: id, context_version: version, context_fingerprint: fingerprint, read_only: true, synthetic: true, turn_id: '1', status: 'accepted' });
const deferred = <T>() => { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done; }); return { promise, resolve }; };
const tick = () => new Promise<void>(done => queueMicrotask(done));
const until = async (predicate: () => boolean) => { for (let i = 0; i < 100 && !predicate(); i++) await tick(); expect(predicate()).toBe(true); };

describe('typed voice lifecycle ownership', () => {
  it('uses the saved server reference and unchanged baseline inputs, then hides a stale final result', async () => {
    const editor = { ...identity, role: 'editor' as const };
    const ref = { context_id: 'd'.repeat(32), workspace_id: 'W', revision: 1 };
    const snapshot = { ...voiceExample('recovery', 'W').snapshot, snapshot_id: `production-${ref.context_id}-r1`, plan_version: `production-plan-${ref.context_id}-r1` };
    const context = { ...ref, snapshot_id: snapshot.snapshot_id, plan_version: snapshot.plan_version, as_of: snapshot.as_of,
      metadata: { synthetic: true, source: 'caller_supplied_synthetic', syntactic_validation: 'accepted', numerical_acceptance: 'not_claimed' },
      created_by: 'p', updated_by: 'p', created_at: snapshot.as_of, updated_at: snapshot.as_of, snapshot };
    const ready = JSON.parse(resultRaw) as Record<string, unknown> & { outcome: { ownership: { session_id: string } } };
    ready.session_id = id; ready.context_version = version; ready.context_fingerprint = fingerprint; ready.production_context_ref = ref; ready.outcome.ownership.session_id = id;
    let reads = 0; let savedCreates = 0; let inlineCreates = 0; let turnBody: { call: { arguments: { arrival_replacements: unknown[]; assignments: unknown[] } } } | undefined;
    const paths: string[] = [];
    const transport: typeof fetch = async (input, init) => {
      const path = String(input); paths.push(`${init?.method || 'GET'} ${path}`);
      if (path === '/v2/workspaces/W/production-contexts' && init?.method === 'GET') return json({ contract_version: '0.2.0', contexts: [{ ...context, snapshot: undefined }], has_more: false });
      if (path === `/v2/production-contexts/${ref.context_id}`) return json({ contract_version: '0.2.0', context: { ...context, revision: ++reads >= 4 ? 2 : 1 } });
      if (path === '/v2/voice/saved-context-sessions') { savedCreates++; expect(JSON.parse(String(init?.body))).toEqual({ contract_version: '0.2.0', context_id: ref.context_id, expected_revision: 1 }); return json({ ...created(), snapshot_id: snapshot.snapshot_id, plan_version: snapshot.plan_version, production_context_ref: ref }, 201); }
      if (path === '/v2/voice/sessions') { inlineCreates++; throw new Error('inline create forbidden'); }
      if (path.endsWith('/turns') && init?.method === 'POST') { turnBody = JSON.parse(String(init.body)); return json({ ...accepted(), production_context_ref: ref }, 202); }
      if (path.endsWith('/result')) return json(ready);
      if (path.endsWith('/cancel')) return json({ ...accepted(), status: 'cancelled' });
      if (init?.method === 'DELETE') return json({ contract_version: '0.2.0', session_id: id, read_only: true, synthetic: true, status: 'closed' });
      throw new Error(`unexpected ${path}`);
    };
    const controller = new VoiceController({ transport, loadSession: async () => editor });
    await controller.refreshIdentity(); controller.selectSource('saved');
    await until(() => controller.state.contexts.length === 1);
    await controller.selectSaved(ref.context_id);
    expect(controller.state.base?.snapshot).toEqual(snapshot);
    await controller.run();
    expect({ savedCreates, inlineCreates }).toEqual({ savedCreates: 1, inlineCreates: 0 });
    expect(turnBody?.call.arguments).toEqual({ arrival_replacements: [], assignments: snapshot.baseline_assignments });
    expect(controller.state.kind).toBe('stale');
    expect(controller.state.comparison).toBeUndefined();
    expect(paths).toContain(`DELETE /v2/voice/sessions/${id}`);
  });
  it('rejects unsafe saved revisions in the generated validator', () => {
    const ref = { context_id: 'd'.repeat(32), workspace_id: 'W', revision: 1 };
    expect(voiceMatches(ref, 'ProductionContextRef')).toBe(true);
    expect(voiceMatches({ ...ref, revision: 1.5 }, 'ProductionContextRef')).toBe(false);
    expect(voiceMatches({ ...ref, revision: Number.MAX_SAFE_INTEGER + 1 }, 'ProductionContextRef')).toBe(false);
  });
  it('blocks viewer saves before POST and keeps a draft separate from the selected stored input', async () => {
    let writes = 0;
    const transport: typeof fetch = async (_input, init) => { if (init?.method === 'POST' || init?.method === 'PUT') writes++; throw new Error('unexpected request'); };
    const controller = new VoiceController({ transport, loadSession: async () => identity });
    await controller.refreshIdentity();
    controller.editSnapshot(JSON.stringify(voiceExample('recovery', 'W').snapshot));
    expect(controller.state.base).toBeUndefined();
    await controller.saveContext(false);
    expect(writes).toBe(0);
    expect(controller.state.kind).toBe('failed');
    expect(controller.state.message).toContain('editor');
  });
  it('accepts server-owned IDs on save and sends the selected revision on replace', async () => {
    const editor = { ...identity, role: 'editor' as const };
    const contextId = 'd'.repeat(32);
    const supplied = voiceExample('recovery_precision', 'W').snapshot;
    let revision = 0; let putBody: Record<string, unknown> | undefined;
    const transport: typeof fetch = async (input, init) => {
      const path = String(input);
      if (path === '/v2/workspaces/W/production-contexts' && init?.method === 'POST') {
        expect(JSON.parse(String(init.body)).snapshot).toEqual(supplied);
        revision = 1;
      } else if (path === `/v2/production-contexts/${contextId}` && init?.method === 'PUT') {
        putBody = JSON.parse(String(init.body)); revision = 2;
      } else if (path === '/v2/workspaces/W/production-contexts' && init?.method === 'GET') {
        return json({ contract_version: '0.2.0', contexts: [], has_more: false });
      } else throw new Error(`unexpected ${path}`);
      const snapshot = { ...supplied, snapshot_id: `production-${contextId}-r${revision}`, plan_version: `production-plan-${contextId}-r${revision}` };
      return json({ contract_version: '0.2.0', context: { context_id: contextId, workspace_id: 'W', revision,
        snapshot_id: snapshot.snapshot_id, plan_version: snapshot.plan_version, as_of: snapshot.as_of,
        metadata: { synthetic: true, source: 'caller_supplied_synthetic', syntactic_validation: 'accepted', numerical_acceptance: 'not_claimed' },
        created_by: 'p', updated_by: 'p', created_at: snapshot.as_of, updated_at: snapshot.as_of, snapshot } }, revision === 1 ? 201 : 200);
    };
    const controller = new VoiceController({ transport, loadSession: async () => editor });
    await controller.refreshIdentity(); controller.selectSource('saved');
    await until(() => controller.state.kind === 'idle');
    controller.editSnapshot(JSON.stringify(supplied));
    await controller.saveContext(false);
    expect(controller.state.selected?.revision).toBe(1);
    expect(controller.state.base?.snapshot.snapshot_id).toBe(`production-${contextId}-r1`);
    await controller.saveContext(true);
    expect(putBody?.expected_revision).toBe(1);
    expect(controller.state.selected?.revision).toBe(2);
    expect(controller.state.base?.snapshot.plan_version).toBe(`production-plan-${contextId}-r2`);
  });
  it.each(['identity recheck', 'context GET 401', 'context GET 403'] as const)('clears saved workspace data after %s session failure during selection', async failure => {
    const contextId = 'd'.repeat(32);
    const summary = { context_id: contextId, workspace_id: 'W', revision: 1, snapshot_id: 'saved', plan_version: 'saved-v1', as_of: '2026-10-07T00:00:00Z',
      metadata: { synthetic: true, source: 'caller_supplied_synthetic', syntactic_validation: 'accepted', numerical_acceptance: 'not_claimed' },
      created_by: 'p', updated_by: 'p', created_at: '2026-10-07T00:00:00Z', updated_at: '2026-10-07T00:00:00Z' };
    let checks = 0;
    const transport: typeof fetch = async (input) => {
      const path = String(input);
      if (path === '/v2/workspaces/W/production-contexts') return json({ contract_version: '0.2.0', contexts: [summary], has_more: false });
      if (path === `/v2/production-contexts/${contextId}`) return json({ contract_version: '0.2.0', error: { code: 'unauthorized', message: 'private detail' } }, failure === 'context GET 403' ? 403 : 401);
      throw new Error(`unexpected ${path}`);
    };
    const controller = new VoiceController({ transport, loadSession: async () => { if (++checks === 3 && failure === 'identity recheck') throw new Error('private detail'); return identity; } });
    await controller.refreshIdentity(); controller.selectSource('saved');
    await until(() => controller.state.contexts.length === 1);
    await controller.selectSaved(contextId);
    expect(controller.state.kind).toBe('signed_out');
    expect(controller.state.identity).toBeUndefined();
    expect(controller.state.contexts).toEqual([]);
    expect(controller.state.selected).toBeUndefined();
    expect(controller.state.base).toBeUndefined();
    expect(controller.state.snapshotText).toBe('');
    expect(controller.state.message).not.toContain('private detail');
  });
  it.each([401, 403])('clears saved input after a successful write followed by list %s session failure', async status => {
    const contextId = 'd'.repeat(32);
    const supplied = voiceExample('recovery', 'W').snapshot;
    const snapshot = { ...supplied, snapshot_id: `production-${contextId}-r1`, plan_version: `production-plan-${contextId}-r1` };
    let writes = 0;
    const transport: typeof fetch = async (input, init) => {
      const path = String(input);
      if (path === '/v2/workspaces/W/production-contexts' && init?.method === 'POST') {
        writes++;
        return json({ contract_version: '0.2.0', context: { context_id: contextId, workspace_id: 'W', revision: 1,
          snapshot_id: snapshot.snapshot_id, plan_version: snapshot.plan_version, as_of: snapshot.as_of,
          metadata: { synthetic: true, source: 'caller_supplied_synthetic', syntactic_validation: 'accepted', numerical_acceptance: 'not_claimed' },
          created_by: 'p', updated_by: 'p', created_at: snapshot.as_of, updated_at: snapshot.as_of, snapshot } }, 201);
      }
      if (path === '/v2/workspaces/W/production-contexts' && init?.method === 'GET') return json({ contract_version: '0.2.0', error: { code: 'unauthorized', message: 'private detail' } }, status);
      throw new Error(`unexpected ${path}`);
    };
    const controller = new VoiceController({ transport, loadSession: async () => ({ ...identity, role: 'editor' }) });
    await controller.refreshIdentity();
    controller.editSnapshot(JSON.stringify(supplied));
    await controller.saveContext(false);
    expect(writes).toBe(1);
    expect(controller.state.kind).toBe('signed_out');
    expect(controller.state.identity).toBeUndefined();
    expect(controller.state.contexts).toEqual([]);
    expect(controller.state.selected).toBeUndefined();
    expect(controller.state.base).toBeUndefined();
    expect(controller.state.snapshotText).toBe('');
    expect(controller.state.message).not.toContain('private detail');
  });
  it('calls the default native transport with the global receiver', async () => {
    let requests = 0;
    vi.stubGlobal('fetch', function (this: typeof globalThis, input: RequestInfo | URL) {
      if (this !== globalThis) throw new TypeError('Illegal invocation');
      expect(String(input)).toBe('/v2/voice/sessions');
      requests++;
      return Promise.resolve(json({ contract_version: '0.2.0', error: { code: 'voice_unavailable', message: 'offline' } }, 503));
    });
    try {
      const controller = new VoiceController({ loadSession: async () => identity });
      await controller.refreshIdentity();
      await controller.run();
      expect(requests).toBe(1);
    } finally { vi.unstubAllGlobals(); }
  });
  it('accepts the authoritative nested ready result shapes without consuming depth on schema references', () => {
    expect(voiceMatches(JSON.parse(resultRaw), 'VoiceHTTPResultResponse')).toBe(true);
    expect(voiceMatches(JSON.parse(precisionRaw), 'VoiceHTTPResultResponse')).toBe(true);
  });
  it('preserves the authoritative unchanged unsupported stale receipt expectation', async () => {
    let startBody: Record<string, unknown> | undefined;
    const transport: typeof fetch = async (input, init) => {
      const path = String(input);
      if (path === '/v2/voice/sessions' && init?.method === 'POST') return json(created(), 201);
      if (path.endsWith('/turns') && init?.method === 'POST') { startBody = JSON.parse(String(init.body)) as Record<string, unknown>; return json({ contract_version: '0.2.0', error: { code: 'voice_unavailable', message: 'offline' } }, 503); }
      if (init?.method === 'DELETE') return json({ contract_version: '0.2.0', session_id: id, read_only: true, synthetic: true, status: 'closed' });
      throw new Error(`unexpected ${path}`);
    };
    const controller = new VoiceController({ transport, loadSession: async () => identity });
    await controller.refreshIdentity();
    controller.selectExample('recovery_unsupported');
    await controller.run();
    const call = startBody?.call as { arguments: { arrival_replacements: { expected_arrival_at: string }[] } } | undefined;
    expect(call?.arguments.arrival_replacements[0].expected_arrival_at).toBe('2026-10-10T09:00:01Z');
  });
  it('hides a cancelled late create and closes its server session before reuse', async () => {
    const lateCreate = deferred<Response>();
    const paths: string[] = [];
    const transport: typeof fetch = async (input, init) => {
      const path = String(input); paths.push(`${init?.method || 'GET'} ${path}`);
      if (init?.method === 'POST' && path === '/v2/voice/sessions') return lateCreate.promise;
      if (init?.method === 'DELETE' && path === `/v2/voice/sessions/${id}`) return json({ contract_version: '0.2.0', session_id: id, read_only: true, synthetic: true, status: 'closed' });
      throw new Error(`unexpected ${path}`);
    };
    const controller = new VoiceController({ transport, loadSession: async () => identity });
    await controller.refreshIdentity();
    const running = controller.run();
    await until(() => paths.includes('POST /v2/voice/sessions'));
    const cancelling = controller.cancel();
    expect(controller.state.kind).toBe('closing');
    expect(controller.state.session).toBeUndefined();
    lateCreate.resolve(json(created(), 201));
    await Promise.all([running, cancelling]);
    expect(controller.state.kind).toBe('cancelled');
    expect(paths).toContain(`DELETE /v2/voice/sessions/${id}`);
    expect(controller.state.session).toBeUndefined();
  });

  it('never adopts a late old start after replacement and explicitly cancels and closes it', async () => {
    const lateStart = deferred<Response>();
    const paths: string[] = [];
    let creates = 0;
    const transport: typeof fetch = async (input, init) => {
      const path = String(input); paths.push(`${init?.method || 'GET'} ${path}`);
      if (init?.method === 'POST' && path === '/v2/voice/sessions') { creates++; return json({ ...created(), session_id: creates === 1 ? id : 'dddddddddddddddddddddddddddddddd' }, 201); }
      if (init?.method === 'POST' && path.endsWith('/turns')) return lateStart.promise;
      if (init?.method === 'POST' && path.endsWith('/cancel')) return json({ ...accepted(), status: 'cancelled' });
      if (init?.method === 'DELETE') return json({ contract_version: '0.2.0', session_id: path.split('/').at(-1), read_only: true, synthetic: true, status: 'closed' });
      throw new Error(`unexpected ${path}`);
    };
    const controller = new VoiceController({ transport, loadSession: async () => identity });
    await controller.refreshIdentity();
    const first = controller.run();
    await until(() => paths.includes(`POST /v2/voice/sessions/${id}/turns`));
    const replacement = controller.run();
    expect(controller.state.session).toBeUndefined();
    lateStart.resolve(json(accepted(), 202));
    await first;
    await until(() => paths.includes(`DELETE /v2/voice/sessions/${id}`));
    expect(paths).toContain(`POST /v2/voice/sessions/${id}/turns/1/cancel`);
    expect(paths).toContain(`DELETE /v2/voice/sessions/${id}`);
    await replacement;
    expect(creates).toBe(2);
    controller.dispose();
  });
  it('bounds a hung accepted-start response and closes the known session', async () => {
    const paths: string[] = [];
    const transport: typeof fetch = async (input, init) => {
      const path = String(input); paths.push(`${init?.method || 'GET'} ${path}`);
      if (init?.method === 'POST' && path === '/v2/voice/sessions') return json(created(), 201);
      if (init?.method === 'POST' && path.endsWith('/turns')) return new Promise<Response>(() => {});
      if (init?.method === 'DELETE') return json({ contract_version: '0.2.0', session_id: id, read_only: true, synthetic: true, status: 'closed' });
      throw new Error(`unexpected ${path}`);
    };
    const controller = new VoiceController({ transport, loadSession: async () => identity, requestTimeoutMs: 10 });
    await controller.refreshIdentity();
    const running = controller.run();
    await new Promise(done => setTimeout(done, 50));
    expect(paths).toContain(`DELETE /v2/voice/sessions/${id}`);
    await running;
    expect(controller.state.kind).toBe('unavailable');
  });
  it('removes prior owner context immediately when the service rejects identity', async () => {
    const transport: typeof fetch = async () => json({ contract_version: '0.2.0', error: { code: 'unauthorized', message: 'raw server detail' } }, 401);
    const controller = new VoiceController({ transport, loadSession: async () => identity });
    await controller.refreshIdentity();
    await controller.run();
    expect(controller.state.kind).toBe('signed_out');
    expect(controller.state.identity).toBeUndefined();
    expect(controller.state.base).toBeUndefined();
    expect(controller.state.message).not.toContain('raw server detail');
  });
  it('clears a loaded owner when a fresh identity check fails before create', async () => {
    let calls = 0;
    const controller = new VoiceController({ loadSession: async () => { if (++calls === 1) return identity; throw new Error('private session detail'); }, transport: async () => { throw new Error('must not create'); } });
    await controller.refreshIdentity();
    await controller.run();
    expect(controller.state.kind).toBe('signed_out');
    expect(controller.state.identity).toBeUndefined();
    expect(controller.state.base).toBeUndefined();
    expect(controller.state.arrivals).toEqual({});
    expect(controller.state.message).not.toContain('private session detail');
  });
  it.each([['recovery', resultRaw], ['recovery_precision', precisionRaw]] as const)('renders an authoritative %s comparison with exact strings', async (example, raw) => {
    const ready = JSON.parse(raw) as Record<string, unknown> & { outcome: { result: { scenario_id: string }; ownership: { session_id: string } } };
    ready.session_id = id; ready.context_version = version; ready.context_fingerprint = fingerprint;
    ready.outcome.ownership.session_id = id;
    const transport: typeof fetch = async (input, init) => {
      const path = String(input);
      if (path === '/v2/voice/sessions' && init?.method === 'POST') return json({ ...created(), scenario_id: ready.outcome.result.scenario_id }, 201);
      if (path.endsWith('/turns') && init?.method === 'POST') return json(accepted(), 202);
      if (path.endsWith('/result')) return json(ready);
      if (init?.method === 'POST' && path.endsWith('/cancel')) return json({ ...accepted(), status: 'cancelled' });
      if (init?.method === 'DELETE') return json({ contract_version: '0.2.0', session_id: id, read_only: true, synthetic: true, status: 'closed' });
      throw new Error(`unexpected ${path}`);
    };
    const controller = new VoiceController({ transport, loadSession: async () => identity });
    await controller.refreshIdentity(); controller.selectExample(example);
    await controller.run();
    expect(controller.state.kind).toBe('compared');
    expect(controller.state.comparison?.scenario_id).toBe(ready.outcome.result.scenario_id);
    expect(typeof controller.state.comparison?.original_baseline.orders[0].output_quantity).toBe('string');
    await controller.cancel();
    expect(controller.state.comparison).toBeUndefined();
  });
  it('does not adopt a late ready poll after cancellation', async () => {
    const latePoll = deferred<Response>(); const paths: string[] = [];
    const transport: typeof fetch = async (input, init) => {
      const path = String(input); paths.push(`${init?.method || 'GET'} ${path}`);
      if (path === '/v2/voice/sessions' && init?.method === 'POST') return json(created(), 201);
      if (path.endsWith('/turns') && init?.method === 'POST') return json(accepted(), 202);
      if (path.endsWith('/result')) return latePoll.promise;
      if (path.endsWith('/cancel')) return json({ ...accepted(), status: 'cancelled' });
      if (init?.method === 'DELETE') return json({ contract_version: '0.2.0', session_id: id, read_only: true, synthetic: true, status: 'closed' });
      throw new Error(`unexpected ${path}`);
    };
    const controller = new VoiceController({ transport, loadSession: async () => identity });
    await controller.refreshIdentity(); const running = controller.run();
    await until(() => paths.some(path => path.endsWith('/result')));
    const cancelled = controller.cancel();
    expect(controller.state.kind).toBe('closing');
    const ready = JSON.parse(resultRaw) as Record<string, unknown> & { outcome: { ownership: { session_id: string } } };
    ready.session_id = id; ready.context_version = version; ready.context_fingerprint = fingerprint; ready.outcome.ownership.session_id = id;
    latePoll.resolve(json(ready));
    await Promise.all([running, cancelled]);
    expect(controller.state.kind).toBe('cancelled');
    expect(controller.state.outcome).toBeUndefined();
    expect(paths).toContain(`POST /v2/voice/sessions/${id}/turns/1/cancel`);
    expect(paths).toContain(`DELETE /v2/voice/sessions/${id}`);
  });
  it('shows cleanup failure and blocks another create', async () => {
    let creates = 0;
    const transport: typeof fetch = async (input, init) => {
      const path = String(input);
      if (path === '/v2/voice/sessions' && init?.method === 'POST') { creates++; return json(created(), 201); }
      if (path.endsWith('/turns') && init?.method === 'POST') return json(accepted(), 202);
      if (path.endsWith('/result')) return json({ ...accepted(), state: 'pending', retry_after_ms: 100 });
      if (path.endsWith('/cancel')) return json({ ...accepted(), status: 'cancelled' });
      if (init?.method === 'DELETE') return json({ contract_version: '0.2.0', error: { code: 'voice_unavailable', message: 'raw detail' } }, 503);
      throw new Error(`unexpected ${path}`);
    };
    const controller = new VoiceController({ transport, loadSession: async () => identity, pause: () => new Promise<void>(() => {}) });
    await controller.refreshIdentity(); const running = controller.run();
    await until(() => controller.state.kind === 'pending');
    await controller.cancel();
    expect(controller.state.kind).toBe('failed');
    expect(controller.state.message).toContain('cleanup');
    await controller.run();
    expect(creates).toBe(1);
    void running;
  });
  it('retries failed known cleanup only on explicit identity refresh before another create', async () => {
    const latePoll = deferred<Response>(); const events: string[] = [];
    let checks = 0; let creates = 0; let deletes = 0;
    const transport: typeof fetch = async (input, init) => {
      const path = String(input);
      if (path === '/v2/voice/sessions' && init?.method === 'POST') {
        events.push('create'); creates++;
        return creates === 1 ? json(created(), 201) : json({ contract_version: '0.2.0', error: { code: 'voice_unavailable', message: 'offline' } }, 503);
      }
      if (path.endsWith('/turns') && init?.method === 'POST') return json(accepted(), 202);
      if (path.endsWith('/result')) return latePoll.promise;
      if (path.endsWith('/cancel') && init?.method === 'POST') return json({ ...accepted(), status: 'cancelled' });
      if (init?.method === 'DELETE') {
        events.push('delete'); deletes++;
        return deletes === 1 ? json({ contract_version: '0.2.0', error: { code: 'voice_unavailable', message: 'offline' } }, 503)
          : json({ contract_version: '0.2.0', session_id: id, read_only: true, synthetic: true, status: 'closed' });
      }
      throw new Error(`unexpected ${path}`);
    };
    const controller = new VoiceController({ transport, loadSession: async () => { checks++; return identity; } });
    await controller.refreshIdentity(); const running = controller.run();
    await until(() => controller.state.kind === 'pending');
    const cancelled = controller.cancel();
    latePoll.resolve(json({ ...accepted(), state: 'pending', retry_after_ms: 100 }));
    await Promise.all([running, cancelled]);
    expect(controller.state.kind).toBe('failed');
    expect(controller.state.session).toBeUndefined();
    expect(controller.state.outcome).toBeUndefined();
    expect({ checks, creates, deletes }).toEqual({ checks: 3, creates: 1, deletes: 1 });
    await controller.run();
    expect({ checks, creates, deletes }).toEqual({ checks: 3, creates: 1, deletes: 1 });
    await controller.refreshIdentity();
    expect({ checks, creates, deletes }).toEqual({ checks: 4, creates: 1, deletes: 2 });
    expect(controller.state.kind).toBe('idle');
    await controller.run();
    expect(creates).toBe(2);
    expect(events).toEqual(['create', 'delete', 'delete', 'create']);
  });
  it('keeps known cleanup unresolved across repeated explicit retry failures', async () => {
    let creates = 0; let deletes = 0;
    const transport: typeof fetch = async (input, init) => {
      const path = String(input);
      if (path === '/v2/voice/sessions' && init?.method === 'POST') { creates++; return json(created(), 201); }
      if (path.endsWith('/turns') && init?.method === 'POST') return json({ contract_version: '0.2.0', error: { code: 'voice_unavailable', message: 'offline' } }, 503);
      if (init?.method === 'DELETE') {
        deletes++;
        return deletes < 3 ? json({ contract_version: '0.2.0', error: { code: 'voice_unavailable', message: 'offline' } }, 503)
          : json({ contract_version: '0.2.0', session_id: id, read_only: true, synthetic: true, status: 'closed' });
      }
      throw new Error(`unexpected ${path}`);
    };
    const controller = new VoiceController({ transport, loadSession: async () => identity });
    await controller.refreshIdentity(); await controller.run();
    expect({ creates, deletes, kind: controller.state.kind }).toEqual({ creates: 1, deletes: 1, kind: 'failed' });
    await controller.refreshIdentity();
    expect({ creates, deletes, kind: controller.state.kind }).toEqual({ creates: 1, deletes: 2, kind: 'failed' });
    expect(controller.state.comparison).toBeUndefined();
    await controller.run();
    expect(creates).toBe(1);
    await controller.refreshIdentity();
    expect({ creates, deletes, kind: controller.state.kind }).toEqual({ creates: 1, deletes: 3, kind: 'idle' });
  });
  it('blocks direct run after execute cleanup fails until explicit identity refresh', async () => {
    let creates = 0; let deletes = 0; let checks = 0;
    const events: string[] = [];
    const transport: typeof fetch = async (input, init) => {
      const path = String(input);
      if (path === '/v2/voice/sessions' && init?.method === 'POST') {
        creates++; events.push('create');
        return creates === 1 ? json(created(), 201) : json({ contract_version: '0.2.0', error: { code: 'voice_unavailable', message: 'offline' } }, 503);
      }
      if (path.endsWith('/turns') && init?.method === 'POST') return json({ contract_version: '0.2.0', error: { code: 'voice_unavailable', message: 'offline' } }, 503);
      if (init?.method === 'DELETE') {
        deletes++; events.push('delete');
        return deletes === 1 ? json({ contract_version: '0.2.0', error: { code: 'voice_unavailable', message: 'offline' } }, 503)
          : json({ contract_version: '0.2.0', session_id: id, read_only: true, synthetic: true, status: 'closed' });
      }
      throw new Error(`unexpected ${path}`);
    };
    const controller = new VoiceController({ transport, loadSession: async () => { checks++; return identity; } });
    await controller.refreshIdentity(); await controller.run();
    expect({ creates, deletes, checks, kind: controller.state.kind }).toEqual({ creates: 1, deletes: 1, checks: 3, kind: 'failed' });
    expect(controller.state.session).toBeUndefined();
    expect(controller.state.comparison).toBeUndefined();
    await controller.run();
    expect({ creates, deletes, checks }).toEqual({ creates: 1, deletes: 1, checks: 3 });
    await controller.refreshIdentity();
    expect({ creates, deletes, checks, kind: controller.state.kind }).toEqual({ creates: 1, deletes: 2, checks: 4, kind: 'idle' });
    await controller.run();
    expect(creates).toBe(2);
    expect(events).toEqual(['create', 'delete', 'delete', 'create']);
  });
  it('hides a ready result when the owner changes during the final identity check', async () => {
    const ready = JSON.parse(resultRaw) as Record<string, unknown> & { outcome: { ownership: { session_id: string } } };
    ready.session_id = id; ready.context_version = version; ready.context_fingerprint = fingerprint; ready.outcome.ownership.session_id = id;
    let checks = 0; let closed = false;
    const transport: typeof fetch = async (input, init) => {
      const path = String(input);
      if (path === '/v2/voice/sessions' && init?.method === 'POST') return json({ ...created(), scenario_id: 'voice-vvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvv' }, 201);
      if (path.endsWith('/turns') && init?.method === 'POST') return json(accepted(), 202);
      if (path.endsWith('/result')) return json(ready);
      if (path.endsWith('/cancel')) return json({ ...accepted(), status: 'cancelled' });
      if (init?.method === 'DELETE') { closed = true; return json({ contract_version: '0.2.0', session_id: id, read_only: true, synthetic: true, status: 'closed' }); }
      throw new Error(`unexpected ${path}`);
    };
    const controller = new VoiceController({ transport, loadSession: async () => ++checks === 4 ? { ...identity, principal_id: 'other' } : identity });
    await controller.refreshIdentity(); await controller.run();
    expect(controller.state.kind).toBe('signed_out');
    expect(controller.state.identity).toBeUndefined();
    expect(controller.state.comparison).toBeUndefined();
    expect(closed).toBe(true);
  });
  it.each([
    ['created', 'missing session ID'], ['created', 'invalid session ID'], ['created', 'foreign snapshot'],
    ['start', 'foreign session ID'], ['start', 'foreign context'], ['start', 'invalid turn ID'],
    ['ready', 'foreign turn ID'], ['ready', 'foreign context'], ['ready', 'foreign outcome session'], ['ready', 'foreign outcome turn'],
  ] as const)('fails closed on %s response with %s', async (phase, fault) => {
    const foreign = 'dddddddddddddddddddddddddddddddd';
    const session = { ...created(), scenario_id: 'voice-vvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvv' } as Record<string, unknown>;
    const turn = { ...accepted() } as Record<string, unknown>;
    const ready = JSON.parse(resultRaw) as Record<string, unknown> & { outcome: { ownership: { session_id: string; turn_id: string } } };
    ready.session_id = id; ready.context_version = version; ready.context_fingerprint = fingerprint; ready.outcome.ownership.session_id = id;
    if (phase === 'created') {
      if (fault === 'missing session ID') delete session.session_id;
      if (fault === 'invalid session ID') session.session_id = 'bad!';
      if (fault === 'foreign snapshot') session.snapshot_id = 'other-snapshot';
    }
    if (phase === 'start') {
      if (fault === 'foreign session ID') turn.session_id = foreign;
      if (fault === 'foreign context') turn.context_version = foreign;
      if (fault === 'invalid turn ID') turn.turn_id = 'not-an-id';
    }
    if (phase === 'ready') {
      if (fault === 'foreign turn ID') ready.turn_id = '2';
      if (fault === 'foreign context') ready.context_version = foreign;
      if (fault === 'foreign outcome session') ready.outcome.ownership.session_id = foreign;
      if (fault === 'foreign outcome turn') ready.outcome.ownership.turn_id = '2';
    }
    const paths: string[] = [];
    const transport: typeof fetch = async (input, init) => {
      const path = String(input); paths.push(`${init?.method || 'GET'} ${path}`);
      if (path === '/v2/voice/sessions' && init?.method === 'POST') return json(session, 201);
      if (path.endsWith('/turns') && init?.method === 'POST') return json(turn, 202);
      if (path.endsWith('/result')) return json(ready);
      if (path.endsWith('/cancel') && init?.method === 'POST') return json({ ...accepted(), status: 'cancelled' });
      if (init?.method === 'DELETE') return json({ contract_version: '0.2.0', session_id: id, read_only: true, synthetic: true, status: 'closed' });
      throw new Error(`unexpected ${path}`);
    };
    const controller = new VoiceController({ transport, loadSession: async () => identity });
    await controller.refreshIdentity(); await controller.run();
    expect(controller.state.kind).toBe('unavailable');
    expect(controller.state.session).toBeUndefined();
    expect(controller.state.turn).toBeUndefined();
    expect(controller.state.outcome).toBeUndefined();
    expect(controller.state.comparison).toBeUndefined();
    if (phase !== 'created' || fault === 'foreign snapshot') expect(paths).toContain(`DELETE /v2/voice/sessions/${id}`);
    if (phase === 'created') expect(paths.some(path => path.endsWith('/turns'))).toBe(false);
    if (phase === 'start') expect(paths.some(path => path.endsWith('/result'))).toBe(false);
  });
  it.each(['input edit', 'example switch', 'unmount'] as const)('suppresses a late ready poll after %s', async action => {
    const latePoll = deferred<Response>(); const paths: string[] = [];
    const ready = JSON.parse(resultRaw) as Record<string, unknown> & { outcome: { ownership: { session_id: string } } };
    ready.session_id = id; ready.context_version = version; ready.context_fingerprint = fingerprint; ready.outcome.ownership.session_id = id;
    const transport: typeof fetch = async (input, init) => {
      const path = String(input); paths.push(`${init?.method || 'GET'} ${path}`);
      if (path === '/v2/voice/sessions' && init?.method === 'POST') return json({ ...created(), scenario_id: 'voice-vvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvv' }, 201);
      if (path.endsWith('/turns') && init?.method === 'POST') return json(accepted(), 202);
      if (path.endsWith('/result')) return latePoll.promise;
      if (path.endsWith('/cancel') && init?.method === 'POST') return json({ ...accepted(), status: 'cancelled' });
      if (init?.method === 'DELETE') return json({ contract_version: '0.2.0', session_id: id, read_only: true, synthetic: true, status: 'closed' });
      throw new Error(`unexpected ${path}`);
    };
    const controller = new VoiceController({ transport, loadSession: async () => identity });
    await controller.refreshIdentity(); const running = controller.run();
    await until(() => paths.some(path => path.endsWith('/result')));
    if (action === 'input edit') controller.editArrival(controller.state.base!.snapshot.receipts[0].receipt_id, '2026-10-10T12:00:00Z');
    if (action === 'example switch') controller.selectExample('recovery_precision');
    if (action === 'unmount') controller.dispose();
    latePoll.resolve(json(ready));
    await running;
    await until(() => paths.includes(`DELETE /v2/voice/sessions/${id}`));
    expect(controller.state.session).toBeUndefined();
    expect(controller.state.turn).toBeUndefined();
    expect(controller.state.outcome).toBeUndefined();
    expect(controller.state.comparison).toBeUndefined();
    expect(paths).toContain(`POST /v2/voice/sessions/${id}/turns/1/cancel`);
  });
});
