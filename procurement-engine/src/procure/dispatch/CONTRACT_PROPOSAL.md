# Shipment milestone event proposal (issue #5)

From Taher (shipment ingestion) to Divyanshu (contracts, event application, propagation). Status: proposal.
Nothing under `contracts/`, `services/gateway/`, migrations or `events/propagation/` was edited.

## What exists and what is missing

- `contracts/examples/shipment.json` (`ShipmentExample`, proposed 0.1.0) carries `event_id`, `workspace_id`,
  `plan_id`, `plan_version`, `occurred_at`, `observed_at`, `shipment_id`, `event_type: "delay"` and
  `assumed_delay_days`.
- `events/propagation/project_arrival_change(snapshot, scenario)` accepts a scenario with `changes:
  [{line_id, arrival_at}]` and one `event_id`. It changes dates only and accepts synthetic scopes only.

The milestone workflow needs fields neither carries. The normaliser in `procure/events/ingest/` already
produces them; I'm proposing them for the shared contract:

| Needed | Why | In `ShipmentExample`? | In projection scenario? |
|---|---|---|---|
| Milestone types (`departed`, `arrived`, `eta_changed`, `port_hold`, `port_released`, `partially_delivered`, `delivered`) | Only some types state a new arrival time | only `delay` | n/a |
| `facility` (`kind`, `code`, `name`) or `lane` (`origin`, `destination`, `mode`) | Event to facility/lane to shipment join (`docs/API_Integration_Guide.md`) | no | no |
| `source` (`provider_id`, `kind`, `reference`) | Provenance | no | no |
| `evidence[]` with `content_sha256`, `retrieved_at`, `valid_until`, `status` | Expired evidence must not drive a change | no | no |
| `eta` (stated estimate) separate from `occurred_at` | A hold or ETA update states a future time; it is not an observed arrival | `assumed_delay_days` only | `arrival_at` only |
| `quantity` (decimal string + unit) for partial deliveries | Partial arrivals change quantity, not just date | no | **not supported** (dates only) |
| Duplicate and conflict rules | Repeated feeds must not apply twice | no | per scenario only |
| `occurred_at_offset` | Local time of the facility, for calendars and display | no | no |

## Proposed `ShipmentMilestoneEvent`

Selected fields from the synthetic port-hold output of `normalize_milestones` (digest abbreviated for display):

```json
{"event_id": "evt-002-hold", "shipment_id": "SHP-SYN-002", "event_type": "port_hold", "raw_type": "port_hold",
 "occurred_at": "2026-10-05T04:30:00+00:00", "occurred_at_offset": "+05:30",
 "observed_at": "2026-10-05T05:00:00+00:00", "eta": "2026-10-12T02:30:00+00:00", "quantity": null,
 "facility": {"kind": "port", "code": "INNSA", "name": "Nhava Sheva (synthetic)"}, "lane": null,
 "source": {"provider_id": "synthetic-port-notices", "kind": "port_notice", "reference": "SYN-NOTICE-117"},
 "evidence": [{"evidence_id": "ev-002-notice", "kind": "port_notice", "locator": "synthetic://notices/117",
               "content_sha256": "a1f0…0003", "retrieved_at": "2026-10-05T05:00:00+00:00",
               "valid_until": "2026-10-15T00:00:00+00:00", "status": "current"}],
 "operating_evidence": true, "flags": [], "synthetic": true, "record_index": 2}
```

Conventions match `projection.py`:
- **Identifiers** are non-empty strings with no surrounding spaces.
- **Instants** need seconds and `Z` or `±HH:MM` (at most 6 fractional digits) and are returned in UTC as `+00:00`.
- **Quantities** are plain decimal strings kept verbatim.

**Rejections** carry a record index and reason:
`missing_identifier`, `invalid_timestamp`, `observed_before_occurred`, `observed_after_as_of`, `unsupported_event_type`,
`missing_location`, `invalid_location`, `missing_source`, `missing_evidence`, `invalid_evidence`, `missing_eta`,
`invalid_quantity`, `conflicting_duplicate`, `invalid_record`, `evidence_after_as_of`.

```json
{"record_index": 12, "event_id": "evt-007-dep", "reason": "conflicting_duplicate", "details": ["evt-007-dep"]}
```

**Duplicates:**
- Copies of an `event_id` that differ only in observation time collapse to the earliest observation:
  `{"event_id": "evt-004-eta", "record_indexes": [4, 5, 6], "kept_record_index": 4}`.
- Copies that differ in anything else are all rejected; which one is right is not guessed.
- Provider corrections therefore need a new `event_id` or an explicit revision field (see question 3).

**Point in time:** a record observed after `as_of`, or with any evidence retrieved after it, is rejected because it was not available then.

## Mapping to the existing projection (implemented, not a contract change)

The local synthetic handoff calls `bind_synthetic_milestones(raw_records, snapshot,
expected_workspace_id=..., expected_plan_version=..., expected_snapshot_id=...)` before
`build_arrival_scenarios(binding, snapshot)`. The factory re-normalizes at the snapshot cutoff;
the builder rejects a loose normalized mapping or a different snapshot. Its outer candidate
keeps a bounded source/evidence receipt and a snapshot-bound digest. The inner scenario emits
at most one candidate per shipment in exactly the existing projection mapping. A test runs
the unmodified `project_arrival_change` on it and gets `screened`. Expected scope is a local
caller assertion, not event ownership, provider authentication, source rights or tenant proof.
This proposal is still unapproved for any connected/public event route or plan application.

```json
{"workspace_id": "synthetic-workspace", "expected_plan_version": "synthetic-plan-v1",
 "source_snapshot_id": "synthetic-snapshot-1", "event_id": "evt-002-hold",
 "scenario_id": "milestone:evt-002-hold", "synthetic": true,
 "changes": [{"line_id": "LINE-002-A", "arrival_at": "2026-10-12T02:30:00+00:00"}]}
```

Stated assumption: all arrival lines of the shipment move to the stated time and quantities are unchanged.

Events that cannot become a date change are excluded with a reason: `no_arrival_information`,
`partial_quantity_not_supported`, `hold_without_new_eta`, `evidence_expired`, `superseded_by_later_event`,
`shipment_sequence_conflict`, `shipment_not_in_snapshot`, `arrival_before_snapshot_as_of`, `not_synthetic`.

## Questions for you

1. **Partial deliveries.** Should the projection accept a quantity split, e.g. `changes: [{line_id, arrival_at,
   quantity}]` or a split into two lines? Today they are excluded.
2. **Line-level versus shipment-level ETA.** Is "every line of the shipment moves together" acceptable, or should
   events carry `line_ids`?
3. **Corrections.** Should a corrected event reuse `event_id` with a `revision`, or always get a new `event_id`? This
   decides whether a changed copy is a conflict or an update.
4. **Multiple events per scenario.** The scenario has one `event_id`. Should combined changes from several
   shipments become one scenario with `event_ids`?
5. **Non-synthetic scope.** The projection rejects non-synthetic snapshots. When real feeds arrive, which gate
   replaces this?
6. **Durable deduplication.** Deduplication here is per call. Durable event-ID deduplication and application belong
   to your event state (#6); this output is meant to feed it.

## Not included

- No route, UI, persistence, provider or calendar integration.
- No claim that any synthetic record reflects real carrier or port data.
- A hold without a stated ETA is exposure, not a delay.
