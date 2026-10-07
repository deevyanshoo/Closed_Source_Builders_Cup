"""Normalised milestones become arrival-change scenarios that the existing propagation screen accepts.

The compatibility test runs Divyanshu's unmodified ``project_arrival_change`` on the output.
Synthetic snapshot and milestones only.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from procure.events.ingest import build_arrival_scenarios, normalize_milestones
from procure.events.propagation import project_arrival_change

HERE = Path(__file__).parent / "fixtures" / "shipments"
FIXTURE = json.loads((HERE / "milestones.json").read_text())
SNAPSHOT = json.loads((HERE / "snapshot.json").read_text())


@pytest.fixture(scope="module")
def built():
    return build_arrival_scenarios(normalize_milestones(FIXTURE["records"], as_of=FIXTURE["as_of"]), SNAPSHOT)


def scenario_for(built, shipment_id):
    return next(s for s in built["scenarios"] if s["shipment_id"] == shipment_id)


def test_scenarios_use_the_propagation_schema_exactly(built):
    s = scenario_for(built, "SHP-SYN-004")
    assert set(s["scenario"]) == {"workspace_id", "expected_plan_version", "source_snapshot_id", "event_id",
                                  "scenario_id", "synthetic", "changes"}
    assert s["scenario"]["workspace_id"] == "synthetic-workspace"
    assert s["scenario"]["expected_plan_version"] == "synthetic-plan-v1"
    assert s["scenario"]["source_snapshot_id"] == "synthetic-snapshot-1"
    assert s["scenario"]["event_id"] == "evt-004-eta"
    assert s["scenario"]["synthetic"] is True
    assert s["scenario"]["changes"] == [{"line_id": "LINE-004-A", "arrival_at": "2026-10-15T06:00:00+00:00"}]
    assert s["basis"] == "eta"


def test_only_supported_shipments_become_scenarios(built):
    assert sorted(s["shipment_id"] for s in built["scenarios"]) == ["SHP-SYN-002", "SHP-SYN-004"]


def test_every_other_event_is_excluded_with_a_reason(built):
    excluded = {(e["event_id"], e["reason"]) for e in built["excluded"]}
    assert excluded == {
        ("evt-001-dep", "no_arrival_information"),
        ("evt-001-arr", "shipment_not_in_snapshot"),
        ("evt-003-part", "partial_quantity_not_supported"),
        ("evt-005-dep", "no_arrival_information"),
        ("evt-005-eta", "superseded_by_later_event"),
        ("evt-005-arr", "arrival_before_snapshot_as_of"),
        ("evt-008-eta", "evidence_expired"),
    }


@pytest.mark.parametrize("shipment_id,at_risk", [("SHP-SYN-002", False), ("SHP-SYN-004", True)])
def test_existing_projection_accepts_the_scenarios(built, shipment_id, at_risk):
    s = scenario_for(built, shipment_id)
    result = project_arrival_change(SNAPSHOT, s["scenario"])
    assert result["status"] == "screened", result
    assert result["event_id"] == s["event_id"]
    assert [c["changed_at"] for c in result["arrival_changes"]] == [c["arrival_at"] for c in s["scenario"]["changes"]]
    assert result["affected_commitments"][0]["material_at_risk"] is at_risk


def test_port_hold_with_a_stated_eta_is_used_with_its_basis(built):
    s = scenario_for(built, "SHP-SYN-002")
    assert (s["event_id"], s["basis"]) == ("evt-002-hold", "eta")
    assert s["scenario"]["changes"] == [{"line_id": "LINE-002-A", "arrival_at": "2026-10-12T02:30:00+00:00"}]
    assert any("quantities are unchanged" in a for a in s["assumptions"])


def test_port_hold_without_an_eta_is_exposure_not_a_delay():
    record = copy.deepcopy(FIXTURE["records"][2])
    record.pop("eta")
    built = build_arrival_scenarios(normalize_milestones([record], as_of=FIXTURE["as_of"]), SNAPSHOT)
    assert built["scenarios"] == []
    assert [e["reason"] for e in built["excluded"]] == ["hold_without_new_eta"]


def test_shipment_with_a_sequence_conflict_is_not_converted():
    dep = copy.deepcopy(FIXTURE["records"][4])
    dep.update(event_id="evt-004-dep", type="departed", occurred_at="2026-10-05T00:00:00Z",
               observed_at="2026-10-05T01:00:00Z")
    dep.pop("eta")
    arr = copy.deepcopy(FIXTURE["records"][4])
    arr.update(event_id="evt-004-early", type="arrived", occurred_at="2026-10-01T00:00:00Z",
               observed_at="2026-10-01T01:00:00Z")
    arr.pop("eta")
    built = build_arrival_scenarios(normalize_milestones([dep, arr], as_of=FIXTURE["as_of"]), SNAPSHOT)
    assert built["scenarios"] == []
    assert {e["reason"] for e in built["excluded"]} == {"shipment_sequence_conflict"}


def test_non_synthetic_input_is_refused_because_the_screen_is_synthetic_only():
    record = copy.deepcopy(FIXTURE["records"][4])
    record["synthetic"] = False
    built = build_arrival_scenarios(normalize_milestones([record], as_of=FIXTURE["as_of"]), SNAPSHOT)
    assert [e["reason"] for e in built["excluded"]] == ["not_synthetic"]


def test_inputs_are_not_modified(built):
    normalized = normalize_milestones(FIXTURE["records"], as_of=FIXTURE["as_of"])
    before_norm, before_snap = copy.deepcopy(normalized), copy.deepcopy(SNAPSHOT)
    build_arrival_scenarios(normalized, SNAPSHOT)
    assert normalized == before_norm and SNAPSHOT == before_snap
