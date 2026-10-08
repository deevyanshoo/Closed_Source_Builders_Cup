"""Read models for the dispatch workspace over normalised shipment milestones.

Both functions are pure: they read the output of ``events.ingest.normalize_milestones``, never
modify it, persist nothing and call no provider. ``now`` is passed explicitly (an instant with a
time zone), so staleness never depends on a hidden clock.

Every result has ``status`` and ``reasons``:
  ok     current data with at least one result
  empty  nothing to show (for example no accepted events), with the reason
  stale  the data cutoff (``as_of``) is older than ``max_age_seconds``; results are still shown
  error  the request or input cannot be read; no results are returned

``affected_shipments`` reports *exposure*: sharing a facility or a lane end with an event does not
confirm a delay or a changed operating status.
"""
from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal

_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$")
DEFAULT_MAX_AGE_SECONDS = 24 * 3600
ARRIVAL_TYPES = {"arrived", "delivered"}
ESTIMATE_TYPES = {"eta_changed", "port_hold"}
EXPOSURE_NOTE = ("Exposure only: sharing a facility or lane end with this event does not confirm a delay or a "
                 "changed operating status.")


def _instant(value: object) -> datetime | None:
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _frame(normalized: object, now: object, max_age_seconds: int) -> tuple[dict, dict | None]:
    """(result skeleton, normalized copy or None when the request is an error)."""
    base = {"status": "ok", "reasons": [], "as_of": None, "age_seconds": None}
    if not (isinstance(normalized, Mapping) and isinstance(normalized.get("events"), list)
            and isinstance(normalized.get("shipments"), Mapping) and isinstance(normalized.get("rejected"), list)
            and _instant(normalized.get("as_of")) is not None):
        return {**base, "status": "error", "reasons": ["invalid_normalized_input"]}, None
    current = _instant(now)
    if current is None:
        return {**base, "status": "error", "reasons": ["invalid_now"]}, None
    as_of = _instant(normalized["as_of"])
    if as_of > current:
        return {**base, "status": "error", "reasons": ["as_of_after_now"]}, None
    age = int((current - as_of).total_seconds())
    base.update(as_of=as_of.isoformat(), age_seconds=age)
    if age > max_age_seconds:
        base.update(status="stale", reasons=["data_older_than_max_age"])
    return base, copy.deepcopy(dict(normalized))


def _entry(event: dict) -> dict:
    keys = ("event_id", "event_type", "raw_type", "occurred_at", "occurred_at_offset", "observed_at", "eta",
            "quantity", "facility", "lane", "source", "operating_evidence", "flags")
    entry = {k: event.get(k) for k in keys}
    entry["evidence"] = [{k: e.get(k) for k in ("evidence_id", "kind", "locator", "status")}
                         for e in event.get("evidence") or []]
    return entry


def _current(events: list[dict]) -> dict:
    def later(event, types):
        return any(o["event_type"] in types and o["occurred_at"] > event["occurred_at"] for o in events)

    open_etas = [e for e in events if e["event_type"] in ESTIMATE_TYPES and e.get("eta") and e["operating_evidence"]
                 and "superseded_by_arrival" not in e["flags"] and not later(e, ARRIVAL_TYPES)]
    latest_eta = max(open_etas, key=lambda e: (e["occurred_at"], e["observed_at"], e["event_id"]), default=None)
    holds = [e for e in events if e["event_type"] == "port_hold" and e["operating_evidence"]
             and not later(e, {"port_released", *ARRIVAL_TYPES})]
    delivered: dict[str, Decimal] = {}
    for e in events:
        if e["event_type"] == "partially_delivered" and e["operating_evidence"] and e.get("quantity"):
            unit = e["quantity"]["unit"]
            delivered[unit] = delivered.get(unit, Decimal(0)) + Decimal(e["quantity"]["amount"])
    return {"last_event_type": events[-1]["event_type"] if events else None,
            "open_eta": {"eta": latest_eta["eta"], "event_id": latest_eta["event_id"]} if latest_eta else None,
            "on_hold": bool(holds),
            "delivered_quantity": {unit: format(total, "f") for unit, total in sorted(delivered.items())},
            "unusable_event_ids": [e["event_id"] for e in events if not e["operating_evidence"]]}


def shipment_timeline(normalized: Mapping, shipment_id: str, *, now: str,
                      max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS, records: list | None = None) -> dict:
    """Milestones for one shipment in occurrence order, with a derived current view.

    Pass the raw ``records`` to list the shipment's rejected records too (rejections carry only a
    record index).
    """
    result, data = _frame(normalized, now, max_age_seconds)
    result.update(shipment_id=shipment_id, events=[], current=None, flags=[], rejected_records=[])
    if data is None:
        return result
    if not isinstance(shipment_id, str) or not shipment_id or shipment_id != shipment_id.strip():
        return {**result, "status": "error", "reasons": ["invalid_shipment_id"]}
    own = sorted((e for e in data["events"] if e.get("shipment_id") == shipment_id),
                 key=lambda e: (e["occurred_at"], e["event_id"]))
    if records is not None:
        result["rejected_records"] = [
            {k: r[k] for k in ("record_index", "event_id", "reason", "details")}
            for r in data["rejected"]
            if 0 <= r["record_index"] < len(records) and isinstance(records[r["record_index"]], Mapping)
            and records[r["record_index"]].get("shipment_id") == shipment_id]
    result["flags"] = list(data["shipments"].get(shipment_id, {}).get("flags", []))
    if not own:
        return {**result, "status": "empty", "reasons": ["no_accepted_events", *result["reasons"]]}
    result.update(events=[_entry(e) for e in own], current=_current(own))
    return result


def affected_shipments(normalized: Mapping, event_id: str, *, now: str,
                       max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS) -> dict:
    """Shipments whose accepted events share a facility or lane end with the given event."""
    result, data = _frame(normalized, now, max_age_seconds)
    result.update(event_id=event_id, trigger_shipment_id=None, location_keys=[], affected=[], excluded=[],
                  note=EXPOSURE_NOTE)
    if data is None:
        return result
    trigger = next((e for e in data["events"] if e.get("event_id") == event_id), None)
    if trigger is None:
        return {**result, "status": "error", "reasons": ["unknown_event"]}
    if not trigger["operating_evidence"]:
        return {**result, "status": "error", "reasons": ["trigger_not_operating_evidence"]}
    keys = set()
    if trigger.get("facility"):
        keys.add(trigger["facility"]["code"])
    if trigger.get("lane"):
        keys.update((trigger["lane"]["origin"], trigger["lane"]["destination"]))
    result.update(trigger_shipment_id=trigger["shipment_id"], location_keys=sorted(keys))

    by_shipment: dict[str, list[dict]] = {}
    for event in data["events"]:
        by_shipment.setdefault(event["shipment_id"], []).append(event)
    for shipment_id in sorted(by_shipment):
        own = sorted(by_shipment[shipment_id], key=lambda e: (e["occurred_at"], e["event_id"]))
        basis = set()
        for e in own:
            if e.get("facility") and e["facility"]["code"] in keys:
                basis.add(f"facility:{e['facility']['code']}")
            for end in ("origin", "destination"):
                if e.get("lane") and e["lane"][end] in keys:
                    basis.add(f"lane_{end}:{e['lane'][end]}")
        if not basis:
            continue
        if any(e["event_type"] == "delivered" for e in own):
            result["excluded"].append({"shipment_id": shipment_id, "reason": "already_delivered"})
            continue
        result["affected"].append({"shipment_id": shipment_id,
                                   "relation": "self" if shipment_id == trigger["shipment_id"] else "shares_location",
                                   "basis": sorted(basis), "latest_event_type": own[-1]["event_type"],
                                   "status": "exposure"})
    if not result["affected"] and result["status"] == "ok":
        result.update(status="empty", reasons=["no_affected_shipments"])
    return result
