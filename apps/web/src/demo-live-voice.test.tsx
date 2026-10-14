import { describe, expect, it } from 'vitest';
import type { OperatorTurn } from './demo-api';
import { acceptsLiveTurn, matchesLiveSession, pcm16Base64, releaseLiveVoice, restoreLiveSession } from './demo-live-voice';

const scope = { sessionId: 's1', companyId: 'solara', contextVersion: 1, epoch: 2 };
const turn = { turn_id: 't1', company_id: 'solara', context_version: 1, status: 'proposal', assistant_text: 'Review this first.', agent_steps: [], evidence_ids: [], proposal: { proposal_id: 'p1', kind: 'watch', summary: 'Watch', parameters: {}, plan_id: null, offer_versions: {}, context_version: 1, expires_at: '2099-01-01T00:00:00Z' }, read_only: true } satisfies OperatorTurn;

describe('live conversation boundary', () => {
  it('requires the current server session and CSRF before opening the microphone', () => {
    const current = { session_id: 's1', company: { company_id: 'solara' }, context_version: 1, csrf_token: 'csrf' };
    expect(matchesLiveSession(current as never, scope, 'csrf')).toBe(true);
    for (const changed of [
      { ...current, session_id: 's2' },
      { ...current, company: { company_id: 'loom' } },
      { ...current, context_version: 2 },
      { ...current, csrf_token: 'rotated' },
    ]) expect(matchesLiveSession(changed as never, scope, 'csrf')).toBe(false);
  });
  it('does not clear a newly selected workspace when stale voice recovery fails', async () => {
    let current = scope;
    const calls: string[] = [];
    let fail!: (reason: Error) => void;
    const read = () => new Promise<never>((_, reject) => { fail = reject; });
    const pending = restoreLiveSession(read, scope, () => current,
      () => calls.push('restored'), () => calls.push('unknown'));
    current = { ...scope, companyId: 'loom' };
    fail(new Error('connection lost'));
    await pending;
    expect(calls).toEqual([]);
    await restoreLiveSession(async () => { throw new Error('connection lost'); }, scope, () => scope,
      () => calls.push('restored'), () => calls.push('unknown'));
    expect(calls).toEqual(['unknown']);
  });
  it('accepts a current read-only tool result and rejects old session, company, context, epoch and proposal versions', () => {
    expect(acceptsLiveTurn(turn, scope, scope)).toBe(true);
    for (const stale of [{ ...scope, sessionId: 's2' }, { ...scope, companyId: 'loom' }, { ...scope, contextVersion: 2 }, { ...scope, epoch: 3 }]) expect(acceptsLiveTurn(turn, scope, stale)).toBe(false);
    expect(acceptsLiveTurn({ ...turn, read_only: false }, scope, scope)).toBe(false);
    expect(acceptsLiveTurn({ ...turn, proposal: { ...turn.proposal!, context_version: 0 } }, scope, scope)).toBe(false);
  });
  it('encodes mono signed PCM16 at 16 kHz with the expected resampling length', () => {
    const encoded = pcm16Base64(new Float32Array([1, 1, -1, -1]), 32000);
    const raw = atob(encoded);
    expect(raw.length).toBe(4);
    expect([...raw].map(char => char.charCodeAt(0))).toEqual([255, 127, 0, 128]);
  });
  it('stops microphone, both audio contexts, queued playback and the socket on end', () => {
    const calls: string[] = [];
    const current = { timer: setTimeout(() => {}, 180000), startupTimer: setTimeout(() => {}, 15000), processor: { onaudioprocess: () => {}, disconnect: () => calls.push('processor') }, source: { disconnect: () => calls.push('source') }, stream: { getTracks: () => [{ stop: () => calls.push('track') }] }, playback: new Set([{ stop: () => calls.push('playback') }]), input: { close: () => { calls.push('input'); return Promise.resolve(); } }, output: { close: () => { calls.push('output'); return Promise.resolve(); } }, socket: { readyState: 1, send: (data: string) => calls.push(JSON.parse(data).type), close: () => calls.push('socket') } };
    const prior = globalThis.WebSocket;
    Object.assign(globalThis, { WebSocket: { OPEN: 1 } });
    try { releaseLiveVoice(current as never); } finally { Object.assign(globalThis, { WebSocket: prior }); }
    expect(calls).toEqual(['processor', 'source', 'track', 'playback', 'input', 'output', 'stop', 'socket']);
    expect(current.processor.onaudioprocess).toBeNull();
    expect(current.playback.size).toBe(0);
  });
});
