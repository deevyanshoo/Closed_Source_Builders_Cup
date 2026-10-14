import { useEffect, useReducer, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { createDemoApi, DemoApiError } from './demo-api';
import type { DemoSession, NewsView, OfferView, OperatorApplied, OperatorOverview, OperatorTurn, PlanView } from './demo-api';
import { LiveVoice } from './demo-live-voice';

export type OperatorScope = { sessionId: string; companyId: string; contextVersion: number; epoch: number };
export type OperatorState = { scope: OperatorScope; requestId: number; overview: OperatorOverview | null; turn: OperatorTurn | null; turns: OperatorTurn[]; reviewed: boolean; outcome: OperatorApplied | null };
export const initialOperatorState: OperatorState = { scope: { sessionId: '', companyId: '', contextVersion: -1, epoch: -1 }, requestId: 0, overview: null, turn: null, turns: [], reviewed: false, outcome: null };
type OperatorAction = { type: 'scope'; scope: OperatorScope } | { type: 'start'; scope: OperatorScope; requestId: number } |
  { type: 'overview'; scope: OperatorScope; overview: OperatorOverview } | { type: 'turn'; scope: OperatorScope; requestId: number; turn: OperatorTurn } |
  { type: 'review'; value: boolean } | { type: 'cancel' } | { type: 'applied'; scope: OperatorScope; outcome: OperatorApplied };
const sameScope = (a: OperatorScope, b: OperatorScope) => a.sessionId === b.sessionId && a.companyId === b.companyId && a.contextVersion === b.contextVersion && a.epoch === b.epoch;
const sameConversation = (a: OperatorScope, b: OperatorScope) => a.sessionId === b.sessionId && a.companyId === b.companyId && a.contextVersion === b.contextVersion;
export function acceptLiveOperatorRequest(current: { generation: number; requestId: number; busy: string }) {
  return { generation: current.generation + 1, requestId: current.requestId + 1, busy: '' };
}
const matches = (scope: OperatorScope, companyId: string, contextVersion: number) => scope.companyId === companyId && scope.contextVersion === contextVersion;
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
    return { id, kind: 'quote', title: reviewedName || 'Supplier unavailable', publisher: null, href: null, detail: `Quote v${offer.version} · ${offer.status} · ${offer.document.filename}` };
  }
  if (plan?.plan_id === id) return { id, kind: 'plan', title: 'Current comparison', publisher: null, href: null, detail: `Plan ID ${id}` };
  return { id, kind: 'unavailable', title: 'Evidence unavailable in this workspace', publisher: null, href: null, detail: '' };
}

export function applyOperatorResult(state: OperatorState, action: OperatorAction): OperatorState {
  if (action.type === 'scope') return sameScope(state.scope, action.scope) ? state : { ...initialOperatorState, scope: action.scope, turns: sameConversation(state.scope, action.scope) ? state.turns ?? [] : [], overview: sameConversation(state.scope, action.scope) ? state.overview : null };
  if (action.type === 'cancel') return { ...state, requestId: state.requestId + 1, turn: null, reviewed: false, outcome: null };
  if (action.type === 'review') return { ...state, reviewed: action.value };
  if (!sameScope(state.scope, action.scope)) return state;
  if (action.type === 'start') return { ...state, requestId: action.requestId, turn: null, reviewed: false, outcome: null };
  if (action.type === 'overview') return matches(action.scope, action.overview.company_id, action.overview.context_version) ? { ...state, overview: action.overview } : state;
  if (action.type === 'turn') return action.requestId === state.requestId && matches(action.scope, action.turn.company_id, action.turn.context_version) && action.turn.read_only === true &&
    (!action.turn.proposal || action.turn.proposal.context_version === action.scope.contextVersion) ? { ...state, turn: action.turn, turns: [...(state.turns ?? []), action.turn], reviewed: false } : state;
  if (action.type === 'applied') return matches(action.scope, action.outcome.company_id, action.outcome.context_version) &&
    action.outcome.session.session_id === action.scope.sessionId && action.outcome.session.company?.company_id === action.scope.companyId && action.outcome.session.context_version === action.scope.contextVersion ?
    { ...state, turn: null, reviewed: false, outcome: action.outcome } : state;
  return state;
}

export function canApplyProposal(state: OperatorState, scope: OperatorScope, dirty: boolean, now: number): boolean {
  const proposal = state.turn?.proposal;
  return !dirty && sameScope(state.scope, scope) && state.reviewed && state.turn?.status === 'proposal' && state.turn.read_only === true && !!proposal &&
    proposal.context_version === scope.contextVersion && Number.isFinite(Date.parse(proposal.expires_at)) && Date.parse(proposal.expires_at) > now;
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
const knownMaterial = (id: unknown, materials: MaterialLabel[]) => typeof id === 'string' ? materials.find(item => item.material_id === id)?.name : undefined;
export function formatOperatorAttention(value: unknown, materials: MaterialLabel[]): OverviewLine {
  const item = record(value);
  const name = knownMaterial(item?.material_id, materials);
  if (item?.synthetic === true && name && typeof item.stock_cover_days === 'string' && /^(?:0|[1-9]\d*)(?:\.\d+)?$/.test(item.stock_cover_days) &&
    typeof item.minimum_lead_time_days === 'number' && Number.isInteger(item.minimum_lead_time_days) && item.minimum_lead_time_days >= 0) {
    return { title: name, detail: `Stock cover: ${item.stock_cover_days} days · shortest listed supplier lead: ${item.minimum_lead_time_days} days`, synthetic: true };
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
export function OperatorPanel({ scope, csrf, dirty, blocked = false, initialState, news = null, offers = [], plan = null, materials = [], onSession, onUnknown }: {
  scope: OperatorScope; csrf: string; dirty: boolean; blocked?: boolean; initialState?: OperatorState; news?: NewsView | null; offers?: OfferView[]; plan?: PlanView | null; materials?: MaterialLabel[]; onSession: (session: DemoSession) => void; onUnknown: () => void;
}) {
  const [state, dispatch] = useReducer(applyOperatorResult, initialState ?? { ...initialOperatorState, scope });
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [voiceRevision, setVoiceRevision] = useState(0);
  const generationRef = useRef(0);
  const mountedRef = useRef(false);
  const scopeRef = useRef(scope);
  const requestRef = useRef(0);
  const overviewRef = useRef(0);
  scopeRef.current = scope;

  function cancel() {
    generationRef.current += 1; requestRef.current += 1;
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
    return () => {
      mountedRef.current = false;
      generationRef.current += 1; requestRef.current += 1; overviewRef.current += 1;
    };
  }, [scope.sessionId, scope.companyId, scope.contextVersion, scope.epoch]);

  async function send(event: FormEvent) {
    event.preventDefault();
    const value = message.trim();
    if (!value || value.length > 1200 || dirty || blocked || busy || state.overview?.available === false) return;
    const captured = scope, generation = ++generationRef.current, requestId = ++requestRef.current;
    setBusy('asking'); setError('');
    dispatch({ type: 'start', scope: captured, requestId });
    try {
      const turn = await api.operatorTurn(csrf, value, captured.contextVersion);
      if (mountedRef.current && generation === generationRef.current && sameScope(scopeRef.current, captured)) {
        if (!matches(captured, turn.company_id, turn.context_version) || turn.read_only !== true || (turn.proposal && turn.proposal.context_version !== captured.contextVersion)) throw new DemoApiError('The operator returned a result outside the current workspace.');
        dispatch({ type: 'turn', scope: captured, requestId, turn });
        setMessage('');
      }
    } catch (cause) { if (mountedRef.current && generation === generationRef.current) setError(errorText(cause)); }
    finally { if (mountedRef.current && generation === generationRef.current) setBusy(''); }
  }

  async function apply() {
    if (!canApplyProposal(state, scope, dirty || blocked, Date.now()) || busy) return;
    const proposal = state.turn!.proposal!;
    const captured = scope, generation = ++generationRef.current;
    setBusy('applying'); setError('');
    setVoiceRevision(value => value + 1);
    try {
      const outcome = await api.confirmOperator(csrf, proposal.proposal_id, captured.contextVersion);
      if (!mountedRef.current || generation !== generationRef.current || !sameScope(scopeRef.current, captured)) return;
      if (outcome.status !== 'applied' || outcome.kind !== proposal.kind || outcome.session.session_id !== captured.sessionId || outcome.session.company?.company_id !== captured.companyId || outcome.session.context_version !== captured.contextVersion || outcome.company_id !== captured.companyId || outcome.context_version !== captured.contextVersion) throw new DemoApiError('The applied result did not match this workspace.');
      dispatch({ type: 'applied', scope: captured, outcome });
      onSession(outcome.session);
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
    if (!mountedRef.current || !sameScope(scopeRef.current, captured) || dirty || blocked) return;
    const next = acceptLiveOperatorRequest({ generation: generationRef.current, requestId: requestRef.current, busy });
    generationRef.current = next.generation;
    requestRef.current = next.requestId;
    setBusy(next.busy);
    const requestId = next.requestId;
    dispatch({ type: 'start', scope: captured, requestId });
    dispatch({ type: 'turn', scope: captured, requestId, turn });
  }

  const proposal = state.turn?.proposal;
  const mayApply = canApplyProposal(state, scope, dirty || blocked, Date.now()) && !busy;
  return <section className="demo-company-panel demo-operator-panel" aria-label="Operator workspace">
    <div className="demo-heading"><div><span className="demo-kicker">Reviewed tools · no automatic actions</span><h2>Operator</h2></div><span className="demo-session">{state.overview?.available ? 'Agent service available' : 'Agent availability unverified'}</span></div>
    <p>Ask about this fictional company, observed news and the current procurement comparison. Specialist activity and evidence IDs are shown below. Replies are read-only; a proposed workspace change needs your review and a separate apply step.</p>
    {dirty && <p className="demo-alert">Run a new comparison before using the operator with edited buyer or scenario inputs.</p>}
    {error && <p className="demo-alert" role="alert">Operator unavailable: {error}</p>}
    {state.overview && <div className="demo-operator-overview"><div><h3>Company attention</h3>{state.overview.attention.length ? <ul>{state.overview.attention.map((item, index) => { const line = formatOperatorAttention(item, materials); return <li key={index}><strong>{line.title}</strong>{line.detail && <span>{line.detail}</span>}{line.synthetic && <small>Synthetic stock and lead-time comparison</small>}</li>; })}</ul> : <p>No attention items returned.</p>}</div><div><h3>Watch items</h3>{state.overview.watch_items.length ? <ul>{state.overview.watch_items.map((item, index) => { const line = formatOperatorWatch(item, materials, scope.companyId); return <li key={index}><strong>{line.title}</strong>{line.detail && <span>{line.detail}</span>}{line.synthetic && <small>Synthetic watch item</small>}</li>; })}</ul> : <p>No watch items saved.</p>}</div></div>}
    {state.overview && <><p className="demo-muted">Available specialists: {state.overview.agents.map(agent => `${agent.name} (${agent.role})`).join('; ') || 'none reported'}.</p>{state.overview.limitations.length > 0 && <p className="demo-alert">Limits: {state.overview.limitations.join('; ')}</p>}{!state.overview.available && <p className="demo-alert">The agent provider is unavailable. Read the company and news panels or try again later.</p>}</>}
    <form onSubmit={send} className="demo-operator-chat"><label htmlFor="demo-operator-message">Your message</label><textarea id="demo-operator-message" value={message} maxLength={1200} onChange={event => setMessage(event.target.value)} placeholder="Ask what needs attention, or request a reviewable watch, recovery, or unsent RFQ proposal." disabled={dirty || blocked || !!busy || state.overview?.available === false} /><div><span>{message.length}/1200 characters</span><button className="demo-button" disabled={!message.trim() || dirty || blocked || !!busy || state.overview?.available === false}>Send</button><button type="button" className="demo-link" disabled={busy === 'applying'} onClick={cancel}>Cancel current turn</button></div></form>
    <LiveVoice key={voiceRevision} scope={scope} csrf={csrf} disabled={dirty || blocked || busy === 'applying' || state.overview?.available === false} onTurn={acceptLiveTurn} />
    {busy === 'asking' && <p role="status">Specialists are reviewing the current workspace…</p>}
    {(state.turns?.length ? state.turns : state.turn ? [state.turn] : []).map((shownTurn) => <div key={shownTurn.turn_id} className="demo-operator-result"><span className="demo-kicker">Read-only outcome · {shownTurn.status}</span><p className="demo-operator-answer">{shownTurn.assistant_text}</p><h3>Evidence in this workspace</h3>{shownTurn.evidence_ids.length ? <ul className="demo-operator-evidence">{shownTurn.evidence_ids.map(id => {
      const evidence = resolveOperatorEvidence(id, scope, news, offers, plan);
      return <li key={id}><code>{id}</code><span>{evidence.kind === 'news' ? 'Observed news' : evidence.kind === 'quote' ? 'Quote' : evidence.kind === 'plan' ? 'Comparison' : 'Unavailable'}</span>{evidence.href ? <a href={evidence.href} target="_blank" rel="noopener noreferrer">{evidence.title} ↗</a> : <strong>{evidence.title}</strong>}{evidence.publisher && <span>Publisher: {evidence.publisher}</span>}{evidence.detail && <small>{evidence.detail}</small>}{evidence.kind === 'news' && !evidence.href && <small>Source link unavailable</small>}</li>;
    })}</ul> : <p>No evidence IDs returned.</p>}<h3>Actual agent activity</h3>{shownTurn.agent_steps.length ? <ol>{shownTurn.agent_steps.map((step, index) => <li key={index}><strong>{step.agent} · {step.role}</strong><span>{step.status} · {step.model} · provider call {step.provider_call} · tool {step.tool}</span><p>{step.summary}</p></li>)}</ol> : <p>No specialist calls were recorded for this turn.</p>}</div>)}
    {proposal && <div className="demo-operator-proposal"><span className="demo-kicker">Reviewable {proposal.kind} proposal</span><h3>{proposal.summary}</h3><p>Company {scope.companyId} · Context v{proposal.context_version} · Expires {proposal.expires_at}</p><p>Plan ID: {proposal.plan_id ?? 'none'} · Offer versions: {JSON.stringify(proposal.offer_versions)}</p><strong>Exact proposed arguments</strong><pre>{JSON.stringify(proposal.parameters, null, 2)}</pre><label><input type="checkbox" checked={state.reviewed} disabled={dirty || blocked || !!busy} onChange={event => dispatch({ type: 'review', value: event.target.checked })} />I reviewed the exact arguments and want to apply this change to the current session.</label><button type="button" className="demo-button" disabled={!mayApply} onClick={apply}>Apply reviewed proposal</button><p>No supplier message or order is sent.</p></div>}
    {state.outcome && <div className="demo-operator-result"><span className="demo-kicker">Applied to server session · {state.outcome.kind}</span><p>This result is the server-confirmed workspace change. The current session and comparison below were restored from its response.</p>{state.outcome.kind === 'watch' ? (() => { const line = formatOperatorWatch(state.outcome.result, materials, scope.companyId); return <p><strong>{line.title}</strong>{line.detail && ` · ${line.detail}`}{line.synthetic && ' · Synthetic watch item'}</p>; })() : <p>{state.outcome.kind === 'recovery' ? 'The hypothetical recovery comparison is available below.' : 'The unsent RFQ draft is available below.'}</p>}</div>}
  </section>;
}
