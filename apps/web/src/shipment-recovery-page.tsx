import { useEffect, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';
import type { SessionResponse, ShipmentRecoveryContext } from '../../../contracts/generated/types';
import { fetchSession } from './state';
import { compareShipmentContext, listShipmentContexts, ShipmentRecoveryError } from './shipment-recovery';
import type { ShipmentViewOutcome } from './shipment-recovery';
import { claimHandoff, parseHandoff, selectHandoffContext } from './quote-shipment-bridge';
import type { QuoteShipmentHandoff } from './quote-shipment-bridge';
import { normalizeQuoteSession } from './offers-service';
import './shipment-recovery.css';
import { WorkspaceHeader, sessionStatus } from './workspace-nav';

type Run = { generation: number; session: SessionResponse; contextId: string; revision: number; requestSha256: string; routeKey: string; aborted: boolean };
type View = { kind: 'checking' | 'loading_contexts' | 'empty' | 'ready' | 'comparing' | 'session_error' | 'unavailable' | 'error'; message?: string };
const sessionKey = (s: SessionResponse) => JSON.stringify([s.principal_id, s.workspace_id, s.role,
  s.identity_mode ?? (s.development_only ? 'development' : '')]);
export function isCurrentShipmentRun(run: Run, generation: number, session: SessionResponse | null, context: ShipmentRecoveryContext | null, aborted: boolean, routeKey: string): boolean {
  return !run.aborted && !aborted && run.generation === generation && !!session && sessionKey(run.session) === sessionKey(session) &&
    !!context && run.contextId === context.context_id && run.revision === context.revision &&
    run.requestSha256 === context.request_sha256 && run.routeKey === routeKey;
}
type ShipmentComparisonDeps<T> = {
  run: Run;
  context: ShipmentRecoveryContext;
  signal: AbortSignal;
  isCurrent: () => boolean;
  fetchIdentity: (signal: AbortSignal) => Promise<SessionResponse>;
  compare: (signal: AbortSignal) => Promise<T>;
};
export async function runShipmentComparison<T>(deps: ShipmentComparisonDeps<T>): Promise<T | null> {
  const { run, context, signal, isCurrent, fetchIdentity, compare } = deps;
  const identityError = () => new ShipmentRecoveryError('The local session changed. Refresh the session.', 'session');
  const before = await fetchIdentity(signal).catch(() => { throw identityError(); });
  if (sessionKey(before) !== sessionKey(run.session)) throw identityError();
  // This must run after the awaited identity check: abort-insensitive transports
  // still must not send a request for a cancelled or replaced run.
  if (!isCurrent()) return null;
  const value = await compare(signal);
  const after = await fetchIdentity(signal).catch(() => { throw identityError(); });
  if (sessionKey(after) !== sessionKey(run.session)) throw identityError();
  return isCurrent() ? value : null;
}
export function subscribeShipmentForeground(documentLike: Pick<Document, 'visibilityState' | 'addEventListener' | 'removeEventListener'>, onForeground: () => void): () => void {
  const onVisibilityChange = () => { if (documentLike.visibilityState === 'visible') onForeground(); };
  documentLike.addEventListener('visibilitychange', onVisibilityChange);
  return () => documentLike.removeEventListener('visibilitychange', onVisibilityChange);
}
export function refreshShipmentOnForeground(clearIdentityBoundState: () => void, reload: () => void): void {
  clearIdentityBoundState();
  reload();
}
const show = (v: unknown) => JSON.stringify(v, null, 2);
function Evidence({ name, rows }: { name: string; rows: unknown[] }) {
  return <details className="shipment-evidence"><summary>{name} · {rows.length}</summary>
    {rows.length ? <ol>{rows.map((row, index) => <li key={index}><pre>{show(row)}</pre></li>)}</ol> : <p>None in this synthetic context.</p>}
  </details>;
}
export function ShipmentOutcomeView({ value }: { value: ShipmentViewOutcome }) {
  const { outcome, context, raw } = value;
  const evidence = outcome.evidence;
  return <section className="shipment-outcome" aria-label="Shipment recovery outcome">
    <div className="shipment-result-head">
      <p className="eyebrow">LOCAL · SYNTHETIC · HOST CONFIGURED · EPHEMERAL</p>
      <h2>{value.kind === 'evaluated' ? 'Independent shipment comparisons' : value.kind === 'no_eligible_scenario' ? 'No eligible source scenario' : 'Comparison rejected'}</h2>
      <p>Workspace {context.workspace_id} · plan {context.plan_version} · propagation {context.propagation_snapshot_id} · production {context.production_snapshot_id} · as of {context.as_of} · revision {context.revision}</p>
      <p><strong>Source authenticity not verified.</strong> These are independent alternatives, not a simultaneous plan. No operational plan is applied or saved.</p>
      {value.kind === 'no_eligible_scenario' && <p>No numerical comparison ran. Excluded and normalization evidence is shown below.</p>}
      {value.kind === 'rejected' && <p role="alert">Stage: {outcome.stage}. Reason: {outcome.reason}. No partial comparisons are available.</p>}
    </div>
    {evidence && <section className="shipment-evidence-group" aria-label="Source evidence">
      <h3>Complete source evidence</h3>
      <Evidence name="Eligible scenarios" rows={evidence.scenarios} />
      <Evidence name="Excluded events" rows={evidence.excluded} />
      <Evidence name="Normalization rejected" rows={evidence.normalization_rejected} />
      <Evidence name="Normalization duplicates" rows={evidence.normalization_duplicates} />
    </section>}
    {outcome.comparisons.map(row => <section className="shipment-comparison" key={row.source_scenario_id} aria-label={`Shipment ${row.shipment_id} scenario ${row.source_scenario_id}`}>
      <h3>Shipment {row.shipment_id}</h3>
      <p>Source scenario {row.source_scenario_id} · production result <strong>{String(row.result.status)}</strong></p>
      {row.result.status === 'unsupported' && <p>Numerical comparison unsupported: {String(row.result.reason)}.</p>}
      <div className="shipment-detail-grid">
        <details><summary>Receipt scenario and supplied recovery</summary><pre>{show(row.receipt_scenario)}</pre><pre>{show(row.recovery)}</pre></details>
        <details><summary>Propagation projection and limitations</summary><pre>{show(row.projection)}</pre></details>
        <details><summary>Complete numerical result</summary><pre>{show(row.result)}</pre></details>
      </div>
    </section>)}
    <details className="shipment-raw"><summary>Complete unchanged adapter outcome JSON</summary><pre>{raw}</pre></details>
    <p className="shipment-limit">Local synthetic modeling; source authenticity, actual forecast accuracy, production login, persistence and deployment are unverified. No supplier is contacted.</p>
  </section>;
}

export function ShipmentRecoveryPage() {
  const location = useLocation();
  const routeKey = `${location.key}\u0000${location.search}`;
  const [session, setSession] = useState<SessionResponse | null>(null);
  const [contexts, setContexts] = useState<ShipmentRecoveryContext[]>([]);
  const [selected, setSelected] = useState<ShipmentRecoveryContext | null>(null);
  const [result, setResult] = useState<ShipmentViewOutcome | null>(null);
  const [view, setView] = useState<View>({ kind: 'checking' });
  const [loadedRouteKey, setLoadedRouteKey] = useState('');
  const [provenance, setProvenance] = useState<QuoteShipmentHandoff | null>(null);
  const [handoffUnavailable, setHandoffUnavailable] = useState(false);
  const generation = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const sessionRef = useRef<SessionResponse | null>(null);
  const selectedRef = useRef<ShipmentRecoveryContext | null>(null);
  const routeRef = useRef(routeKey);
  routeRef.current = routeKey;
  const handoffRef = useRef<{ routeKey: string; value: QuoteShipmentHandoff | null } | null>(null);
  function invalidate() {
    generation.current++;
    controller.current?.abort();
    controller.current = null;
    setResult(null);
  }
  async function load() {
    invalidate();
    const current = generation.current;
    const capturedRoute = routeRef.current;
    if (handoffRef.current?.routeKey !== capturedRoute) {
      handoffRef.current = { routeKey: capturedRoute, value: claimHandoff(location.state, location.search) };
    }
    const handoff = handoffRef.current.value;
    const abort = new AbortController(); controller.current = abort;
    sessionRef.current = null; selectedRef.current = null;
    setSession(null); setSelected(null); setContexts([]); setResult(null); setProvenance(null); setHandoffUnavailable(false); setLoadedRouteKey(''); setView({ kind: 'checking' });
    try {
      const identity = await fetchSession(abort.signal);
      if (current !== generation.current || abort.signal.aborted || capturedRoute !== routeRef.current) return;
      sessionRef.current = identity; setSession(identity); setView({ kind: 'loading_contexts' });
      const list = await listShipmentContexts(identity.workspace_id, abort.signal);
      const fresh = await fetchSession(abort.signal);
      if (current !== generation.current || abort.signal.aborted || capturedRoute !== routeRef.current ||
          sessionKey(identity) !== sessionKey(fresh) || sessionKey(identity) !== sessionKey(sessionRef.current!)) {
        if (current === generation.current && !abort.signal.aborted) throw new ShipmentRecoveryError('The local session changed. Refresh the session.', 'session');
        return;
      }
      const triple = parseHandoff(location.search);
      let quoteIdentity = null;
      try { quoteIdentity = normalizeQuoteSession(identity); } catch { /* private-machine catalog remains ordinary, without quote handoff */ }
      const match = quoteIdentity ? selectHandoffContext(list, handoff, triple, quoteIdentity) : null;
      setContexts(list); selectedRef.current = match; setSelected(match);
      if (match && handoff) setProvenance(handoff);
      else handoffRef.current = { routeKey: capturedRoute, value: null };
      setHandoffUnavailable(location.search.length > 0 && !match);
      setLoadedRouteKey(capturedRoute);
      setView({ kind: list.length ? 'ready' : 'empty' });
    } catch (error) {
      if (current !== generation.current || abort.signal.aborted || capturedRoute !== routeRef.current) return;
      handoffRef.current = { routeKey: capturedRoute, value: null }; setProvenance(null);
      const sessionLost = error instanceof ShipmentRecoveryError && error.kind === 'session';
      if (sessionLost) { sessionRef.current = null; setSession(null); setContexts([]); setSelected(null); selectedRef.current = null; }
      setView({ kind: sessionLost ? 'session_error' : 'unavailable', message: error instanceof Error ? error.message : 'Local service unavailable.' });
    } finally { if (current === generation.current) controller.current = null; }
  }
  useEffect(() => { void load(); return () => { generation.current++; controller.current?.abort(); }; }, [routeKey, location.state]);
  useEffect(() => subscribeShipmentForeground(document, () => refreshShipmentOnForeground(() => {
    // A consumed quote handoff is one-use. On return, synchronously discard all
    // identity-bound display before establishing a fresh session/catalog.
    handoffRef.current = { routeKey: routeRef.current, value: null };
    invalidate(); sessionRef.current = null; selectedRef.current = null;
    setSession(null); setContexts([]); setSelected(null); setResult(null); setProvenance(null);
    setHandoffUnavailable(false); setLoadedRouteKey(''); setView({ kind: 'checking' });
  }, () => { void load(); })), [routeKey, location.state]);
  function choose(indexText: string) {
    invalidate();
    handoffRef.current = { routeKey, value: null }; setProvenance(null);
    setHandoffUnavailable(false);
    const i = Number(indexText);
    const next = indexText !== '' && Number.isSafeInteger(i) && i >= 0 ? contexts[i] ?? null : null;
    selectedRef.current = next; setSelected(next); setView({ kind: next ? 'ready' : 'empty' });
  }
  async function compare() {
    if (!sessionRef.current || !selectedRef.current || loadedRouteKey !== routeRef.current) return;
    invalidate();
    const identity = sessionRef.current, context = selectedRef.current;
    const current = generation.current;
    const abort = new AbortController(); controller.current = abort;
    const run: Run = { generation: current, session: identity, contextId: context.context_id, revision: context.revision,
      requestSha256: context.request_sha256, routeKey: routeRef.current, aborted: false };
    setView({ kind: 'comparing' });
    try {
      const value = await runShipmentComparison({
        run, context, signal: abort.signal,
        isCurrent: () => isCurrentShipmentRun(run, generation.current, sessionRef.current, selectedRef.current, abort.signal.aborted, routeRef.current),
        fetchIdentity: fetchSession,
        compare: signal => compareShipmentContext(identity.workspace_id, context, signal),
      });
      if (value === null) return;
      setResult(value); setView({ kind: 'ready' });
    } catch (error) {
      if (!isCurrentShipmentRun(run, generation.current, sessionRef.current, selectedRef.current, abort.signal.aborted, routeRef.current)) return;
      if (error instanceof ShipmentRecoveryError && error.kind === 'session') { void load(); return; }
      if (error instanceof ShipmentRecoveryError && (error.kind === 'stale' || error.kind === 'missing_context')) { void load(); return; }
      setView({ kind: error instanceof ShipmentRecoveryError && error.kind === 'unavailable' ? 'unavailable' : 'error', message: error instanceof Error ? error.message : 'Comparison unavailable.' });
    } finally { if (current === generation.current) controller.current = null; }
  }
  function cancel() { invalidate(); setView({ kind: selectedRef.current ? 'ready' : 'empty' }); }
  return <div className="shell shipment-shell">
    <WorkspaceHeader section="SHIPMENT RECOVERY" status={sessionStatus(session)} />
    <main>
      <div className="shipment-intro"><p className="eyebrow">LOCAL · SYNTHETIC · HOST CONFIGURED · EPHEMERAL</p><h1>Shipment recovery evidence.</h1><p>Compare complete host-configured shipment contexts through the local read-only gateway. Source authenticity not verified. No plan is applied or saved.</p></div>
      <section className="shipment-session" aria-label="Current local session">
        {view.kind === 'checking' && <p role="status">Checking local session…</p>}
        {session && <p>Local {session.identity_mode === 'private_machine' ? 'private machine' : 'development'} identity · workspace {session.workspace_id} · {session.role}. Interactive production sign-in is unavailable.</p>}
      </section>
      {view.kind === 'loading_contexts' && <p className="shipment-status" role="status">Loading host-configured context metadata…</p>}
      {view.kind === 'empty' && <div className="notice" role="status">The enabled local catalog has no shipment contexts. Ask the host operator to configure a synthetic context.</div>}
      {loadedRouteKey === routeKey && handoffUnavailable && <div className="notice" role="status">The quote handoff could not be verified against this session and catalog. No context was selected. Choose a host-configured context explicitly if you want an independent comparison.</div>}
      {(view.kind === 'session_error' || view.kind === 'unavailable' || view.kind === 'error') && <div className="notice notice-error" role="alert"><strong>{view.kind === 'session_error' ? 'Session unavailable' : 'Shipment comparison unavailable'}</strong><p>{view.message}</p><button className="secondary-button" type="button" onClick={() => void load()}>Retry local session and contexts</button></div>}
      {session && loadedRouteKey === routeKey && contexts.length > 0 && <section className="shipment-controls" aria-label="Shipment recovery controls">
        <label className="field"><span className="field-label">Host-configured synthetic context</span><select value={selected ? String(contexts.indexOf(selected)) : ''} onChange={event => choose(event.target.value)}><option value="">Select a context explicitly</option>{contexts.map((row, i) => <option key={`${row.context_id}:${row.revision}:${row.request_sha256}:${i}`} value={String(i)}>{row.context_id} · {row.plan_version} · {row.propagation_snapshot_id} / {row.production_snapshot_id} · revision {row.revision}</option>)}</select></label>
        {selected && <p>Context {selected.context_id} · digest {selected.request_sha256}. Digest identifies host-owned bytes, not source authenticity.</p>}
        {selected && provenance && <p>Quote source checked at {provenance.source_checked_at} for source date {provenance.source_as_of}. Synthetic scenario as of {provenance.context_as_of}. Host configured synthetic binding; source authenticity not verified. This is a read-only association, not a quote-caused shipment or applied plan.</p>}
        {selected && !provenance && <p>Synthetic scenario as of {selected.as_of}. Source authenticity not verified; no current quote eligibility is claimed.</p>}
        <div className="shipment-actions"><button className="primary-button" type="button" onClick={() => void compare()} disabled={!selected || view.kind === 'comparing'}>Compare selected context</button>{view.kind === 'comparing' && <button className="secondary-button" type="button" onClick={cancel}>Cancel comparison</button>}</div>
        {view.kind === 'comparing' && <p role="status">Comparing independent scenarios…</p>}
      </section>}
      {loadedRouteKey === routeKey && result && <ShipmentOutcomeView value={result} />}
    </main>
    <footer>Closed Source / local synthetic shipment recovery <span>No plan is applied or supplier contacted.</span></footer>
  </div>;
}
