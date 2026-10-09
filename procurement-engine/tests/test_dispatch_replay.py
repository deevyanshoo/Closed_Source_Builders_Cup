"""Port-delay replay: one labelled synthetic event over the synthetic fixtures, before and after.

The replay reuses the milestone normaliser, the dispatch read models and the unmodified
propagation projection. It never computes recovery. Synthetic data only.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from procure.dispatch.replay import REPLAY_NOTE, replay_event

HERE = Path(__file__).parent / "fixtures"
RECORDS = json.loads((HERE / "shipments" / "milestones.json").read_text())["records"]
SNAPSHOT = json.loads((HERE / "shipments" / "snapshot.json").read_text())
HAZARDS = json.loads((HERE / "route_evidence" / "hazards.json").read_text())["records"]
SAMPLES = {s["sample_id"]: s for s in json.loads((HERE / "shipments" / "replay_events.json").read_text())["samples"]}
AS_OF = "2026-10-08T12:00:00Z"
NOW = "2026-10-08T13:00:00Z"


def run(sample_id=None, *, event=None, as_of=AS_OF, now=NOW, records=RECORDS, snapshot=SNAPSHOT):
    event = event if event is not None else SAMPLES[sample_id]["record"]
    return replay_event(records, event, as_of=as_of, now=now, snapshot=snapshot, route_evidence_records=HAZARDS)


def change_for(result, shipment_id):
    return next(c for c in result["changes"] if c["shipment_id"] == shipment_id)


def test_sample_fixture_is_labelled_synthetic():
    data = json.loads((HERE / "shipments" / "replay_events.json").read_text())
    assert data["synthetic"] is True and "SYNTHETIC" in data["notice"]
    assert all(s["record"]["synthetic"] is True for s in data["samples"])


def test_hold_with_new_eta_moves_the_expected_arrival_and_keeps_the_hold():
    r = run("innsa-hold-new-eta")
    assert (r["status"], r["reasons"]) == ("ok", [])
    assert r["injected"] == {"event_id": "rpl-innsa-hold", "shipment_id": "SHP-SYN-002", "event_type": "port_hold",
                             "synthetic": True, "label": "Synthetic replay event, not observed data"}
    c = change_for(r, "SHP-SYN-002")
    assert (c["before"]["status"], c["after"]["status"]) == ("on_hold", "on_hold")
    assert c["before"]["expected_arrival"]["at"] == "2026-10-12T02:30:00+00:00"
    assert c["after"]["expected_arrival"] == {"at": "2026-10-16T02:30:00+00:00", "basis": "eta",
                                              "event_id": "rpl-innsa-hold"}
    assert c["changed"] == ["expected_arrival"]
    assert [x["shipment_id"] for x in r["changes"]] == ["SHP-SYN-002"]


def test_status_change_is_explained_by_signals():
    r = run("innsa-release")
    c = change_for(r, "SHP-SYN-002")
    assert (c["before"]["status"], c["after"]["status"]) == ("on_hold", "delayed")
    assert c["signals_removed"] == ["on_hold"]
    assert c["signals_added"] == []
    assert "eta_after_baseline" in c["after"]["signals"]
    assert c["changed"] == ["status", "signals", "on_hold", "last_event_type"]


def test_hold_without_eta_is_a_hold_not_a_measured_delay():
    r = run("inmaa-hold-no-eta")
    c = change_for(r, "SHP-SYN-004")
    assert (c["before"]["status"], c["after"]["status"]) == ("delayed", "on_hold")
    assert c["signals_added"] == ["on_hold"]
    assert r["material_screen"] == {"status": "not_run", "reason": "hold_without_new_eta", "event_id": "rpl-inmaa-hold"}


def test_timeline_marks_the_injected_entry_only():
    r = run("innsa-hold-new-eta")
    t = r["timeline"]
    assert t["shipment_id"] == "SHP-SYN-002"
    assert [e["event_id"] for e in t["before"]] == ["evt-002-hold"]
    assert [(e["event_id"], e["replay_injected"]) for e in t["after"]] == [
        ("evt-002-hold", False), ("rpl-innsa-hold", True)]
    assert t["after"][1]["synthetic"] is True
    assert t["current_before"]["open_eta"]["event_id"] == "evt-002-hold"
    assert t["current_after"]["open_eta"]["event_id"] == "rpl-innsa-hold"


def test_affected_shipments_are_exposure_only():
    r = run("innsa-hold-new-eta")
    assert r["affected"]["before"] == []
    after = r["affected"]["after"]
    assert [(a["shipment_id"], a["relation"], a["status"]) for a in after["affected"]] == [
        ("SHP-SYN-001", "shares_location", "exposure"), ("SHP-SYN-002", "self", "exposure")]
    assert after["note"].startswith("Exposure only")


def test_route_evidence_is_unchanged_and_stays_split():
    r = run("innsa-hold-new-eta")
    ev = r["route_evidence"]
    assert ev["location_keys"] == ["INNSA"]
    assert ev["confirmed"] == [{"evidence_id": "rev-port-002", "operating_status": "restricted"}]
    assert ev["exposure"] == []
    assert ev["changed_by_replay"] is False
    hold = run("inmaa-hold-no-eta")["route_evidence"]
    # A synthetic port hold at INMAA does not turn the nearby weather hazard into a confirmed status.
    assert hold["confirmed"] == []
    assert hold["exposure"] == [{"evidence_id": "rev-haz-001", "kind": "weather_hazard"}]


def test_material_screen_reuses_the_propagation_projection():
    r = run("innsa-hold-new-eta")
    m = r["material_screen"]
    assert m["status"] == "screened" and m["synthetic"] is True
    assert m["event_id"] == "rpl-innsa-hold"
    assert m["arrival_changes"][0]["line_id"] == "LINE-002-A"
    assert m["arrival_changes"][0]["changed_at"] == "2026-10-16T02:30:00+00:00"
    assert m["arrival_changes"][0]["quantity"] == "3000"
    assert m["limitations"]


def test_event_that_states_no_arrival_is_not_screened():
    m = run("innsa-release")["material_screen"]
    assert m == {"status": "not_run", "reason": "no_arrival_information", "event_id": "rpl-innsa-release"}


def test_material_screen_needs_the_snapshot_cutoff():
    r = run("innsa-hold-new-eta", as_of="2026-10-08T11:00:00Z")
    assert r["status"] == "ok"
    assert r["material_screen"] == {"status": "not_run", "reason": "as_of_differs_from_snapshot",
                                    "event_id": "rpl-innsa-hold"}
    assert run("innsa-hold-new-eta", snapshot=None)["material_screen"]["reason"] == "no_snapshot"


def test_event_after_as_of_is_rejected():
    r = run("eta-after-cutoff")
    assert (r["status"], r["reasons"]) == ("error", ["event_after_as_of"])
    assert r["changes"] == [] and r["material_screen"] is None
    early = copy.deepcopy(SAMPLES["innsa-hold-new-eta"]["record"])
    early["evidence"][0]["retrieved_at"] = "2026-10-08T12:00:01Z"
    assert run(event=early)["reasons"] == ["event_after_as_of"]


def test_unlabelled_or_invalid_events_are_rejected():
    plain = copy.deepcopy(SAMPLES["innsa-hold-new-eta"]["record"])
    plain["synthetic"] = False
    assert run(event=plain)["reasons"] == ["injected_not_synthetic"]
    missing = copy.deepcopy(SAMPLES["innsa-hold-new-eta"]["record"])
    del missing["evidence"]
    r = run(event=missing)
    assert (r["status"], r["reasons"], r["rejection"]) == ("error", ["injected_event_rejected"],
                                                             {"reason": "missing_evidence", "details": []})
    assert run(event="not a record")["reasons"] == ["invalid_injected_event"]


def test_reused_event_id_is_rejected_not_merged():
    clash = copy.deepcopy(SAMPLES["innsa-hold-new-eta"]["record"])
    clash["event_id"] = "evt-002-hold"
    assert run(event=clash)["reasons"] == ["event_id_already_used"]


def test_replay_is_deterministic_and_does_not_mutate_inputs():
    records, snapshot = copy.deepcopy(RECORDS), copy.deepcopy(SNAPSHOT)
    event = copy.deepcopy(SAMPLES["innsa-hold-new-eta"]["record"])
    first = replay_event(records, event, as_of=AS_OF, now=NOW, snapshot=snapshot, route_evidence_records=HAZARDS)
    second = replay_event(records, event, as_of=AS_OF, now=NOW, snapshot=snapshot, route_evidence_records=HAZARDS)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert records == RECORDS and snapshot == SNAPSHOT and event == SAMPLES["innsa-hold-new-eta"]["record"]


def test_stale_and_no_change_states():
    stale = run("innsa-hold-new-eta", now="2026-10-10T12:00:01Z")
    assert (stale["status"], stale["reasons"]) == ("stale", ["data_older_than_max_age"])
    assert stale["changes"]
    same = copy.deepcopy(SAMPLES["innsa-hold-new-eta"]["record"])
    same.update(event_id="rpl-same", eta=None, type="departed", shipment_id="SHP-SYN-005",
                occurred_at="2026-09-24T00:00:00Z", observed_at="2026-10-08T06:00:00Z",
                facility={"kind": "port", "code": "CNSHA"})
    nothing = run(event=same)
    assert (nothing["status"], nothing["reasons"]) == ("empty", ["no_changes"])


def test_result_links_to_recovery_and_never_computes_it():
    r = run("innsa-hold-new-eta")
    assert r["recovery_link"] == "/shipment-recovery"
    assert r["note"] == REPLAY_NOTE and "recovery" in REPLAY_NOTE.lower()
    assert not any(k in r for k in ("recovery", "recovery_options", "recommendation"))
    assert r["synthetic"] is True


def test_as_of_after_now_is_an_error():
    r = run("innsa-hold-new-eta", now="2026-10-08T11:00:00Z")
    assert (r["status"], r["reasons"]) == ("error", ["as_of_after_now"])
