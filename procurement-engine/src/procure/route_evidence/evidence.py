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
OPERATING_KINDS = {"port_operations", "facility_operations"}
_MAX_RECORDS = 10_000
_MAX_QUERY_KEYS = 1_000
_MAX_TEXT = 16_384
_MAX_DETAILS = 100


class _Reject(Exception):
    def __init__(self, reason: str, *details: str):
        super().__init__(reason)
        self.reason = reason
        self.details = tuple(details)


def _text(value: object) -> bool:
    return isinstance(value, str) and 0 < len(value) <= _MAX_TEXT and bool(value.strip())


def _optional_text(value: object) -> bool:
    return value is None or (isinstance(value, str) and len(value) <= _MAX_TEXT)


def _instant(value: object, name: str) -> datetime:
    if not isinstance(value, str) or len(value) > 35 or not _TIMESTAMP.fullmatch(value):
        raise _Reject("invalid_timestamp", name)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise _Reject("invalid_timestamp", name) from exc


def _location(record: Mapping) -> tuple[dict | None, dict | None, list[str]]:
    facility, lane = record.get("facility"), record.get("lane")
    if facility is None and lane is None:
        raise _Reject("missing_location")
    keys = []
    if facility is not None:
        if type(facility) is not dict or not _text(facility.get("kind")) or not _text(facility.get("code")) \
                or not _optional_text(facility.get("name")):
            raise _Reject("invalid_location", "facility")
        facility = {k: facility[k] for k in ("kind", "code", "name") if k in facility}
        keys.append(facility["code"])
    if lane is not None:
        if type(lane) is not dict or not all(_text(lane.get(k)) for k in ("origin", "destination", "mode")):
            raise _Reject("invalid_location", "lane")
        lane = {k: lane[k] for k in ("origin", "destination", "mode")}
        keys.extend((lane["origin"], lane["destination"]))
    return facility, lane, sorted(set(keys))


def _record(record: object, as_of: datetime) -> dict:
    if type(record) is not dict or type(record.get("synthetic")) is not bool:
        raise _Reject("invalid_record", "synthetic")
    evidence_id = record.get("evidence_id")
    if not _text(evidence_id) or evidence_id != evidence_id.strip():
        raise _Reject("missing_identifier", "evidence_id")
    kind = record.get("kind")
    if not _text(kind) or kind not in KINDS:
        raise _Reject("unsupported_kind")
    retrieved = _instant(record.get("retrieved_at"), "retrieved_at")
    period = record.get("observed_period")
    if type(period) is not dict:
        raise _Reject("invalid_timestamp", "observed_period")
    start = _instant(period.get("start"), "observed_period.start")
    end = None if period.get("end") is None else _instant(period.get("end"), "observed_period.end")
    valid_until = None if record.get("valid_until") is None else _instant(record.get("valid_until"), "valid_until")
    source = record.get("source")
    if type(source) is not dict or not _text(source.get("provider_id")) or \
            not _text(source.get("kind")) or source["kind"] not in HAZARD_SOURCES | OPERATING_SOURCES \
            or not _optional_text(source.get("reference")):
        raise _Reject("missing_source")
    if not _optional_text(record.get("summary")):
        raise _Reject("invalid_record", "summary")
    status, operating = record.get("status"), record.get("operating_status")
    if not _text(status) or status not in ("exposure", "confirmed"):
        raise _Reject("invalid_status")
    if status == "confirmed" and operating is None:
        raise _Reject("missing_operating_status")
    if status == "exposure" and operating is not None:
        raise _Reject("operating_status_needs_confirmation")
    if status == "confirmed" and (not _text(operating) or operating not in OPERATING_STATUSES):
        raise _Reject("invalid_status")
    if status == "confirmed" and source["kind"] not in OPERATING_SOURCES:
        raise _Reject("confirmation_requires_operating_source", source["kind"])
    if status == "confirmed" and kind not in OPERATING_KINDS:
        raise _Reject("confirmation_requires_operating_kind")
    facility, lane, keys = _location(record)
    if not isinstance(record.get("content_sha256"), str) or len(record["content_sha256"]) != 64 \
            or not _SHA256.fullmatch(record["content_sha256"]):
        raise _Reject("invalid_record", "content_sha256")
    if end is not None and end < start:
        raise _Reject("invalid_period")
    if retrieved > as_of:
        raise _Reject("retrieved_after_as_of")
    expiry = "no_stated_expiry" if valid_until is None else ("expired" if valid_until < as_of else "current")
    period_state = "future" if start > as_of else ("past" if end is not None and end < as_of else "current")
    return {"evidence_id": evidence_id, "kind": record["kind"], "summary": record.get("summary"),
            "facility": facility, "lane": lane, "location_keys": keys,
            "source": {"provider_id": source["provider_id"], "kind": source["kind"],
                       "reference": source.get("reference")},
            "retrieved_at": retrieved.isoformat(),
            "observed_period": {"start": start.isoformat(), "end": None if end is None else end.isoformat()},
            "status": status, "operating_status": operating,
            "valid_until": None if valid_until is None else valid_until.isoformat(), "expiry": expiry,
            "period_state": period_state,
            "content_sha256": record["content_sha256"], "synthetic": record["synthetic"]}


def normalize_route_evidence(records: list, *, as_of: str) -> dict:
    if type(records) is not list or len(records) > _MAX_RECORDS:
        raise ValueError("records must be a bounded list")
    try:
        cutoff = _instant(as_of, "as_of")
    except _Reject as exc:
        raise ValueError("as_of must be a valid instant with seconds and a time zone") from exc
    accepted, rejected = defaultdict(list), []
    for index, record in enumerate(records):
        try:
            item = _record(record, cutoff)
            accepted[item["evidence_id"]].append((index, item))
        except _Reject as exc:
            evidence_id = record.get("evidence_id") if type(record) is dict else None
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


_ITEM_KEYS = {"evidence_id", "kind", "summary", "facility", "lane", "location_keys", "source",
              "retrieved_at", "observed_period", "status", "operating_status", "valid_until",
              "expiry", "period_state", "content_sha256", "synthetic"}


def _valid_result(result: object) -> bool:
    """Reapply normalization rules to bounded caller-supplied rows before bucket selection."""
    if type(result) is not dict or set(result) != {"as_of", "evidence", "rejected"}:
        return False
    try:
        cutoff = _instant(result["as_of"], "as_of")
    except _Reject:
        return False
    if result["as_of"] != cutoff.isoformat():
        return False
    evidence, rejected = result["evidence"], result["rejected"]
    if type(evidence) is not list or type(rejected) is not list or max(len(evidence), len(rejected)) > _MAX_RECORDS:
        return False
    previous_id = ""
    for item in evidence:
        if type(item) is not dict or set(item) != _ITEM_KEYS:
            return False
        if (type(item["location_keys"]) is not list or len(item["location_keys"]) > 3
                or not all(_text(key) for key in item["location_keys"])):
            return False
        if item["expiry"] not in ("current", "expired", "no_stated_expiry") or item["period_state"] not in ("past", "current", "future"):
            return False
        if (type(item["source"]) is not dict or set(item["source"]) != {"provider_id", "kind", "reference"}
                or type(item["observed_period"]) is not dict
                or set(item["observed_period"]) != {"start", "end"}):
            return False
        if item["facility"] is not None and (type(item["facility"]) is not dict
                or not {"kind", "code"} <= set(item["facility"])
                or not set(item["facility"]) <= {"kind", "code", "name"}):
            return False
        if item["lane"] is not None and (type(item["lane"]) is not dict
                or set(item["lane"]) != {"origin", "destination", "mode"}):
            return False
        try:
            canonical = _record(item, cutoff)
        except _Reject:
            return False
        if canonical != item or canonical["evidence_id"] <= previous_id:
            return False
        previous_id = canonical["evidence_id"]
    for row in rejected:
        if type(row) is not dict or set(row) != {"record_index", "evidence_id", "reason", "details"}:
            return False
        if type(row["record_index"]) is not int or row["record_index"] < 0:
            return False
        if row["evidence_id"] is not None and not _text(row["evidence_id"]):
            return False
        if (not _text(row["reason"]) or type(row["details"]) is not list
                or len(row["details"]) > _MAX_DETAILS
                or not all(_optional_text(detail) and detail is not None for detail in row["details"])):
            return False
    return True


def evidence_for_locations(result: Mapping, location_keys: list[str]) -> dict:
    """Evidence touching any of the facility or lane-end keys, split by what it can support."""
    found = {"confirmed": [], "exposure": [], "expired": [], "outside_period": []}
    base = {"status": "error", "reasons": [], "as_of": None, "location_keys": [], **found}
    if (type(location_keys) is not list or len(location_keys) > _MAX_QUERY_KEYS
            or not all(_text(key) and key == key.strip() for key in location_keys)):
        return {**base, "reasons": ["invalid_location_keys"]}
    wanted = set(location_keys)
    if not _valid_result(result):
        return {**base, "reasons": ["invalid_normalized_input"]}
    for item in result["evidence"]:
        if not wanted.intersection(item["location_keys"]):
            continue
        bucket = "expired" if item["expiry"] == "expired" else (
            "outside_period" if item["period_state"] != "current" else item["status"])
        found[bucket].append(copy.deepcopy(item))
    status = "ok" if any(found.values()) else "empty"
    return {"status": status, "reasons": [], "as_of": result["as_of"], "location_keys": sorted(wanted), **found}
