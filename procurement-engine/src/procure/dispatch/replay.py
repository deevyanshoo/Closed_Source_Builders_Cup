"""Port-delay replay: inject one labelled synthetic milestone and compare before and after.

``replay_event(records, event, as_of=..., now=..., snapshot=None, route_evidence_records=None)``
is pure and deterministic: it never modifies its inputs, persists nothing, calls no provider and
reads no clock. It reuses, unchanged:
  events.ingest.normalize_milestones          both views are normalised at ``as_of``
  dispatch.shipment_list / shipment_timeline  statuses and timeline entries
  dispatch.affected_shipments                 exposure from the injected event
  events.ingest.build_arrival_scenarios and   the material screen for the injected event, only
  events.propagation.project_arrival_change   when it states an arrival time
It never computes recovery options; the result links to the existing recovery page.

The injected record must be labelled ``synthetic: true``. It is rejected when it occurred, was
observed or had evidence retrieved after ``as_of`` (``event_after_as_of``), reuses an existing
event ID, or is rejected by the normaliser. Route evidence is not changed by a replay: an injected
milestone is not a port authority notice, so confirmed operating status still comes only from the
route-evidence records, and shared locations stay exposure.

Status: ``error`` with reasons and no views; ``empty`` with ``no_changes`` when no shipment row
differs; ``stale`` when the cutoff is older than the read models' maximum age; otherwise ``ok``.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping

from ..events.ingest import bind_synthetic_milestones, build_arrival_scenarios, normalize_milestones
from ..events.propagation import project_arrival_change
from ..route_evidence import evidence_for_locations, normalize_route_evidence
from .read_models import DEFAULT_MAX_AGE_SECONDS, _TIMESTAMP, _instant, affected_shipments, shipment_timeline
from .summary import shipment_list

RECOVERY_LINK = "/shipment-recovery"
INJECTED_LABEL = "Synthetic replay event, not observed data"
REPLAY_NOTE = ("Replay of one synthetic event over synthetic fixtures. It shows what the read models would report; "
               "it does not change stored data and does not compute recovery options.")
ROUTE_NOTE = ("Unchanged by the replay: a milestone is not an operating notice, so confirmed status comes only from "
              "route evidence and shared locations stay exposure.")
COMPARED = ("status", "expected_arrival", "signals", "on_hold", "last_event_type", "delivered_quantity")


def _skeleton() -> dict:
    return {"status": "ok", "reasons": [], "as_of": None, "age_seconds": None, "synthetic": True,
            "injected": None, "rejection": None, "shipments": {"before": [], "after": []}, "changes": [],
            "timeline": None, "affected": {"before": [], "after": None}, "route_evidence": None,
            "material_screen": None, "recovery_link": RECOVERY_LINK, "note": REPLAY_NOTE}


def _error(result: dict, reason: str, **extra) -> dict:
    return {**result, **extra, "status": "error", "reasons": [reason]}


def _after_cutoff(event: Mapping, cutoff) -> bool:
    times = [event.get("occurred_at"), event.get("observed_at")]
    evidence = event.get("evidence")
    if isinstance(evidence, list):
        times += [item.get("retrieved_at") for item in evidence if isinstance(item, Mapping)]
    return any(instant is not None and instant > cutoff for instant in map(_instant, times))


def _location_keys(event: Mapping) -> list[str]:
    keys = set()
    facility, lane = event.get("facility"), event.get("lane")
    if isinstance(facility, Mapping) and isinstance(facility.get("code"), str):
        keys.add(facility["code"])
    if isinstance(lane, Mapping):
        keys.update(lane[end] for end in ("origin", "destination") if isinstance(lane.get(end), str))
    return sorted(keys)


def _changes(before: list[dict], after: list[dict]) -> list[dict]:
    old = {row["shipment_id"]: row for row in before}
    new = {row["shipment_id"]: row for row in after}
    out = []
    for shipment_id in sorted(old.keys() | new.keys()):
        a, b = old.get(shipment_id), new.get(shipment_id)
        changed = [f for f in COMPARED if (a or {}).get(f) != (b or {}).get(f)]
        if not changed:
            continue
        out.append({"shipment_id": shipment_id, "before": a, "after": b, "changed": changed,
                    "signals_added": sorted(set((b or {}).get("signals", [])) - set((a or {}).get("signals", []))),
                    "signals_removed": sorted(set((a or {}).get("signals", [])) - set((b or {}).get("signals", [])))})
    return out


def _route_evidence(route_evidence: Mapping | None, keys: list[str]) -> dict | None:
    if route_evidence is None:
        return None
    found = evidence_for_locations(route_evidence, keys) if keys else {"confirmed": [], "exposure": []}
    return {"location_keys": keys,
            "confirmed": [{"evidence_id": e["evidence_id"], "operating_status": e["operating_status"]}
                          for e in found["confirmed"]],
            "exposure": [{"evidence_id": e["evidence_id"], "kind": e["kind"]} for e in found["exposure"]],
            "changed_by_replay": False, "note": ROUTE_NOTE}


def _material_screen(records: list, event: Mapping, snapshot: Mapping | None, as_of: str) -> dict:
    event_id = event["event_id"]
    if snapshot is None:
        return {"status": "not_run", "reason": "no_snapshot", "event_id": event_id}
    snapshot_as_of = _instant(snapshot.get("as_of")) if isinstance(snapshot, Mapping) else None
    if snapshot_as_of is None or snapshot_as_of != _instant(as_of):
        return {"status": "not_run", "reason": "as_of_differs_from_snapshot", "event_id": event_id}
    try:
        binding = bind_synthetic_milestones(
            [*records, event], snapshot, expected_workspace_id=snapshot.get("workspace_id"),
            expected_plan_version=snapshot.get("plan_version"), expected_snapshot_id=snapshot.get("snapshot_id"))
        built = build_arrival_scenarios(binding, snapshot, scenario_prefix="replay")
    except ValueError:
        return {"status": "not_run", "reason": "invalid_synthetic_input", "event_id": event_id}
    scenario = next((s for s in built["scenarios"] if s["event_id"] == event_id), None)
    if scenario is None:
        reason = next((e["reason"] for e in built["excluded"] if e["event_id"] == event_id), "not_converted")
        return {"status": "not_run", "reason": reason, "event_id": event_id}
    return {**project_arrival_change(snapshot, scenario["scenario"]), "assumptions": scenario["assumptions"]}


def replay_event(records: list, event: object, *, as_of: str, now: str, snapshot: Mapping | None = None,
                 route_evidence_records: list | None = None,
                 max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS) -> dict:
    result = _skeleton()
    if not isinstance(as_of, str) or not _TIMESTAMP.fullmatch(as_of):
        return _error(result, "invalid_as_of")
    if not isinstance(records, list):
        return _error(result, "invalid_records")
    if not isinstance(event, Mapping) or not isinstance(event.get("event_id"), str):
        return _error(result, "invalid_injected_event")
    if event.get("synthetic") is not True:
        return _error(result, "injected_not_synthetic")
    records, event, snapshot = copy.deepcopy(records), copy.deepcopy(dict(event)), copy.deepcopy(snapshot)
    if _after_cutoff(event, _instant(as_of)):
        return _error(result, "event_after_as_of")
    if any(isinstance(r, Mapping) and r.get("event_id") == event["event_id"] for r in records):
        return _error(result, "event_id_already_used")

    before = normalize_milestones(records, as_of=as_of)
    after = normalize_milestones([*records, event], as_of=as_of)
    rejected = next((r for r in after["rejected"] if r["record_index"] == len(records)), None)
    if rejected is not None:
        return _error(result, "injected_event_rejected",
                      rejection={"reason": rejected["reason"], "details": rejected["details"]})
    route_evidence = (None if route_evidence_records is None
                      else normalize_route_evidence(route_evidence_records, as_of=as_of))

    views = {}
    for name, normalized in (("before", before), ("after", after)):
        listed = shipment_list(normalized, now=now, snapshot=snapshot, route_evidence=route_evidence,
                               max_age_seconds=max_age_seconds)
        if listed["status"] == "error":
            return _error(result, listed["reasons"][0])
        views[name] = listed
    injected = next(e for e in after["events"] if e["event_id"] == event["event_id"])
    shipment_id = injected["shipment_id"]
    timelines = {name: shipment_timeline(normalized, shipment_id, now=now, max_age_seconds=max_age_seconds)
                 for name, normalized in (("before", before), ("after", after))}
    affected = affected_shipments(after, injected["event_id"], now=now, max_age_seconds=max_age_seconds)

    result.update(
        as_of=views["after"]["as_of"], age_seconds=views["after"]["age_seconds"],
        injected={"event_id": injected["event_id"], "shipment_id": shipment_id, "event_type": injected["event_type"],
                  "synthetic": True, "label": INJECTED_LABEL},
        shipments={"before": views["before"]["shipments"], "after": views["after"]["shipments"]},
        changes=_changes(views["before"]["shipments"], views["after"]["shipments"]),
        timeline={"shipment_id": shipment_id, "before": timelines["before"]["events"],
                  "after": [{**e, "replay_injected": e["event_id"] == injected["event_id"]}
                            for e in timelines["after"]["events"]],
                  "current_before": timelines["before"]["current"], "current_after": timelines["after"]["current"]},
        affected={"before": [], "after": affected},
        route_evidence=_route_evidence(route_evidence, _location_keys(injected)),
        material_screen=_material_screen(records, event, snapshot, as_of),
    )
    stale = views["after"]["reasons"] if views["after"]["status"] == "stale" else []
    if not result["changes"]:
        result.update(status="empty", reasons=["no_changes", *stale])
    elif stale:
        result.update(status="stale", reasons=list(stale))
    return result
