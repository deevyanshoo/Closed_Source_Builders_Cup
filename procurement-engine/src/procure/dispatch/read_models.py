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

import re
from collections.abc import Mapping
from datetime import datetime, timezone

_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$")
DEFAULT_MAX_AGE_SECONDS = 24 * 3600
ARRIVAL_TYPES = {"arrived", "delivered"}
ESTIMATE_TYPES = {"eta_changed", "port_hold"}
EXPOSURE_NOTE = ("Exposure only: sharing a facility or lane end with this event does not confirm a delay or a "
                 "changed operating status.")
_AMOUNT = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EVENT_TYPES = {"departed", "arrived", "eta_changed", "port_hold", "port_released",
                "partially_delivered", "delivered"}
_MAX_ROWS = 10_000
_MAX_EVIDENCE = 100
_MAX_TEXT = 16_384
_MAX_AMOUNT_DIGITS = 4_000


def _text(value: object) -> bool:
    return isinstance(value, str) and 0 < len(value) <= _MAX_TEXT and bool(value.strip())


def _identifier(value: object) -> bool:
    return _text(value) and value == value.strip()


def _location(value: object, required: tuple[str, ...]) -> bool:
    return value is None or (type(value) is dict and all(_text(value.get(k)) for k in required)
                             and (value.get("name") is None or (isinstance(value["name"], str)
                                                                and len(value["name"]) <= _MAX_TEXT)))


def _valid_event(event: object) -> bool:
    if type(event) is not dict or not all(_identifier(event.get(k)) for k in ("event_id", "shipment_id")):
        return False
    if not all(k in event for k in ("eta", "quantity", "facility", "lane", "source", "evidence")):
        return False
    if not isinstance(event.get("event_type"), str) or event["event_type"] not in _EVENT_TYPES or not _text(event.get("raw_type")):
        return False
    if not all(_instant(event.get(k)) is not None for k in ("occurred_at", "observed_at")):
        return False
    if not isinstance(event.get("occurred_at_offset"), str) or not re.fullmatch(r"(?:Z|[+-]\d{2}:\d{2})", event["occurred_at_offset"]):
        return False
    if event.get("eta") is not None and _instant(event["eta"]) is None:
        return False
    if type(event.get("operating_evidence")) is not bool:
        return False
    flags = event.get("flags")
    if type(flags) is not list or len(flags) > _MAX_EVIDENCE or not all(_text(flag) for flag in flags):
        return False
    quantity = event.get("quantity")
    if quantity is not None and (type(quantity) is not dict or not isinstance(quantity.get("amount"), str)
            or len(quantity["amount"]) > _MAX_AMOUNT_DIGITS or not _AMOUNT.fullmatch(quantity["amount"])
            or not _text(quantity.get("unit"))):
        return False
    if event["event_type"] == "partially_delivered" and quantity is None:
        return False
    if (event.get("facility") is None and event.get("lane") is None) or not _location(event.get("facility"), ("kind", "code")) or not _location(event.get("lane"), ("origin", "destination", "mode")):
        return False
    source = event.get("source")
    if type(source) is not dict or not all(_text(source.get(k)) for k in ("provider_id", "kind")) or (
            source.get("reference") is not None and not (isinstance(source["reference"], str)
                                                          and len(source["reference"]) <= _MAX_TEXT)):
        return False
    evidence = event.get("evidence")
    if type(evidence) is not list or not 0 < len(evidence) <= _MAX_EVIDENCE:
        return False
    for item in evidence:
        if type(item) is not dict or not all(_text(item.get(k)) for k in ("evidence_id", "kind", "locator")):
            return False
        if "valid_until" not in item:
            return False
        if not isinstance(item.get("content_sha256"), str) or not _SHA256.fullmatch(item["content_sha256"]):
            return False
        if _instant(item.get("retrieved_at")) is None or (item.get("valid_until") is not None
                                                         and _instant(item["valid_until"]) is None):
            return False
        if not isinstance(item.get("status"), str) or item["status"] not in {"expired", "current", "no_stated_expiry"}:
            return False
    return True


def _valid_rejection(row: object) -> bool:
    return (type(row) is dict and type(row.get("record_index")) is int and row["record_index"] >= 0
            and (row.get("event_id") is None or _identifier(row["event_id"]))
            and _text(row.get("reason")) and type(row.get("details")) is list
            and len(row["details"]) <= _MAX_EVIDENCE
            and all(isinstance(d, str) and len(d) <= _MAX_TEXT for d in row["details"]))


def _valid_data(normalized: object) -> bool:
    if type(normalized) is not dict or _instant(normalized.get("as_of")) is None:
        return False
    events, shipments, rejected = (normalized.get(k) for k in ("events", "shipments", "rejected"))
    if type(events) is not list or type(shipments) is not dict or type(rejected) is not list:
        return False
    if max(len(events), len(shipments), len(rejected)) > _MAX_ROWS:
        return False
    if not all(_valid_event(event) for event in events) or not all(_valid_rejection(row) for row in rejected):
        return False
    for shipment_id, shipment in shipments.items():
        if not _identifier(shipment_id) or type(shipment) is not dict or type(shipment.get("flags")) is not list:
            return False
        if len(shipment["flags"]) > _MAX_EVIDENCE or not all(_text(flag) for flag in shipment["flags"]):
            return False
    return True


def _snapshot(normalized: dict) -> dict:
    """Copy only the validated fields used by this view; ignore arbitrary caller extras."""
    events = []
    for event in normalized["events"]:
        row = {key: event[key] for key in ("event_id", "shipment_id", "event_type", "raw_type", "occurred_at",
                                             "occurred_at_offset", "observed_at", "eta", "operating_evidence")}
        row["flags"] = list(event["flags"])
        row["quantity"] = None if event["quantity"] is None else {k: event["quantity"][k] for k in ("amount", "unit")}
        row["facility"] = None if event["facility"] is None else {
            k: event["facility"][k] for k in ("kind", "code", "name") if k in event["facility"]}
        row["lane"] = None if event["lane"] is None else {
            k: event["lane"][k] for k in ("origin", "destination", "mode", "name") if k in event["lane"]}
        row["source"] = {k: event["source"].get(k) for k in ("provider_id", "kind", "reference")}
        row["evidence"] = [{k: item[k] for k in ("evidence_id", "kind", "locator", "content_sha256",
                                                 "retrieved_at", "valid_until", "status")}
                           for item in event["evidence"]]
        events.append(row)
    return {"events": events,
            "shipments": {sid: {"flags": list(row["flags"])} for sid, row in normalized["shipments"].items()},
            "rejected": [{k: list(row[k]) if k == "details" else row[k]
                          for k in ("record_index", "event_id", "reason", "details")}
                         for row in normalized["rejected"]]}


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
    if not _valid_data(normalized):
        return {**base, "status": "error", "reasons": ["invalid_normalized_input"]}, None
    if type(max_age_seconds) is not int or max_age_seconds < 0:
        return {**base, "status": "error", "reasons": ["invalid_max_age_seconds"]}, None
    current = _instant(now)
    if current is None:
        return {**base, "status": "error", "reasons": ["invalid_now"]}, None
    as_of = _instant(normalized["as_of"])
    if as_of > current:
        return {**base, "status": "error", "reasons": ["as_of_after_now"]}, None
    elapsed = current - as_of
    age = elapsed.days * 86400 + elapsed.seconds
    base.update(as_of=as_of.isoformat(), age_seconds=age)
    if age > max_age_seconds or (age == max_age_seconds and elapsed.microseconds > 0):
        base.update(status="stale", reasons=["data_older_than_max_age"])
    return base, _snapshot(normalized)


def _entry(event: dict) -> dict:
    keys = ("event_id", "event_type", "raw_type", "occurred_at", "occurred_at_offset", "observed_at", "eta",
            "quantity", "facility", "lane", "source", "operating_evidence", "flags")
    entry = {k: event.get(k) for k in keys}
    entry["evidence"] = [{k: e[k] for k in ("evidence_id", "kind", "locator", "content_sha256",
                                           "retrieved_at", "valid_until", "status")}
                         for e in event.get("evidence") or []]
    return entry


def _current(events: list[dict]) -> dict:
    def later(event, types):
        return any(o["operating_evidence"] and o["event_type"] in types
                   and o["occurred_at"] > event["occurred_at"] for o in events)

    open_etas = [e for e in events if e["event_type"] in ESTIMATE_TYPES and e.get("eta") and e["operating_evidence"]
                 and not later(e, ARRIVAL_TYPES)]
    latest_eta = max(open_etas, key=lambda e: (e["occurred_at"], e["observed_at"], e["event_id"]), default=None)
    holds = [e for e in events if e["event_type"] == "port_hold" and e["operating_evidence"]
             and not later(e, {"port_released", *ARRIVAL_TYPES})]
    delivered: dict[str, tuple[int, int]] = {}
    for e in events:
        if e["event_type"] == "partially_delivered" and e["operating_evidence"] and e.get("quantity"):
            unit = e["quantity"]["unit"]
            amount = e["quantity"]["amount"]
            whole, _, fraction = amount.partition(".")
            scale = len(fraction)
            number = int(whole + fraction)
            previous, old_scale = delivered.get(unit, (0, 0))
            new_scale = max(scale, old_scale)
            delivered[unit] = (previous * 10 ** (new_scale - old_scale)
                               + number * 10 ** (new_scale - scale), new_scale)
    def format_amount(total: tuple[int, int]) -> str:
        number, scale = total
        digits = str(number).zfill(scale + 1)
        return digits if not scale else digits[:-scale] + "." + digits[-scale:]
    return {"last_event_type": events[-1]["event_type"] if events else None,
            "open_eta": {"eta": latest_eta["eta"], "event_id": latest_eta["event_id"]} if latest_eta else None,
            "on_hold": bool(holds),
            "delivered_quantity": {unit: format_amount(total) for unit, total in sorted(delivered.items())},
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
    if records is not None and (type(records) is not list or len(records) > _MAX_ROWS):
        return {**result, "status": "error", "reasons": ["invalid_records"]}
    own = sorted((e for e in data["events"] if e.get("shipment_id") == shipment_id),
                 key=lambda e: (e["occurred_at"], e["event_id"]))
    if records is not None:
        result["rejected_records"] = [
            {k: r[k] for k in ("record_index", "event_id", "reason", "details")}
            for r in data["rejected"]
            if 0 <= r["record_index"] < len(records) and type(records[r["record_index"]]) is dict
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
        if not event["operating_evidence"]:
            continue
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
