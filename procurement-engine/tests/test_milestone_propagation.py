"""Normalised milestones become arrival-change scenarios that the existing propagation screen accepts.

The compatibility test runs Divyanshu's unmodified ``project_arrival_change`` on the output.
Synthetic snapshot and milestones only.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from procure.events.ingest import bind_synthetic_milestones, build_arrival_scenarios, normalize_milestones
from procure.events.propagation import project_arrival_change

HERE = Path(__file__).parent / "fixtures" / "shipments"
FIXTURE = json.loads((HERE / "milestones.json").read_text())
SNAPSHOT = json.loads((HERE / "snapshot.json").read_text())


@pytest.fixture(scope="module")
def built():
    return scenarios(FIXTURE["records"])


def bound(records, snapshot=SNAPSHOT):
    return bind_synthetic_milestones(
        records, snapshot, expected_workspace_id=snapshot["workspace_id"],
        expected_plan_version=snapshot["plan_version"], expected_snapshot_id=snapshot["snapshot_id"])


def scenarios(records, snapshot=SNAPSHOT):
    return build_arrival_scenarios(bound(records, snapshot), snapshot)


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
    built = scenarios([record])
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
    built = scenarios([dep, arr])
    assert built["scenarios"] == []
    assert {e["reason"] for e in built["excluded"]} == {"shipment_sequence_conflict"}


def test_non_synthetic_input_is_refused_because_the_screen_is_synthetic_only():
    record = copy.deepcopy(FIXTURE["records"][4])
    record["synthetic"] = False
    built = scenarios([record])
    assert [e["reason"] for e in built["excluded"]] == ["not_synthetic"]


def test_inputs_are_not_modified(built):
    records = copy.deepcopy(FIXTURE["records"])
    before_records, before_snap = copy.deepcopy(records), copy.deepcopy(SNAPSHOT)
    scenarios(records)
    assert records == before_records and SNAPSHOT == before_snap


def test_future_milestone_is_normalized_at_snapshot_cutoff():
    row = copy.deepcopy(FIXTURE["records"][4])
    row["occurred_at"] = "2026-10-09T00:00:00Z"
    row["observed_at"] = "2026-10-09T01:00:00Z"
    row["evidence"][0]["retrieved_at"] = "2026-10-09T01:00:00Z"
    result = scenarios([row])
    assert result["scenarios"] == []
    assert [r["reason"] for r in result["normalization_rejected"]] == ["observed_after_as_of"]


def test_expired_evidence_is_recomputed_at_new_snapshot_cutoff():
    row = copy.deepcopy(FIXTURE["records"][4])
    row["evidence"][0]["valid_until"] = "2026-10-09T00:00:00Z"
    newer = copy.deepcopy(SNAPSHOT)
    newer["as_of"] = "2026-10-10T00:00:00Z"
    result = scenarios([row], newer)
    assert result["scenarios"] == []
    assert result["excluded"] == [{"event_id": row["event_id"], "shipment_id": row["shipment_id"],
                                   "reason": "evidence_expired"}]


def test_future_retrieved_reference_rejects_mixed_record_before_scenario_selection():
    row = copy.deepcopy(FIXTURE["records"][4])
    future = copy.deepcopy(row["evidence"][0])
    future["evidence_id"] = "future-reference"
    future["retrieved_at"] = "2026-10-09T00:00:00Z"
    row["evidence"].append(future)
    result = scenarios([row])
    assert result["scenarios"] == []
    assert [(r["reason"], r["details"]) for r in result["normalization_rejected"]] == [
        ("evidence_after_as_of", ["ev:future-reference"])]


def test_future_retrieval_copy_cannot_poison_valid_event_or_duplicate_receipt():
    valid = copy.deepcopy(FIXTURE["records"][4])
    future = copy.deepcopy(valid)
    future["observed_at"] = "2026-10-07T00:00:00Z"
    future["evidence"][0]["retrieved_at"] = "2026-10-09T00:00:00Z"
    result = scenarios([valid, future])
    assert len(result["scenarios"]) == 1
    assert result["normalization_duplicates"] == []
    assert [r["reason"] for r in result["normalization_rejected"]] == ["evidence_after_as_of"]


def test_equivalent_utc_cutoff_is_accepted_for_the_same_full_snapshot():
    offset_snapshot = copy.deepcopy(SNAPSHOT)
    offset_snapshot["as_of"] = "2026-10-08T17:30:00+05:30"
    binding = bound(FIXTURE["records"], offset_snapshot)
    result = build_arrival_scenarios(binding, SNAPSHOT)
    assert [s["shipment_id"] for s in result["scenarios"]] == ["SHP-SYN-002", "SHP-SYN-004"]


def test_arbitrary_or_forged_normalized_mapping_cannot_enter_builder():
    normalized = normalize_milestones([FIXTURE["records"][4]], as_of=FIXTURE["as_of"])
    normalized["events"][0]["source"]["provider_id"] = "forged-source"
    normalized["events"][0]["evidence"] = []
    normalized["events"][0]["operating_evidence"] = True
    with pytest.raises((TypeError, ValueError)):
        build_arrival_scenarios(normalized, SNAPSHOT)
    with pytest.raises((TypeError, ValueError)):
        bind_synthetic_milestones(normalized, SNAPSHOT, expected_workspace_id=SNAPSHOT["workspace_id"],
                                  expected_plan_version=SNAPSHOT["plan_version"],
                                  expected_snapshot_id=SNAPSHOT["snapshot_id"])


@pytest.mark.parametrize("field,value", [
    ("workspace_id", "foreign-workspace"), ("plan_version", "new-plan"),
    ("snapshot_id", "new-snapshot"), ("as_of", "2026-10-09T12:00:00Z"),
    ("inventory", []),
])
def test_bound_input_rejects_changed_snapshot_content_or_scope(field, value):
    binding = bound([FIXTURE["records"][4]])
    changed = copy.deepcopy(SNAPSHOT)
    changed[field] = value
    with pytest.raises((TypeError, ValueError)):
        build_arrival_scenarios(binding, changed)


def test_expected_scope_must_match_snapshot_identity():
    with pytest.raises((TypeError, ValueError)):
        bind_synthetic_milestones([FIXTURE["records"][4]], SNAPSHOT,
                                  expected_workspace_id="foreign-workspace",
                                  expected_plan_version=SNAPSHOT["plan_version"],
                                  expected_snapshot_id=SNAPSHOT["snapshot_id"])


def test_source_receipt_preserves_evidence_and_original_input_mutations_cannot_change_it():
    records = [copy.deepcopy(FIXTURE["records"][4])]
    binding = bound(records)
    before = build_arrival_scenarios(binding, SNAPSHOT)["scenarios"][0]
    receipt = before["source_receipt"]
    assert receipt["as_of"] == "2026-10-08T12:00:00+00:00"
    assert (receipt["workspace_id"], receipt["plan_version"], receipt["snapshot_id"]) == \
           (SNAPSHOT["workspace_id"], SNAPSHOT["plan_version"], SNAPSHOT["snapshot_id"])
    assert receipt["event"]["source"] == {"provider_id": "synthetic-carrier-feed", "kind": "carrier_feed",
                                           "reference": "SYN-BL-0004"}
    assert receipt["event"]["evidence"][0]["content_sha256"] == records[0]["evidence"][0]["content_sha256"]
    assert receipt["event"]["evidence"][0]["retrieved_at"] == "2026-10-04T10:00:00+00:00"
    assert receipt["event"]["observed_at"] == "2026-10-04T10:00:00+00:00"
    assert receipt["event"]["record_index"] == 0
    assert len(receipt["binding_sha256"]) == 64
    records[0]["source"]["provider_id"] = "changed-after-binding"
    records[0]["evidence"][0]["content_sha256"] = "0" * 64
    assert build_arrival_scenarios(binding, SNAPSHOT)["scenarios"][0] == before
    assert scenarios(records)["scenarios"][0]["source_receipt"]["binding_sha256"] != receipt["binding_sha256"]


def test_binding_rejects_direct_construction_or_tampered_owned_bytes():
    from procure.events.ingest.scenarios import _BoundSyntheticMilestones
    with pytest.raises((TypeError, ValueError)):
        _BoundSyntheticMilestones(b"{}", b"{}", "0" * 64, "0" * 64)
    binding = bound([FIXTURE["records"][4]])
    with pytest.raises((AttributeError, TypeError)):
        binding._normalized_bytes = b"{}"
    object.__setattr__(binding, "_normalized_bytes", b"{}")
    with pytest.raises((TypeError, ValueError)):
        build_arrival_scenarios(binding, SNAPSHOT)


def test_same_event_in_another_plan_has_different_scenario_identity():
    record = [FIXTURE["records"][4]]
    first = scenarios(record)["scenarios"][0]
    other = copy.deepcopy(SNAPSHOT)
    other["plan_version"] = "synthetic-plan-v2"
    second = scenarios(record, other)["scenarios"][0]
    assert first["event_id"] == second["event_id"]
    assert first["scenario"]["scenario_id"] != second["scenario"]["scenario_id"]
    assert first["source_receipt"]["binding_sha256"] != second["source_receipt"]["binding_sha256"]


def test_non_string_quantity_is_not_coerced_by_binding():
    row = copy.deepcopy(FIXTURE["records"][3])
    row["quantity"]["amount"] = 20
    with pytest.raises((TypeError, ValueError)):
        bound([row])


def test_raw_record_count_is_capped_before_normalization():
    with pytest.raises((TypeError, ValueError)):
        bound([{}] * 257)


def test_source_receipt_is_bounded_before_emission():
    row = copy.deepcopy(FIXTURE["records"][4])
    row["evidence"][0]["locator"] = "synthetic://" + "x" * (140 << 10)
    binding = bound([row])
    with pytest.raises((TypeError, ValueError)):
        build_arrival_scenarios(binding, SNAPSHOT)


@pytest.mark.parametrize("shipment_id,expected_shortfall", [
    ("SHP-SYN-002", "0"), ("SHP-SYN-004", "3000"),
])
def test_complete_public_projection_matches_explicit_scenario(built, shipment_id, expected_shortfall):
    chosen = scenario_for(built, shipment_id)
    explicit = {"workspace_id": SNAPSHOT["workspace_id"], "expected_plan_version": SNAPSHOT["plan_version"],
                "source_snapshot_id": SNAPSHOT["snapshot_id"], "event_id": chosen["event_id"],
                "scenario_id": chosen["scenario"]["scenario_id"], "synthetic": True,
                "changes": copy.deepcopy(chosen["scenario"]["changes"])}
    assert set(chosen["scenario"]) == set(explicit)
    actual = project_arrival_change(SNAPSHOT, chosen["scenario"])
    assert actual == project_arrival_change(SNAPSHOT, explicit)
    aluminium = next(d for row in actual["affected_commitments"] for d in row["demands"]
                     if d["component_id"] == "CMP-ALUMINIUM")
    assert aluminium["baseline"]["shortfall"] == "0"
    assert aluminium["changed"]["shortfall"] == expected_shortfall
