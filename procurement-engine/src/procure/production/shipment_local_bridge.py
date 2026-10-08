"""Fixed, offline stdin/stdout transport for host-owned shipment comparisons."""
from __future__ import annotations

import hashlib
import json
import re
import sys

from procure.production._exact import ScreenError, instant
from procure.production.shipment_recovery import (
    _Abort, _bytes, _GRAPH_KEYS, _hash, _RAW_KEYS, _RAW_NESTED,
    compare_shipment_production_recovery,
)


_OUTER_KEYS = frozenset(("bridge_version", "host_context_json", "request_json"))
_HOST_KEYS = frozenset((
    "principal_id", "workspace_id", "request_id", "context_id", "context_revision",
    "plan_version", "propagation_snapshot_id", "production_snapshot_id", "as_of",
    "request_sha256",
))
_REQUEST_KEYS = frozenset((
    "adapter_version", "raw_records", "propagation_snapshot", "production_snapshot",
    "joins", "recovery_assignments",
))
_HEX32 = re.compile(r"[0-9a-f]{32}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_REVISION = re.compile(r"[1-9][0-9]*\Z")
_GRAPH_ROWS = {
    "nodes": frozenset(("id", "kind")),
    "edges": frozenset(("from", "to")),
    "inventory": frozenset(("component_id", "unit", "starting_stock")),
    "arrival_lines": frozenset(("line_id", "shipment_id", "purchase_order_id",
                                "component_id", "unit", "quantity", "arrival_at")),
    "commitments": frozenset(("commitment_id", "production_order_id", "required_at",
                              "due_at", "demands")),
}
_DEMAND_KEYS = frozenset(("component_id", "unit", "quantity"))
_REQUEST_LIMIT = 2 << 20
_PART_LIMIT = 1 << 20
_HOST_LIMIT = 8 << 10
_OUTCOME_LIMIT = 4 << 20
_STDIN_LIMIT = (4 << 20) + (32 << 10)
_STDOUT_LIMIT = (8 << 20) + (32 << 10)


class ProtocolError(ValueError):
    """A fixed child-protocol rejection, never serialized to stdout."""


def _pairs(items):
    value = {}
    for key, child in items:
        if key in value:
            raise ProtocolError("duplicate key")
        value[key] = child
    return value


def _number(_value):
    raise ProtocolError("number token")


def _decoded_unicode(value):
    """Validate JSON-decoded keys and values, including escaped Unicode."""
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if depth > 32:
            raise ProtocolError("JSON depth")
        if type(item) is str:
            try:
                item.encode("utf-8", errors="strict")
            except UnicodeError as exc:
                raise ProtocolError("invalid decoded Unicode") from exc
        elif type(item) is dict:
            for key, child in item.items():
                pending.append((key, depth + 1))
                pending.append((child, depth + 1))
        elif type(item) is list:
            pending.extend((child, depth + 1) for child in item)


def _decode(raw: bytes):
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(text, object_pairs_hook=_pairs, parse_int=_number,
                           parse_float=_number, parse_constant=_number)
        # Escapes can create lone surrogates in decoded keys or values.
        _decoded_unicode(value)
        return value
    except (UnicodeError, TypeError, ValueError, RecursionError) as exc:
        raise ProtocolError("invalid JSON") from exc


def _utf8(value: object, limit: int) -> bytes:
    if type(value) is not str:
        raise ProtocolError("string required")
    try:
        raw = value.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ProtocolError("invalid Unicode") from exc
    if len(raw) > limit:
        raise ProtocolError("byte limit")
    return raw


def _id(value: object) -> bool:
    return type(value) is str and 0 < len(value) <= 256 and value == value.strip()


def _host(raw: bytes) -> dict:
    if len(raw) > _HOST_LIMIT:
        raise ProtocolError("host byte limit")
    host = _decode(raw)
    if type(host) is not dict or set(host) != _HOST_KEYS:
        raise ProtocolError("host fields")
    for name in ("principal_id", "workspace_id", "plan_version",
                 "propagation_snapshot_id", "production_snapshot_id"):
        if not _id(host[name]):
            raise ProtocolError("host ID")
    if (type(host["request_id"]) is not str or not _HEX32.fullmatch(host["request_id"])
            or type(host["context_id"]) is not str or not _HEX32.fullmatch(host["context_id"])):
        raise ProtocolError("opaque ID")
    revision = host["context_revision"]
    if (type(revision) is not str or len(revision) > 10 or
            not _REVISION.fullmatch(revision) or int(revision) > 2147483647):
        raise ProtocolError("revision")
    if (type(host["request_sha256"]) is not str or
            not _HEX64.fullmatch(host["request_sha256"])):
        raise ProtocolError("request hash")
    if type(host["as_of"]) is not str or len(host["as_of"]) > 40:
        raise ProtocolError("cutoff")
    try:
        instant(host["as_of"])
    except ScreenError as exc:
        raise ProtocolError("cutoff") from exc
    return host


def _preflight_fields(request: dict):
    """Reject undeclared authority before canonical ownership copying."""
    records = request["raw_records"]
    if type(records) is list:
        for record in records:
            if type(record) is not dict:
                continue  # The accepted factory reports malformed records.
            if set(record) - _RAW_KEYS:
                raise ProtocolError("raw fields")
            for name, allowed in _RAW_NESTED.items():
                value = record.get(name)
                rows = value if name == "evidence" and type(value) is list else [value]
                for row in rows:
                    if type(row) is dict and set(row) - allowed:
                        raise ProtocolError("raw nested fields")
    graph = request["propagation_snapshot"]
    if type(graph) is not dict:
        return
    if set(graph) - _GRAPH_KEYS:
        raise ProtocolError("graph fields")
    for name, allowed in _GRAPH_ROWS.items():
        rows = graph.get(name)
        if type(rows) is not list:
            continue
        for row in rows:
            if type(row) is dict and set(row) - allowed:
                raise ProtocolError("graph row fields")
            if name == "commitments" and type(row) is dict:
                demands = row.get("demands")
                if type(demands) is list:
                    for demand in demands:
                        if type(demand) is dict and set(demand) - _DEMAND_KEYS:
                            raise ProtocolError("graph demand fields")


def _request(raw: bytes, host: dict) -> dict:
    if len(raw) > _REQUEST_LIMIT:
        raise ProtocolError("request byte limit")
    if hashlib.sha256(raw).hexdigest() != host["request_sha256"]:
        raise ProtocolError("request hash mismatch")
    request = _decode(raw)
    if (type(request) is not dict or set(request) != _REQUEST_KEYS or
            request.get("adapter_version") != "1"):
        raise ProtocolError("request fields")
    _preflight_fields(request)
    try:
        _bytes(request, _REQUEST_LIMIT, "input")
        _bytes(request["raw_records"], _PART_LIMIT, "input")
        _bytes(request["propagation_snapshot"], _PART_LIMIT, "input")
    except _Abort as exc:
        raise ProtocolError("request bound or type") from exc
    graph, production = request["propagation_snapshot"], request["production_snapshot"]
    if type(graph) is not dict or type(production) is not dict:
        raise ProtocolError("snapshot type")
    for snapshot, snapshot_id in (
        (graph, host["propagation_snapshot_id"]),
        (production, host["production_snapshot_id"]),
    ):
        if (snapshot.get("workspace_id") != host["workspace_id"] or
                snapshot.get("plan_version") != host["plan_version"] or
                snapshot.get("snapshot_id") != snapshot_id or
                snapshot.get("synthetic") is not True):
            raise ProtocolError("snapshot scope")
        try:
            if instant(snapshot.get("as_of")) != instant(host["as_of"]):
                raise ProtocolError("snapshot cutoff")
        except ScreenError as exc:
            raise ProtocolError("snapshot cutoff") from exc
    return request


def _json_bytes(value: object, limit: int) -> bytes:
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError) as exc:
        raise ProtocolError("output serialization") from exc
    if len(encoded) > limit:
        raise ProtocolError("output byte limit")
    return encoded


def process(raw: bytes) -> bytes:
    """Return one fully validated response or raise before writing anything."""
    if type(raw) is not bytes or len(raw) > _STDIN_LIMIT:
        raise ProtocolError("stdin byte limit")
    outer = _decode(raw)
    if (type(outer) is not dict or set(outer) != _OUTER_KEYS or
            outer.get("bridge_version") != "1"):
        raise ProtocolError("outer fields")
    host_raw = _utf8(outer["host_context_json"], _HOST_LIMIT)
    request_raw = _utf8(outer["request_json"], _REQUEST_LIMIT)
    host = _host(host_raw)
    request = _request(request_raw, host)
    binding = {
        "adapter_version": "1", "synthetic": True,
        "workspace_id": host["workspace_id"], "plan_version": host["plan_version"],
        "propagation_snapshot_id": host["propagation_snapshot_id"],
        "production_snapshot_id": host["production_snapshot_id"], "as_of": host["as_of"],
        "raw_sha256": _hash(request["raw_records"]),
        "propagation_sha256": _hash(request["propagation_snapshot"]),
        "production_sha256": _hash(request["production_snapshot"]),
        "joins_sha256": _hash(request["joins"]),
        "assignments_sha256": _hash(request["recovery_assignments"]),
    }
    outcome = compare_shipment_production_recovery(request, expected_binding=binding)
    if type(outcome) is not dict or outcome.get("status") not in (
            "evaluated", "no_eligible_scenario", "rejected"):
        raise ProtocolError("adapter outcome")
    if outcome.get("status") != "rejected" and outcome.get("binding") != dict(
            binding, binding_sha256=_hash(binding)):
        raise ProtocolError("adapter binding")
    outcome_json = _json_bytes(outcome, _OUTCOME_LIMIT).decode("utf-8")
    response = {
        "bridge_version": "1",
        "binding": {
            "principal_id": host["principal_id"], "workspace_id": host["workspace_id"],
            "request_id": host["request_id"], "context_id": host["context_id"],
            "context_revision": host["context_revision"],
            "host_sha256": hashlib.sha256(host_raw).hexdigest(),
            "request_sha256": host["request_sha256"],
        },
        "outcome_json": outcome_json,
    }
    return _json_bytes(response, _STDOUT_LIMIT)


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(_STDIN_LIMIT + 1)
        response = process(raw)
    except (ProtocolError, _Abort, ScreenError, ValueError, TypeError,
            UnicodeError, RecursionError, OverflowError):
        return 1
    except Exception:
        return 1
    sys.stdout.buffer.write(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
