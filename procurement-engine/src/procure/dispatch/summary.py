"""Shipment list for the dispatch workspace, with an evidence-based status per shipment.

``shipment_list(normalized, now=..., snapshot=None, route_evidence=None)`` is pure and builds on
``shipment_timeline``. Status precedence:

  delivered  an accepted delivered milestone
  on_hold    an open port hold
  delayed    the expected arrival (actual arrival, else open ETA) is after the plan baseline
  at_risk    not yet arrived, and current route evidence touches one of its facilities or lane ends:
             any exposure, or a confirmed operating status other than normal
  on_time    a single plan baseline exists and the expected arrival is not after it
  unknown    none of the above can be shown, for example no plan baseline

The plan baseline is the ``arrival_at`` of the shipment's lines in the propagation snapshot; lines
with different times make it ambiguous. ``signals`` list every observation behind the status.
"""
from __future__ import annotations

from collections.abc import Mapping

from ..route_evidence import evidence_for_locations
from .read_models import DEFAULT_MAX_AGE_SECONDS, _MAX_ROWS, _frame, _identifier, _instant, shipment_timeline

STATUS_NOTE = ("Status needs evidence: delayed and on time compare with the plan baseline, at risk needs current "
               "route evidence, and unknown means neither is available.")


def _baselines(snapshot: object, cutoff: str) -> tuple[dict[str, set[str]] | None, bool]:
    if snapshot is None:
        return {}, False
    if not isinstance(snapshot, Mapping) or not all(
        _identifier(snapshot.get(key)) for key in ("workspace_id", "plan_version", "snapshot_id")
    ):
        return None, False
    snapshot_time = _instant(snapshot.get("as_of"))
    lines = snapshot.get("arrival_lines")
    if snapshot_time is None or type(lines) is not list or len(lines) > _MAX_ROWS:
        return None, False
    out: dict[str, set[str]] = {}
    for line in lines:
        if not isinstance(line, Mapping) or not _identifier(line.get("shipment_id")) or (
            "line_id" in line and not _identifier(line["line_id"])
        ):
            return None, False
        instant = _instant(line.get("arrival_at"))
        if instant is None:
            return None, False
        out.setdefault(line["shipment_id"], set()).add(instant.isoformat())
    if snapshot_time > _instant(cutoff):
        return {}, True
    return out, False


def _location_keys(events: list[dict]) -> list[str]:
    keys = set()
    for e in events:
        if e.get("facility"):
            keys.add(e["facility"]["code"])
        if e.get("lane"):
            keys.update((e["lane"]["origin"], e["lane"]["destination"]))
    return sorted(keys)


def _row(timeline: dict, baselines: set[str] | None, route_evidence: Mapping | None,
         future_baseline: bool = False) -> dict:
    events, current = timeline["events"], timeline["current"]
    usable = [e for e in events if e["operating_evidence"]]
    types = {e["event_type"] for e in usable}
    arrivals = sorted((e for e in usable if e["event_type"] in ("arrived", "delivered")),
                      key=lambda e: (e["occurred_at"], e["event_id"]))
    if arrivals:
        expected = {"at": arrivals[-1]["occurred_at"], "basis": "actual_arrival", "event_id": arrivals[-1]["event_id"]}
    elif current["open_eta"]:
        expected = {"at": current["open_eta"]["eta"], "basis": "eta", "event_id": current["open_eta"]["event_id"]}
    else:
        expected = None
    signals = set()
    if arrivals:
        signals.add("arrived")
    if "partially_delivered" in types:
        signals.add("partially_delivered")
    if current["on_hold"]:
        signals.add("on_hold")
    if current["unusable_event_ids"]:
        signals.add("evidence_expired")
    baseline = None
    if future_baseline:
        signals.add("baseline_unavailable_at_cutoff")
    elif not baselines:
        signals.add("no_baseline")
    elif len(baselines) > 1:
        signals.add("baseline_ambiguous")
    else:
        baseline = next(iter(baselines))
    late = bool(baseline and expected and _instant(expected["at"]) > _instant(baseline))
    if late:
        signals.add("arrived_after_baseline" if expected["basis"] == "actual_arrival" else "eta_after_baseline")
    keys = _location_keys(events)
    active_keys = _location_keys(usable)
    if route_evidence is not None and not arrivals and active_keys:
        found = evidence_for_locations(route_evidence, active_keys)
        signals.update(f"route_confirmed:{e['operating_status']}" for e in found["confirmed"]
                       if e["operating_status"] != "normal")
        if found["exposure"]:
            signals.add("route_exposure")
    if "delivered" in types:
        status = "delivered"
    elif current["on_hold"]:
        status = "on_hold"
    elif late:
        status = "delayed"
    elif any(s == "route_exposure" or s.startswith("route_confirmed:") for s in signals):
        status = "at_risk"
    elif baseline and expected:
        status = "on_time"
    else:
        status = "unknown"
    return {"shipment_id": timeline["shipment_id"], "status": status, "signals": sorted(signals),
            "baseline_arrival_at": baseline, "expected_arrival": expected,
            "last_event_type": current["last_event_type"], "on_hold": current["on_hold"],
            "delivered_quantity": current["delivered_quantity"], "location_keys": keys}


def shipment_list(normalized: Mapping, *, now: str, snapshot: Mapping | None = None,
                  route_evidence: Mapping | None = None, max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS) -> dict:
    result, data = _frame(normalized, now, max_age_seconds)
    result.update(shipments=[], note=STATUS_NOTE)
    if data is None:
        return result
    baselines, future_baseline = _baselines(snapshot, result["as_of"])
    if baselines is None:
        return {**result, "status": "error", "reasons": ["invalid_snapshot"]}
    if route_evidence is not None:
        validated_route = evidence_for_locations(route_evidence, [])
        if (validated_route["status"] == "error"
                or _instant(validated_route["as_of"]) != _instant(result["as_of"])):
            return {**result, "status": "error", "reasons": ["invalid_route_evidence"]}
    shipment_ids = sorted({e["shipment_id"] for e in data["events"]})
    if not shipment_ids:
        return {**result, "status": "empty", "reasons": ["no_accepted_events", *result["reasons"]]}
    for shipment_id in shipment_ids:
        timeline = shipment_timeline(normalized, shipment_id, now=now, max_age_seconds=max_age_seconds)
        if timeline["status"] == "error":
            return {**result, "status": "error", "reasons": timeline["reasons"], "shipments": []}
        result["shipments"].append(_row(timeline, baselines.get(shipment_id), route_evidence, future_baseline))
    return result
