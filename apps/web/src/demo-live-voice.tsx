import { useEffect, useRef, useState } from 'react';
import { createDemoApi } from './demo-api';
import type { DemoSession, OperatorTurn } from './demo-api';

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
  return sameLiveScope(captured, current) && turn.company_id === captured.companyId && turn.context_version === captured.contextVersion && turn.read_only === true &&
    typeof turn.turn_id === 'string' && typeof turn.assistant_text === 'string' && Array.isArray(turn.agent_steps) && Array.isArray(turn.evidence_ids) &&
    ['completed', 'proposal', 'needs_clarification', 'unavailable'].includes(turn.status ?? '') &&
    (!turn.proposal || turn.proposal.context_version === captured.contextVersion);
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

export function LiveVoice({ scope, csrf, disabled, onTurn, onSession, onUnknown }: { scope: LiveScope; csrf: string; disabled: boolean; onTurn: (turn: OperatorTurn, scope: LiveScope) => void; onSession: (session: DemoSession) => void; onUnknown: () => void }) {
  const [status, setStatus] = useState<'idle' | 'connecting' | 'listening' | 'muted'>('idle');
  const [error, setError] = useState('');
  const [captions, setCaptions] = useState<{ direction: string; text: string }[]>([]);
  const active = useRef<ActiveVoice | null>(null);
  const generation = useRef(0);
  const scopeRef = useRef(scope);
  scopeRef.current = scope;
  const onTurnRef = useRef(onTurn);
  onTurnRef.current = onTurn;

  function stopPlayback(current: ActiveVoice) {
    current.playback.forEach(node => { try { node.stop(); } catch { /* Already stopped. */ } });
    current.playback.clear();
    current.nextTime = current.output.currentTime;
  }
  function end() {
    generation.current += 1;
    const current = active.current;
    if (!current) { setStatus('idle'); return; }
    active.current = null;
    releaseLiveVoice(current);
    setStatus('idle');
  }
  useEffect(() => () => { generation.current += 1; end(); }, [scope.sessionId, scope.companyId, scope.contextVersion, scope.epoch]);
  useEffect(() => { if (disabled) end(); }, [disabled]);

  async function start() {
    if (disabled || active.current) return;
    if (!navigator.mediaDevices?.getUserMedia || typeof AudioContext === 'undefined' || typeof WebSocket === 'undefined') { setError('Live voice is unavailable in this browser. Use the text composer.'); return; }
    const captured = scope;
    const attempt = ++generation.current;
    setError(''); setCaptions([]); setStatus('connecting');
    let stream: MediaStream | null = null;
    let input: AudioContext | null = null;
    let output: AudioContext | null = null;
    let socket: WebSocket | null = null;
    try {
      const canonical = await api.session();
      if (attempt !== generation.current || !sameLiveScope(scopeRef.current, captured)) return;
      if (!matchesLiveSession(canonical, captured, csrf)) {
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
      socket.onopen = () => { if (active.current === current) socket!.send(JSON.stringify({ type: 'start', csrf_token: csrf, expected_context_version: captured.contextVersion })); };
      socket.onmessage = event => {
        if (active.current !== current || !sameLiveScope(scopeRef.current, captured)) return;
        let data: Record<string, unknown>;
        try { data = JSON.parse(String(event.data)); } catch { setError('Live voice returned an unreadable event.'); end(); return; }
        if (data.type === 'ready') { clearTimeout(current.startupTimer); current.ready = true; setStatus(current.muted ? 'muted' : 'listening'); }
        else if (data.type === 'audio' && typeof data.data === 'string' && data.sample_rate_hertz === 24000) {
          try {
            const node = current.output.createBufferSource(); node.buffer = pcm24Buffer(current.output, data.data); node.connect(current.output.destination);
            node.onended = () => current.playback.delete(node);
            node.start(Math.max(current.output.currentTime, current.nextTime));
            current.nextTime = Math.max(current.output.currentTime, current.nextTime) + node.buffer.duration;
            current.playback.add(node);
          } catch { setError('Received audio could not be played.'); }
        } else if (data.type === 'interrupted') stopPlayback(current);
        else if (data.type === 'transcript' && (data.direction === 'input' || data.direction === 'output') && typeof data.text === 'string') setCaptions(old => [...old.slice(-19), { direction: data.direction as string, text: data.text as string }]);
        else if (data.type === 'tool_result' && acceptsLiveTurn(data.result, captured, scopeRef.current)) onTurnRef.current(data.result, captured);
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
          else if (cause instanceof Error && cause.name === 'DemoApiError') { onUnknown(); setError('The demo session could not be checked. Refresh the session, then start voice again.'); }
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
    setStatus(current.muted ? 'muted' : 'listening');
  }
  return <div className="demo-operator-voice"><strong>Live conversation · Google speech</strong><p>Start a hands-free conversation. Speech pauses automatically; interrupt by speaking. Proposals still require review and Apply.</p><div><button type="button" className="demo-button" disabled={disabled || status !== 'idle'} onClick={start}>Start conversation</button><button type="button" className="demo-link" disabled={status === 'idle'} onClick={mute}>{status === 'muted' ? 'Unmute' : 'Mute microphone'}</button><button type="button" className="demo-link" disabled={status === 'idle'} onClick={end}>End conversation</button></div><p role="status">{status === 'connecting' ? 'Connecting live voice…' : status === 'listening' ? 'Listening and responding' : status === 'muted' ? 'Microphone muted' : 'Voice is off. Text chat is available.'}</p>{error && <p className="demo-alert" role="alert">{error}</p>}{captions.length > 0 && <ol className="demo-live-captions" aria-label="Live transcript">{captions.map((caption, index) => <li key={index}><strong>{caption.direction === 'input' ? 'You' : 'Operator'}:</strong> {caption.text}</li>)}</ol>}</div>;
}
