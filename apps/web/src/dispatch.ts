// Dispatch workspace data layer.
//
// Until the gateway serves dispatch routes, the page uses a LOCAL FIXTURE ADAPTER: the output of the Python
// dispatch read models over the labelled synthetic shipment, snapshot and route-evidence fixtures
// (procurement-engine/scripts/export_dispatch_fixtures_for_web.py), at a fixed cutoff. Responses have the same
// shape as the read-only Python /dispatch routes. Quantities stay decimal strings; nothing here is live data.
import raw from './dispatch-fixtures.json?raw';

export type ReadStatus = 'ok' | 'empty' | 'stale' | 'error';
export type ShipmentStatus = 'delivered' | 'on_hold' | 'delayed' | 'at_risk' | 'on_time' | 'unknown';
export type Facility = { kind: string; code: string; name?: string };
export type Lane = { origin: string; destination: string; mode: string };
export type Expected = { at: string; basis: 'eta' | 'actual_arrival'; event_id: string };
export type ShipmentRow = {
  shipment_id: string; status: ShipmentStatus; signals: string[]; baseline_arrival_at: string | null;
  expected_arrival: Expected | null; last_event_type: string | null; on_hold: boolean;
  delivered_quantity: Record<string, string>; location_keys: string[];
};
export type ListResponse = { status: ReadStatus; reasons: string[]; as_of: string | null; shipments: ShipmentRow[]; note: string };
export type TimelineEvent = {
  event_id: string; event_type: string; raw_type: string; occurred_at: string; occurred_at_offset: string;
  observed_at: string; eta: string | null; quantity: { amount: string; unit: string } | null;
  facility: Facility | null; lane: Lane | null; source: { provider_id: string; kind: string; reference: string | null };
  evidence: { evidence_id: string; kind: string; locator: string; status: string }[];
  operating_evidence: boolean; flags: string[];
};
export type TimelineCurrent = {
  last_event_type: string | null; open_eta: { eta: string; event_id: string } | null; on_hold: boolean;
  delivered_quantity: Record<string, string>; unusable_event_ids: string[];
};
export type LeadTime = {
  status: 'ok' | 'not_available' | 'unsupported_country' | 'unsupported_year' | 'source_unavailable' | 'invalid_range';
  reason: string | null; basis: 'departure_to_arrival' | 'cutoff_to_expected_arrival' | null; country: string | null;
  location: string | null; start: string | null; end: string | null; working_days: number | null;
  calendar_days: number | null; weekend_days: number | null; holidays: { date: string; name: string }[];
  not_applied: { date: string; name: string; reason: string }[]; synthetic: boolean | null;
  source: { provider_id: string; kind: string } | null; convention: string; note: string;
};
export type TimelineResponse = {
  status: ReadStatus; reasons: string[]; as_of: string | null; shipment_id: string; events: TimelineEvent[];
  current: TimelineCurrent | null; flags: string[]; rejected_records: { record_index: number; event_id: string | null; reason: string }[];
  lead_time?: LeadTime;
};
export type AffectedRow = { shipment_id: string; relation: 'self' | 'shares_location'; basis: string[]; latest_event_type: string; status: 'exposure' };
export type AffectedResponse = {
  status: ReadStatus; reasons: string[]; event_id: string; trigger_shipment_id: string | null; location_keys: string[];
  affected: AffectedRow[]; excluded: { shipment_id: string; reason: string }[]; note: string;
};
export type EvidenceItem = {
  evidence_id: string; kind: string; summary: string | null; location_keys: string[];
  source: { provider_id: string; kind: string; reference: string | null }; retrieved_at: string;
  observed_period: { start: string; end: string | null }; status: 'exposure' | 'confirmed'; operating_status: string | null;
  valid_until: string | null; expiry: string; period_state?: string; synthetic: boolean;
};
export type EvidenceResponse = {
  status: ReadStatus; reasons: string[]; as_of: string | null; location_keys: string[];
  confirmed: EvidenceItem[]; exposure: EvidenceItem[]; expired: EvidenceItem[]; outside_period: EvidenceItem[];
};
export type ReplaySample = {
  sample_id: string; title: string; description: string; event_id: string; shipment_id: string; event_type: string; synthetic: true;
};
export type ReplaySamplesResponse = { notice: string; samples: ReplaySample[] };
export type ReplayChange = {
  shipment_id: string; before: ShipmentRow | null; after: ShipmentRow | null; changed: string[];
  signals_added: string[]; signals_removed: string[];
};
export type MaterialScreen =
  | { status: 'not_run'; reason: string; event_id: string }
  | { status: 'unsupported'; reason: string; details: string[] }
  | {
    status: 'screened'; synthetic: true; event_id: string; scenario_id: string; as_of: string;
    arrival_changes: { line_id: string; component_id: string; quantity: string; unit: string; baseline_at: string; changed_at: string }[];
    affected_commitments: {
      commitment_id: string; production_order_id: string; required_at: string; due_at: string; material_at_risk: boolean;
      demands: { component_id: string; unit: string; quantity: string; comparison: string;
        baseline: { shortfall: string }; changed: { shortfall: string } }[];
    }[];
    limitations: string[]; assumptions: string[];
  };
export type ReplayResponse = {
  status: ReadStatus; reasons: string[]; as_of: string | null; synthetic: true; sample: ReplaySample;
  injected: { event_id: string; shipment_id: string; event_type: string; synthetic: true; label: string } | null;
  rejection: { reason: string; details: string[] } | null;
  shipments: { before: ShipmentRow[]; after: ShipmentRow[] }; changes: ReplayChange[];
  timeline: { shipment_id: string; before: TimelineEvent[]; after: (TimelineEvent & { replay_injected: boolean })[];
    current_before: TimelineCurrent | null; current_after: TimelineCurrent | null } | null;
  affected: { before: AffectedRow[]; after: AffectedResponse | null };
  route_evidence: { location_keys: string[]; confirmed: { evidence_id: string; operating_status: string }[];
    exposure: { evidence_id: string; kind: string }[]; changed_by_replay: false; note: string } | null;
  material_screen: MaterialScreen | null; recovery_link: string; note: string;
};
type FixtureData = {
  synthetic: true; source: string; generated_from_revision: string; as_of: string; notice: string; files: string[];
  shipments: ListResponse; timelines: Record<string, TimelineResponse>; affected: Record<string, AffectedResponse>;
  route_evidence: Record<string, EvidenceResponse>;
  replay: { notice: string; samples: ReplaySample[]; results: Record<string, ReplayResponse> };
};

export class DispatchError extends Error {
  constructor(message: string, readonly kind: 'not_found' | 'unavailable') { super(message); }
}

const data = JSON.parse(raw) as FixtureData;
export const dispatchFixtureInfo = { asOf: data.as_of, revision: data.generated_from_revision, notice: data.notice, files: data.files };

export type DispatchAdapter = ReturnType<typeof createDispatchFixtureAdapter>;

export function createDispatchFixtureAdapter() {
  const copy = <T,>(value: T): T => structuredClone(value);
  return {
    source: 'local_fixture_adapter' as const,
    synthetic: true as const,
    async listShipments(): Promise<ListResponse> { return copy(data.shipments); },
    async timeline(shipmentId: string): Promise<TimelineResponse> {
      const found = data.timelines[shipmentId];
      if (found) return copy(found);
      return { status: 'empty', reasons: ['no_accepted_events'], as_of: data.as_of, shipment_id: shipmentId,
        events: [], current: null, flags: [], rejected_records: [] };
    },
    async affected(eventId: string): Promise<AffectedResponse> {
      const found = data.affected[eventId];
      if (!found) throw new DispatchError(`No accepted event ${eventId} in the synthetic data.`, 'not_found');
      return copy(found);
    },
    async routeEvidence(shipmentId: string): Promise<EvidenceResponse> {
      const found = data.route_evidence[shipmentId];
      if (found) return copy(found);
      return { status: 'empty', reasons: [], as_of: data.as_of, location_keys: [], confirmed: [], exposure: [], expired: [], outside_period: [] };
    },
    async replaySamples(): Promise<ReplaySamplesResponse> {
      return copy({ notice: data.replay.notice, samples: data.replay.samples });
    },
    async replay(sampleId: string): Promise<ReplayResponse> {
      const found = data.replay.results[sampleId];
      if (!found) throw new DispatchError(`No synthetic replay sample ${sampleId}.`, 'not_found');
      return copy(found);
    },
  };
}

const STATUS_LABELS: Record<ShipmentStatus, string> = {
  on_time: 'On time', at_risk: 'At risk', delayed: 'Delayed', delivered: 'Delivered', on_hold: 'On hold', unknown: 'Unknown',
};
export const statusLabel = (status: ShipmentStatus) => STATUS_LABELS[status] ?? status;
export const humanize = (text: string) => text.replaceAll('_', ' ').replace(':', ': ');

export function formatInstant(utc: string | null | undefined, offset?: string): string {
  if (!utc) return 'Not stated';
  const time = Date.parse(utc);
  if (Number.isNaN(time)) return utc;
  const iso = new Date(time).toISOString();
  const text = `${iso.slice(0, 10)} ${iso.slice(11, 16)} UTC`;
  const m = offset ? /^([+-])(\d{2}):(\d{2})$/.exec(offset) : null;
  if (!m) return text;
  const minutes = (m[1] === '-' ? -1 : 1) * (Number(m[2]) * 60 + Number(m[3]));
  const local = new Date(time + minutes * 60_000).toISOString().slice(11, 16);
  return `${text} (${local} local, ${offset})`;
}

export function formatQuantities(quantities: Record<string, string>): string {
  const parts = Object.entries(quantities).map(([unit, amount]) => `${amount} ${unit}`);
  return parts.length ? parts.join(', ') : 'None delivered';
}

export function freshness(asOf: string, now: Date, maxAgeSeconds = 86_400): { ageSeconds: number; stale: boolean } {
  const ageSeconds = Math.max(0, Math.floor((now.getTime() - Date.parse(asOf)) / 1000));
  return { ageSeconds, stale: ageSeconds > maxAgeSeconds };
}

function expectedText(row: ShipmentRow | null): string {
  const e = row?.expected_arrival;
  if (!e) return 'no usable ETA';
  return `${formatInstant(e.at)} (${e.basis === 'eta' ? 'stated ETA' : 'actual arrival'})`;
}

/** One line per changed field, in the read model's field order. */
export function explainChange(change: ReplayChange): string[] {
  const { before, after } = change;
  const status = (row: ShipmentRow | null) => (row ? statusLabel(row.status) : 'not listed');
  const words = (items: string[]) => (items.length ? items.map(humanize).join(', ') : 'none');
  const lines: Record<string, () => string> = {
    status: () => `Status: ${status(before)} → ${status(after)}`,
    expected_arrival: () => `Expected arrival: ${expectedText(before)} → ${expectedText(after)}`,
    signals: () => `Signals added: ${words(change.signals_added)} · removed: ${words(change.signals_removed)}`,
    on_hold: () => `On hold: ${before?.on_hold ? 'yes' : 'no'} → ${after?.on_hold ? 'yes' : 'no'}`,
    last_event_type: () => `Last milestone: ${humanize(before?.last_event_type ?? 'none')} → ${humanize(after?.last_event_type ?? 'none')}`,
    delivered_quantity: () => `Delivered: ${formatQuantities(before?.delivered_quantity ?? {})} → ${formatQuantities(after?.delivered_quantity ?? {})}`,
  };
  return change.changed.map(field => (lines[field] ?? (() => humanize(field)))());
}

const REPLAY_REASONS: Record<string, string> = {
  event_after_as_of: 'Rejected: the event happened, was observed or had evidence retrieved after the data cutoff, so it was not known then.',
  injected_not_synthetic: 'Rejected: a replay event must be labelled synthetic.',
  injected_event_rejected: 'Rejected by the milestone normaliser.',
  event_id_already_used: 'Rejected: the event ID is already used by a recorded milestone.',
  no_changes: 'No shipment status, ETA or signal changed.',
  hold_without_new_eta: 'A hold without a new ETA is exposure, not a measured delay, so no arrival change is screened.',
  no_arrival_information: 'This event states no new arrival time, so no arrival change is screened.',
  superseded_by_later_event: 'A later event for this shipment already states the arrival.',
  shipment_not_in_snapshot: 'The plan snapshot has no arrival line for this shipment.',
  as_of_differs_from_snapshot: 'The plan snapshot is at a different cutoff, so the material screen was not run.',
  no_snapshot: 'No plan snapshot, so the material screen was not run.',
};
export const replayReason = (reason: string) => REPLAY_REASONS[reason] ?? humanize(reason);

const LEAD_TIME_UNAVAILABLE: Record<string, string> = {
  not_available: 'No departure and arrival, or cutoff and ETA, to count between.',
  unsupported_country: 'No approved holiday calendar for this country',
  unsupported_year: 'No holiday calendar for that year',
  source_unavailable: 'The holiday calendar source is unavailable',
  invalid_range: 'The expected arrival is before the start date',
};

/** Lead time as display text. Never shows a number unless the calendar supports the country and year. */
export function formatLeadTime(lead: LeadTime): { headline: string; detail: string; source: string } {
  const source = lead.synthetic
    ? 'Synthetic holiday table: invented dates, not real public holidays.'
    : lead.source ? `Holidays from ${lead.source.provider_id}.` : '';
  if (lead.status !== 'ok' || lead.working_days === null) {
    const where = lead.country ? ` (${lead.country})` : '';
    const reason = lead.reason && lead.status !== 'not_available' ? `: ${humanize(lead.reason)}` : '';
    return { headline: 'Not available', detail: `${LEAD_TIME_UNAVAILABLE[lead.status] ?? humanize(lead.status)}${where}${reason}`, source };
  }
  const span = lead.basis === 'departure_to_arrival' ? `Departure ${lead.start} to arrival ${lead.end}` : `Cutoff ${lead.start} to expected arrival ${lead.end}`;
  const holidays = lead.holidays.length
    ? `${lead.holidays.length} ${lead.holidays.length === 1 ? 'holiday' : 'holidays'} (${lead.holidays.map(h => h.name).join(', ')})`
    : 'no holidays';
  const where = lead.location ? ` (${humanize(lead.location)})` : '';
  return {
    headline: `${lead.working_days} working ${lead.working_days === 1 ? 'day' : 'days'}`,
    detail: `${span} in ${lead.country}${where}: ${lead.calendar_days} calendar days, ${lead.weekend_days} weekend days, ${holidays}.`,
    source,
  };
}
