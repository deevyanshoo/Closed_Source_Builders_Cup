import { useContext, useEffect, useMemo, useRef, useState } from 'react';
import { Link, NavLink } from 'react-router-dom';
import { HOSTED_RECOVERY_NOTE, HostedDemoContext, readCapabilities } from './dispatch-hosted';
import type { HostedCapabilities } from './dispatch-hosted';
import {
  createDispatchFixtureAdapter, dispatchReport, DispatchError, dispatchFixtureInfo, explainChange, formatInstant, formatLeadTime, formatQuantities, freshness,
  hazardStateLabel, humanize, replayReason, statusLabel,
} from './dispatch';
import type {
  AffectedResponse, DispatchAdapter, EvidenceItem, EvidenceResponse, HazardAssessment, HazardResponse, HazardValue, LeadTime, ListResponse,
  MaterialScreen, ReplayResponse, ReplaySample, ReplaySamplesResponse, ShipmentRow, TimelineEvent, TimelineResponse,
} from './dispatch';
import { ReportDownloads } from './report-downloads';
import './dispatch.css';

type Load<T> = { kind: 'loading' } | { kind: 'error'; message: string } | { kind: 'ready'; value: T };


// Same destinations as the planning header, plus planning and offers; NavLink marks the current page.
const WORKSPACE_PAGES: [string, string][] = [
  ['/planning', 'Planning'], ['/offers', 'Supplier offers'], ['/dispatch', 'Synthetic dispatch workspace'],
  ['/shipment-recovery', 'Shipment recovery evidence'], ['/recovery', 'Synthetic recovery comparison'], ['/scenarios', 'Typed scenarios'],
];

function HostedNav() {
  return <nav className="workspace-nav" aria-label="Workspace pages">
    <a className="recovery-back" href="/demo">Back to the demo</a>
    <NavLink className="recovery-back" to="/dispatch" end>Synthetic dispatch workspace</NavLink>
  </nav>;
}

/** Local workspace link to recovery comparisons; on the hosted demo, a note because that page is not served. */
export function RecoveryLink({ to, note }: { to: string; note: string }) {
  const hosted = useContext(HostedDemoContext);
  return <p className="dispatch-recovery">
    {hosted ? <small>{HOSTED_RECOVERY_NOTE}</small> : <><Link className="secondary-button" to={to}>View recovery options</Link><small>{note}</small></>}
  </p>;
}

function WorkspaceNav() {
  return <nav className="workspace-nav" aria-label="Workspace pages">
    {WORKSPACE_PAGES.map(([to, label]) => <NavLink key={to} className="recovery-back" to={to} end>{label}</NavLink>)}
  </nav>;
}

export function StateNotice({ kind, message, title }: { kind: 'loading' | 'empty' | 'error' | 'stale'; message?: string; title?: string }) {
  if (kind === 'loading') return <p className="dispatch-state" aria-busy="true">Loading dispatch data…</p>;
  if (kind === 'error') return <div className="notice notice-error" role="alert"><strong>{title ?? 'Could not load'}</strong><p>{message}</p></div>;
  if (kind === 'stale') return <div className="notice notice-error dispatch-stale" role="status"><strong>Old data</strong><p>{message}</p></div>;
  return <p className="dispatch-state dispatch-empty">{message ?? 'Nothing to show.'}</p>;
}

function readError(reasons: string[]): string {
  return reasons.length ? `Dispatch data unavailable: ${reasons.map(humanize).join(', ')}.` : 'Dispatch data unavailable.';
}

export function ShipmentList({ response, selected, onSelect }: { response: ListResponse; selected: string | null; onSelect: (id: string) => void }) {
  if (response.status === 'error' || !['ok', 'empty', 'stale'].includes(response.status))
    return <StateNotice kind="error" message={readError(response.reasons)} />;
  if (response.status === 'empty') return <StateNotice kind="empty" message="No shipments with accepted milestones at this cutoff." />;
  if (!response.shipments.length) return <StateNotice kind="error" message="Shipment rows unavailable." />;
  const rows = response.shipments;
  return <>{response.status === 'stale' && <StateNotice kind="stale" message={`Shipment data is dated ${formatInstant(response.as_of)}.`} />}
    <ul className="dispatch-list" aria-label="Shipments">
    {rows.map(row => <li key={row.shipment_id}>
      <button type="button" aria-pressed={selected === row.shipment_id} onClick={() => onSelect(row.shipment_id)}>
        <span className="dispatch-id">{row.shipment_id}</span>
        <span className="dispatch-status" data-status={row.status}><span className="sr-only">Status: </span>{statusLabel(row.status)}</span>
        {row.signals.length > 0 && <small><span className="sr-only">Signals: </span>{row.signals.map(humanize).join(' · ')}</small>}
      </button>
    </li>)}
  </ul></>;
}

function location(event: TimelineEvent): string {
  const parts = [];
  if (event.facility) parts.push(`${event.facility.name ?? event.facility.kind} (${event.facility.code})`);
  if (event.lane) parts.push(`${event.lane.origin} → ${event.lane.destination} by ${event.lane.mode}`);
  return parts.join('; ');
}

function LeadTimeFact({ lead }: { lead: LeadTime }) {
  const view = formatLeadTime(lead);
  return <div className="dispatch-lead-time" data-status={lead.status}>
    <dt>Working-day lead time</dt>
    <dd><strong>{view.headline}</strong><small>{view.detail}</small>{view.source && <small>{view.source}</small>}</dd>
  </div>;
}

export function ShipmentDetail({ row, timeline, onShowAffected }: { row: ShipmentRow; timeline: TimelineResponse; onShowAffected: (eventId: string) => void }) {
  if (timeline.status === 'error' || !['ok', 'empty', 'stale'].includes(timeline.status))
    return <StateNotice kind="error" message={readError(timeline.reasons)} />;
  if (timeline.status === 'empty') return <StateNotice kind="empty" message="No accepted milestones at this cutoff." />;
  const current = timeline.current;
  return <section className="dispatch-detail" aria-label={`Shipment ${row.shipment_id}`}>
    {timeline.status === 'stale' && <StateNotice kind="stale" message={`Milestone data is dated ${formatInstant(timeline.as_of)}.`} />}
    <div className="dispatch-detail-head">
      <h2>{row.shipment_id}</h2>
      <span className="dispatch-status" data-status={row.status}><span className="sr-only">Status: </span>{statusLabel(row.status)}</span>
    </div>
    <dl className="dispatch-facts">
      <div><dt>Expected arrival</dt><dd>{row.expected_arrival ? `${formatInstant(row.expected_arrival.at)} (${row.expected_arrival.basis === 'eta' ? 'stated ETA' : 'actual arrival'})` : 'No usable ETA'}</dd></div>
      <div><dt>Plan baseline</dt><dd>{row.baseline_arrival_at ? formatInstant(row.baseline_arrival_at) : 'No plan baseline'}</dd></div>
      <div><dt>Delivered so far</dt><dd>{formatQuantities(current?.delivered_quantity ?? row.delivered_quantity)}</dd></div>
      <div><dt>On hold</dt><dd>{(current?.on_hold ?? row.on_hold) ? 'Yes' : 'No'}</dd></div>
      {timeline.lead_time && <LeadTimeFact lead={timeline.lead_time} />}
    </dl>
    <RecoveryLink to="/shipment-recovery" note="Opens the existing shipment recovery comparisons. It does not preselect this shipment yet." />
    <h3>Milestones</h3>
    {timeline.events.length === 0
      ? <StateNotice kind="empty" message={`No accepted milestones (${timeline.reasons.map(humanize).join(', ') || 'none'}).`} />
      : <ol className="dispatch-timeline">
        {timeline.events.map(event => <li key={event.event_id} data-usable={event.operating_evidence}>
          <div className="dispatch-event-head"><strong>{humanize(event.event_type)}</strong><code>{event.event_id}</code></div>
          <p>Occurred {formatInstant(event.occurred_at, event.occurred_at_offset)} · observed {formatInstant(event.observed_at)}</p>
          {event.eta && <p>Stated ETA {formatInstant(event.eta)}</p>}
          {event.quantity && <p>Quantity {event.quantity.amount} {event.quantity.unit}</p>}
          <p>{location(event)} · source {event.source.provider_id} ({humanize(event.source.kind)})</p>
          <p className="dispatch-evidence">Evidence: {event.evidence.map(e => `${e.evidence_id} (${humanize(e.status)})`).join(', ')}</p>
          {event.flags.length > 0 && <p className="dispatch-flags">{event.flags.map(humanize).join(' · ')}</p>}
          {event.operating_evidence
            ? <button type="button" className="text-button" onClick={() => onShowAffected(event.event_id)}>Show affected shipments</button>
            : <small>Evidence expired: not used to find affected shipments.</small>}
        </li>)}
      </ol>}
    {timeline.rejected_records.length > 0 && <div className="dispatch-rejected">
      <h3>Rejected records</h3>
      <ul>{timeline.rejected_records.map(r => <li key={r.record_index}>Record {r.record_index}{r.event_id ? ` (${r.event_id})` : ''}: {humanize(r.reason)}</li>)}</ul>
    </div>}
  </section>;
}

function EvidenceCard({ item }: { item: EvidenceItem }) {
  return <li className="dispatch-evidence-card">
    <div className="dispatch-event-head">
      <strong>{item.summary ?? humanize(item.kind)}</strong>
      <span className="dispatch-chip" data-kind={item.status}><span className="sr-only">Evidence status: </span>{item.status === 'confirmed' ? `confirmed: ${item.operating_status}` : 'exposure'}</span>
    </div>
    <dl>
      <div><dt>Source</dt><dd>{item.source.provider_id} ({humanize(item.source.kind)}){item.source.reference ? `, ${item.source.reference}` : ''}</dd></div>
      <div><dt>Retrieved</dt><dd>{formatInstant(item.retrieved_at)}</dd></div>
      <div><dt>Observed</dt><dd>{formatInstant(item.observed_period.start)} to {item.observed_period.end ? formatInstant(item.observed_period.end) : 'open'}</dd></div>
      <div><dt>Expiry</dt><dd>{item.valid_until ? `${formatInstant(item.valid_until)} (${humanize(item.expiry)})` : humanize(item.expiry)}</dd></div>
    </dl>
  </li>;
}

export function RouteEvidencePanel({ evidence }: { evidence: EvidenceResponse }) {
  if (evidence.status === 'error' || !['ok', 'empty', 'stale'].includes(evidence.status))
    return <StateNotice kind="error" message={readError(evidence.reasons)} />;
  const sections: [keyof Pick<EvidenceResponse, 'confirmed' | 'exposure' | 'expired' | 'outside_period'>, string, string][] = [
    ['confirmed', 'Confirmed operating status', 'Asserted by a port authority, carrier or facility operator.'],
    ['exposure', 'Exposure only', 'A hazard or condition nearby. It does not prove a closure or delay.'],
    ['expired', 'Expired evidence', 'Past its validity; shown for history only.'],
    ['outside_period', 'Outside observed period', 'Not describing the current period.'],
  ];
  if (evidence.status === 'empty')
    return <StateNotice kind="empty" message="No route evidence for this shipment's facilities or lanes." />;
  if (sections.every(([key]) => evidence[key].length === 0))
    return <StateNotice kind="error" message="Route evidence entries unavailable." />;
  return <div className="dispatch-evidence-panel">
    {evidence.status === 'stale' && <StateNotice kind="stale" message={`Route evidence is dated ${formatInstant(evidence.as_of)}.`} />}
    {sections.filter(([key]) => evidence[key].length).map(([key, title, help]) => <section key={key} aria-label={title}>
      <h4>{title}</h4><p className="field-hint">{help}</p>
      <ul>{evidence[key].map(item => <EvidenceCard key={item.evidence_id} item={item} />)}</ul>
    </section>)}
  </div>;
}

export function AffectedPanel({ affected }: { affected: AffectedResponse }) {
  if (affected.status === 'error' || !['ok', 'empty', 'stale'].includes(affected.status))
    return <StateNotice kind="error" message={readError(affected.reasons)} />;
  return <section className="dispatch-affected" aria-label="Affected shipments">
    {affected.status === 'stale' && <StateNotice kind="stale" message={`Affected-shipment data is dated ${formatInstant(dispatchFixtureInfo.asOf)}.`} />}
    <h3>Shipments sharing a location with {affected.event_id}</h3>
    <p className="dispatch-note">{affected.note}</p>
    {affected.status === 'empty'
      ? <StateNotice kind="empty" message="No other shipments share this event's facility or lane." />
      : affected.affected.length === 0 ? <StateNotice kind="error" message="Affected-shipment rows unavailable." />
      : <ul>{affected.affected.map(a => <li key={a.shipment_id}>
        <strong>{a.shipment_id}</strong> {a.relation === 'self' ? '(this shipment)' : ''}
        <span className="dispatch-chip" data-kind="exposure">exposure</span>
        <small>{a.basis.map(humanize).join(', ')} · last milestone {humanize(a.latest_event_type)}</small>
      </li>)}</ul>}
    {affected.excluded.length > 0 && <p className="field-hint">Excluded: {affected.excluded.map(e => `${e.shipment_id} (${humanize(e.reason)})`).join(', ')}</p>}
  </section>;
}

export function ReplaySamplePicker({ samples, selected, onSelect }: { samples: ReplaySample[]; selected: string | null; onSelect: (id: string) => void }) {
  return <ul className="dispatch-replay-samples" aria-label="Sample events">
    {samples.map(sample => <li key={sample.sample_id}>
      <button type="button" aria-pressed={selected === sample.sample_id} onClick={() => onSelect(sample.sample_id)}>
        <strong>{sample.title}</strong>
        <span className="dispatch-chip" data-kind="synthetic">synthetic</span>
        <small>{sample.description}</small>
      </button>
    </li>)}
  </ul>;
}

function MaterialScreenView({ screen }: { screen: MaterialScreen | null }) {
  if (!screen) return null;
  if (screen.status === 'not_run') return <p className="field-hint">{replayReason(screen.reason)}</p>;
  if (screen.status === 'unsupported') return <p className="field-hint">The propagation screen could not use this change: {humanize(screen.reason)}.</p>;
  return <div className="dispatch-material">
    <ul>{screen.arrival_changes.map(c => <li key={c.line_id}>
      <strong>{c.line_id}</strong> {c.quantity} {c.unit} of {c.component_id}: plan {formatInstant(c.baseline_at)} → {formatInstant(c.changed_at)}
    </li>)}</ul>
    {screen.affected_commitments.map(c => <p key={c.commitment_id}>
      {c.commitment_id} ({c.production_order_id}), needed {formatInstant(c.required_at)}: {c.material_at_risk ? 'material shortfall at the requirement time' : 'no material shortfall at the requirement time'}
      {c.demands.map(d => ` · ${d.component_id} shortfall ${d.baseline.shortfall} → ${d.changed.shortfall} ${d.unit} (${humanize(d.comparison)})`).join('')}
    </p>)}
    <p className="field-hint">{[...screen.assumptions, ...screen.limitations].join(' ')}</p>
  </div>;
}

export function ReplayResult({ result }: { result: ReplayResponse }) {
  if (result.status === 'error') {
    const why = result.reasons.map(replayReason).join(' ');
    const detail = result.rejection ? ` (${humanize(result.rejection.reason)})` : '';
    return <StateNotice kind="error" title="Event rejected" message={`${why}${detail}`} />;
  }
  const timeline = result.timeline;
  return <div className="dispatch-replay-result">
    {result.injected && <p className="dispatch-note">{result.injected.label}: {humanize(result.injected.event_type)} for {result.injected.shipment_id} ({result.injected.event_id}).</p>}
    {result.status === 'stale' && <StateNotice kind="stale" message="The data cutoff is older than a day. The replay describes that cutoff, not now." />}
    <h3>What changed</h3>
    {result.changes.length === 0
      ? <StateNotice kind="empty" message={replayReason('no_changes')} />
      : <ul className="dispatch-changes">{result.changes.map(change => <li key={change.shipment_id}>
        <div className="dispatch-event-head">
          <strong>{change.shipment_id}</strong>
          <span>
            <span className="sr-only">Status before: </span>{change.before && <span className="dispatch-status" data-status={change.before.status}>{statusLabel(change.before.status)}</span>}
            {' → '}
            <span className="sr-only"> after: </span>{change.after && <span className="dispatch-status" data-status={change.after.status}>{statusLabel(change.after.status)}</span>}
          </span>
        </div>
        <ul>{explainChange(change).map(line => <li key={line}>{line}</li>)}</ul>
      </li>)}</ul>}
    {timeline && <>
      <h3>Milestones for {timeline.shipment_id}, after the replay</h3>
      <ol className="dispatch-timeline">{timeline.after.map(event => <li key={event.event_id} data-injected={event.replay_injected}>
        <div className="dispatch-event-head"><strong>{humanize(event.event_type)}</strong>{event.replay_injected ? <span className="dispatch-chip" data-kind="synthetic">Injected (synthetic)</span> : <code>{event.event_id}</code>}</div>
        <p>Occurred {formatInstant(event.occurred_at, event.occurred_at_offset)}{event.eta ? ` · stated ETA ${formatInstant(event.eta)}` : ''}</p>
      </li>)}</ol>
    </>}
    {result.affected.after && <AffectedPanel affected={result.affected.after} />}
    {result.route_evidence && <section className="dispatch-replay-route" aria-label="Route evidence at the event location">
      <h3>Route evidence at {result.route_evidence.location_keys.join(', ')}</h3>
      <p className="field-hint">{result.route_evidence.note}</p>
      <p>Confirmed: {result.route_evidence.confirmed.length ? result.route_evidence.confirmed.map(e => `${e.evidence_id} (${e.operating_status})`).join(', ') : 'none'}</p>
      <p>Exposure: {result.route_evidence.exposure.length ? result.route_evidence.exposure.map(e => `${e.evidence_id} (${humanize(e.kind)})`).join(', ') : 'none'}</p>
    </section>}
    <h3>Material screen</h3>
    <MaterialScreenView screen={result.material_screen} />
    <RecoveryLink to={result.recovery_link} note={result.note} />
  </div>;
}

function ReplaySection({ adapter }: { adapter: DispatchAdapter }) {
  const [samples, setSamples] = useState<Load<ReplaySamplesResponse>>({ kind: 'loading' });
  const [selected, setSelected] = useState<string | null>(null);
  const [result, setResult] = useState<Load<ReplayResponse> | null>(null);
  const serial = useRef(0);

  useEffect(() => {
    adapter.replaySamples()
      .then(value => setSamples({ kind: 'ready', value }))
      .catch(error => setSamples({ kind: 'error', message: error instanceof Error ? error.message : 'Samples unavailable.' }));
  }, [adapter]);

  function run(id: string) {
    const mine = ++serial.current;
    setSelected(id);
    setResult({ kind: 'loading' });
    adapter.replay(id)
      .then(value => { if (mine === serial.current) setResult({ kind: 'ready', value }); })
      .catch(error => { if (mine === serial.current) setResult({ kind: 'error', message: error instanceof Error ? error.message : 'Replay unavailable.' }); });
  }

  return <section className="dispatch-replay" aria-label="Replay a sample delay">
    <h2>Replay a sample delay</h2>
    <p className="field-hint">Inject one synthetic event over the same fixtures and compare before and after. Nothing is saved, and recovery is not computed here.</p>
    {samples.kind === 'loading' && <StateNotice kind="loading" />}
    {samples.kind === 'error' && <StateNotice kind="error" message={samples.message} />}
    {samples.kind === 'ready' && <>
      {samples.value.samples.length === 0
        ? <StateNotice kind="empty" message="No sample events." />
        : <ReplaySamplePicker samples={samples.value.samples} selected={selected} onSelect={run} />}
      <p className="field-hint">{samples.value.notice}</p>
    </>}
    {result?.kind === 'loading' && <StateNotice kind="loading" />}
    {result?.kind === 'error' && <StateNotice kind="error" message={result.message} />}
    {result?.kind === 'ready' && <ReplayResult result={result.value} />}
  </section>;
}

function hazardValue(v: HazardValue): string {
  if (v.rule_id === 'gdacs_alert_within_radius')
    return `${v.alert_level} ${v.event_type} alert ${v.event_id}, ${v.distance_km} km away${v.period ? ` (${formatInstant(v.period.start)} to ${formatInstant(v.period.end)})` : ''}`;
  const value = v.value == null ? 'not available' : `${v.value} ${v.unit}`;
  return `${humanize(v.rule_id)}: ${value}${v.at ? ` at ${formatInstant(v.at)}` : ''}`;
}

function ruleOutcome(v: HazardValue): string {
  if (v.met) return 'threshold met';
  if (v.met === false) return v.reason ? humanize(v.reason) : 'below threshold';
  return v.reason ? `undecided: ${humanize(v.reason)}` : 'undecided';
}

function HazardCard({ item }: { item: HazardAssessment }) {
  const stale = item.freshness === 'stale';
  return <li className="dispatch-hazard-card" data-state={item.state} data-usable={item.usable}>
    <div className="dispatch-event-head">
      <strong>{item.provider_id} · {item.location.name ?? item.location.code} ({item.location.code})</strong>
      <span className="dispatch-chip" data-kind={item.state === 'exposure_found' ? 'exposure' : 'neutral'}><span className="sr-only">Assessment: </span>{hazardStateLabel(item.state)}</span>
    </div>
    {item.reason && item.state !== 'exposure_found' && <p className="field-hint">Reason: {humanize(item.reason)}</p>}
    {stale && <p className="dispatch-note">Stale: retrieved too long before the cutoff. Not usable for planning.</p>}
    {!item.usable && !stale && item.state !== 'unsupported_location' && <p className="field-hint">Not usable for planning.</p>}
    <dl>
      <div><dt>Retrieved</dt><dd>{formatInstant(item.retrieved_at)}</dd></div>
      <div><dt>Period</dt><dd>{item.period ? `${formatInstant(item.period.start)} to ${formatInstant(item.period.end)}` : item.values.some(v => v.period) ? 'Per event below' : 'Not available'}</dd></div>
      <div><dt>Valid until</dt><dd>{formatInstant(item.valid_until)}</dd></div>
      <div><dt>Thresholds</dt><dd>{item.thresholds_version}</dd></div>
    </dl>
    {item.values.length > 0 && <table className="dispatch-hazard-values">
      <caption className="field-hint">Measured or reported values against the stated rule</caption>
      <thead><tr><th scope="col">Value</th><th scope="col">Threshold</th><th scope="col">Outcome</th></tr></thead>
      <tbody>{item.values.map((v, i) => <tr key={`${v.rule_id}:${v.event_id ?? i}`}>
        <td>{hazardValue(v)}</td><td>{v.rule_id === 'gdacs_alert_within_radius' ? v.threshold : `${v.threshold} ${v.unit}`}</td><td>{ruleOutcome(v)}</td>
      </tr>)}</tbody>
    </table>}
  </li>;
}

export function HazardPanel({ hazards }: { hazards: HazardResponse }) {
  return <section className="dispatch-hazards" aria-label="Hazard and weather exposure">
    <h4>Hazard and weather exposure <span className="dispatch-chip" data-kind="exposure">Exposure, not confirmed</span></h4>
    <p className="field-hint">{hazards.note}</p>
    {hazards.status === 'stale' && <StateNotice kind="stale" message="These provider answers describe an old cutoff." />}
    {hazards.assessments.length === 0
      ? <StateNotice kind="empty" message="No hazard or weather assessments for this shipment." />
      : <ul>{hazards.assessments.map(a => <HazardCard key={`${a.provider_id}:${a.location.code}:${a.retrieved_at}`} item={a} />)}</ul>}
    {dispatchFixtureInfo.hazardNotice && <p className="field-hint">{dispatchFixtureInfo.hazardNotice}</p>}
  </section>;
}

export function DispatchPage({ capabilities: given }: { capabilities?: HostedCapabilities | null } = {}) {
  // Hosted navigation comes from the server's capabilities, never from the URL (review, 16 Oct).
  const [fetched, setFetched] = useState<HostedCapabilities | null>(null);
  useEffect(() => { if (given === undefined) readCapabilities().then(setFetched); }, [given]);
  const capabilities = given === undefined ? fetched : given;
  const hostedDemo = !!capabilities?.hosted && capabilities.served_pages.includes('/dispatch');
  const adapter = useMemo(() => createDispatchFixtureAdapter(), []);
  const [list, setList] = useState<Load<ListResponse>>({ kind: 'loading' });
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<Load<{ timeline: TimelineResponse; evidence: EvidenceResponse }> | null>(null);
  const [affected, setAffected] = useState<Load<AffectedResponse> | null>(null);
  const [hazards, setHazards] = useState<Load<HazardResponse> | null>(null);
  const serial = useRef(0);
  const age = freshness(dispatchFixtureInfo.asOf, new Date());

  useEffect(() => {
    adapter.listShipments()
      .then(value => { setList({ kind: 'ready', value }); if ((value.status === 'ok' || value.status === 'stale') && value.shipments[0]) select(value.shipments[0].shipment_id); })
      .catch(error => setList({ kind: 'error', message: error instanceof Error ? error.message : 'Shipments unavailable.' }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [adapter]);

  function select(id: string) {
    const mine = ++serial.current;
    setSelected(id);
    setAffected(null);
    setDetail({ kind: 'loading' });
    setHazards({ kind: 'loading' });
    adapter.hazards(id)
      .then(value => { if (mine === serial.current) setHazards({ kind: 'ready', value }); })
      .catch(error => { if (mine === serial.current) setHazards({ kind: 'error', message: error instanceof Error ? error.message : 'Hazard exposure unavailable.' }); });
    Promise.all([adapter.timeline(id), adapter.routeEvidence(id)])
      .then(([timeline, evidence]) => { if (mine === serial.current) setDetail({ kind: 'ready', value: { timeline, evidence } }); })
      .catch(error => { if (mine === serial.current) setDetail({ kind: 'error', message: error instanceof Error ? error.message : 'Shipment unavailable.' }); });
  }

  function showAffected(eventId: string) {
    const mine = serial.current;
    setAffected({ kind: 'loading' });
    adapter.affected(eventId)
      .then(value => { if (mine === serial.current) setAffected({ kind: 'ready', value }); })
      .catch(error => { if (mine === serial.current) setAffected({ kind: 'error', message: error instanceof DispatchError ? error.message : 'Affected shipments unavailable.' }); });
  }

  const rows = list.kind === 'ready' ? list.value.shipments : [];
  const row = rows.find(r => r.shipment_id === selected) ?? null;
  const hours = Math.floor(age.ageSeconds / 3600);

  return <HostedDemoContext.Provider value={hostedDemo}><main className="shell dispatch-shell">
    <header className="masthead workspace-masthead">
      <div className="brand">CLOSED<span>SOURCE</span><sup>DISPATCH</sup></div>
      <div className="masthead-meta">Synthetic · local adapter<span className="red-dot" aria-hidden="true">●</span></div>
      {hostedDemo ? <HostedNav /> : <WorkspaceNav />}
    </header>
    <section className="recovery-intro">
      <p className="eyebrow">Dispatch workspace · issue 5</p>
      <h1>Shipments, milestones and route evidence</h1>
      <p>Status comes from evidence: delayed and on time compare with the plan baseline, at risk needs current route evidence, and unknown means neither is available. Nearby hazards are exposure, not confirmed delays.</p>
    </section>
    <div className="dispatch-adapter" role="note">
      <strong>SYNTHETIC FIXTURE DATA · LOCAL ADAPTER · NOT CONNECTED TO THE GATEWAY</strong>
      <span>{dispatchFixtureInfo.notice} Cutoff {formatInstant(dispatchFixtureInfo.asOf)}, exported at revision {dispatchFixtureInfo.revision}. Read only.</span>
    </div>
    {(age.stale || (list.kind === 'ready' && list.value.status === 'stale')) &&
      <StateNotice kind="stale" message={`Data cutoff is ${hours} hours old. Statuses describe that cutoff, not now.`} />}

    <div className="dispatch-grid">
      <aside className="dispatch-column">
        <h2>Shipments</h2>
        {list.kind === 'loading' && <StateNotice kind="loading" />}
        {list.kind === 'error' && <StateNotice kind="error" message={list.message} />}
        {list.kind === 'ready' && <ShipmentList response={list.value} selected={selected} onSelect={select} />}
      </aside>
      <div className="dispatch-column dispatch-main">
        {!selected && list.kind === 'ready' && list.value.status !== 'empty' && <StateNotice kind="empty" message="Select a shipment." />}
        {detail?.kind === 'loading' && <StateNotice kind="loading" />}
        {detail?.kind === 'error' && <StateNotice kind="error" message={detail.message} />}
        {detail?.kind === 'ready' && row && <>
          <ShipmentDetail row={row} timeline={detail.value.timeline} onShowAffected={showAffected} />
          {affected?.kind === 'loading' && <StateNotice kind="loading" />}
          {affected?.kind === 'error' && <StateNotice kind="error" message={affected.message} />}
          {affected?.kind === 'ready' && <AffectedPanel affected={affected.value} />}
          <section className="dispatch-route" aria-label="Route evidence">
            <h3>Route evidence</h3>
            <RouteEvidencePanel evidence={detail.value.evidence} />
            {hazards?.kind === 'loading' && <StateNotice kind="loading" />}
            {hazards?.kind === 'error' && <StateNotice kind="error" message={hazards.message} />}
            {hazards?.kind === 'ready' && <HazardPanel hazards={hazards.value} />}
          </section>
          <section className="dispatch-export" aria-label="Export this shipment">
            <h3>Export</h3>
            <ReportDownloads label={`shipment ${row.shipment_id}`} build={() => dispatchReport({ row, timeline: detail.value.timeline,
              evidence: detail.value.evidence, hazards: hazards?.kind === 'ready' ? hazards.value : null,
              hazard_state: hazards?.kind === 'loading' || hazards?.kind === 'error' ? hazards : undefined }, new Date())} />
          </section>
        </>}
      </div>
    </div>
    <ReplaySection adapter={adapter} />
    <footer><span>Synthetic shipments, ports and hazards only</span><span>Read only · no supplier messages or plan changes</span></footer>
  </main></HostedDemoContext.Provider>;
}
