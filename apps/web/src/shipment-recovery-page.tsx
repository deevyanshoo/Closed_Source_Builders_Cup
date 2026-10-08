import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import type { SessionResponse, ShipmentRecoveryContext } from '../../../contracts/generated/types';
import { fetchSession } from './state';
import { compareShipmentContext, listShipmentContexts, ShipmentRecoveryError } from './shipment-recovery';
import type { ShipmentViewOutcome } from './shipment-recovery';
import './shipment-recovery.css';

type Run = { generation: number; session: SessionResponse; contextId: string; revision: number; aborted: boolean };
type View = { kind: 'checking' | 'loading_contexts' | 'empty' | 'ready' | 'comparing' | 'session_error' | 'unavailable' | 'error'; message?: string };
const sessionKey = (s: SessionResponse) => [s.principal_id, s.workspace_id, s.role, s.identity_mode].join('\u0000');
export function isCurrentShipmentRun(run: Run, generation: number, session: SessionResponse | null, context: ShipmentRecoveryContext | null, aborted: boolean): boolean {
  return !run.aborted && !aborted && run.generation === generation && !!session && sessionKey(run.session) === sessionKey(session) &&
    !!context && run.contextId === context.context_id && run.revision === context.revision;
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
  const [session, setSession] = useState<SessionResponse | null>(null);
  const [contexts, setContexts] = useState<ShipmentRecoveryContext[]>([]);
  const [selected, setSelected] = useState<ShipmentRecoveryContext | null>(null);
  const [result, setResult] = useState<ShipmentViewOutcome | null>(null);
  const [view, setView] = useState<View>({ kind: 'checking' });
  const generation = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const sessionRef = useRef<SessionResponse | null>(null);
  const selectedRef = useRef<ShipmentRecoveryContext | null>(null);
  function invalidate() {
    generation.current++;
    controller.current?.abort();
    controller.current = null;
    setResult(null);
  }
  async function load() {
    invalidate();
    const current = generation.current;
    const abort = new AbortController(); controller.current = abort;
    sessionRef.current = null; selectedRef.current = null;
    setSession(null); setSelected(null); setContexts([]); setView({ kind: 'checking' });
    try {
      const identity = await fetchSession(abort.signal);
      if (current !== generation.current || abort.signal.aborted) return;
      sessionRef.current = identity; setSession(identity); setView({ kind: 'loading_contexts' });
      const list = await listShipmentContexts(identity.workspace_id, abort.signal);
      if (current !== generation.current || abort.signal.aborted || sessionKey(identity) !== sessionKey(sessionRef.current!)) return;
      setContexts(list); selectedRef.current = list[0] ?? null; setSelected(list[0] ?? null);
      setView({ kind: list.length ? 'ready' : 'empty' });
    } catch (error) {
      if (current !== generation.current || abort.signal.aborted) return;
      const sessionLost = error instanceof ShipmentRecoveryError && error.kind === 'session';
      if (sessionLost) { sessionRef.current = null; setSession(null); setContexts([]); setSelected(null); selectedRef.current = null; }
      setView({ kind: sessionLost ? 'session_error' : 'unavailable', message: error instanceof Error ? error.message : 'Local service unavailable.' });
    } finally { if (current === generation.current) controller.current = null; }
  }
  useEffect(() => { void load(); return () => { generation.current++; controller.current?.abort(); }; }, []);
  function choose(id: string) {
    invalidate();
    const next = contexts.find(item => item.context_id === id) ?? null;
    selectedRef.current = next; setSelected(next); setView({ kind: next ? 'ready' : 'empty' });
  }
  async function compare() {
    if (!sessionRef.current || !selectedRef.current) return;
    invalidate();
    const identity = sessionRef.current, context = selectedRef.current;
    const current = generation.current;
    const abort = new AbortController(); controller.current = abort;
    const run: Run = { generation: current, session: identity, contextId: context.context_id, revision: context.revision, aborted: false };
    setView({ kind: 'comparing' });
    try {
      const value = await compareShipmentContext(identity.workspace_id, context, abort.signal);
      if (!isCurrentShipmentRun(run, generation.current, sessionRef.current, selectedRef.current, abort.signal.aborted)) return;
      setResult(value); setView({ kind: 'ready' });
    } catch (error) {
      if (!isCurrentShipmentRun(run, generation.current, sessionRef.current, selectedRef.current, abort.signal.aborted)) return;
      if (error instanceof ShipmentRecoveryError && error.kind === 'session') { void load(); return; }
      if (error instanceof ShipmentRecoveryError && (error.kind === 'stale' || error.kind === 'missing_context')) { void load(); return; }
      setView({ kind: error instanceof ShipmentRecoveryError && error.kind === 'unavailable' ? 'unavailable' : 'error', message: error instanceof Error ? error.message : 'Comparison unavailable.' });
    } finally { if (current === generation.current) controller.current = null; }
  }
  function cancel() { invalidate(); setView({ kind: selectedRef.current ? 'ready' : 'empty' }); }
  return <div className="shell shipment-shell">
    <header className="masthead"><div className="brand">CLOSED <span>SOURCE</span><sup> / SHIPMENT RECOVERY</sup></div><Link className="recovery-back" to="/planning">Planning workspace</Link><Link className="recovery-back" to="/recovery">Receipt comparison</Link></header>
    <main>
      <div className="shipment-intro"><p className="eyebrow">LOCAL · SYNTHETIC · HOST CONFIGURED · EPHEMERAL</p><h1>Shipment recovery evidence.</h1><p>Compare complete host-configured shipment contexts through the local read-only gateway. Source authenticity not verified. No plan is applied or saved.</p></div>
      <section className="shipment-session" aria-label="Current local session">
        {view.kind === 'checking' && <p role="status">Checking local session…</p>}
        {session && <p>Local {session.identity_mode === 'private_machine' ? 'private machine' : 'development'} identity · workspace {session.workspace_id} · {session.role}. Interactive production sign-in is unavailable.</p>}
      </section>
      {view.kind === 'loading_contexts' && <p className="shipment-status" role="status">Loading host-configured context metadata…</p>}
      {view.kind === 'empty' && <div className="notice" role="status">The enabled local catalog has no shipment contexts. Ask the host operator to configure a synthetic context.</div>}
      {(view.kind === 'session_error' || view.kind === 'unavailable' || view.kind === 'error') && <div className="notice notice-error" role="alert"><strong>{view.kind === 'session_error' ? 'Session unavailable' : 'Shipment comparison unavailable'}</strong><p>{view.message}</p><button className="secondary-button" type="button" onClick={() => void load()}>Retry local session and contexts</button></div>}
      {session && contexts.length > 0 && <section className="shipment-controls" aria-label="Shipment recovery controls">
        <label className="field"><span className="field-label">Host-configured synthetic context</span><select value={selected?.context_id ?? ''} onChange={event => choose(event.target.value)}>{contexts.map(row => <option key={row.context_id} value={row.context_id}>{row.context_id} · {row.plan_version} · {row.propagation_snapshot_id} / {row.production_snapshot_id} · revision {row.revision}</option>)}</select></label>
        {selected && <p>Context {selected.context_id} · digest {selected.request_sha256}. Digest identifies host-owned bytes, not source authenticity.</p>}
        <div className="shipment-actions"><button className="primary-button" type="button" onClick={() => void compare()} disabled={!selected || view.kind === 'comparing'}>Compare selected context</button>{view.kind === 'comparing' && <button className="secondary-button" type="button" onClick={cancel}>Cancel comparison</button>}</div>
        {view.kind === 'comparing' && <p role="status">Comparing independent scenarios…</p>}
      </section>}
      {result && <ShipmentOutcomeView value={result} />}
    </main>
    <footer>Closed Source / local synthetic shipment recovery <span>No plan is applied or supplier contacted.</span></footer>
  </div>;
}
