import { describe, expect, it, vi } from 'vitest';
import type { OperatorTurn } from './demo-api';
import { renderToStaticMarkup } from 'react-dom/server';
import { createRef } from 'react';
import { voiceAudioLevel, smoothVoiceLevel, acceptedResearchProgress, settleLiveResearch, voiceResearchLabel, acceptsLiveTurn, appendLiveCaption, claimLiveVoiceStart, endActiveVoice, finalizedLiveCaptions, LiveVoice, matchesLiveSession, pcm16Base64, preflightLiveVoice, queueLivePlayback, releaseLiveVoice, restoreLiveSession, updateLiveCaptions, visibleLiveCaptions, visibleLiveVoiceState, voiceResponsePendingAfterEvent, voiceThinkingAfterEvent, voiceVisualState, liveVoiceStatusLabel, nextVoiceReconnectDelay, acceptsVoiceConnectionStatus, isTerminalVoiceClose, verifyVoiceReconnect, shouldReconnectVoiceError, armVoiceHealthyTimer, clearVoiceHealthyTimer } from './demo-live-voice';

vi.mock('./demo-activity', () => ({ AssistantOrb: () => null, ActivityIndicator: () => null }));

const scope = { sessionId: 's1', companyId: 'solara', contextVersion: 1, epoch: 2 };
const turn = { turn_id: 't1', company_id: 'solara', context_version: 1, status: 'proposal', assistant_text: 'Review this first.', agent_steps: [], evidence_ids: [], proposal: { proposal_id: 'p1', kind: 'watch', summary: 'Watch', parameters: {}, plan_id: null, offer_versions: {}, context_version: 1, expires_at: '2099-01-01T00:00:00Z' }, read_only: true } satisfies OperatorTurn;

describe('live conversation boundary', () => {
  it('distinguishes the browser microphone prompt from provider connection', () => {
    expect(liveVoiceStatusLabel('connecting', true)).toContain('Allow microphone access');
    expect(liveVoiceStatusLabel('connecting', false)).toBe('Connecting live voice…');
  });
  it('keeps a ready call past three minutes without a lifetime timer', () => {
    vi.useFakeTimers();
    try {
      const current = { ready: true, muted: false, playback: new Set() };
      vi.advanceTimersByTime(180001);
      expect(voiceVisualState(current, false)).toBe('listening');
      expect(vi.getTimerCount()).toBe(0);
    } finally { vi.useRealTimers(); }
  });
  it('bounds reconnect attempts with increasing delays and rejects stale provider status', () => {
    expect([0, 1, 2, 3].map(nextVoiceReconnectDelay)).toEqual([500, 1000, 2000, null]);
    expect(acceptsVoiceConnectionStatus({ type: 'connection_status', state: 'reconnecting', context_version: 1 }, scope)).toBe('reconnecting');
    expect(acceptsVoiceConnectionStatus({ type: 'connection_status', state: 'ready', context_version: 1 }, scope)).toBe('ready');
    expect(acceptsVoiceConnectionStatus({ type: 'connection_status', state: 'ready', context_version: 2 }, scope)).toBeNull();
    expect([1008, 4001, 4290].every(isTerminalVoiceClose)).toBe(true);
    expect([1000, 1006, 1011].some(isTerminalVoiceClose)).toBe(false);
  });
  it('reconnects only a prior-ready transient provider error, leaving terminal frames terminal', () => {
    expect(shouldReconnectVoiceError('voice_unavailable', false)).toBe(false);
    expect(shouldReconnectVoiceError('voice_unavailable', true)).toBe(true);
    for (const code of ['session_expired', 'stale_context', 'invalid_start', 'quota_exceeded', 'session_busy']) {
      expect(shouldReconnectVoiceError(code, true)).toBe(false);
    }
  });
  it('stops an uncertain history write or canonical read after 15 seconds without starting a transport', async () => {
    vi.useFakeTimers();
    const canonical = { session_id: 's1', company: { company_id: 'solara' }, context_version: 1, csrf_token: 'csrf' };
    try {
      const unread = vi.fn(async () => canonical as never);
      const waitingWrite = verifyVoiceReconnect(new Promise<void>(() => {}), unread, scope, () => true, 'csrf');
      await vi.advanceTimersByTimeAsync(15000);
      await expect(waitingWrite).resolves.toEqual({ state: 'timeout' });
      expect(unread).not.toHaveBeenCalled();
      const waitingRead = verifyVoiceReconnect(Promise.resolve(), () => new Promise<never>(() => {}), scope, () => true, 'csrf');
      await vi.advanceTimersByTimeAsync(15000);
      await expect(waitingRead).resolves.toEqual({ state: 'timeout' });
      expect(vi.getTimerCount()).toBe(0);
    } finally { vi.useRealTimers(); }
  });
  it('cancels an in-flight reconnect check immediately on End', async () => {
    vi.useFakeTimers();
    try {
      const cancelled = new AbortController();
      const pending = verifyVoiceReconnect(new Promise<void>(() => {}), async () => { throw new Error('must not read'); }, scope, () => true, 'csrf', cancelled.signal);
      cancelled.abort();
      await expect(pending).resolves.toEqual({ state: 'cancelled' });
      expect(vi.getTimerCount()).toBe(0);
    } finally { vi.useRealTimers(); }
  });
  it('resets retry budget after stable healthy audio or muted readiness, and rearms after a drop', () => {
    vi.useFakeTimers();
    try {
      const current = { ready: true, reconnectAttempts: 2, healthyTimer: undefined as ReturnType<typeof setTimeout> | undefined };
      armVoiceHealthyTimer(current, () => current.ready);
      vi.advanceTimersByTime(29999);
      expect(current.reconnectAttempts).toBe(2);
      clearVoiceHealthyTimer(current);
      expect(current.healthyTimer).toBeUndefined();
      vi.advanceTimersByTime(1);
      expect(current.reconnectAttempts).toBe(2);
      armVoiceHealthyTimer(current, () => current.ready);
      vi.advanceTimersByTime(30000);
      expect(current.reconnectAttempts).toBe(0);
      expect(current.healthyTimer).toBeUndefined();
    } finally { vi.useRealTimers(); }
  });
  it('waits for completed history before reading the canonical session and cancels stale reconnects', async () => {
    const canonical = { session_id: 's1', company: { company_id: 'solara' }, context_version: 1, csrf_token: 'csrf' };
    let finishWrite!: () => void;
    const write = new Promise<void>(resolve => { finishWrite = resolve; });
    const read = vi.fn(async () => canonical as never);
    const reconnect = verifyVoiceReconnect(write, read, scope, () => true, 'csrf');
    await Promise.resolve();
    expect(read).not.toHaveBeenCalled();
    finishWrite();
    await expect(reconnect).resolves.toMatchObject({ state: 'current' });
    expect(read).toHaveBeenCalledTimes(1);
    await expect(verifyVoiceReconnect(Promise.resolve(), read, scope, () => false, 'csrf')).resolves.toEqual({ state: 'cancelled' });
    expect(read).toHaveBeenCalledTimes(1);
    let finishRead!: (value: never) => void;
    let active = true;
    const inFlight = verifyVoiceReconnect(Promise.resolve(), () => new Promise(resolve => { finishRead = resolve; }), scope, () => active, 'csrf');
    await Promise.resolve();
    active = false;
    finishRead(canonical as never);
    await expect(inFlight).resolves.toEqual({ state: 'cancelled' });
    await expect(verifyVoiceReconnect(Promise.resolve(), async () => ({ ...canonical, csrf_token: 'rotated' } as never), scope, () => true, 'csrf')).resolves.toMatchObject({ state: 'changed' });
  });
  it('renders the spatial lens for the existing live voice instance', () => {
    const html = renderToStaticMarkup(<LiveVoice ref={createRef()} scope={scope} csrf="csrf" disabled={false} onTurn={() => {}} onSession={() => {}} onUnknown={() => {}} />);
    expect(html).toContain('class="zoorq-lens');
    expect(html).toContain('data-state="idle"');
    expect(html).toContain('Voice is off. Text chat is available.');
  });
  it('claims a single start while canonical session and microphone are pending', () => {
    const startup = { current: false };
    expect(claimLiveVoiceStart(startup, false, false)).toBe(true);
    expect(claimLiveVoiceStart(startup, false, false)).toBe(false);
    startup.current = false;
    expect(claimLiveVoiceStart(startup, true, false)).toBe(false);
    expect(claimLiveVoiceStart(startup, false, true)).toBe(false);
  });
  it('releases a voice connection only once when End and dock close both occur', () => {
    const calls: string[] = [];
    const current = { startupTimer: setTimeout(() => {}, 15000), processor: { onaudioprocess: () => {}, disconnect: () => calls.push('processor') }, source: { disconnect: () => calls.push('source') }, stream: { getTracks: () => [{ stop: () => calls.push('track') }] }, playback: new Set([{ stop: () => calls.push('playback') }]), input: { close: () => Promise.resolve() }, output: { close: () => Promise.resolve() }, socket: { readyState: 1, send: () => calls.push('stop'), close: () => calls.push('socket') } };
    const active = { current: current as never };
    const prior = globalThis.WebSocket;
    Object.assign(globalThis, { WebSocket: { OPEN: 1 } });
    try { expect(endActiveVoice(active)).toBe(true); expect(endActiveVoice(active)).toBe(false); }
    finally { Object.assign(globalThis, { WebSocket: prior }); }
    expect(calls.filter(call => call === 'track')).toHaveLength(1);
    expect(calls.filter(call => call === 'stop')).toHaveLength(1);
    expect(calls.filter(call => call === 'socket')).toHaveLength(1);
  });
  it('persists only completed voice captions once at the turn boundary', () => {
    let captions = updateLiveCaptions([], { type: 'transcript', direction: 'input', text: 'What changed' });
    captions = updateLiveCaptions(captions, { type: 'transcript', direction: 'input', text: ' for copper?' });
    expect(finalizedLiveCaptions(captions, 0)).toEqual([]);
    captions = updateLiveCaptions(captions, { type: 'turn_complete' });
    expect(finalizedLiveCaptions(captions, 0)).toEqual([{ direction: 'input', text: 'What changed for copper?' }]);
    expect(finalizedLiveCaptions(captions, 1)).toEqual([]);
  });
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
  it('accepts only a same-scope server-confirmed utility session', () => {
    const canonical = { session_id: 's1', company: { company_id: 'solara' }, context_version: 1, csrf_token: 'csrf' };
    const utility = { ...turn, status: 'completed', proposal: null, read_only: false, session: canonical, action_result: { tool: 'fill_form', summary: 'Quantity updated' } };
    expect(acceptsLiveTurn(utility, scope, scope)).toBe(true);
    expect(acceptsLiveTurn({ ...utility, session: { ...canonical, session_id: 's2' } }, scope, scope)).toBe(false);
    expect(acceptsLiveTurn({ ...utility, action_result: null }, scope, scope)).toBe(false);
  });
  it('accepts a read-only navigation result with a matching canonical session and rejects stale/no-result variants', () => {
    const canonical = { session_id: 's1', company: { company_id: 'solara' }, context_version: 1, csrf_token: 'csrf' };
    const navigate = { ...turn, status: 'completed', proposal: null, read_only: true, session: canonical, action_result: { tool: 'navigate', summary: 'Open news view.', view: 'news' } };
    expect(acceptsLiveTurn(navigate, scope, scope)).toBe(true);
    expect(acceptsLiveTurn({ ...navigate, session: { ...canonical, context_version: 2 } }, scope, scope)).toBe(false);
    expect(acceptsLiveTurn({ ...navigate, action_result: null }, scope, scope)).toBe(false);
  });
  it('accepts only a scoped server-confirmed research tool result with validated workspace research', () => {
    const canonical = { session_id: 's1', company: { company_id: 'solara' }, context_version: 1, csrf_token: 'csrf' };
    const research = { research_id: 'r1', query: 'Port status', status: 'completed', checked_at: '2026-10-15T10:00:00Z', answer: 'Open.', sources: [] };
    const confirmed = { ...turn, status: 'completed', proposal: null, read_only: false, session: canonical,
      action_result: { tool: 'confirm_research', summary: 'Research complete' }, workspace: { research } };
    expect(acceptsLiveTurn(confirmed, scope, scope)).toBe(true);
    expect(acceptsLiveTurn({ ...confirmed, workspace: null }, scope, scope)).toBe(false);
    expect(acceptsLiveTurn({ ...confirmed, session: { ...canonical, context_version: 2 } }, scope, scope)).toBe(false);
  });
  it('encodes mono signed PCM16 at 16 kHz with the expected resampling length', () => {
    const encoded = pcm16Base64(new Float32Array([1, 1, -1, -1]), 32000);
    const raw = atob(encoded);
    expect(raw.length).toBe(4);
    expect([...raw].map(char => char.charCodeAt(0))).toEqual([255, 127, 0, 128]);
  });
  it('stops microphone, both audio contexts, queued playback and the socket on end', () => {
    const calls: string[] = [];
    const current = { startupTimer: setTimeout(() => {}, 15000), processor: { onaudioprocess: () => {}, disconnect: () => calls.push('processor') }, source: { disconnect: () => calls.push('source') }, stream: { getTracks: () => [{ stop: () => calls.push('track') }] }, playback: new Set([{ stop: () => calls.push('playback') }]), input: { close: () => { calls.push('input'); return Promise.resolve(); } }, output: { close: () => { calls.push('output'); return Promise.resolve(); } }, socket: { readyState: 1, send: (data: string) => calls.push(JSON.parse(data).type), close: () => calls.push('socket') } };
    const prior = globalThis.WebSocket;
    Object.assign(globalThis, { WebSocket: { OPEN: 1 } });
    try { releaseLiveVoice(current as never); } finally { Object.assign(globalThis, { WebSocket: prior }); }
    expect(calls).toEqual(['processor', 'source', 'track', 'playback', 'input', 'output', 'stop', 'socket']);
    expect(current.processor.onaudioprocess).toBeNull();
    expect(current.playback.size).toBe(0);
  });
  it('cancels pending reconnect and healthy timers on End', () => {
    vi.useFakeTimers();
    const calls: string[] = [];
    const current = { startupTimer: setTimeout(() => calls.push('startup'), 15000), reconnectTimer: setTimeout(() => calls.push('reconnect'), 500), healthyTimer: setTimeout(() => calls.push('healthy'), 30000), socketGeneration: 2,
      processor: { onaudioprocess: () => {}, disconnect: () => {} }, source: { disconnect: () => {} }, stream: { getTracks: () => [{ stop: () => {} }] }, playback: new Set(), input: { close: () => Promise.resolve() }, output: { close: () => Promise.resolve() }, socket: { readyState: 1, send: () => {}, close: () => {}, onopen: () => calls.push('old event') } };
    const previous = globalThis.WebSocket;
    Object.assign(globalThis, { WebSocket: { OPEN: 1 } });
    try {
      releaseLiveVoice(current as never);
      vi.advanceTimersByTime(180001);
      expect(calls).toEqual([]);
      expect(current.socketGeneration).toBe(3);
      expect(current.socket.onopen).toBeNull();
    } finally { Object.assign(globalThis, { WebSocket: previous }); vi.useRealTimers(); }
  });
  it('merges more than twenty transcript fragments without dropping the start of the sentence', () => {
    let captions: { direction: 'input' | 'output'; text: string }[] = [];
    for (const fragment of ['The', ...Array.from({ length: 25 }, (_, index) => ` supplier ${index + 1}`)]) {
      captions = appendLiveCaption(captions, 'input', fragment);
    }
    captions = appendLiveCaption(captions, 'output', 'I found');
    captions = appendLiveCaption(captions, 'output', ' three options.');
    expect(captions).toEqual([
      { direction: 'input', text: `The${Array.from({ length: 25 }, (_, index) => ` supplier ${index + 1}`).join('')}` },
      { direction: 'output', text: 'I found three options.' },
    ]);
    for (let index = 0; index < 25; index++) captions = appendLiveCaption(captions, index % 2 ? 'output' : 'input', `Turn ${index}`);
    expect(captions).toHaveLength(24);
    expect(captions[0].text).toBe('Turn 1');
  });
  it('starts a new assistant caption after turn completion without splitting deltas within either utterance', () => {
    let captions = updateLiveCaptions([], { type: 'transcript', direction: 'output', text: 'First' });
    captions = updateLiveCaptions(captions, { type: 'transcript', direction: 'output', text: ' answer.' });
    captions = updateLiveCaptions(captions, { type: 'turn_complete' });
    captions = updateLiveCaptions(captions, { type: 'transcript', direction: 'output', text: 'Second' });
    captions = updateLiveCaptions(captions, { type: 'transcript', direction: 'output', text: ' answer.' });
    expect(captions.map(caption => caption.text)).toEqual(['First answer.', 'Second answer.']);
  });
  it('starts a new assistant caption after interruption, while retaining bounded history', () => {
    let captions = updateLiveCaptions([], { type: 'transcript', direction: 'output', text: 'Interrupted' });
    captions = updateLiveCaptions(captions, { type: 'interrupted' });
    captions = updateLiveCaptions(captions, { type: 'transcript', direction: 'output', text: 'Restarted' });
    captions = updateLiveCaptions(captions, { type: 'transcript', direction: 'output', text: ' response.' });
    expect(captions.map(caption => caption.text)).toEqual(['Interrupted', 'Restarted response.']);
    for (let index = 0; index < 25; index++) {
      captions = updateLiveCaptions(captions, { type: 'turn_complete' });
      captions = updateLiveCaptions(captions, { type: 'transcript', direction: 'output', text: `Reply ${index}` });
    }
    expect(captions).toHaveLength(24);
    expect(captions[0].text).toBe('Reply 1');
  });
  it('hides retained captions immediately when company or context changes', () => {
    const captions = [{ direction: 'input' as const, text: 'Our private buyer question' }];
    expect(visibleLiveCaptions(captions, scope, scope)).toEqual(captions);
    expect(visibleLiveCaptions(captions, scope, { ...scope, companyId: 'loom' })).toEqual([]);
    expect(visibleLiveCaptions(captions, scope, { ...scope, contextVersion: 2 })).toEqual([]);
  });
  it('does not show an old voice status or error in a newly selected scope', () => {
    expect(visibleLiveVoiceState('thinking', 'Connection lost', scope, scope)).toEqual({ status: 'thinking', error: 'Connection lost' });
    expect(visibleLiveVoiceState('thinking', 'Connection lost', scope, { ...scope, companyId: 'loom' })).toEqual({ status: 'idle', error: '' });
    expect(visibleLiveVoiceState('speaking', 'Connection lost', scope, { ...scope, contextVersion: 2 })).toEqual({ status: 'idle', error: '' });
  });
  it('tags an unsupported-browser preflight alert to the current scope and hides it after a switch', () => {
    for (const missing of ['microphone', 'audioContext', 'webSocket'] as const) {
      const available = { microphone: true, audioContext: true, webSocket: true, [missing]: false };
      const preflight = preflightLiveVoice(scope, available);
      expect(visibleLiveVoiceState('idle', preflight.error, preflight.scope, scope).error)
        .toBe('Live voice is unavailable in this browser. Use the text composer.');
      expect(visibleLiveVoiceState('idle', preflight.error, preflight.scope, { ...scope, companyId: 'loom' }).error)
        .toBe('');
    }
    expect(preflightLiveVoice(scope, { microphone: true, audioContext: true, webSocket: true }).error).toBe('');
  });
  it('shows thinking for input, speaking through queued playback, and muted only after audio ends', () => {
    const playback = new Set<object>();
    const current = { ready: false, muted: false, playback };
    expect(voiceVisualState(current, false)).toBe('connecting');
    current.ready = true;
    expect(voiceVisualState(current, false)).toBe('listening');
    expect(voiceVisualState(current, true)).toBe('thinking');
    const node = {};
    playback.add(node);
    current.muted = true;
    expect(voiceVisualState(current, false)).toBe('speaking');
    playback.delete(node);
    expect(voiceVisualState(current, false)).toBe('muted');
  });
  it('does not advertise Listening in an audio playback gap before the provider turn completes', () => {
    const current = { ready: true, muted: false, playback: new Set<object>(), responsePending: false };
    current.responsePending = voiceResponsePendingAfterEvent(current.responsePending, 'input_transcript');
    current.responsePending = voiceResponsePendingAfterEvent(current.responsePending, 'tool_result');
    current.responsePending = voiceResponsePendingAfterEvent(current.responsePending, 'audio');
    const node = {};
    current.playback.add(node);
    expect(voiceVisualState(current, false)).toBe('speaking');
    current.playback.delete(node);
    expect(voiceVisualState(current, false)).toBe('thinking');
    current.responsePending = voiceResponsePendingAfterEvent(current.responsePending, 'output_transcript');
    expect(voiceVisualState(current, false)).toBe('thinking');
    current.responsePending = voiceResponsePendingAfterEvent(current.responsePending, 'turn_complete');
    expect(voiceVisualState(current, false)).toBe('listening');
    current.muted = true;
    expect(voiceVisualState(current, false)).toBe('muted');
    current.responsePending = voiceResponsePendingAfterEvent(current.responsePending, 'audio');
    expect(voiceVisualState(current, false)).toBe('thinking');
    current.responsePending = voiceResponsePendingAfterEvent(current.responsePending, 'interrupted');
    expect(voiceVisualState(current, false)).toBe('muted');
  });
  it('tracks real transcript and tool activity until audio, interruption or turn completion', () => {
    expect(voiceThinkingAfterEvent(false, 'transcript')).toBe(true);
    expect(voiceThinkingAfterEvent(false, 'tool_result')).toBe(true);
    expect(voiceThinkingAfterEvent(true, 'audio')).toBe(false);
    expect(voiceThinkingAfterEvent(true, 'turn_complete')).toBe(false);
    expect(voiceThinkingAfterEvent(true, 'interrupted')).toBe(false);
  });
  it('keeps playback scheduled after turn completion until every audio node ends', () => {
    const starts: number[] = [];
    const nodes: { onended?: () => void; buffer?: { duration: number }; start: (when: number) => void; connect: () => void }[] = [];
    const output = {
      currentTime: 1,
      createBuffer: (_channels: number, length: number) => ({ duration: length / 24000, getChannelData: () => new Float32Array(length) }),
      createBufferSource: () => {
        const node = { start: (when: number) => starts.push(when), connect: () => {}, onended: undefined as (() => void) | undefined, buffer: undefined as { duration: number } | undefined };
        nodes.push(node);
        return node;
      },
      destination: {},
    };
    const current = { output, playback: new Set<object>(), nextTime: 0, ready: true, muted: false };
    const encoded = btoa(String.fromCharCode(...new Uint8Array(4800)));
    const states: string[] = [];
    queueLivePlayback(current as never, encoded, () => states.push(voiceVisualState(current, false)));
    queueLivePlayback(current as never, encoded, () => states.push(voiceVisualState(current, false)));
    expect(starts).toEqual([1, 1.1]);
    expect(voiceVisualState(current, false)).toBe('speaking');
    nodes[0].onended?.();
    expect(voiceVisualState(current, false)).toBe('speaking');
    nodes[1].onended?.();
    expect(states).toEqual(['speaking', 'speaking', 'speaking', 'listening']);
  });
});


describe('authoritative research progress', () => {
  const event = { type: 'research_progress', phase: 'started', proposal_id: 'p1', company_id: 'solara', context_version: 1 };
  it('accepts progress only on the captured current company and session epoch', () => {
    expect(acceptedResearchProgress(event, scope, scope, 'p1')).toBe('started');
    expect(acceptedResearchProgress({ ...event, company_id: 'meridian' }, scope, scope, 'p1')).toBeNull();
    expect(acceptedResearchProgress({ ...event, context_version: 2 }, scope, scope, 'p1')).toBeNull();
    expect(acceptedResearchProgress(event, scope, { ...scope, epoch: 3 }, 'p1')).toBeNull();
    expect(acceptedResearchProgress({ ...event, phase: 'anything' }, scope, scope, 'p1')).toBeNull();
  });
  it('keeps the orb thinking while verification or research outlives a provider turn', () => {
    for (const researchPhase of ['checking_consent', 'started'] as const) {
      expect(voiceVisualState({ ready: true, muted: false, playback: new Set(), responsePending: false, researchPhase }, false)).toBe('thinking');
    }
    expect(voiceVisualState({ ready: true, muted: false, playback: new Set(), responsePending: false, researchPhase: 'awaiting_consent' }, false)).toBe('listening');
  });
  it('distinguishes checking consent from an authoritatively started search', () => {
    expect(voiceResearchLabel('checking_consent')).toBe('Checking your confirmation…');
    expect(voiceResearchLabel('started')).toBe('Researching the public web…');
    expect(voiceResearchLabel('awaiting_consent')).toBe('Ready for “confirm research”');
    expect(voiceResearchLabel(null)).toBeNull();
  });
});


describe('delayed research completion', () => {
  const research = { research_id: 'r1', query: 'Public shipping status', status: 'completed', checked_at: '2026-10-15T10:00:00Z', answer: 'Sources are ready.', sources: [] };
  const result = { ...turn, status: 'completed', proposal: null, action_result: { tool: 'confirm_research', summary: 'Done' }, workspace: { research } } as OperatorTurn;
  it('settles a result after the provider turn without requiring another audio frame', () => {
    const current = { ready: true, muted: false, playback: new Set(), responsePending: false, researchPhase: 'started' as const };
    expect(settleLiveResearch(current, result)).toBe(true);
    expect(current.responsePending).toBe(false);
    expect(current.researchPhase).toBe('completed');
    expect(voiceVisualState(current, false)).toBe('listening');
    expect(settleLiveResearch(current, result)).toBe(true);
    expect(voiceVisualState(current, false)).toBe('listening');
  });
  it('settles unavailable research and rejects superseded or regressive progress', () => {
    const current = { ready: true, muted: false, playback: new Set(), responsePending: true, researchPhase: 'started' as const };
    expect(settleLiveResearch(current, { ...result, workspace: { research: { ...research, status: 'unavailable' } } } as OperatorTurn)).toBe(true);
    expect(current.researchPhase).toBe('unavailable');
    expect(voiceVisualState(current, false)).toBe('listening');
    const event = { type: 'research_progress', phase: 'started', proposal_id: 'old', company_id: 'solara', context_version: 1 };
    expect(acceptedResearchProgress(event, scope, scope, 'new')).toBeNull();
    expect(acceptedResearchProgress({ ...event, proposal_id: 'new' }, scope, scope, 'new', 'completed')).toBeNull();
  });
});

describe('actual voice energy', () => {
  it('routes playback through the one output meter instead of duplicating the destination', () => {
    const meter = {};
    const connect = vi.fn();
    const output = {
      currentTime: 1, destination: {},
      createBuffer: (_channels: number, length: number) => ({ duration: length / 24000, getChannelData: () => new Float32Array(length) }),
      createBufferSource: () => ({ connect, start: vi.fn(), buffer: null, onended: null }),
    };
    const current = { output, outputMeter: meter, playback: new Set(), nextTime: 0 };
    queueLivePlayback(current as never, btoa(String.fromCharCode(0, 0)), () => {});
    expect(connect).toHaveBeenCalledExactlyOnceWith(meter);
    expect(current.playback.size).toBe(1);
  });

  it('rejects silence and malformed energy, clamps loud input and responds to speech', () => {
    expect(voiceAudioLevel(new Float32Array(256))).toBe(0);
    expect(voiceAudioLevel(new Float32Array([NaN, Infinity]))).toBe(0);
    expect(voiceAudioLevel(new Float32Array(256).fill(1))).toBe(1);
    expect(voiceAudioLevel(new Float32Array(256).fill(0.1))).toBeGreaterThan(0.5);
  });
  it('attacks quickly and releases gently without inventing energy from silence', () => {
    expect(smoothVoiceLevel(0, 0)).toBe(0);
    expect(smoothVoiceLevel(0, 1)).toBeGreaterThan(0.6);
    expect(smoothVoiceLevel(1, 0)).toBeGreaterThan(0);
    expect(smoothVoiceLevel(1, 0)).toBeLessThan(1);
  });
});
