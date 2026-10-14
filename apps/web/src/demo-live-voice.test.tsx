import { describe, expect, it } from 'vitest';
import type { OperatorTurn } from './demo-api';
import { acceptsLiveTurn, pcm16Base64, releaseLiveVoice } from './demo-live-voice';

const scope = { sessionId: 's1', companyId: 'solara', contextVersion: 1, epoch: 2 };
const turn = { turn_id: 't1', company_id: 'solara', context_version: 1, status: 'proposal', assistant_text: 'Review this first.', agent_steps: [], evidence_ids: [], proposal: { proposal_id: 'p1', kind: 'watch', summary: 'Watch', parameters: {}, plan_id: null, offer_versions: {}, context_version: 1, expires_at: '2099-01-01T00:00:00Z' }, read_only: true } satisfies OperatorTurn;

describe('live conversation boundary', () => {
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
    const current = { timer: setTimeout(() => {}, 180000), processor: { onaudioprocess: () => {}, disconnect: () => calls.push('processor') }, source: { disconnect: () => calls.push('source') }, stream: { getTracks: () => [{ stop: () => calls.push('track') }] }, playback: new Set([{ stop: () => calls.push('playback') }]), input: { close: () => { calls.push('input'); return Promise.resolve(); } }, output: { close: () => { calls.push('output'); return Promise.resolve(); } }, socket: { readyState: 1, send: (data: string) => calls.push(JSON.parse(data).type), close: () => calls.push('socket') } };
    const prior = globalThis.WebSocket;
    Object.assign(globalThis, { WebSocket: { OPEN: 1 } });
    try { releaseLiveVoice(current as never); } finally { Object.assign(globalThis, { WebSocket: prior }); }
    expect(calls).toEqual(['processor', 'source', 'track', 'playback', 'input', 'output', 'stop', 'socket']);
    expect(current.processor.onaudioprocess).toBeNull();
    expect(current.playback.size).toBe(0);
  });
});
