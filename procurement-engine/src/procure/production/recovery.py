"""Pure receipt-arrival scenario comparison composed from the public production screen."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone

from procure.production import screen_production_schedules


_SCENARIO_FIELDS = {"workspace_id", "expected_plan_version", "source_snapshot_id",
                    "scenario_id", "synthetic", "arrival_replacements"}
_REPLACEMENT_FIELDS = {"receipt_id", "expected_arrival_at", "arrival_at"}
_RECOVERY_FIELDS = {"workspace_id", "expected_plan_version", "source_snapshot_id",
                    "scenario_id", "synthetic", "assignments"}


class _Unsupported(Exception):
    def __init__(self, reason: str):
        self.reason = reason


def _record(value: object, fields: set[str], reason: str) -> Mapping:
    if not isinstance(value, Mapping):
        raise _Unsupported(reason)
    if any(not isinstance(key, str) for key in value) or set(value) != fields:
        raise _Unsupported(reason)
    return value


def _id(value: object, reason: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise _Unsupported(reason)
    return value


def _utc(value: str) -> datetime:
    # Every caller-provided value reaches here only after the public screen has
    # validated its strict timestamp grammar, offset, precision and chronology.
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _mutable(value: object) -> object:
    """Copy validated mapping/sequence structure without requiring pickle support."""
    if isinstance(value, Mapping):
        return {key: _mutable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_mutable(item) for item in value]
    return value


def _screen(snapshot: Mapping, alternative: Mapping) -> dict:
    result = screen_production_schedules(snapshot, alternative)
    if result["status"] != "screened":
        raise _Unsupported(result["reason"])
    return result


def _changed_order_ids(before: dict, after: dict) -> list[str]:
    previous = {row["order_id"]: row for row in before["orders"]}
    current = {row["order_id"]: row for row in after["orders"]}
    return sorted(order_id for order_id in previous if previous[order_id] != current[order_id])


def compare_receipt_recovery(snapshot: Mapping, scenario: Mapping,
                             recovery: Mapping) -> dict:
    """Compare an original baseline, delayed baseline and supplied recovery.

    The caller supplies every date and assignment. Nothing is applied or chosen.
    """
    try:
        scenario = _record(scenario, _SCENARIO_FIELDS, "invalid_scenario")
        recovery = _record(recovery, _RECOVERY_FIELDS, "invalid_recovery")
        for field in ("workspace_id", "expected_plan_version", "source_snapshot_id",
                      "scenario_id"):
            _id(scenario[field], "invalid_scope")
            _id(recovery[field], "invalid_scope")
        if scenario["synthetic"] is not True or recovery["synthetic"] is not True:
            raise _Unsupported("invalid_scope")
        if (scenario["workspace_id"] != recovery["workspace_id"] or
                scenario["expected_plan_version"] != recovery["expected_plan_version"] or
                scenario["source_snapshot_id"] != recovery["source_snapshot_id"] or
                scenario["scenario_id"] != recovery["scenario_id"]):
            raise _Unsupported("invalid_scope")
        if not isinstance(scenario["arrival_replacements"], (list, tuple)):
            raise _Unsupported("invalid_replacement")

        replacements = {}
        for raw in scenario["arrival_replacements"]:
            row = _record(raw, _REPLACEMENT_FIELDS, "invalid_replacement")
            receipt_id = _id(row["receipt_id"], "invalid_replacement")
            if receipt_id in replacements:
                raise _Unsupported("duplicate_replacement")
            replacements[receipt_id] = row

        original = _screen(snapshot, recovery)
        if (scenario["workspace_id"] != original["workspace_id"] or
                scenario["expected_plan_version"] != original["plan_version"] or
                scenario["source_snapshot_id"] != original["snapshot_id"]):
            raise _Unsupported("invalid_scope")
        original_baseline = original["baseline"]

        # The public screen has already checked all original receipt rows and
        # rejected conflicting repeated IDs. Keep indices for every identical
        # original row so a replacement never updates just the first copy.
        receipt_indices = {}
        for index, row in enumerate(snapshot["receipts"]):
            receipt_indices.setdefault(row["receipt_id"], []).append(index)
        if any(receipt_id not in receipt_indices for receipt_id in replacements):
            raise _Unsupported("unknown_receipt")

        if replacements:
            expected_snapshot = _mutable(snapshot)
            for receipt_id, row in replacements.items():
                for index in receipt_indices[receipt_id]:
                    expected_snapshot["receipts"][index]["arrival_at"] = row["expected_arrival_at"]
            baseline_alternative = dict(recovery)
            baseline_alternative["assignments"] = _mutable(snapshot["baseline_assignments"])
            _screen(expected_snapshot, baseline_alternative)
            for receipt_id, row in replacements.items():
                original_time = _utc(snapshot["receipts"][receipt_indices[receipt_id][0]]["arrival_at"])
                if original_time != _utc(row["expected_arrival_at"]):
                    raise _Unsupported("stale_receipt")

        disrupted_snapshot = _mutable(snapshot)
        for receipt_id, row in replacements.items():
            for index in receipt_indices[receipt_id]:
                disrupted_snapshot["receipts"][index]["arrival_at"] = row["arrival_at"]
        disrupted = _screen(disrupted_snapshot, recovery)

        changes = []
        changed_components = set()
        for receipt_id, row in replacements.items():
            source = snapshot["receipts"][receipt_indices[receipt_id][0]]
            before = _utc(source["arrival_at"])
            after = _utc(row["arrival_at"])
            if before != after:
                changes.append({"receipt_id": receipt_id,
                                "before_arrival_at": before.isoformat(),
                                "after_arrival_at": after.isoformat()})
                changed_components.add(source["component_id"])
        changes.sort(key=lambda change: change["receipt_id"])

        components_by_product = {
            row["product_id"]: {line["component_id"] for line in row["bom"]}
            for row in snapshot["products"]
        }
        exposed = sorted({row["order_id"] for row in snapshot["orders"]
                          if components_by_product[row["product_id"]] & changed_components})
        disrupted_baseline = disrupted["baseline"]
        recovery_schedule = disrupted["alternative"]
        return {
            "status": "compared", "synthetic": True,
            "workspace_id": original["workspace_id"],
            "plan_version": original["plan_version"],
            "snapshot_id": original["snapshot_id"],
            "scenario_id": scenario["scenario_id"],
            "as_of": original["as_of"],
            "modeled_checks": original["modeled_checks"],
            "unmodeled_constraints": original["unmodeled_constraints"],
            "original_baseline": original_baseline,
            "disrupted_baseline": disrupted_baseline,
            "recovery": recovery_schedule,
            "receipt_changes": changes,
            "potentially_exposed_order_ids": exposed,
            "disruption_changed_order_ids": _changed_order_ids(
                original_baseline, disrupted_baseline),
            "recovery_changed_order_ids": _changed_order_ids(
                disrupted_baseline, recovery_schedule),
        }
    except _Unsupported as rejected:
        return {"status": "unsupported", "reason": rejected.reason, "details": []}
