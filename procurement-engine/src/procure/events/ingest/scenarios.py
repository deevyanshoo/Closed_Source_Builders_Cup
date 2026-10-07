"""Turn normalised milestones into arrival-change scenarios for ``events/propagation``.

``build_arrival_scenarios(normalized, snapshot)`` produces, per shipment, at most one scenario
in exactly the mapping ``project_arrival_change(snapshot, scenario)`` accepts
(workspace_id, expected_plan_version, source_snapshot_id, event_id, scenario_id, synthetic,
changes=[{line_id, arrival_at}]). It is pure and never applies or persists anything.

An event can change an arrival date when it states one: an actual ``arrived`` or ``delivered``
time, an ``eta_changed`` ETA, or a ``port_hold`` that states a new ETA. Everything else is
listed in ``excluded`` with a reason rather than converted:
  no_arrival_information          departed or released: no new arrival time is stated
  partial_quantity_not_supported  the projection changes dates only, never quantities
  hold_without_new_eta            a hold is exposure, not a measured delay
  evidence_expired                every evidence reference expired before as_of
  superseded_by_later_event       a later-occurring event for the shipment states arrival
  shipment_sequence_conflict      the shipment's own events disagree on order
  shipment_not_in_snapshot        no arrival line names this shipment
  arrival_before_snapshot_as_of   the stated time precedes the snapshot (already received)
  not_synthetic                   the propagation screen accepts synthetic scopes only

Assumption stated on every scenario: all arrival lines of the shipment move to the stated time
and quantities are unchanged.
"""
from __future__ import annotations

import copy
from collections import defaultdict
from collections.abc import Mapping

from .milestones import ESTIMATE_TYPES, _instant

NO_ARRIVAL_INFORMATION = {"departed", "port_released"}
ACTUAL_ARRIVAL = {"arrived", "delivered"}


def _excluded(event: dict, reason: str) -> dict:
    return {"event_id": event["event_id"], "shipment_id": event["shipment_id"], "reason": reason}


def _own_reason(event: dict, snapshot_synthetic: bool) -> str | None:
    if not event["synthetic"] or not snapshot_synthetic:
        return "not_synthetic"
    if event["event_type"] in NO_ARRIVAL_INFORMATION:
        return "no_arrival_information"
    if event["event_type"] == "partially_delivered":
        return "partial_quantity_not_supported"
    if event["event_type"] == "port_hold" and event["eta"] is None:
        return "hold_without_new_eta"
    if not event["operating_evidence"]:
        return "evidence_expired"
    return None


def build_arrival_scenarios(normalized: Mapping, snapshot: Mapping, *, scenario_prefix: str = "milestone") -> dict:
    normalized, snapshot = copy.deepcopy(normalized), copy.deepcopy(snapshot)
    snapshot_as_of = _instant(snapshot.get("as_of"), "as_of")
    lines: dict[str, list[str]] = defaultdict(list)
    for line in snapshot.get("arrival_lines") or []:
        lines[line["shipment_id"]].append(line["line_id"])

    by_shipment: dict[str, list[dict]] = defaultdict(list)
    for event in normalized["events"]:
        by_shipment[event["shipment_id"]].append(event)

    scenarios, excluded = [], []
    for shipment_id in sorted(by_shipment):
        own = by_shipment[shipment_id]
        if "sequence_conflict" in normalized["shipments"].get(shipment_id, {}).get("flags", []):
            excluded.extend(_excluded(e, "shipment_sequence_conflict") for e in own)
            continue
        candidates = []
        for event in own:
            reason = _own_reason(event, snapshot.get("synthetic") is True)
            if reason:
                excluded.append(_excluded(event, reason))
            else:
                candidates.append(event)
        if not candidates:
            continue
        chosen = max(candidates, key=lambda e: (e["occurred_at"], e["observed_at"], e["event_id"]))
        excluded.extend(_excluded(e, "superseded_by_later_event") for e in candidates if e is not chosen)
        basis = "eta" if chosen["event_type"] in ESTIMATE_TYPES else "actual_arrival"
        arrival_at = chosen["eta"] if basis == "eta" else chosen["occurred_at"]
        if shipment_id not in lines:
            excluded.append(_excluded(chosen, "shipment_not_in_snapshot"))
            continue
        if _instant(arrival_at, "arrival_at") < snapshot_as_of:
            excluded.append(_excluded(chosen, "arrival_before_snapshot_as_of"))
            continue
        assumptions = [f"Every arrival line of {shipment_id} moves to this {basis.replace('_', ' ')} time; "
                       "quantities are unchanged."]
        if basis == "eta":
            assumptions.append("An ETA is a stated estimate, not an observed arrival.")
        scenarios.append({
            "event_id": chosen["event_id"], "shipment_id": shipment_id, "basis": basis, "arrival_at": arrival_at,
            "assumptions": assumptions,
            "scenario": {"workspace_id": snapshot["workspace_id"], "expected_plan_version": snapshot["plan_version"],
                         "source_snapshot_id": snapshot["snapshot_id"], "event_id": chosen["event_id"],
                         "scenario_id": f"{scenario_prefix}:{chosen['event_id']}", "synthetic": True,
                         "changes": [{"line_id": line_id, "arrival_at": arrival_at}
                                     for line_id in sorted(lines[shipment_id])]},
        })
    excluded.sort(key=lambda e: (e["shipment_id"], e["event_id"]))
    return {"scenarios": scenarios, "excluded": excluded}
