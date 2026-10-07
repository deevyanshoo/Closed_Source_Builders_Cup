"""Normalise raw shipment milestone records into explicit, validated events.

``normalize_milestones(records, as_of=...)`` is pure: it never mutates its input, persists
anything or calls a provider. Conventions follow ``events/propagation/projection.py``:
identifiers are non-empty strings without surrounding spaces, instants need seconds and a
``Z`` or ``+HH:MM`` offset (at most six fractional digits) and are returned in UTC as
``YYYY-MM-DDTHH:MM:SS+00:00``, quantities are plain non-negative decimal strings kept
verbatim, and invalid input is rejected with a reason code instead of being repaired.

Rules
- A record observed after ``as_of`` is rejected: it was not available at that time.
- A record observed before it occurred is rejected.
- Event types map from an explicit alias table; anything else is ``unsupported_event_type``.
- Each event needs a facility or a lane, a source and at least one evidence reference.
- Copies of one ``event_id`` that differ only in when they were observed are duplicates; the
  earliest observation is kept. Copies that differ in anything else are all rejected as
  ``conflicting_duplicate``: which copy is right is not guessed.
- Evidence whose ``valid_until`` is before ``as_of`` is kept and marked ``expired``. An event
  whose evidence has all expired is kept for review but is not operating evidence.
- Evidence retrieved after ``as_of`` rejects the whole event before duplicate grouping;
  evidence retrieved later than observation but by ``as_of`` may corroborate it.
- Flags describe, never change, events: ``observed_out_of_order``, ``superseded_by_arrival``,
  ``evidence_expired``; shipments can carry ``sequence_conflict``.
"""
from __future__ import annotations

import copy
import json
import re
from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime, timezone

# Same instant pattern as events/propagation/projection.py.
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$")
_AMOUNT = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

EVENT_TYPES = {  # canonical type -> accepted raw labels (lower case, spaces and hyphens as underscores)
    "departed": {"departed", "departure"},
    "arrived": {"arrived", "arrival"},
    "eta_changed": {"eta_changed", "eta_update"},
    "port_hold": {"port_hold"},
    "port_released": {"port_released"},
    "partially_delivered": {"partially_delivered", "partial_delivery"},
    "delivered": {"delivered"},
}
_ALIASES = {alias: canonical for canonical, aliases in EVENT_TYPES.items() for alias in aliases}
ARRIVAL_TYPES = {"arrived", "partially_delivered", "delivered"}
ESTIMATE_TYPES = {"eta_changed", "port_hold"}


class _Reject(Exception):
    def __init__(self, reason: str, *details: str):
        super().__init__(reason)
        self.reason = reason
        self.details = tuple(details)


def _identifier(record: Mapping, name: str) -> str:
    value = record.get(name)
    if not isinstance(value, str) or not value or value != value.strip():
        raise _Reject("missing_identifier", name)
    return value


def _instant(value: object, name: str) -> datetime:
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        raise _Reject("invalid_timestamp", name)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError) as exc:
        raise _Reject("invalid_timestamp", name) from exc
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _offset(text: str) -> str:
    return "+00:00" if text.endswith("Z") else text[-6:]


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _location(record: Mapping) -> tuple[dict | None, dict | None]:
    facility, lane = record.get("facility"), record.get("lane")
    if facility is None and lane is None:
        raise _Reject("missing_location")
    out = []
    for name, value, required in (("facility", facility, ("kind", "code")), ("lane", lane, ("origin", "destination", "mode"))):
        if value is None:
            out.append(None)
            continue
        if not isinstance(value, Mapping) or not all(_text(value.get(k)) for k in required) or (
                value.get("name") is not None and not isinstance(value.get("name"), str)):
            raise _Reject("invalid_location", name)
        out.append({k: value[k] for k in (*required, "name") if k in value})
    return out[0], out[1]


def _source(record: Mapping) -> dict:
    source = record.get("source")
    if not isinstance(source, Mapping) or not _text(source.get("provider_id")) or not _text(source.get("kind")) or (
            source.get("reference") is not None and not isinstance(source.get("reference"), str)):
        raise _Reject("missing_source")
    return {"provider_id": source["provider_id"], "kind": source["kind"], "reference": source.get("reference")}


def _evidence(record: Mapping, as_of: datetime) -> list[dict]:
    items = record.get("evidence")
    if not isinstance(items, list) or not items:
        raise _Reject("missing_evidence")
    out = []
    for index, item in enumerate(items):
        label = f"ev:{item.get('evidence_id')}" if isinstance(item, Mapping) and _text(item.get("evidence_id")) \
            else f"ev:#{index}"
        if not isinstance(item, Mapping) or not all(_text(item.get(k)) for k in ("evidence_id", "kind", "locator")) \
                or not isinstance(item.get("content_sha256"), str) or not _SHA256.fullmatch(item["content_sha256"]):
            raise _Reject("invalid_evidence", label)
        try:
            retrieved = _instant(item.get("retrieved_at"), "retrieved_at")
            valid_until = None if item.get("valid_until") is None else _instant(item["valid_until"], "valid_until")
        except _Reject as exc:
            raise _Reject("invalid_evidence", label) from exc
        if retrieved > as_of:
            raise _Reject("evidence_after_as_of", label)
        status = "no_stated_expiry" if valid_until is None else ("expired" if valid_until < as_of else "current")
        out.append({"evidence_id": item["evidence_id"], "kind": item["kind"], "locator": item["locator"],
                    "content_sha256": item["content_sha256"], "retrieved_at": _iso(retrieved),
                    "valid_until": None if valid_until is None else _iso(valid_until), "status": status})
    return out


def _quantity(record: Mapping) -> dict | None:
    quantity = record.get("quantity")
    if quantity is None:
        return None
    if not isinstance(quantity, Mapping) or not isinstance(quantity.get("amount"), str) or \
            not _AMOUNT.fullmatch(quantity["amount"]) or not _text(quantity.get("unit")):
        raise _Reject("invalid_quantity", "quantity")
    return {"amount": quantity["amount"], "unit": quantity["unit"]}


def _event(record: object, index: int, as_of: datetime) -> dict:
    if not isinstance(record, Mapping):
        raise _Reject("invalid_record")
    if not isinstance(record.get("synthetic"), bool):
        raise _Reject("invalid_record", "synthetic")
    event_id = _identifier(record, "event_id")
    shipment_id = _identifier(record, "shipment_id")
    raw_type = record.get("type")
    key = re.sub(r"[\s-]+", "_", raw_type.strip().lower()) if isinstance(raw_type, str) else ""
    if key not in _ALIASES:
        raise _Reject("unsupported_event_type", str(raw_type))
    event_type = _ALIASES[key]
    occurred = _instant(record.get("occurred_at"), "occurred_at")
    observed = _instant(record.get("observed_at"), "observed_at")
    eta = None if record.get("eta") is None else _instant(record.get("eta"), "eta")
    if observed > as_of:
        raise _Reject("observed_after_as_of")
    if observed < occurred:
        raise _Reject("observed_before_occurred")
    facility, lane = _location(record)
    source = _source(record)
    evidence = _evidence(record, as_of)
    if event_type == "eta_changed" and eta is None:
        raise _Reject("missing_eta")
    quantity = _quantity(record)
    if event_type == "partially_delivered" and quantity is None:
        raise _Reject("invalid_quantity", "quantity")
    operating = any(e["status"] != "expired" for e in evidence)
    return {"event_id": event_id, "shipment_id": shipment_id, "event_type": event_type, "raw_type": raw_type,
            "occurred_at": _iso(occurred), "occurred_at_offset": _offset(record["occurred_at"]),
            "observed_at": _iso(observed), "eta": None if eta is None else _iso(eta), "quantity": quantity,
            "facility": facility, "lane": lane, "source": source, "evidence": evidence,
            "operating_evidence": operating, "flags": [] if operating else ["evidence_expired"],
            "synthetic": record["synthetic"], "record_index": index}


def _assertion(event: dict) -> str:
    """What an event claims, without when it was observed or fetched."""
    claim = {k: v for k, v in event.items() if k not in ("observed_at", "record_index", "flags")}
    claim["evidence"] = [{k: v for k, v in e.items() if k != "retrieved_at"} for e in event["evidence"]]
    return json.dumps(claim, sort_keys=True)


def normalize_milestones(records: list, *, as_of: str) -> dict:
    if not isinstance(as_of, str) or not _TIMESTAMP.fullmatch(as_of):
        raise ValueError("as_of must be an instant with seconds and a time zone, for example 2026-10-08T12:00:00Z")
    cutoff = datetime.fromisoformat(as_of.replace("Z", "+00:00")).astimezone(timezone.utc)
    records = copy.deepcopy(list(records))

    valid, rejected = [], []
    for index, record in enumerate(records):
        try:
            valid.append(_event(record, index, cutoff))
        except _Reject as exc:
            event_id = record.get("event_id") if isinstance(record, Mapping) else None
            rejected.append({"record_index": index, "event_id": event_id if _text(event_id) else None,
                             "reason": exc.reason, "details": list(exc.details)})

    groups: dict[str, list[dict]] = defaultdict(list)
    for event in valid:
        groups[event["event_id"]].append(event)
    events, duplicates = [], []
    for event_id, copies in groups.items():
        if len(copies) == 1:
            events.append(copies[0])
        elif len({_assertion(c) for c in copies}) == 1:
            kept = min(copies, key=lambda c: (c["observed_at"], c["record_index"]))
            events.append(kept)
            duplicates.append({"event_id": event_id, "record_indexes": sorted(c["record_index"] for c in copies),
                               "kept_record_index": kept["record_index"]})
        else:
            rejected.extend({"record_index": c["record_index"], "event_id": event_id,
                             "reason": "conflicting_duplicate", "details": [event_id]} for c in copies)

    events.sort(key=lambda e: (e["shipment_id"], e["occurred_at"], e["event_id"]))
    shipments: dict[str, dict] = {}
    for shipment_id in sorted({e["shipment_id"] for e in events}):
        own = [e for e in events if e["shipment_id"] == shipment_id]
        flags = []
        for event in own:
            if any(o["occurred_at"] > event["occurred_at"] and o["observed_at"] < event["observed_at"] for o in own):
                event["flags"].append("observed_out_of_order")
            if event["event_type"] in ESTIMATE_TYPES and any(
                    o["event_type"] in {"arrived", "delivered"} and o["occurred_at"] > event["occurred_at"] for o in own):
                event["flags"].append("superseded_by_arrival")
        departures = [e["occurred_at"] for e in own if e["event_type"] == "departed"]
        if departures and any(e["event_type"] in ARRIVAL_TYPES and e["occurred_at"] < min(departures) for e in own):
            flags.append("sequence_conflict")
        shipments[shipment_id] = {"event_ids": [e["event_id"] for e in own], "flags": flags}

    rejected.sort(key=lambda r: (r["record_index"], r["reason"]))
    duplicates.sort(key=lambda d: d["event_id"])
    return {"as_of": _iso(cutoff), "events": events, "rejected": rejected, "duplicates": duplicates,
            "shipments": shipments}
