import { useEffect, useLayoutEffect, useReducer, useRef, useState } from 'react';
import type { FormEvent, KeyboardEvent as ReactKeyboardEvent, ReactNode, Ref } from 'react';
import { createDemoApi, DemoApiError } from './demo-api';
import type { DemoSession, NewsView, OfferView, OperatorApplied, OperatorHistory, OperatorOverview, OperatorTurn, PlanView, PreparedDraft, RecoveryView, ResearchResult } from './demo-api';
import { LiveVoice } from './demo-live-voice';
import type { LiveVoiceHandle } from './demo-live-voice';
import { ActivityIndicator } from './demo-activity';
import { displaySupplierMentions, roundDecimalText, supplierDisplayName } from './demo-display';
import { ResearchOutcome, validatedResearchResult } from './demo-research';
import { MarkdownAnswer } from './demo-markdown';
import './demo-assistant.css';

export type OperatorScope = { sessionId: string; companyId: string; contextVersion: number; epoch: number };
export type OperatorSeed = { id: number; sessionId: string; companyId: string; contextVersion: number; epoch: number; message: string };
export function operatorSeedMessage(seed: OperatorSeed | null | undefined, scope: OperatorScope): string | null {
  const message = seed?.message;
  return seed?.sessionId === scope.sessionId && seed.companyId === scope.companyId && seed.contextVersion === scope.contextVersion &&
    seed.epoch === scope.epoch && message?.trim() && message.length <= 1200 ? message : null;
}
export function focusValidOperatorSeed(seed: OperatorSeed | null | undefined, scope: OperatorScope, focus: () => void): string | null {
  const message = operatorSeedMessage(seed, scope);
  if (message) focus();
  return message;
}
export function shouldFollowOperatorHistory(view: { scrollTop: number; scrollHeight: number; clientHeight: number }): boolean {
  return view.scrollHeight - view.clientHeight - view.scrollTop <= 72;
}
type OperatorExchange = { id: number | string; question: string | null; turn?: OperatorTurn; error?: string; channel?: 'text' | 'voice'; answer?: string; speaker?: 'input' | 'output'; research?: ResearchResult; created_at?: string };
const MAX_EXCHANGES = 20;
export type OperatorState = { scope: OperatorScope; requestId: number; overview: OperatorOverview | null; turn: OperatorTurn | null; turns: OperatorTurn[]; exchanges: OperatorExchange[]; pendingQuestion: string | null; reviewed: boolean; outcome: OperatorApplied | null };
export const initialOperatorState: OperatorState = { scope: { sessionId: '', companyId: '', contextVersion: -1, epoch: -1 }, requestId: 0, overview: null, turn: null, turns: [], exchanges: [], pendingQuestion: null, reviewed: false, outcome: null };
type OperatorAction = { type: 'scope'; scope: OperatorScope } | { type: 'start'; scope: OperatorScope; requestId: number; question?: string } |
  { type: 'overview'; scope: OperatorScope; overview: OperatorOverview } | { type: 'turn'; scope: OperatorScope; requestId: number; turn: OperatorTurn } |
  { type: 'failed'; scope: OperatorScope; requestId: number; error: string } |
  { type: 'review'; value: boolean } | { type: 'cancel' } | { type: 'applied'; scope: OperatorScope; outcome: OperatorApplied } |
  { type: 'history'; scope: OperatorScope; history: OperatorHistory };
const sameScope = (a: OperatorScope, b: OperatorScope) => a.sessionId === b.sessionId && a.companyId === b.companyId && a.contextVersion === b.contextVersion && a.epoch === b.epoch;
const sameConversation = (a: OperatorScope, b: OperatorScope) => a.sessionId === b.sessionId && a.companyId === b.companyId && a.contextVersion === b.contextVersion;
export function acceptLiveOperatorRequest(current: { generation: number; requestId: number; busy: string }) {
  return { generation: current.generation + 1, requestId: current.requestId + 1, busy: '' };
}
const matches = (scope: OperatorScope, companyId: string, contextVersion: number) => scope.companyId === companyId && scope.contextVersion === contextVersion;
export const matchesUtilitySession = (turn: OperatorTurn, scope: OperatorScope) => !turn.proposal &&
  !!turn.session && turn.session.session_id === scope.sessionId && turn.session.company?.company_id === scope.companyId &&
  turn.session.context_version === scope.contextVersion && !!turn.action_result && typeof turn.action_result.tool === 'string' &&
  typeof turn.action_result.summary === 'string';
export const acceptsOperatorTurn = (turn: OperatorTurn, scope: OperatorScope) => matches(scope, turn.company_id, turn.context_version) &&
  (turn.action_result?.tool !== 'confirm_research' || !!validatedResearchResult(turn.workspace?.research)) &&
  (turn.read_only === true ? turn.proposal ? turn.proposal.context_version === scope.contextVersion && !turn.session && !turn.action_result :
    turn.session || turn.action_result ? matchesUtilitySession(turn, scope) : true : matchesUtilitySession(turn, scope));
const sourceHref = (value: string): string | null => {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && !url.username && !url.password ? url.href : null;
  } catch { return null; }
};
export type ResolvedOperatorEvidence = { id: string; kind: 'news' | 'quote' | 'plan' | 'unavailable'; title: string; publisher: string | null; href: string | null; detail: string };
export function resolveOperatorEvidence(id: string, scope: OperatorScope, news: NewsView | null, offers: OfferView[], plan: PlanView | null = null): ResolvedOperatorEvidence {
  if (news?.company_id === scope.companyId && news.context_version === scope.contextVersion) {
    const item = news.items.find(entry => entry.item_id === id);
    if (item) return { id, kind: 'news', title: item.title, publisher: item.publisher, href: sourceHref(item.url), detail: `${item.provider} · retrieved ${item.retrieved_at}` };
  }
  const offer = offers.find(entry => entry.offer_id === id);
  if (offer) {
    const supplier = offer.fields.supplier_name;
    const reviewedName = supplier && ['extracted', 'corrected'].includes(supplier.status) && typeof supplier.value === 'string' ? supplier.value.trim() : '';
    return { id, kind: 'quote', title: reviewedName ? supplierDisplayName(reviewedName) : 'Supplier unavailable', publisher: null, href: null, detail: `Quote v${offer.version} · ${offer.status} · ${offer.document.filename}` };
  }
  if (plan?.plan_id === id) return { id, kind: 'plan', title: 'Current comparison', publisher: null, href: null, detail: `Plan ID ${id}` };
  return { id, kind: 'unavailable', title: 'Evidence unavailable in this workspace', publisher: null, href: null, detail: '' };
}

export function researchFromTurn(turn: OperatorTurn, scope: OperatorScope): ResearchResult | null {
  return turn.status === 'completed' && turn.company_id === scope.companyId && turn.context_version === scope.contextVersion &&
    (turn.read_only || turn.action_result?.tool === 'confirm_research' && matchesUtilitySession(turn, scope))
    ? validatedResearchResult(turn.workspace?.research) : null;
}
export function latestVoiceResearch(state: OperatorState, scope: OperatorScope): ResearchResult | null {
  if (!sameScope(state.scope, scope)) return null;
  const latest = state.exchanges.at(-1);
  return latest?.research ? validatedResearchResult(latest.research) : latest?.turn ? researchFromTurn(latest.turn, scope) : null;
}
export function confirmVoiceTransition(kind: NonNullable<OperatorTurn['proposal']>['kind'], voiceOpen: boolean,
    close: () => void, reset: () => void): boolean {
  if (kind === 'research' && voiceOpen) return true;
  if (voiceOpen) close();
  reset();
  return false;
}

export function applyOperatorResult(state: OperatorState, action: OperatorAction): OperatorState {
  if (action.type === 'scope') return sameScope(state.scope, action.scope) ? state : { ...initialOperatorState, scope: action.scope, turns: sameConversation(state.scope, action.scope) ? state.turns ?? [] : [], exchanges: sameConversation(state.scope, action.scope) ? state.exchanges ?? [] : [], overview: sameConversation(state.scope, action.scope) ? state.overview : null };
  if (action.type === 'cancel') return { ...state, requestId: state.requestId + 1, pendingQuestion: null, turn: null, reviewed: false, outcome: null };
  if (action.type === 'review') return { ...state, reviewed: action.value };
  if (!sameScope(state.scope, action.scope)) return state;
  if (action.type === 'history') {
    if (!matches(action.scope, action.history.company_id, action.history.context_version)) return state;
    const existing = new Set(state.exchanges.map(exchange => exchange.id));
    const currentTurnIds = new Set(state.exchanges.flatMap(exchange => exchange.turn ? [exchange.turn.turn_id] : []));
    const historical = action.history.exchanges.filter(exchange => !existing.has(exchange.id) && (!exchange.turn || !currentTurnIds.has(exchange.turn.turn_id))).map(exchange => ({ ...exchange, turn: exchange.turn ? { ...exchange.turn, proposal: null } : undefined }));
    return { ...state, exchanges: [...historical, ...state.exchanges].slice(-MAX_EXCHANGES) };
  }
  if (action.type === 'start') return { ...state, requestId: action.requestId, pendingQuestion: action.question?.trim() || null, turn: null, reviewed: false, outcome: null };
  if (action.type === 'overview') return matches(action.scope, action.overview.company_id, action.overview.context_version) ? { ...state, overview: action.overview } : state;
  if (action.type === 'turn') return action.requestId === state.requestId && acceptsOperatorTurn(action.turn, action.scope) ?
    { ...state, turn: action.turn, turns: [...(state.turns ?? []), action.turn].slice(-MAX_EXCHANGES), exchanges: [...(state.exchanges ?? []), { id: action.requestId, question: state.pendingQuestion, turn: action.turn }].slice(-MAX_EXCHANGES), pendingQuestion: null, reviewed: false } : state;
  if (action.type === 'failed') return action.requestId === state.requestId && !!state.pendingQuestion ?
    { ...state, exchanges: [...(state.exchanges ?? []), { id: action.requestId, question: state.pendingQuestion, error: action.error }].slice(-MAX_EXCHANGES), pendingQuestion: null } : state;
  if (action.type === 'applied') {
    if (!matches(action.scope, action.outcome.company_id, action.outcome.context_version) ||
        action.outcome.session.session_id !== action.scope.sessionId ||
        action.outcome.session.company?.company_id !== action.scope.companyId ||
        action.outcome.session.context_version !== action.scope.contextVersion) return state;
    if (action.outcome.kind === 'research') {
      const research = validatedResearchResult(action.outcome.result);
      if (!research) return state;
      const exchanges = state.exchanges.some(exchange => exchange.id === research.research_id) ? state.exchanges :
        [...state.exchanges, { id: research.research_id, channel: 'text' as const, question: null, research, created_at: research.checked_at }].slice(-MAX_EXCHANGES);
      return { ...state, turn: null, reviewed: false, outcome: null, exchanges };
    }
    return { ...state, turn: null, reviewed: false, outcome: action.outcome };
  }
  return state;
}

export function planProposalCurrent(proposal: OperatorTurn['proposal'], plan: PlanView | null, offers: OfferView[], recovery: RecoveryView | null = null): boolean {
  if (proposal?.kind !== 'recovery' && proposal?.kind !== 'rfq') return true;
  const boundPlan = proposal.kind === 'recovery' && recovery
    ? plan?.plan_id === recovery.scenario.plan_id &&
      Object.keys(plan.offer_versions).length === Object.keys(recovery.scenario.offer_versions).length &&
      Object.entries(plan.offer_versions).every(([id, version]) => recovery.scenario.offer_versions[id] === version)
      ? recovery.baseline : null
    : plan;
  if (!proposal.plan_id || boundPlan?.plan_id !== proposal.plan_id) return false;
  const bound = proposal.offer_versions;
  const current = boundPlan.offer_versions;
  const ids = Object.keys(bound);
  return ids.length > 0 && ids.length === Object.keys(current).length && ids.every(id =>
    Number.isInteger(bound[id]) && bound[id] === current[id] && offers.some(offer => offer.offer_id === id && offer.version === bound[id]));
}

export function canApplyProposal(state: OperatorState, scope: OperatorScope, dirty: boolean, now: number, plan: PlanView | null = null, offers: OfferView[] = [], recovery: RecoveryView | null = null): boolean {
  const proposal = state.turn?.proposal;
  return !dirty && sameScope(state.scope, scope) && state.reviewed && state.turn?.status === 'proposal' && state.turn.read_only === true && !!proposal &&
    proposal.context_version === scope.contextVersion && Number.isFinite(Date.parse(proposal.expires_at)) && Date.parse(proposal.expires_at) > now &&
    planProposalCurrent(proposal, plan, offers, recovery) &&
    (proposal.kind !== 'research' || typeof proposal.parameters.query === 'string' && !!proposal.parameters.query.trim() && proposal.parameters.query.length <= 400 && proposal.parameters.destination === 'Google Search via Vertex AI');
}

export const MAX_VOICE_BYTES = 500000;
export const MAX_VOICE_MS = 20000;
export const VOICE_MIME = 'audio/webm;codecs=opus';
export function validateAudioCapture(value: { bytes: number; durationMs: number; sampleRate: number | undefined; mimeType: string }): string | null {
  if (value.mimeType !== VOICE_MIME) return 'This browser must support WebM Opus recording.';
  if (!value.sampleRate || !Number.isInteger(value.sampleRate) || value.sampleRate <= 0) return 'The microphone sample rate is unavailable; voice cannot be sent safely.';
  if (value.durationMs > MAX_VOICE_MS) return 'Voice turns are limited to 20 seconds.';
  if (value.bytes > MAX_VOICE_BYTES) return 'Voice turns are limited to 500 KB.';
  if (value.bytes <= 0) return 'No microphone audio was captured.';
  return null;
}

const api = createDemoApi();
const errorText = (error: unknown) => error instanceof Error ? error.message : 'The request could not be completed.';
type MaterialLabel = { material_id: string; name: string };
type OverviewLine = { title: string; detail: string; synthetic: boolean };
const record = (value: unknown): Record<string, unknown> | null => value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
const detail = (value: unknown): string => value === null || value === undefined ? 'Unavailable' : typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean' ? String(value) : JSON.stringify(value) ?? 'Unavailable';
export function scopedPreparedDrafts(value: unknown, scope: OperatorScope): PreparedDraft[] | null {
  if (!Array.isArray(value) || value.length > 24) return null;
  if (!value.every(draft => {
    const item = record(draft);
    return item?.company_id === scope.companyId && item.status === 'draft_unsent' &&
      (item.kind === 'email' || item.kind === 'rfq') &&
      ['draft_id', 'material_id', 'recipient_label', 'subject', 'body', 'created_at'].every(key => typeof item[key] === 'string');
  })) return null;
  return value as PreparedDraft[];
}
function draftPreview(turn: OperatorTurn | null): { recipient: string; subject: string; body: string } | null {
  if (turn?.proposal?.kind !== 'simulate_email' && turn?.proposal?.kind !== 'rfq') return null;
  const args = turn.proposal.parameters;
  const preview = record(args.draft_preview) ?? args;
  const recipient = preview.recipient_label ?? preview.supplier;
  const body = preview.body ?? preview.text;
  return typeof recipient === 'string' && typeof preview.subject === 'string' && typeof body === 'string' ?
    { recipient, subject: preview.subject, body } : null;
}
type QuotePreview = { offer_id: string; fixture_id: string; source_filename: string; expected_version: number; review_values: Record<string, unknown> };
function quotePreviews(turn: OperatorTurn | null): { materialId: string; offerIds: string[]; rows: QuotePreview[] } | null {
  if (turn?.proposal?.kind !== 'review_quotes') return null;
  const args = turn.proposal.parameters;
  if (typeof args.material_id !== 'string' || !Array.isArray(args.offer_ids) || !args.offer_ids.every(id => typeof id === 'string') || !Array.isArray(args.preview)) return null;
  const rows = args.preview.filter(row => {
    const item = record(row);
    return !!item && typeof item.offer_id === 'string' && typeof item.fixture_id === 'string' && typeof item.source_filename === 'string' && typeof item.expected_version === 'number' && !!record(item.review_values);
  }) as QuotePreview[];
  const offerIds = args.offer_ids as string[];
  return rows.length === args.preview.length && rows.length === offerIds.length && new Set(offerIds).size === offerIds.length &&
    rows.every(row => offerIds.includes(row.offer_id) && turn.proposal!.offer_versions[row.offer_id] === row.expected_version) ?
    { materialId: args.material_id, offerIds, rows } : null;
}
function originalQuoteField(offer: OfferView | undefined, name: string): string {
  if (!offer) return 'Unavailable';
  const match = /^items\[(\d+)\]\.(.+)$/.exec(name);
  const field = match ? offer.items[Number(match[1])]?.[match[2]] : offer.fields[name];
  return field ? detail(field.value) : 'Unavailable';
}
const knownMaterial = (id: unknown, materials: MaterialLabel[]) => typeof id === 'string' ? materials.find(item => item.material_id === id)?.name : undefined;
export function formatOperatorAttention(value: unknown, materials: MaterialLabel[]): OverviewLine {
  const item = record(value);
  const name = knownMaterial(item?.material_id, materials);
  if (item?.synthetic === true && name && typeof item.stock_cover_days === 'string' && /^(?:0|[1-9]\d*)(?:\.\d+)?$/.test(item.stock_cover_days) &&
    typeof item.minimum_lead_time_days === 'number' && Number.isInteger(item.minimum_lead_time_days) && item.minimum_lead_time_days >= 0) {
    return { title: name, detail: `Stock cover: ${item.stock_cover_days.includes('.') ? roundDecimalText(item.stock_cover_days, 1) : item.stock_cover_days} days · shortest listed supplier lead: ${item.minimum_lead_time_days} days`, synthetic: true };
  }
  return { title: 'Attention details unavailable', detail: '', synthetic: false };
}
export function formatOperatorWatch(value: unknown, materials: MaterialLabel[], companyId?: string): OverviewLine {
  const item = record(value);
  const name = knownMaterial(item?.material_id, materials);
  const date = typeof item?.created_at === 'string' ? Date.parse(item.created_at) : NaN;
  if (item?.synthetic === true && name && Number.isFinite(date) && (!companyId || item.company_id === companyId)) {
    return { title: name, detail: `Saved ${new Date(date).toISOString().slice(0, 16).replace('T', ' ')} UTC`, synthetic: true };
  }
  return { title: 'Watch details unavailable', detail: '', synthetic: false };
}
function OperatorAnalytics({ analytics }: { analytics: Record<string, unknown> }) {
  return <div className="demo-operator-analytics"><h3>Computed analytics</h3>{analytics.source === 'fictional_company_profile' && typeof analytics.as_of === 'string' && <p className="demo-muted">Fictional company profile · {analytics.as_of}</p>}{Object.entries(analytics).map(([name, value]) => {
    if (!Array.isArray(value)) return null;
    const rows = value.filter(item => record(item)) as Record<string, unknown>[];
    const columns = [...new Set(rows.flatMap(row => Object.keys(row)))].slice(0, 12);
    return <div key={name} className="demo-table-wrap"><h4>{name.replaceAll('_', ' ').replace(/^./, letter => letter.toUpperCase())}</h4>{rows.length ? <table><thead><tr>{columns.map(column => <th key={column}>{column.replaceAll('_', ' ')}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={index}>{columns.map(column => <td key={column}>{Array.isArray(row[column]) ? (row[column] as unknown[]).map(entry => record(entry)?.name ?? detail(entry)).join(', ') || 'None listed' : detail(row[column])}</td>)}</tr>)}</tbody></table> : <p>No rows returned.</p>}</div>;
  })}</div>;
}
function OperatorAnswer({ turn, scope, supplierNames }: { turn: OperatorTurn; scope: OperatorScope; supplierNames: string[] }) {
  const research = researchFromTurn(turn, scope);
  return research ? <ResearchOutcome result={research} /> : <div className="demo-operator-answer"><MarkdownAnswer text={displaySupplierMentions(turn.assistant_text, supplierNames)} /></div>;
}
export function VoiceConversationSurface({ open, onClose, voice, proposal, research, surfaceRef }: {
  open: boolean; onClose: () => void; voice: ReactNode; proposal?: ReactNode; research?: ResearchResult | null; surfaceRef?: Ref<HTMLDivElement>;
}) {
  function keys(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (!open) return;
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); onClose(); return; }
    if (event.key !== 'Tab') return;
    event.stopPropagation();
    const buttons = [...event.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), a[href], textarea:not(:disabled)')].filter(item => item.offsetParent !== null);
    const first = buttons[0], last = buttons.at(-1);
    if (!first || !last) { event.preventDefault(); return; }
    if (event.shiftKey && (document.activeElement === first || document.activeElement === event.currentTarget)) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
  return <div className="demo-voice-screen" hidden={!open} role={open ? 'dialog' : undefined} aria-modal={open ? true : undefined} aria-label={open ? 'Voice conversation' : undefined} tabIndex={-1} ref={surfaceRef} onKeyDown={keys}>
    <div className="demo-voice-screen-header"><div><span className="demo-kicker">Live conversation</span><h2>Voice</h2></div><button type="button" className="demo-voice-back" onClick={onClose}>Back to chat</button></div>
    <div className="demo-voice-screen-body" data-has-proposal={!!proposal || !!research}>{proposal}{research && <div className="demo-voice-research-result"><span className="demo-kicker">Public research completed</span><ResearchOutcome result={research} /><p>You can ask a follow-up by voice. This result was returned by the confirmed web request.</p></div>}{voice}</div>
  </div>;
}
export function OperatorPanel({ scope, csrf, dirty, blocked = false, closeSignal, initialState, news = null, offers = [], plan = null, recovery = null, materials = [], seed, riskSummary, beforeTurn, onClose, onSession, onNavigate, onPreparedDrafts, onUnknown }: {
  scope: OperatorScope; csrf: string; dirty: boolean; blocked?: boolean; closeSignal?: number; initialState?: OperatorState; news?: NewsView | null; offers?: OfferView[]; plan?: PlanView | null; recovery?: RecoveryView | null; materials?: MaterialLabel[]; seed?: OperatorSeed | null; riskSummary?: { high: number; medium: number; low: number; checkedAt: string } | null; beforeTurn?: () => Promise<DemoSession>; onClose?: () => void; onSession: (session: DemoSession, action?: OperatorTurn['action_result']) => void; onNavigate?: (view: 'company' | 'news' | 'procurement' | 'outbox' | 'drafts') => void; onPreparedDrafts?: (drafts: PreparedDraft[], scope: OperatorScope) => void; onUnknown: () => void;
}) {
  const [rawState, dispatch] = useReducer(applyOperatorResult, initialState ?? { ...initialOperatorState, scope });
  const state = sameScope(rawState.scope, scope) ? rawState : {
    ...initialOperatorState, scope,
    turns: sameConversation(rawState.scope, scope) ? rawState.turns : [],
    exchanges: sameConversation(rawState.scope, scope) ? rawState.exchanges : [],
  };
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [voiceRevision, setVoiceRevision] = useState(0);
  const [voiceOpen, setVoiceOpen] = useState(false);
  const generationRef = useRef(0);
  const mountedRef = useRef(false);
  const scopeRef = useRef(scope);
  const requestRef = useRef(0);
  const overviewRef = useRef(0);
  const proposalRef = useRef<HTMLDivElement | null>(null);
  const voiceRef = useRef<LiveVoiceHandle | null>(null);
  const voiceSurfaceRef = useRef<HTMLDivElement | null>(null);
  const micRef = useRef<HTMLButtonElement | null>(null);
  const messageRef = useRef<HTMLTextAreaElement | null>(null);
  const historyRef = useRef<HTMLDivElement | null>(null);
  const followHistoryRef = useRef(true);
  const forceHistoryScrollRef = useRef(false);
  const historyScopeRef = useRef(`${scope.sessionId}:${scope.companyId}:${scope.contextVersion}`);
  const restoreVoiceFocusRef = useRef(false);
  const lastCloseSignal = useRef(closeSignal);
  const voiceScopeRef = useRef(scope);
  scopeRef.current = scope;
  function closeVoice(restoreFocus = true) {
    voiceRef.current?.end();
    restoreVoiceFocusRef.current = restoreFocus;
    setVoiceOpen(false);
  }
  function openVoice() {
    if (blocked || busy || state.overview?.available === false || voiceOpen) return;
    setVoiceOpen(true);
    voiceRef.current?.start();
  }
  useEffect(() => {
    if (voiceOpen) voiceSurfaceRef.current?.focus();
    else if (restoreVoiceFocusRef.current) {
      restoreVoiceFocusRef.current = false;
      if (micRef.current && !micRef.current.disabled) micRef.current.focus();
      else messageRef.current?.focus();
    }
  }, [voiceOpen]);
  useEffect(() => {
    if (lastCloseSignal.current !== closeSignal) {
      lastCloseSignal.current = closeSignal;
      closeVoice(false);
    }
  }, [closeSignal]);
  useEffect(() => {
    if (!sameScope(voiceScopeRef.current, scope)) {
      voiceScopeRef.current = scope;
      closeVoice(false);
    }
  }, [scope.sessionId, scope.companyId, scope.contextVersion, scope.epoch]);
  useEffect(() => {
    const message = focusValidOperatorSeed(seed, scopeRef.current, () => messageRef.current?.focus());
    if (message) setMessage(message);
  }, [seed?.id, seed?.companyId, scope.companyId, scope.sessionId, scope.contextVersion, scope.epoch]);
  useLayoutEffect(() => {
    const history = historyRef.current;
    if (!history) return;
    const historyScope = `${scope.sessionId}:${scope.companyId}:${scope.contextVersion}`;
    if (historyScopeRef.current !== historyScope) {
      historyScopeRef.current = historyScope;
      followHistoryRef.current = true;
    }
    if (followHistoryRef.current || forceHistoryScrollRef.current) {
      history.scrollTop = history.scrollHeight;
      followHistoryRef.current = true;
    }
    forceHistoryScrollRef.current = false;
  }, [scope.sessionId, scope.companyId, scope.contextVersion, state.exchanges, state.turns, state.turn, state.outcome, state.pendingQuestion]);

  function cancel() {
    const proposal = state.turn?.proposal;
    if (proposal && sameScope(state.scope, scope)) void api.cancelOperator(csrf, proposal.proposal_id, scope.contextVersion).catch(cause => {
      if (sameScope(scopeRef.current, scope)) setError(`The proposal could not be cancelled on the server. ${errorText(cause)}`);
    });
    generationRef.current += 1; requestRef.current += 1;
    closeVoice();
    setVoiceRevision(value => value + 1);
    setBusy(''); setError('');
    dispatch({ type: 'cancel' });
  }

  useEffect(() => {
    mountedRef.current = true;
    const captured = scope;
    const id = ++overviewRef.current;
    setBusy(''); setError('');
    dispatch({ type: 'scope', scope: captured });
    api.operator().then(overview => {
      if (mountedRef.current && id === overviewRef.current && sameScope(scopeRef.current, captured)) dispatch({ type: 'overview', scope: captured, overview });
    }).catch(cause => { if (mountedRef.current && id === overviewRef.current && sameScope(scopeRef.current, captured)) setError(errorText(cause)); });
    api.operatorHistory().then(history => {
      if (mountedRef.current && id === overviewRef.current && sameScope(scopeRef.current, captured)) dispatch({ type: 'history', scope: captured, history });
    }).catch(cause => { if (mountedRef.current && id === overviewRef.current && sameScope(scopeRef.current, captured)) setError(`Conversation history unavailable: ${errorText(cause)}`); });
    return () => {
      mountedRef.current = false;
      generationRef.current += 1; requestRef.current += 1; overviewRef.current += 1;
    };
  }, [scope.sessionId, scope.companyId, scope.contextVersion, scope.epoch]);

  async function send(event: FormEvent) {
    event.preventDefault();
    const value = message.trim();
    if (!value || value.length > 1200 || blocked || busy || voiceOpen || state.overview?.available === false) return;
    const captured = scope, generation = ++generationRef.current, requestId = ++requestRef.current;
    forceHistoryScrollRef.current = true;
    setBusy('asking'); setError('');
    dispatch({ type: 'start', scope: captured, requestId, question: value });
    let acceptedTurn = false;
    try {
      const canonical = beforeTurn ? await beforeTurn() : null;
      if (!mountedRef.current || generation !== generationRef.current || !sameScope(scopeRef.current, captured)) return;
      const turn = await api.operatorTurn(canonical?.csrf_token ?? csrf, value, captured.contextVersion);
      if (mountedRef.current && generation === generationRef.current && sameScope(scopeRef.current, captured)) {
        if (!acceptsOperatorTurn(turn, captured)) throw new DemoApiError('The operator returned a result outside the current workspace.');
        dispatch({ type: 'turn', scope: captured, requestId, turn });
        acceptedTurn = true;
        const drafts = scopedPreparedDrafts(turn.workspace?.drafts, captured);
        if (drafts) onPreparedDrafts?.(drafts, captured);
        if (matchesUtilitySession(turn, captured) && turn.session) { onSession(turn.session, turn.action_result); if (turn.action_result?.view) onNavigate?.(turn.action_result.view); }
        setMessage('');
      }
    } catch (cause) { if (mountedRef.current && generation === generationRef.current && sameScope(scopeRef.current, captured)) {
      if (!acceptedTurn) dispatch({ type: 'failed', scope: captured, requestId, error: errorText(cause) });
      setError(errorText(cause));
    } }
    finally { if (mountedRef.current && generation === generationRef.current) setBusy(''); }
  }

  async function apply() {
    if (!canApplyProposal(state, scope, dirty || blocked, Date.now(), plan, offers, recovery) || !reviewCurrent || busy) return;
    const proposal = state.turn!.proposal!;
    const captured = scope, generation = ++generationRef.current;
    confirmVoiceTransition(proposal.kind, voiceOpen, () => closeVoice(false), () => setVoiceRevision(value => value + 1));
    setBusy(proposal.kind === 'research' ? 'researching' : 'applying'); setError('');
    try {
      const outcome = await api.confirmOperator(csrf, proposal.proposal_id, captured.contextVersion);
      if (!mountedRef.current || generation !== generationRef.current || !sameScope(scopeRef.current, captured)) return;
      if (outcome.status !== 'applied' || outcome.kind !== proposal.kind || outcome.session.session_id !== captured.sessionId || outcome.session.company?.company_id !== captured.companyId || outcome.session.context_version !== captured.contextVersion || outcome.company_id !== captured.companyId || outcome.context_version !== captured.contextVersion) throw new DemoApiError('The applied result did not match this workspace.');
      if (outcome.kind === 'research' && !validatedResearchResult(outcome.result)) throw new DemoApiError('The research result could not be verified.');
      dispatch({ type: 'applied', scope: captured, outcome });
      onSession(outcome.session);
      if (outcome.kind === 'simulate_email' || outcome.kind === 'rfq') onNavigate?.('drafts');
      const id = ++overviewRef.current;
      api.operator().then(overview => { if (mountedRef.current && id === overviewRef.current && sameScope(scopeRef.current, captured)) dispatch({ type: 'overview', scope: captured, overview }); }).catch(() => {});
    } catch (cause) {
      if (!mountedRef.current || generation !== generationRef.current) return;
      dispatch({ type: 'cancel' });
      try {
        const canonical = await api.session();
        if (mountedRef.current && generation === generationRef.current) { onSession(canonical); setError(`The proposal result was uncertain. The current server session was restored. ${errorText(cause)}`); }
      } catch {
        if (mountedRef.current && generation === generationRef.current) onUnknown();
      }
    } finally { if (mountedRef.current && generation === generationRef.current) setBusy(''); }
  }

  function acceptLiveTurn(turn: OperatorTurn, captured: OperatorScope) {
    if (!mountedRef.current || !sameScope(scopeRef.current, captured) || blocked || !acceptsOperatorTurn(turn, captured)) return;
    const next = acceptLiveOperatorRequest({ generation: generationRef.current, requestId: requestRef.current, busy });
    generationRef.current = next.generation;
    requestRef.current = next.requestId;
    setBusy(next.busy);
    const requestId = next.requestId;
    dispatch({ type: 'start', scope: captured, requestId });
    dispatch({ type: 'turn', scope: captured, requestId, turn });
    const drafts = scopedPreparedDrafts(turn.workspace?.drafts, captured);
    if (drafts) onPreparedDrafts?.(drafts, captured);
    if (matchesUtilitySession(turn, captured) && turn.session) { onSession(turn.session, turn.action_result); if (turn.action_result?.view) onNavigate?.(turn.action_result.view); }
  }

  const proposal = state.turn?.proposal;
  useEffect(() => {
    const card = proposalRef.current;
    if (proposal?.proposal_id && card?.getClientRects().length) {
      card.focus({ preventScroll: true });
    }
  }, [proposal?.proposal_id]);
  const supplierNames = offers.flatMap(offer => {
    const field = offer.fields.supplier_name;
    return field && ['extracted', 'corrected'].includes(field.status) && typeof field.value === 'string' ? [field.value] : [];
  });
  const recipient = record(proposal?.parameters)?.recipient_label;
  if (typeof recipient === 'string') supplierNames.push(recipient);
  const reviewPreview = quotePreviews(state.turn);
  const preparedPreview = draftPreview(state.turn);
  const researchCurrent = proposal?.kind !== 'research' || typeof proposal.parameters.query === 'string' && !!proposal.parameters.query.trim() && proposal.parameters.query.length <= 400 && proposal.parameters.destination === 'Google Search via Vertex AI';
  const planCurrent = planProposalCurrent(proposal ?? null, plan, offers, recovery);
  const reviewCurrent = researchCurrent && planCurrent && (proposal?.kind !== 'review_quotes' || !!reviewPreview && reviewPreview.rows.every(row =>
    offers.some(offer => offer.offer_id === row.offer_id && offer.version === row.expected_version && offer.fixture_id === row.fixture_id && offer.document.filename === row.source_filename)));

  const mayApply = canApplyProposal(state, scope, dirty || blocked, Date.now(), plan, offers, recovery) && reviewCurrent && !busy;
  const proposalCard = proposal ? (<div ref={proposalRef} className="demo-operator-proposal demo-current-proposal" role="region" aria-label="Current proposal requiring review" tabIndex={-1}><span className="demo-kicker">Reviewable {proposal.kind} proposal</span><h3>{displaySupplierMentions(proposal.summary, supplierNames)}</h3><p>Review this request before it expires.</p>{!planCurrent && <p className="demo-alert">The comparison or quote versions changed. Request a new proposal.</p>}{proposal.kind === 'research' && <div className="demo-research-consent"><p><strong>Public query to share:</strong> {typeof proposal.parameters.query === 'string' ? proposal.parameters.query : 'Unavailable'}</p><p><strong>Destination:</strong> {typeof proposal.parameters.destination === 'string' ? proposal.parameters.destination : 'Unavailable'}</p><p>Only the exact public query shown above is shared with Google Search via Vertex AI after your confirmation.</p>{!researchCurrent && <p className="demo-alert">The query or destination is invalid. Request a new proposal.</p>}</div>}{proposal.kind === 'review_quotes' && <div className="demo-quote-preview"><h4>Review sample quotes</h4>{reviewPreview ? <><p>Material: {reviewPreview.materialId} · Offer IDs: {reviewPreview.offerIds.join(', ')}</p>{reviewPreview.rows.map(row => {
      const original = offers.find(offer => offer.offer_id === row.offer_id);
      return <div key={row.offer_id} className="demo-table-wrap"><h5>{row.source_filename} · {row.offer_id} · Expected version {row.expected_version}</h5><p>Original quote version: {original?.version ?? 'Unavailable'} · Fixture: {row.fixture_id}</p><table><thead><tr><th>Field</th><th>Current original field</th><th>Suggested synthetic document value</th></tr></thead><tbody>{Object.entries(row.review_values).map(([field, value]) => <tr key={field}><td>{field}</td><td>Original: {originalQuoteField(original, field)}</td><td>Reviewer value: {detail(value)}</td></tr>)}</tbody></table></div>;
    })}</> : <p className="demo-alert">Quote preview unavailable. Request a new proposal.</p>}{!reviewCurrent && <p className="demo-alert">A quote source or version changed. Request a new review proposal.</p>}</div>}{preparedPreview && <div className="demo-draft-preview" id="demo-current-draft"><span className="demo-kicker">Prepared unsent draft · review before Apply</span><p><strong>To:</strong> {displaySupplierMentions(preparedPreview.recipient, supplierNames)}</p><p><strong>Subject: {preparedPreview.subject}</strong></p><pre>{displaySupplierMentions(preparedPreview.body, supplierNames)}</pre><button type="button" className="demo-link" onClick={() => { if (voiceOpen) closeVoice(false); onNavigate?.('drafts'); }}>View draft in workspace</button></div>}<details className="demo-assistant-trace"><summary>Exact proposed arguments</summary><p>Company {scope.companyId} · Context v{proposal.context_version} · Expires {proposal.expires_at}</p><p>Plan ID: {proposal.plan_id ?? 'none'} · Offer versions: {JSON.stringify(proposal.offer_versions)}</p><pre>{JSON.stringify(proposal.parameters, null, 2)}</pre></details><label><input type="checkbox" checked={state.reviewed} disabled={dirty || blocked || !!busy || !reviewCurrent} onChange={event => dispatch({ type: 'review', value: event.target.checked })} />{proposal.kind === 'research' ? 'I reviewed the public query and want to search the web.' : 'I reviewed the exact arguments and want to apply this change to the current session.'}</label><button type="button" className="demo-button" disabled={!mayApply} onClick={apply}>{proposal.kind === 'research' ? 'Research web' : 'Apply reviewed proposal'}</button><p>{proposal.kind === 'simulate_email' ? 'No external delivery. Applying records a simulated sent message in this session only.' : proposal.kind === 'review_quotes' ? 'Only these synthetic quote values will be corrected and saved after your review. No supplier message or order is sent.' : proposal.kind === 'research' ? 'Select Research web to confirm this query. Voice requests can instead be confirmed aloud after the query is read.' : 'No supplier message or order is sent.'}</p></div>) : null;
  return <section className="demo-company-panel demo-operator-panel demo-assistant-panel" aria-label="Operator workspace">
    <div className="demo-heading demo-assistant-heading"><div><span className="demo-kicker">Workspace assistant</span><h2>Operator</h2><p>Ask a question. Actions wait for your review.</p></div><span className="demo-session">{state.overview?.available === false ? 'Unavailable' : 'Ready to help'}</span>{onClose && <button type="button" className="demo-assistant-close" aria-label="Close operator chat" onClick={onClose}>Close</button>}</div>
    <VoiceConversationSurface open={voiceOpen} onClose={() => closeVoice()} surfaceRef={voiceSurfaceRef} proposal={voiceOpen ? proposalCard : null} research={voiceOpen && !proposal ? latestVoiceResearch(state, scope) : null}
      voice={<LiveVoice key={voiceRevision} ref={voiceRef} scope={scope} csrf={csrf} disabled={blocked || busy === 'applying' || state.overview?.available === false} beforeStart={beforeTurn} onTurn={acceptLiveTurn} onSession={onSession} onUnknown={onUnknown} onExit={() => closeVoice()} />} />
    {riskSummary && <p className="demo-operator-risk"><strong>Current company risks:</strong> {riskSummary.high} high · {riskSummary.medium} medium · {riskSummary.low} low <small>Checked {riskSummary.checkedAt}</small></p>}
    {dirty && <p className="demo-alert">Edited buyer inputs will be saved to this temporary session before the next question or voice conversation. Run a new comparison before reviewing a recovery or RFQ proposal.</p>}
    {error && <p className="demo-alert" role="alert">Operator unavailable: {error}</p>}
    {state.overview && <details className="demo-assistant-attention"><summary><span>Company attention</span><strong>{state.overview.attention.length} {state.overview.attention.length === 1 ? 'signal' : 'signals'} · {state.overview.watch_items.length} {state.overview.watch_items.length === 1 ? 'watch' : 'watches'}</strong></summary><div className="demo-operator-overview"><div><h3>Company attention</h3>{state.overview.attention.length ? <ul>{state.overview.attention.map((item, index) => { const line = formatOperatorAttention(item, materials); return <li key={index}><strong>{line.title}</strong>{line.detail && <span>{line.detail}</span>}{line.synthetic && <small>Synthetic stock and lead-time comparison</small>}</li>; })}</ul> : <p>No attention items returned.</p>}</div><div><h3>Watch items</h3>{state.overview.watch_items.length ? <ul>{state.overview.watch_items.map((item, index) => { const line = formatOperatorWatch(item, materials, scope.companyId); return <li key={index}><strong>{line.title}</strong>{line.detail && <span>{line.detail}</span>}{line.synthetic && <small>Synthetic watch item</small>}</li>; })}</ul> : <p>No watch items saved.</p>}</div></div><p className="demo-muted">Available specialists: {state.overview.agents.map(agent => `${agent.name} (${agent.role})`).join('; ') || 'none reported'}.</p>{state.overview.limitations.length > 0 && <p className="demo-alert">Limits: {state.overview.limitations.join('; ')}</p>}</details>}
    {state.overview?.available === false && <p className="demo-alert">The agent provider is unavailable. Read the company and news panels or try again later.</p>}
    {busy === 'asking' && <ActivityIndicator state="working" label="Specialists are reviewing the current workspace…" size={20} theme="dark" />}
    {busy === 'applying' && <ActivityIndicator state="working" label="Applying the reviewed proposal…" size={20} theme="dark" />}
    {busy === 'researching' && <ActivityIndicator state="working" label="Searching the public web with the reviewed query…" size={20} theme="dark" />}
    {!voiceOpen && proposalCard}
    <div ref={historyRef} className="demo-assistant-history" role="log" aria-label="Assistant conversation" aria-live="polite" aria-relevant="additions text" onScroll={event => { followHistoryRef.current = shouldFollowOperatorHistory(event.currentTarget); }}>
    {(state.exchanges?.length ? state.exchanges : (state.turns?.length ? state.turns : state.turn ? [state.turn] : []).map((turn, id): OperatorExchange => ({ id, question: null, turn }))).map(exchange => <div key={exchange.id} className="demo-assistant-exchange">{exchange.question && <div className="demo-assistant-question"><span className="demo-kicker">You</span><p>{exchange.question}</p></div>}{exchange.answer && !exchange.turn && !exchange.research && <div className="demo-operator-result"><span className="demo-kicker">{exchange.channel === 'voice' ? exchange.speaker === 'input' ? 'You · voice' : 'Operator · voice' : 'Saved answer'}</span><div className="demo-operator-answer"><MarkdownAnswer text={exchange.answer} /></div></div>}{exchange.research && <ResearchOutcome result={exchange.research} />}{exchange.error && <div className="demo-assistant-failure" role="status"><span className="demo-kicker">Request failed</span><p>{exchange.error}</p></div>}{exchange.turn && <div className="demo-operator-result"><span className="demo-kicker">{exchange.turn.status === 'proposal' ? 'Proposal ready' : 'Operator'}</span><OperatorAnswer turn={exchange.turn} scope={scope} supplierNames={supplierNames} />{exchange.turn.action_result && <p className="demo-assistant-result-note">{exchange.turn.action_result.summary}{exchange.turn.action_result.fields_changed?.length ? ` · Filled: ${exchange.turn.action_result.fields_changed.join(', ')}` : ''}</p>}<details className="demo-assistant-trace"><summary>Evidence and specialist activity · {exchange.turn.evidence_ids.length} references · {exchange.turn.agent_steps.length} steps</summary>{exchange.turn.analytics && <OperatorAnalytics analytics={exchange.turn.analytics} />}<h3>Evidence in this workspace</h3>{exchange.turn.evidence_ids.length ? <ul className="demo-operator-evidence">{exchange.turn.evidence_ids.map(id => {
      const evidence = resolveOperatorEvidence(id, scope, news, offers, plan);
      return <li key={id}><code>{id}</code><span>{evidence.kind === 'news' ? 'Observed news' : evidence.kind === 'quote' ? 'Quote' : evidence.kind === 'plan' ? 'Comparison' : 'Unavailable'}</span>{evidence.href ? <a href={evidence.href} target="_blank" rel="noopener noreferrer">{evidence.title} ↗</a> : <strong>{evidence.title}</strong>}{evidence.publisher && <span>Publisher: {evidence.publisher}</span>}{evidence.detail && <small>{evidence.detail}</small>}{evidence.kind === 'news' && !evidence.href && <small>Source link unavailable</small>}</li>;
    })}</ul> : <p>No evidence IDs returned.</p>}<h3>Actual agent activity</h3>{exchange.turn.agent_steps.length ? <ol>{exchange.turn.agent_steps.map((step, index) => <li key={index}><strong>{step.agent} · {step.role}</strong><span>{step.status} · {step.model} · provider call {step.provider_call} · tool {step.tool}</span><p>{step.summary}</p></li>)}</ol> : <p>No specialist calls were recorded for this turn.</p>}</details></div>}</div>)}
    {state.outcome && <div className="demo-operator-result"><span className="demo-kicker">{state.outcome.kind === 'research' ? 'Approved public research' : `Applied to server session · ${state.outcome.kind}`}</span>{state.outcome.kind !== 'research' && <p>This result is the server-confirmed workspace change. The current session and comparison below were restored from its response.</p>}{state.outcome.kind === 'research' ? <ResearchOutcome result={state.outcome.result as ResearchResult} /> : state.outcome.kind === 'watch' ? (() => { const line = formatOperatorWatch(state.outcome.result, materials, scope.companyId); return <p><strong>{line.title}</strong>{line.detail && ` · ${line.detail}`}{line.synthetic && ' · Synthetic watch item'}</p>; })() : <p>{state.outcome.kind === 'recovery' ? 'The hypothetical recovery comparison is available below.' : state.outcome.kind === 'simulate_email' ? 'A simulated sent record is available in the outbox. No external delivery occurred.' : state.outcome.kind === 'review_quotes' ? 'The reviewed synthetic quotes were restored from the confirmed server session.' : 'The unsent RFQ draft is available in Drafts.'}</p>}{(state.outcome.kind === 'rfq' || state.outcome.kind === 'simulate_email') && <button type="button" className="demo-link" onClick={() => onNavigate?.('drafts')}>View draft</button>}</div>}
    </div>
    <form onSubmit={send} className="demo-operator-chat demo-assistant-composer"><label htmlFor="demo-operator-message">Your message</label><div className="demo-composer-box"><textarea ref={messageRef} id="demo-operator-message" value={message} maxLength={1200} onChange={event => setMessage(event.target.value)} placeholder="Ask about a story or your workspace" disabled={blocked || !!busy || voiceOpen || state.overview?.available === false} /><button ref={micRef} type="button" className="demo-composer-mic" aria-label="Open voice conversation" title="Open voice conversation" disabled={blocked || !!busy || voiceOpen || state.overview?.available === false} onClick={openVoice}><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="3" width="6" height="12" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3m-4 0h8" /></svg></button><button className="demo-button" disabled={!message.trim() || blocked || !!busy || voiceOpen || state.overview?.available === false}>Send</button></div><div className="demo-composer-meta"><span>{message.length}/1200 characters</span><button type="button" className="demo-link" disabled={busy === 'applying' || busy === 'researching'} onClick={cancel}>Cancel current turn</button></div></form>
  </section>;
}
