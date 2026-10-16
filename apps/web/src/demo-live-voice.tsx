import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { createDemoApi, DemoApiError } from './demo-api';
import type { DemoSession, OperatorTurn } from './demo-api';
import { ZoorqLens } from './zoorq-lens';
import { validatedResearchResult } from './demo-research';
import { isCurrentSavedDraft } from './demo-operator-contract';

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
  const utility = (!turn.proposal || isCurrentSavedDraft(turn, captured)) && !!turn.session && turn.session.session_id === captured.sessionId &&
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
export function voiceAudioLevel(samples: Float32Array): number {
  if (!samples.length) return 0;
  let energy = 0;
  for (let i = 0; i < samples.length; i++) {
    const sample = Number.isFinite(samples[i]) ? samples[i] : 0;
    energy += sample * sample;
  }
  return Math.min(1, Math.max(0, (Math.sqrt(energy / samples.length) - 0.008) * 7));
}
export function smoothVoiceLevel(previous: number, next: number): number {
  const from = Number.isFinite(previous) ? Math.min(1, Math.max(0, previous)) : 0;
  const to = Number.isFinite(next) ? Math.min(1, Math.max(0, next)) : 0;
  return from + (to - from) * (to > from ? 0.72 : 0.32);
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
export type VoiceResearchPhase = 'awaiting_consent' | 'checking_consent' | 'started' | 'completed' | 'unavailable';
const RESEARCH_PHASES = new Set<VoiceResearchPhase>(['awaiting_consent', 'checking_consent', 'started', 'completed', 'unavailable']);
export function acceptedResearchProgress(event: Record<string, unknown>, captured: LiveScope, current: LiveScope,
  proposalId: string | null, currentPhase?: VoiceResearchPhase | null): VoiceResearchPhase | null {
  if (!sameLiveScope(captured, current) || event.type !== 'research_progress' || event.company_id !== captured.companyId ||
      event.context_version !== captured.contextVersion || !proposalId || event.proposal_id !== proposalId ||
      typeof event.phase !== 'string' || !RESEARCH_PHASES.has(event.phase as VoiceResearchPhase)) return null;
  const phase = event.phase as VoiceResearchPhase;
  if ((currentPhase === 'completed' || currentPhase === 'unavailable') && phase !== currentPhase) return null;
  if (currentPhase === 'started' && (phase === 'awaiting_consent' || phase === 'checking_consent')) return null;
  return phase;
}
export function settleLiveResearch(current: { responsePending: boolean; researchPhase?: VoiceResearchPhase | null }, result: OperatorTurn): boolean {
  if (result.action_result?.tool !== 'confirm_research') return false;
  const research = validatedResearchResult(result.workspace?.research);
  if (!research) return false;
  current.responsePending = false;
  current.researchPhase = research.status;
  return true;
}
export function voiceResearchLabel(phase: VoiceResearchPhase | null | undefined): string | null {
  return phase === 'checking_consent' ? 'Checking your confirmation…' : phase === 'started' ? 'Researching the public web…' :
    phase === 'awaiting_consent' ? 'Ready for “confirm research”' : phase === 'completed' ? 'Research complete' :
    phase === 'unavailable' ? 'Research unavailable' : null;
}

type ActiveVoice = { socket: WebSocket; stream: MediaStream; input: AudioContext; output: AudioContext; source: MediaStreamAudioSourceNode; processor: ScriptProcessorNode; playback: Set<AudioBufferSourceNode>; nextTime: number; timer: ReturnType<typeof setTimeout>; startupTimer: ReturnType<typeof setTimeout>; scope: LiveScope; ready: boolean; muted: boolean; responsePending: boolean; researchPhase?: VoiceResearchPhase | null; researchProposalId?: string | null; outputMeter?: AnalyserNode; inputLevel?: number };
type Caption = { direction: 'input' | 'output'; text: string; sealed?: true };
type VoiceVisualState = 'connecting' | 'listening' | 'thinking' | 'speaking' | 'muted';
export function liveVoiceStatusLabel(status: 'idle' | VoiceVisualState, permissionPending: boolean): string | null {
  if (status !== 'connecting') return null;
  return permissionPending ? 'Allow microphone access in Chrome to start voice. You can end this request.' : 'Connecting live voice…';
}
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

export function voiceVisualState(current: { ready: boolean; muted: boolean; playback: Set<unknown>; responsePending?: boolean; researchPhase?: VoiceResearchPhase | null }, thinking: boolean): VoiceVisualState {
  if (!current.ready) return 'connecting';
  if (current.playback.size) return 'speaking';
  if (thinking || current.responsePending || current.researchPhase === 'checking_consent' || current.researchPhase === 'started') return 'thinking';
  return current.muted ? 'muted' : 'listening';
}

export function voiceResponsePendingAfterEvent(previous: boolean,
  event: 'input_transcript' | 'output_transcript' | 'tool_result' | 'audio' | 'turn_complete' | 'interrupted'): boolean {
  return event === 'turn_complete' || event === 'interrupted' ? false : true;
}

export function voiceThinkingAfterEvent(previous: boolean,
  event: 'transcript' | 'tool_result' | 'audio' | 'turn_complete' | 'interrupted'): boolean {
  if (event === 'transcript' || event === 'tool_result') return true;
  if (event === 'audio' || event === 'turn_complete' || event === 'interrupted') return false;
  return previous;
}

export function queueLivePlayback(current: Pick<ActiveVoice, 'output' | 'playback' | 'nextTime' | 'outputMeter'>,
  encoded: string, onState: () => void): void {
  const node = current.output.createBufferSource();
  node.buffer = pcm24Buffer(current.output, encoded);
  node.connect(current.outputMeter ?? current.output.destination);
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
  current.outputMeter?.disconnect();
  current.stream.getTracks().forEach(track => track.stop());
  current.playback.forEach(node => { try { node.stop(); } catch { /* Already stopped. */ } });
  current.playback.clear();
  void current.input.close(); void current.output.close();
  if (current.socket.readyState === WebSocket.OPEN) current.socket.send(JSON.stringify({ type: 'stop' }));
  current.socket.close();
}

export function endActiveVoice(active: { current: ActiveVoice | null }): boolean {
  const current = active.current;
  if (!current) return false;
  active.current = null;
  releaseLiveVoice(current);
  return true;
}

export type LiveVoiceHandle = { start: () => void; end: () => void };
export function claimLiveVoiceStart(starting: { current: boolean }, active: boolean, disabled: boolean): boolean {
  if (starting.current || active || disabled) return false;
  starting.current = true;
  return true;
}
type LiveVoiceProps = { scope: LiveScope; csrf: string; disabled: boolean; beforeStart?: () => Promise<DemoSession>; onTurn: (turn: OperatorTurn, scope: LiveScope) => void; onSession: (session: DemoSession) => void; onUnknown: () => void; onExit?: () => void };
export const LiveVoice = forwardRef<LiveVoiceHandle, LiveVoiceProps>(function LiveVoice({ scope, csrf, disabled, beforeStart, onTurn, onSession, onUnknown, onExit }, ref) {
  const [status, setStatus] = useState<'idle' | VoiceVisualState>('idle');
  const [permissionPending, setPermissionPending] = useState(false);
  const [microphoneMuted, setMicrophoneMuted] = useState(false);
  const [audioLevel, setAudioLevel] = useState(0);
  const [researchPhase, setResearchPhase] = useState<VoiceResearchPhase | null>(null);
  const [error, setError] = useState('');
  const [captions, setCaptions] = useState<Caption[]>([]);
  const [captionScope, setCaptionScope] = useState<LiveScope | null>(null);
  const active = useRef<ActiveVoice | null>(null);
  const starting = useRef(false);
  const thinking = useRef(false);
  const generation = useRef(0);
  const permissionTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const scopeRef = useRef(scope);
  scopeRef.current = scope;
  const onTurnRef = useRef(onTurn);
  onTurnRef.current = onTurn;
  const pendingCaptions = useRef<Caption[]>([]);
  const historyWrites = useRef<Promise<void>>(Promise.resolve());

  useEffect(() => {
    const samples = new Float32Array(256);
    const timer = window.setInterval(() => {
      const current = active.current;
      if (!current || !sameLiveScope(current.scope, scopeRef.current) || document.hidden) return;
      const visual = voiceVisualState(current, thinking.current);
      let level = visual === 'listening' ? current.inputLevel ?? 0 : 0;
      if (visual === 'speaking' && current.outputMeter) {
        current.outputMeter.getFloatTimeDomainData(samples);
        level = voiceAudioLevel(samples);
      }
      setAudioLevel(old => {
        const next = smoothVoiceLevel(old, level);
        return Math.abs(next - old) > 0.008 ? next : old;
      });
    }, 100);
    return () => window.clearInterval(timer);
  }, []);

  function stopPlayback(current: ActiveVoice) {
    current.playback.forEach(node => { try { node.stop(); } catch { /* Already stopped. */ } });
    current.playback.clear();
    current.nextTime = current.output.currentTime;
  }
  function end() {
    if (permissionTimer.current) { clearTimeout(permissionTimer.current); permissionTimer.current = null; }
    setPermissionPending(false);
    setAudioLevel(0);
    generation.current += 1;
    starting.current = false;
    thinking.current = false;
    setMicrophoneMuted(false);
    setResearchPhase(null);
    endActiveVoice(active);
    setStatus('idle');
  }
  useImperativeHandle(ref, () => ({ start: () => { void start(); }, end }));
  useEffect(() => () => { end(); setCaptions([]); setCaptionScope(null); setError(''); }, [scope.sessionId, scope.companyId, scope.contextVersion, scope.epoch]);
  useEffect(() => { if (disabled) end(); }, [disabled]);

  async function start() {
    if (!claimLiveVoiceStart(starting, !!active.current, disabled)) return;
    const preflight = preflightLiveVoice(scope, {
      microphone: typeof navigator !== 'undefined' && !!navigator.mediaDevices?.getUserMedia,
      audioContext: typeof AudioContext !== 'undefined', webSocket: typeof WebSocket !== 'undefined',
    });
    setCaptionScope(preflight.scope);
    if (preflight.error) { starting.current = false; setError(preflight.error); setStatus('idle'); return; }
    const captured = scope;
    const attempt = ++generation.current;
    thinking.current = false;
    setMicrophoneMuted(false);
    setResearchPhase(null);
    setError(''); setCaptions([]); setStatus('connecting');
    pendingCaptions.current = [];
    let stream: MediaStream | null = null;
    let input: AudioContext | null = null;
    let output: AudioContext | null = null;
    let socket: WebSocket | null = null;
    let requestTimer: ReturnType<typeof setTimeout> | null = null;
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
      setPermissionPending(true);
      requestTimer = setTimeout(() => { if (attempt !== generation.current) return; setError('Microphone permission is still pending. Allow access in Chrome, then start again.'); end(); }, 30000);
      permissionTimer.current = requestTimer;
      stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
      if (permissionTimer.current === requestTimer) { clearTimeout(requestTimer); permissionTimer.current = null; }
      if (attempt === generation.current) setPermissionPending(false);
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
        scope: captured, ready: false, muted: false, responsePending: false };
      try {
        const outputMeter = output.createAnalyser();
        outputMeter.fftSize = 256;
        outputMeter.connect(output.destination);
        current.outputMeter = outputMeter;
      } catch { /* Metering is optional; playback can use the existing destination. */ }
      active.current = current;
      let pending: number[] = [];
      processor.onaudioprocess = event => {
        if (active.current !== current || !sameLiveScope(scopeRef.current, captured) || !current.ready || current.socket.readyState !== WebSocket.OPEN) return;
        const samples = event.inputBuffer.getChannelData(0);
        current.inputLevel = current.muted ? 0 : voiceAudioLevel(samples);
        if (current.muted) return;
        pending.push(...samples);
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
        else if (data.type === 'research_progress') {
          const phase = acceptedResearchProgress(data, captured, scopeRef.current, current.researchProposalId ?? null, current.researchPhase);
          if (phase) {
            current.researchPhase = phase; setResearchPhase(phase);
            if (phase === 'completed' || phase === 'unavailable') { current.responsePending = false; thinking.current = false; }
            setStatus(voiceVisualState(current, thinking.current));
          }
        }
        else if (data.type === 'audio' && typeof data.data === 'string' && data.sample_rate_hertz === 24000) {
          try {
            thinking.current = voiceThinkingAfterEvent(thinking.current, 'audio');
            current.responsePending = voiceResponsePendingAfterEvent(current.responsePending, 'audio');
            queueLivePlayback(current, data.data, () => {
              if (active.current === current && sameLiveScope(scopeRef.current, captured)) setStatus(voiceVisualState(current, thinking.current));
            });
          } catch { setError('Received audio could not be played.'); }
        } else if (data.type === 'interrupted') {
          stopPlayback(current);
          if (current.researchPhase === 'checking_consent' || current.researchPhase === 'awaiting_consent') { current.researchPhase = null; setResearchPhase(null); }
          current.responsePending = voiceResponsePendingAfterEvent(current.responsePending, 'interrupted');
          setCaptions(old => updateLiveCaptions(old, { type: 'interrupted' }));
          pendingCaptions.current = [];
          thinking.current = voiceThinkingAfterEvent(thinking.current, 'interrupted');
          setStatus(voiceVisualState(current, thinking.current));
        }
        else if (data.type === 'transcript' && (data.direction === 'input' || data.direction === 'output') && typeof data.text === 'string') {
          if (data.direction === 'input' && (current.researchPhase === 'completed' || current.researchPhase === 'unavailable')) { current.researchPhase = null; current.researchProposalId = null; setResearchPhase(null); }
          pendingCaptions.current = updateLiveCaptions(pendingCaptions.current, { type: 'transcript', direction: data.direction, text: data.text });
          setCaptions(old => updateLiveCaptions(old, { type: 'transcript', direction: data.direction as Caption['direction'], text: data.text as string }));
          thinking.current = voiceThinkingAfterEvent(thinking.current, 'transcript');
          current.responsePending = voiceResponsePendingAfterEvent(current.responsePending, data.direction === 'input' ? 'input_transcript' : 'output_transcript');
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
          current.responsePending = voiceResponsePendingAfterEvent(current.responsePending, 'turn_complete');
          setStatus(voiceVisualState(current, thinking.current));
        }
        else if (data.type === 'tool_result' && acceptsLiveTurn(data.result, captured, scopeRef.current)) {
          if (data.result.proposal?.kind === 'research') {
            if (current.researchProposalId !== data.result.proposal.proposal_id) { current.researchPhase = null; setResearchPhase(null); }
            current.researchProposalId = data.result.proposal.proposal_id;
          }
          if (settleLiveResearch(current, data.result)) {
            thinking.current = false;
            setResearchPhase(current.researchPhase ?? null);
          } else {
            thinking.current = voiceThinkingAfterEvent(thinking.current, 'tool_result');
            current.responsePending = voiceResponsePendingAfterEvent(current.responsePending, 'tool_result');
          }
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
      if (requestTimer && permissionTimer.current === requestTimer) { clearTimeout(requestTimer); permissionTimer.current = null; }
      if (attempt === generation.current) setPermissionPending(false);
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
    } finally {
      if (attempt === generation.current) starting.current = false;
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
  const researchLabel = captionScope && sameLiveScope(captionScope, scope) ? voiceResearchLabel(researchPhase) : null;
  const statusLabel = researchLabel && visible.status !== 'idle' && visible.status !== 'connecting' && visible.status !== 'speaking' && visible.status !== 'muted' ? researchLabel : visible.status === 'connecting' ? liveVoiceStatusLabel(visible.status, permissionPending) :
    visible.status === 'listening' ? 'Listening' : visible.status === 'thinking' ? active.current?.responsePending && !thinking.current ? 'Finishing response…' : 'Thinking' :
    visible.status === 'speaking' ? microphoneMuted ? 'Speaking · microphone muted' : 'Speaking' :
    visible.status === 'muted' ? 'Microphone muted' : 'Voice is off. Text chat is available.';
  const shownCaptions = visibleLiveCaptions(captions, captionScope, scope);
  return <div className="demo-operator-voice">
    <p>Speak naturally. You can interrupt an answer by speaking.</p>
    <div className="demo-voice-orb"><ZoorqLens state={orbState} level={visible.status === 'listening' || visible.status === 'speaking' ? audioLevel : 0} /></div>
    <div className="demo-voice-state" role="status" aria-live="polite" aria-atomic="true"><strong>{statusLabel}</strong></div>
    <div className="demo-voice-controls">{visible.status === 'idle' && <button type="button" className="demo-button" disabled={disabled} onClick={start}>Start conversation</button>}<button type="button" className="demo-link" disabled={visible.status === 'idle'} onClick={mute}>{microphoneMuted ? 'Unmute' : 'Mute microphone'}</button><button type="button" className="demo-link" onClick={() => { end(); onExit?.(); }}>End conversation</button></div>
    {visible.error && <p className="demo-alert" role="alert">{visible.error}</p>}
    {shownCaptions.length > 0 && <ol className="demo-live-captions" aria-label="Live transcript">{shownCaptions.map((caption, index) => <li className={`demo-caption ${caption.direction === 'input' ? 'is-user' : 'is-assistant'}`} key={index}><strong>{caption.direction === 'input' ? 'You' : 'Operator'}:</strong> {caption.text}</li>)}</ol>}
  </div>;
});
