"""Turn normalised milestones into arrival-change scenarios for ``events/propagation``.

``bind_synthetic_milestones(records, snapshot, expected_*_id)`` normalizes raw
records at the snapshot cutoff and owns the resulting local synthetic binding.
``build_arrival_scenarios(binding, snapshot)`` produces, per shipment, at most one scenario
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
import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping

from .milestones import ESTIMATE_TYPES, _instant, normalize_milestones

NO_ARRIVAL_INFORMATION = {"departed", "port_released"}
ACTUAL_ARRIVAL = {"arrived", "delivered"}
_INPUT_LIMIT = 1 << 20
_RECEIPT_LIMIT = 128 << 10
_MAX_DEPTH = 16
_MAX_ENTRIES = 16384
_FACTORY_TOKEN = object()


class _BoundSyntheticMilestones:
    """Owned local bytes; construction is reserved for the normalization factory."""

    __slots__ = ("_snapshot_bytes", "_normalized_bytes", "_raw_sha256",
                 "_snapshot_sha256", "_normalized_sha256", "_binding_sha256")

    def __init__(self, snapshot_bytes: bytes, normalized_bytes: bytes, raw_sha256: str,
                 snapshot_sha256: str, normalized_sha256: str, binding_sha256: str,
                 *, _token: object = None):
        if _token is not _FACTORY_TOKEN:
            raise TypeError("binding must come from the local factory")
        for name, value in (("_snapshot_bytes", snapshot_bytes), ("_normalized_bytes", normalized_bytes),
                            ("_raw_sha256", raw_sha256), ("_snapshot_sha256", snapshot_sha256),
                            ("_normalized_sha256", normalized_sha256), ("_binding_sha256", binding_sha256)):
            object.__setattr__(self, name, value)

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("bound milestones are immutable")


def _checked_json_bytes(value: object) -> bytes:
    """Bound plain JSON before ownership copying or milestone normalization."""
    count, budget, seen = [0], [0], set()

    def check(item: object, depth: int) -> None:
        if depth > _MAX_DEPTH:
            raise ValueError("invalid synthetic input")
        if type(item) is str:
            if len(item) > _INPUT_LIMIT:
                raise ValueError("invalid synthetic input")
            budget[0] += len(item.encode("utf-8"))
            if budget[0] > _INPUT_LIMIT:
                raise ValueError("invalid synthetic input")
            return
        if item is None or type(item) is bool:
            return
        if type(item) not in (dict, list):
            raise ValueError("invalid synthetic input")
        identity = id(item)
        if identity in seen:
            raise ValueError("invalid synthetic input")
        seen.add(identity)
        try:
            count[0] += len(item)
            if count[0] > _MAX_ENTRIES:
                raise ValueError("invalid synthetic input")
            if type(item) is dict:
                for key, child in item.items():
                    if type(key) is not str:
                        raise ValueError("invalid synthetic input")
                    check(key, depth + 1)
                    check(child, depth + 1)
            else:
                for child in item:
                    check(child, depth + 1)
        finally:
            seen.remove(identity)

    try:
        check(value, 0)
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(encoded) > _INPUT_LIMIT:
            raise ValueError("invalid synthetic input")
        return encoded
    except (TypeError, UnicodeError, RecursionError, OverflowError):
        raise ValueError("invalid synthetic input") from None


def _json_bytes(value: object, limit: int) -> bytes:
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(encoded) > limit:
            raise ValueError("synthetic receipt too large")
        return encoded
    except (TypeError, UnicodeError, RecursionError, OverflowError):
        raise ValueError("invalid synthetic result") from None


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _binding_sha(raw_sha: str, snapshot_sha: str, normalized_sha: str) -> str:
    return _sha(_json_bytes([raw_sha, snapshot_sha, normalized_sha], _INPUT_LIMIT))


def _canonical_snapshot(snapshot: object) -> tuple[dict, bytes]:
    if type(snapshot) is not dict:
        raise ValueError("invalid synthetic snapshot")
    owned = json.loads(_checked_json_bytes(snapshot))
    try:
        owned["as_of"] = _instant(owned.get("as_of"), "as_of").isoformat()
    except Exception:
        raise ValueError("invalid synthetic snapshot") from None
    return owned, _checked_json_bytes(owned)


def bind_synthetic_milestones(records: object, snapshot: object, *,
                              expected_workspace_id: str, expected_plan_version: str,
                              expected_snapshot_id: str) -> _BoundSyntheticMilestones:
    """Make an immutable, caller-scoped synthetic input at the snapshot cutoff."""
    if type(records) is not list or len(records) > 256:
        raise ValueError("raw milestone records required")
    raw_bytes = _checked_json_bytes(records)
    owned_snapshot, snapshot_bytes = _canonical_snapshot(snapshot)
    for name, expected in (("workspace_id", expected_workspace_id),
                           ("plan_version", expected_plan_version),
                           ("snapshot_id", expected_snapshot_id)):
        if type(expected) is not str or not expected or expected != expected.strip() or \
                owned_snapshot.get(name) != expected:
            raise ValueError("synthetic snapshot scope mismatch")
    if owned_snapshot.get("synthetic") is not True:
        raise ValueError("synthetic snapshot required")
    normalized = normalize_milestones(json.loads(raw_bytes), as_of=owned_snapshot["as_of"])
    normalized_bytes = _json_bytes(normalized, _INPUT_LIMIT)
    raw_sha, snapshot_sha, normalized_sha = _sha(raw_bytes), _sha(snapshot_bytes), _sha(normalized_bytes)
    return _BoundSyntheticMilestones(snapshot_bytes, normalized_bytes, raw_sha, snapshot_sha,
                                     normalized_sha, _binding_sha(raw_sha, snapshot_sha, normalized_sha),
                                     _token=_FACTORY_TOKEN)


def _open_binding(binding: object, snapshot: object) -> tuple[dict, dict]:
    if type(binding) is not _BoundSyntheticMilestones:
        raise ValueError("bound synthetic milestones required")
    owned_snapshot, snapshot_bytes = _canonical_snapshot(snapshot)
    if snapshot_bytes != binding._snapshot_bytes or _sha(snapshot_bytes) != binding._snapshot_sha256 or \
            _sha(binding._normalized_bytes) != binding._normalized_sha256 or \
            _binding_sha(binding._raw_sha256, binding._snapshot_sha256,
                         binding._normalized_sha256) != binding._binding_sha256:
        raise ValueError("synthetic binding mismatch")
    normalized = json.loads(binding._normalized_bytes)
    if normalized.get("as_of") != owned_snapshot["as_of"]:
        raise ValueError("synthetic cutoff mismatch")
    return normalized, owned_snapshot


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


def build_arrival_scenarios(binding: _BoundSyntheticMilestones, snapshot: Mapping,
                            *, scenario_prefix: str = "milestone") -> dict:
    if type(scenario_prefix) is not str or not scenario_prefix or \
            scenario_prefix != scenario_prefix.strip() or len(scenario_prefix) > 64:
        raise ValueError("invalid scenario prefix")
    normalized, snapshot = _open_binding(binding, snapshot)
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
        identity = _sha(_json_bytes([snapshot["workspace_id"], snapshot["plan_version"],
                                     snapshot["snapshot_id"], binding._snapshot_sha256,
                                     chosen["event_id"]], _INPUT_LIMIT))
        receipt = {
            "as_of": normalized["as_of"], "workspace_id": snapshot["workspace_id"],
            "plan_version": snapshot["plan_version"], "snapshot_id": snapshot["snapshot_id"],
            "raw_sha256": binding._raw_sha256, "snapshot_sha256": binding._snapshot_sha256,
            "normalized_sha256": binding._normalized_sha256, "binding_sha256": binding._binding_sha256,
            "event": {key: copy.deepcopy(chosen[key]) for key in
                      ("event_id", "shipment_id", "event_type", "occurred_at", "observed_at",
                       "record_index", "synthetic", "source", "evidence")},
        }
        _json_bytes(receipt, _RECEIPT_LIMIT)
        scenarios.append({
            "event_id": chosen["event_id"], "shipment_id": shipment_id, "basis": basis, "arrival_at": arrival_at,
            "assumptions": assumptions, "source_receipt": receipt,
            "scenario": {"workspace_id": snapshot["workspace_id"], "expected_plan_version": snapshot["plan_version"],
                         "source_snapshot_id": snapshot["snapshot_id"], "event_id": chosen["event_id"],
                         "scenario_id": f"{scenario_prefix}:{identity}", "synthetic": True,
                         "changes": [{"line_id": line_id, "arrival_at": arrival_at}
                                     for line_id in sorted(lines[shipment_id])]},
        })
    excluded.sort(key=lambda e: (e["shipment_id"], e["event_id"]))
    return {"scenarios": scenarios, "excluded": excluded,
            "normalization_rejected": normalized["rejected"],
            "normalization_duplicates": normalized["duplicates"]}
