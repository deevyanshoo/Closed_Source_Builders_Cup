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
export type TimelineResponse = {
  status: ReadStatus; reasons: string[]; as_of: string | null; shipment_id: string; events: TimelineEvent[];
  current: TimelineCurrent | null; flags: string[]; rejected_records: { record_index: number; event_id: string | null; reason: string }[];
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
type FixtureData = {
  synthetic: true; source: string; generated_from_revision: string; as_of: string; notice: string; files: string[];
  shipments: ListResponse; timelines: Record<string, TimelineResponse>; affected: Record<string, AffectedResponse>;
  route_evidence: Record<string, EvidenceResponse>;
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
