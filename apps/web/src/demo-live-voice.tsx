import { useEffect, useRef, useState } from 'react';
import { createDemoApi, DemoApiError } from './demo-api';
import type { DemoSession, OperatorTurn } from './demo-api';
import { AssistantOrb } from './demo-activity';
import { validatedResearchResult } from './demo-research';

const api = createDemoApi();
const CONNECT_TIMEOUT_MS = 15000;

export type LiveScope = { sessionId: string; companyId: string; contextVersion: number; epoch: number };
export const sameLiveScope = (a: LiveScope, b: LiveScope) => a.sessionId === b.sessionId && a.companyId === b.companyId && a.contextVersion === b.contextVersion && a.epoch === b.epoch;
export const matchesLiveSession = (session: DemoSession, scope: LiveScope, csrf: string) =>
  session.session_id === scope.sessionId && session.company?.company_id === scope.companyId &&
  session.context_version === scope.contextVersion && session.csrf_token === csrf;
export async function restoreLiveSession(read: () => Promise<DemoSession>, captured: LiveScope,
  current: () => LiveScope, onSession: (session: DemoSession) => void, onUnknown: () => void): Promise<void> {
  try {
    const session = await read();
    if (sameLiveScope(current(), captured)) onSession(session);
  } catch {
    if (sameLiveScope(current(), captured)) onUnknown();
  }
}
export function acceptsLiveTurn(value: unknown, captured: LiveScope, current: LiveScope): value is OperatorTurn {
  if (!value || typeof value !== 'object') return false;
  const turn = value as Partial<OperatorTurn>;
  const utility = !turn.proposal && !!turn.session && turn.session.session_id === captured.sessionId &&
    turn.session.company?.company_id === captured.companyId && turn.session.context_version === captured.contextVersion &&
    !!turn.action_result && typeof turn.action_result.tool === 'string' && typeof turn.action_result.summary === 'string';
  const validResearch = turn.action_result?.tool !== 'confirm_research' || !!validatedResearchResult(turn.workspace?.research);
  return validResearch && sameLiveScope(captured, current) && turn.company_id === captured.companyId && turn.context_version === captured.contextVersion && typeof turn.read_only === 'boolean' &&
    typeof turn.turn_id === 'string' && typeof turn.assistant_text === 'string' && Array.isArray(turn.agent_steps) && Array.isArray(turn.evidence_ids) &&
    ['completed', 'proposal', 'needs_clarification', 'unavailable'].includes(turn.status ?? '') &&
    (turn.read_only ? turn.proposal ? turn.proposal.context_version === captured.contextVersion && !turn.session && !turn.action_result :
      turn.session || turn.action_result ? utility : true : utility);
}
export function pcm16Base64(samples: Float32Array, sourceRate: number): string {
  const length = Math.floor(samples.length * 16000 / sourceRate);
  const bytes = new Uint8Array(length * 2);
  const view = new DataView(bytes.buffer);
  for (let i = 0; i < length; i++) {
    const sample = Math.max(-1, Math.min(1, samples[Math.floor(i * sourceRate / 16000)] ?? 0));
    view.setInt16(i * 2, sample < 0 ? sample * 32768 : sample * 32767, true);
  }
  let binary = '';
  for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
  return btoa(binary);
}
function pcm24Buffer(context: AudioContext, encoded: string): AudioBuffer {
  const binary = atob(encoded);
  const buffer = context.createBuffer(1, Math.floor(binary.length / 2), 24000);
  const output = buffer.getChannelData(0);
  for (let i = 0; i < output.length; i++) {
    const raw = binary.charCodeAt(i * 2) | (binary.charCodeAt(i * 2 + 1) << 8);
    output[i] = (raw & 0x8000 ? raw - 0x10000 : raw) / 32768;
  }
  return buffer;
}
type ActiveVoice = { socket: WebSocket; stream: MediaStream; input: AudioContext; output: AudioContext; source: MediaStreamAudioSourceNode; processor: ScriptProcessorNode; playback: Set<AudioBufferSourceNode>; nextTime: number; timer: ReturnType<typeof setTimeout>; startupTimer: ReturnType<typeof setTimeout>; scope: LiveScope; ready: boolean; muted: boolean };
type Caption = { direction: 'input' | 'output'; text: string; sealed?: true };
type VoiceVisualState = 'connecting' | 'listening' | 'thinking' | 'speaking' | 'muted';
const CAPTION_LIMIT = 24;
const CAPTION_TEXT_LIMIT = 16000;

export function appendLiveCaption(captions: Caption[], direction: Caption['direction'], fragment: string): Caption[] {
  if (!fragment.trim()) return captions;
  const text = fragment.replace(/\s+/g, ' ');
  const last = captions[captions.length - 1];
  if (last?.direction === direction && !last.sealed) {
    const separator = /\s$/.test(last.text) || /^\s|^[,.!?;:]/.test(text) ? '' : ' ';
    const joined = last.text + separator + text;
    if (joined.length <= CAPTION_TEXT_LIMIT) return [...captions.slice(0, -1), { direction, text: joined }];
  }
  return [...captions, { direction, text: text.slice(0, CAPTION_TEXT_LIMIT) }].slice(-CAPTION_LIMIT);
}

export function updateLiveCaptions(captions: Caption[], event:
  { type: 'transcript'; direction: Caption['direction']; text: string } |
  { type: 'turn_complete' | 'interrupted' }): Caption[] {
  if (event.type === 'transcript') {
    const last = captions[captions.length - 1];
    const current = last && last.direction !== event.direction && !last.sealed ? [...captions.slice(0, -1), { ...last, sealed: true as const }] : captions;
    return appendLiveCaption(current, event.direction, event.text);
  }
  const last = captions[captions.length - 1];
  return last && !last.sealed ? [...captions.slice(0, -1), { ...last, sealed: true }] : captions;
}

export function finalizedLiveCaptions(captions: Caption[], after: number): { direction: Caption['direction']; text: string }[] {
  return captions.slice(after).filter(caption => caption.sealed && caption.text.trim()).map(({ direction, text }) => ({ direction, text: text.trim() }));
}

export function visibleLiveCaptions(captions: Caption[], captured: LiveScope | null, current: LiveScope): Caption[] {
  return captured && sameLiveScope(captured, current) ? captions : [];
}

export function visibleLiveVoiceState(status: 'idle' | VoiceVisualState, error: string,
  captured: LiveScope | null, current: LiveScope): { status: 'idle' | VoiceVisualState; error: string } {
  return captured && sameLiveScope(captured, current) ? { status, error } : { status: 'idle', error: '' };
}

export function preflightLiveVoice(scope: LiveScope, available:
  { microphone: boolean; audioContext: boolean; webSocket: boolean }): { scope: LiveScope; error: string } {
  return { scope, error: available.microphone && available.audioContext && available.webSocket ? '' :
    'Live voice is unavailable in this browser. Use the text composer.' };
}

export function voiceVisualState(current: { ready: boolean; muted: boolean; playback: Set<unknown> }, thinking: boolean): VoiceVisualState {
  if (!current.ready) return 'connecting';
  if (current.playback.size) return 'speaking';
  if (thinking) return 'thinking';
  return current.muted ? 'muted' : 'listening';
}

export function voiceThinkingAfterEvent(previous: boolean,
  event: 'transcript' | 'tool_result' | 'audio' | 'turn_complete' | 'interrupted'): boolean {
  if (event === 'transcript' || event === 'tool_result') return true;
  if (event === 'audio' || event === 'turn_complete' || event === 'interrupted') return false;
  return previous;
}

export function queueLivePlayback(current: Pick<ActiveVoice, 'output' | 'playback' | 'nextTime'>,
  encoded: string, onState: () => void): void {
  const node = current.output.createBufferSource();
  node.buffer = pcm24Buffer(current.output, encoded);
  node.connect(current.output.destination);
  node.onended = () => { current.playback.delete(node); onState(); };
  const startTime = Math.max(current.output.currentTime, current.nextTime);
  node.start(startTime);
  current.nextTime = startTime + node.buffer.duration;
  current.playback.add(node);
  onState();
}
export function releaseLiveVoice(current: ActiveVoice): void {
  clearTimeout(current.timer); clearTimeout(current.startupTimer);
  current.processor.onaudioprocess = null;
  current.processor.disconnect(); current.source.disconnect();
  current.stream.getTracks().forEach(track => track.stop());
  current.playback.forEach(node => { try { node.stop(); } catch { /* Already stopped. */ } });
  current.playback.clear();
  void current.input.close(); void current.output.close();
  if (current.socket.readyState === WebSocket.OPEN) current.socket.send(JSON.stringify({ type: 'stop' }));
  current.socket.close();
}

export function LiveVoice({ scope, csrf, disabled, beforeStart, onTurn, onSession, onUnknown }: { scope: LiveScope; csrf: string; disabled: boolean; beforeStart?: () => Promise<DemoSession>; onTurn: (turn: OperatorTurn, scope: LiveScope) => void; onSession: (session: DemoSession) => void; onUnknown: () => void }) {
  const [status, setStatus] = useState<'idle' | VoiceVisualState>('idle');
  const [microphoneMuted, setMicrophoneMuted] = useState(false);
  const [error, setError] = useState('');
  const [captions, setCaptions] = useState<Caption[]>([]);
  const [captionScope, setCaptionScope] = useState<LiveScope | null>(null);
  const active = useRef<ActiveVoice | null>(null);
  const thinking = useRef(false);
  const generation = useRef(0);
  const scopeRef = useRef(scope);
  scopeRef.current = scope;
  const onTurnRef = useRef(onTurn);
  onTurnRef.current = onTurn;
  const pendingCaptions = useRef<Caption[]>([]);
  const historyWrites = useRef<Promise<void>>(Promise.resolve());

  function stopPlayback(current: ActiveVoice) {
    current.playback.forEach(node => { try { node.stop(); } catch { /* Already stopped. */ } });
    current.playback.clear();
    current.nextTime = current.output.currentTime;
  }
  function end() {
    generation.current += 1;
    const current = active.current;
    thinking.current = false;
    setMicrophoneMuted(false);
    if (!current) { setStatus('idle'); return; }
    active.current = null;
    releaseLiveVoice(current);
    setStatus('idle');
  }
  useEffect(() => () => { end(); setCaptions([]); setCaptionScope(null); setError(''); }, [scope.sessionId, scope.companyId, scope.contextVersion, scope.epoch]);
  useEffect(() => { if (disabled) end(); }, [disabled]);

  async function start() {
    if (disabled || active.current) return;
    const preflight = preflightLiveVoice(scope, {
      microphone: typeof navigator !== 'undefined' && !!navigator.mediaDevices?.getUserMedia,
      audioContext: typeof AudioContext !== 'undefined', webSocket: typeof WebSocket !== 'undefined',
    });
    setCaptionScope(preflight.scope);
    if (preflight.error) { setError(preflight.error); setStatus('idle'); return; }
    const captured = scope;
    const attempt = ++generation.current;
    thinking.current = false;
    setMicrophoneMuted(false);
    setError(''); setCaptions([]); setStatus('connecting');
    pendingCaptions.current = [];
    let stream: MediaStream | null = null;
    let input: AudioContext | null = null;
    let output: AudioContext | null = null;
    let socket: WebSocket | null = null;
    try {
      const synchronized = beforeStart ? await beforeStart() : null;
      if (attempt !== generation.current || !sameLiveScope(scopeRef.current, captured)) return;
      const canonical = await api.session();
      if (attempt !== generation.current || !sameLiveScope(scopeRef.current, captured)) return;
      if (!matchesLiveSession(canonical, captured, synchronized?.csrf_token ?? csrf)) {
        onSession(canonical);
        setError('The demo session changed or expired. The current workspace has been restored; review it, then start voice again.');
        setStatus('idle');
        return;
      }
      stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
      if (attempt !== generation.current || !sameLiveScope(scopeRef.current, captured)) { stream.getTracks().forEach(track => track.stop()); return; }
      input = new AudioContext({ sampleRate: 16000 });
      output = new AudioContext({ sampleRate: 24000 });
      await Promise.all([input.resume(), output.resume()]);
      if (attempt !== generation.current || !sameLiveScope(scopeRef.current, captured)) { stream.getTracks().forEach(track => track.stop()); void input.close(); void output.close(); return; }
      const source = input.createMediaStreamSource(stream);
      const processor = input.createScriptProcessor(2048, 1, 1);
      source.connect(processor); processor.connect(input.destination);
      const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
      socket = new WebSocket(`${scheme}//${location.host}/api/demo/voice/live`);
      const current: ActiveVoice = { socket, stream, input, output, source, processor, playback: new Set(), nextTime: 0,
        timer: setTimeout(() => { setError('This voice conversation reached its three-minute limit. Start another conversation.'); end(); }, 180000),
        startupTimer: setTimeout(() => { setError('The live voice service did not become ready. Try starting again.'); end(); }, CONNECT_TIMEOUT_MS),
        scope: captured, ready: false, muted: false };
      active.current = current;
      let pending: number[] = [];
      processor.onaudioprocess = event => {
        if (active.current !== current || !current.ready || current.muted || current.socket.readyState !== WebSocket.OPEN) return;
        pending.push(...event.inputBuffer.getChannelData(0));
        const count = Math.round(input!.sampleRate / 10);
        while (pending.length >= count) {
          const chunk = pending.splice(0, count);
          current.socket.send(JSON.stringify({ type: 'audio', data: pcm16Base64(new Float32Array(chunk), input!.sampleRate) }));
        }
      };
      socket.onopen = () => { if (active.current === current) socket!.send(JSON.stringify({ type: 'start', csrf_token: canonical.csrf_token, expected_context_version: captured.contextVersion })); };
      socket.onmessage = event => {
        if (active.current !== current || !sameLiveScope(scopeRef.current, captured)) return;
        let data: Record<string, unknown>;
        try { data = JSON.parse(String(event.data)); } catch { setError('Live voice returned an unreadable event.'); end(); return; }
        if (data.type === 'ready') { clearTimeout(current.startupTimer); current.ready = true; setStatus(voiceVisualState(current, thinking.current)); }
        else if (data.type === 'audio' && typeof data.data === 'string' && data.sample_rate_hertz === 24000) {
          try {
            thinking.current = voiceThinkingAfterEvent(thinking.current, 'audio');
            queueLivePlayback(current, data.data, () => {
              if (active.current === current && sameLiveScope(scopeRef.current, captured)) setStatus(voiceVisualState(current, thinking.current));
            });
          } catch { setError('Received audio could not be played.'); }
        } else if (data.type === 'interrupted') {
          stopPlayback(current);
          setCaptions(old => updateLiveCaptions(old, { type: 'interrupted' }));
          pendingCaptions.current = [];
          thinking.current = voiceThinkingAfterEvent(thinking.current, 'interrupted');
          setStatus(voiceVisualState(current, thinking.current));
        }
        else if (data.type === 'transcript' && (data.direction === 'input' || data.direction === 'output') && typeof data.text === 'string') {
          pendingCaptions.current = updateLiveCaptions(pendingCaptions.current, { type: 'transcript', direction: data.direction, text: data.text });
          setCaptions(old => updateLiveCaptions(old, { type: 'transcript', direction: data.direction as Caption['direction'], text: data.text as string }));
          thinking.current = voiceThinkingAfterEvent(thinking.current, 'transcript');
          setStatus(voiceVisualState(current, thinking.current));
        } else if (data.type === 'turn_complete') {
          const complete = finalizedLiveCaptions(updateLiveCaptions(pendingCaptions.current, { type: 'turn_complete' }), 0);
          pendingCaptions.current = [];
          for (const caption of complete) {
            historyWrites.current = historyWrites.current.then(async () => {
              await api.appendOperatorVoice(canonical.csrf_token, caption.text, caption.direction, captured.contextVersion);
            }).catch(cause => {
              if (active.current === current && sameLiveScope(scopeRef.current, captured)) setError(`Voice history could not be saved: ${cause instanceof Error ? cause.message : 'Unknown error'}`);
            });
          }
          setCaptions(old => updateLiveCaptions(old, { type: 'turn_complete' }));
          thinking.current = voiceThinkingAfterEvent(thinking.current, 'turn_complete');
          setStatus(voiceVisualState(current, thinking.current));
        }
        else if (data.type === 'tool_result' && acceptsLiveTurn(data.result, captured, scopeRef.current)) {
          thinking.current = voiceThinkingAfterEvent(thinking.current, 'tool_result');
          setStatus(voiceVisualState(current, thinking.current));
          onTurnRef.current(data.result, captured);
        }
        else if (data.type === 'error') {
          if (data.code === 'session_expired' || data.code === 'stale_context') {
            void restoreLiveSession(() => api.session(), captured, () => scopeRef.current, onSession, onUnknown);
          }
          setError(typeof data.message === 'string' ? data.message : 'Live voice is unavailable.'); end();
        }
      };
      socket.onerror = () => { if (active.current === current) { setError('The live voice connection failed. Check your connection, refresh the demo session, then try again.'); end(); } };
      socket.onclose = () => { if (active.current === current) { setError(current.ready ? 'The live voice connection ended. Start another conversation.' : 'The live voice connection closed before it became ready. Refresh the demo session, then try again.'); end(); } };
    } catch (cause) {
      stream?.getTracks().forEach(track => track.stop());
      if (socket) socket.close();
      if (input) void input.close(); if (output) void output.close();
      if (attempt === generation.current && sameLiveScope(scopeRef.current, captured)) {
        setStatus('idle');
        if (!stream) {
          if (cause instanceof Error && cause.name === 'NotAllowedError') setError('Microphone access was denied. Allow microphone access for this site, then try again.');
          else if (cause instanceof Error && cause.name === 'NotFoundError') setError('No microphone was found. Connect one, then try again.');
          else if (cause instanceof DemoApiError && (cause.code === 'session_expired' || [401, 403, 410].includes(cause.status))) { onUnknown(); setError('The demo session could not be checked. Refresh the session, then start voice again.'); }
          else if (cause instanceof DemoApiError) setError(cause.message);
          else setError('The microphone or demo session could not be opened. Check your device and connection, then try again.');
        } else setError('The microphone audio connection could not start. Try again.');
      }
    }
  }
  function mute() {
    const current = active.current;
    if (!current) return;
    current.muted = !current.muted;
    if (current.muted && current.socket.readyState === WebSocket.OPEN) current.socket.send(JSON.stringify({ type: 'audio_end' }));
    setMicrophoneMuted(current.muted);
    setStatus(voiceVisualState(current, thinking.current));
  }
  const visible = visibleLiveVoiceState(status, error, captionScope, scope);
  const orbState = visible.error ? 'error' : visible.status;
  const statusLabel = visible.status === 'connecting' ? 'Connecting live voice…' :
    visible.status === 'listening' ? 'Listening' : visible.status === 'thinking' ? 'Thinking' :
    visible.status === 'speaking' ? microphoneMuted ? 'Speaking · microphone muted' : 'Speaking' :
    visible.status === 'muted' ? 'Microphone muted' : 'Voice is off. Text chat is available.';
  const shownCaptions = visibleLiveCaptions(captions, captionScope, scope);
  return <div className="demo-operator-voice">
    <strong>Live conversation · Google speech</strong>
    <p>Talk naturally and interrupt by speaking. For web research, hear the public query, then say “confirmed” or “confirm research”. Business changes still need review and Apply.</p>
    <div className="demo-voice-orb"><AssistantOrb state={orbState} size={64} theme="dark" /></div>
    <div className="demo-voice-state" role="status" aria-live="polite" aria-atomic="true"><strong>{statusLabel}</strong></div>
    <div className="demo-voice-controls"><button type="button" className="demo-button" disabled={disabled || visible.status !== 'idle'} onClick={start}>Start conversation</button><button type="button" className="demo-link" disabled={visible.status === 'idle'} onClick={mute}>{microphoneMuted ? 'Unmute' : 'Mute microphone'}</button><button type="button" className="demo-link" disabled={visible.status === 'idle'} onClick={end}>End conversation</button></div>
    {visible.error && <p className="demo-alert" role="alert">{visible.error}</p>}
    {shownCaptions.length > 0 && <ol className="demo-live-captions" aria-label="Live transcript">{shownCaptions.map((caption, index) => <li className={`demo-caption ${caption.direction === 'input' ? 'is-user' : 'is-assistant'}`} key={index}><strong>{caption.direction === 'input' ? 'You' : 'Operator'}:</strong> {caption.text}</li>)}</ol>}
  </div>;
}
