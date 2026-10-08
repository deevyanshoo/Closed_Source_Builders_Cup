"""Route evidence records: hazards and operating notices for facilities and lanes.

``normalize_route_evidence(records, as_of=...)`` validates records into a fixed shape and is pure:
no input is modified, nothing is persisted and no provider is called. Conventions follow the
shipment milestone normaliser: instants need seconds and a time zone and are returned in UTC,
identifiers are non-empty strings, and invalid records are rejected with a reason code.

Each record states its source, when it was retrieved, the period it describes, and an expiry.
Status is either:
  exposure   a hazard or condition near a facility or lane. Weather and hazard sources can only
             ever be exposure: proximity does not prove a closure or delay.
  confirmed  an operating status (closed, restricted, delayed, normal) asserted by an operating
             source: a port authority notice, carrier confirmation or facility operator.

Expiry is ``current``, ``expired`` (``valid_until`` before ``as_of``) or ``no_stated_expiry``.
A record retrieved after ``as_of`` is rejected: it was not available at that time.
"""
from __future__ import annotations

import copy
import json
import re
from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime, timezone

_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
KINDS = {"weather_hazard", "route_hazard", "port_operations", "facility_operations"}
HAZARD_SOURCES = {"weather_service", "hazard_feed"}
OPERATING_SOURCES = {"port_authority_notice", "carrier_confirmation", "facility_operator"}
OPERATING_STATUSES = {"closed", "restricted", "delayed", "normal"}


class _Reject(Exception):
    def __init__(self, reason: str, *details: str):
        super().__init__(reason)
        self.reason = reason
        self.details = tuple(details)


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _instant(value: object, name: str) -> datetime:
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        raise _Reject("invalid_timestamp", name)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError as exc:
        raise _Reject("invalid_timestamp", name) from exc


def _location(record: Mapping) -> tuple[dict | None, dict | None, list[str]]:
    facility, lane = record.get("facility"), record.get("lane")
    if facility is None and lane is None:
        raise _Reject("missing_location")
    keys = []
    if facility is not None:
        if not isinstance(facility, Mapping) or not _text(facility.get("kind")) or not _text(facility.get("code")):
            raise _Reject("invalid_location", "facility")
        facility = {k: facility[k] for k in ("kind", "code", "name") if k in facility}
        keys.append(facility["code"])
    if lane is not None:
        if not isinstance(lane, Mapping) or not all(_text(lane.get(k)) for k in ("origin", "destination", "mode")):
            raise _Reject("invalid_location", "lane")
        lane = {k: lane[k] for k in ("origin", "destination", "mode")}
        keys.extend((lane["origin"], lane["destination"]))
    return facility, lane, sorted(set(keys))


def _record(record: object, as_of: datetime) -> dict:
    if not isinstance(record, Mapping) or not isinstance(record.get("synthetic"), bool):
        raise _Reject("invalid_record", "synthetic")
    evidence_id = record.get("evidence_id")
    if not _text(evidence_id) or evidence_id != evidence_id.strip():
        raise _Reject("missing_identifier", "evidence_id")
    if record.get("kind") not in KINDS:
        raise _Reject("unsupported_kind", str(record.get("kind")))
    retrieved = _instant(record.get("retrieved_at"), "retrieved_at")
    period = record.get("observed_period")
    if not isinstance(period, Mapping):
        raise _Reject("invalid_timestamp", "observed_period")
    start = _instant(period.get("start"), "observed_period.start")
    end = None if period.get("end") is None else _instant(period.get("end"), "observed_period.end")
    valid_until = None if record.get("valid_until") is None else _instant(record.get("valid_until"), "valid_until")
    source = record.get("source")
    if not isinstance(source, Mapping) or not _text(source.get("provider_id")) or \
            source.get("kind") not in HAZARD_SOURCES | OPERATING_SOURCES:
        raise _Reject("missing_source")
    status, operating = record.get("status"), record.get("operating_status")
    if status not in ("exposure", "confirmed"):
        raise _Reject("invalid_status", str(status))
    if status == "confirmed" and operating is None:
        raise _Reject("missing_operating_status")
    if status == "exposure" and operating is not None:
        raise _Reject("operating_status_needs_confirmation")
    if status == "confirmed" and operating not in OPERATING_STATUSES:
        raise _Reject("invalid_status", str(operating))
    if status == "confirmed" and source["kind"] not in OPERATING_SOURCES:
        raise _Reject("confirmation_requires_operating_source", source["kind"])
    facility, lane, keys = _location(record)
    if not isinstance(record.get("content_sha256"), str) or not _SHA256.fullmatch(record["content_sha256"]):
        raise _Reject("invalid_record", "content_sha256")
    if end is not None and end < start:
        raise _Reject("invalid_period")
    if retrieved > as_of:
        raise _Reject("retrieved_after_as_of")
    expiry = "no_stated_expiry" if valid_until is None else ("expired" if valid_until < as_of else "current")
    return {"evidence_id": evidence_id, "kind": record["kind"], "summary": record.get("summary"),
            "facility": facility, "lane": lane, "location_keys": keys,
            "source": {"provider_id": source["provider_id"], "kind": source["kind"],
                       "reference": source.get("reference")},
            "retrieved_at": retrieved.isoformat(),
            "observed_period": {"start": start.isoformat(), "end": None if end is None else end.isoformat()},
            "status": status, "operating_status": operating,
            "valid_until": None if valid_until is None else valid_until.isoformat(), "expiry": expiry,
            "content_sha256": record["content_sha256"], "synthetic": record["synthetic"]}


def normalize_route_evidence(records: list, *, as_of: str) -> dict:
    if not isinstance(as_of, str) or not _TIMESTAMP.fullmatch(as_of):
        raise ValueError("as_of must be an instant with seconds and a time zone, for example 2026-10-08T12:00:00Z")
    cutoff = datetime.fromisoformat(as_of.replace("Z", "+00:00")).astimezone(timezone.utc)
    accepted, rejected = defaultdict(list), []
    for index, record in enumerate(copy.deepcopy(list(records))):
        try:
            item = _record(record, cutoff)
            accepted[item["evidence_id"]].append((index, item))
        except _Reject as exc:
            evidence_id = record.get("evidence_id") if isinstance(record, Mapping) else None
            rejected.append({"record_index": index, "evidence_id": evidence_id if _text(evidence_id) else None,
                             "reason": exc.reason, "details": list(exc.details)})
    evidence = []
    for evidence_id, copies in accepted.items():
        if len({json.dumps(item, sort_keys=True) for _, item in copies}) == 1:
            evidence.append(copies[0][1])
        else:
            rejected.extend({"record_index": index, "evidence_id": evidence_id, "reason": "conflicting_duplicate",
                             "details": [evidence_id]} for index, _ in copies)
    evidence.sort(key=lambda e: e["evidence_id"])
    rejected.sort(key=lambda r: (r["record_index"], r["reason"]))
    return {"as_of": cutoff.isoformat(), "evidence": evidence, "rejected": rejected}


def evidence_for_locations(result: Mapping, location_keys: list[str]) -> dict:
    """Evidence touching any of the facility or lane-end keys, split by what it can support."""
    wanted = set(location_keys)
    found = {"confirmed": [], "exposure": [], "expired": []}
    for item in result["evidence"]:
        if not wanted & set(item["location_keys"]):
            continue
        bucket = "expired" if item["expiry"] == "expired" else item["status"]
        found[bucket].append(copy.deepcopy(item))
    status = "ok" if any(found.values()) else "empty"
    return {"status": status, "as_of": result["as_of"], "location_keys": sorted(wanted), **found}
