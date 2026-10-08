"""Shipment list status for the dispatch workspace: delivered, on hold, delayed, at risk, on time or unknown.

Status needs evidence: "delayed" and "on time" compare against the plan baseline from the propagation
snapshot, and "at risk" needs current route evidence. Without a baseline the status is "unknown".
Synthetic fixtures only.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from procure.dispatch import shipment_list
from procure.events.ingest import normalize_milestones
from procure.route_evidence import normalize_route_evidence

HERE = Path(__file__).parent / "fixtures"
MILESTONES = json.loads((HERE / "shipments" / "milestones.json").read_text())
SNAPSHOT = json.loads((HERE / "shipments" / "snapshot.json").read_text())
HAZARDS = json.loads((HERE / "route_evidence" / "hazards.json").read_text())
NOW = "2026-10-08T13:00:00Z"


def normalized(records=None):
    return normalize_milestones(records if records is not None else MILESTONES["records"], as_of=MILESTONES["as_of"])


def evidence(records=None):
    return normalize_route_evidence(records if records is not None else HAZARDS["records"], as_of=HAZARDS["as_of"])


@pytest.fixture(scope="module")
def listing():
    return shipment_list(normalized(), now=NOW, snapshot=SNAPSHOT, route_evidence=evidence())


def row(listing, shipment_id):
    return next(s for s in listing["shipments"] if s["shipment_id"] == shipment_id)


def test_every_fixture_shipment_gets_an_evidence_based_status(listing):
    assert listing["status"] == "ok"
    assert [(s["shipment_id"], s["status"]) for s in listing["shipments"]] == [
        ("SHP-SYN-001", "unknown"), ("SHP-SYN-002", "on_hold"), ("SHP-SYN-003", "unknown"),
        ("SHP-SYN-004", "delayed"), ("SHP-SYN-005", "on_time"), ("SHP-SYN-008", "unknown")]


def test_signals_explain_each_status(listing):
    assert row(listing, "SHP-SYN-001")["signals"] == ["arrived", "no_baseline"]
    assert row(listing, "SHP-SYN-002")["signals"] == ["eta_after_baseline", "on_hold", "route_confirmed:restricted"]
    assert row(listing, "SHP-SYN-003")["signals"] == ["no_baseline", "partially_delivered"]
    assert row(listing, "SHP-SYN-004")["signals"] == ["eta_after_baseline", "route_exposure"]
    assert row(listing, "SHP-SYN-005")["signals"] == ["arrived"]
    assert row(listing, "SHP-SYN-008")["signals"] == ["evidence_expired", "no_baseline"]


def test_expected_arrival_and_baseline_are_shown(listing):
    delayed = row(listing, "SHP-SYN-004")
    assert delayed["baseline_arrival_at"] == "2026-10-13T00:00:00+00:00"
    assert delayed["expected_arrival"] == {"at": "2026-10-15T06:00:00+00:00", "basis": "eta", "event_id": "evt-004-eta"}
    arrived = row(listing, "SHP-SYN-005")
    assert arrived["expected_arrival"] == {"at": "2026-10-07T06:00:00+00:00", "basis": "actual_arrival",
                                           "event_id": "evt-005-arr"}
    assert row(listing, "SHP-SYN-008")["expected_arrival"] is None


def test_delivered_quantity_stays_an_exact_decimal(listing):
    assert row(listing, "SHP-SYN-003")["delivered_quantity"] == {"tonne": "20.000"}


def test_location_keys_join_shipments_to_route_evidence(listing):
    assert row(listing, "SHP-SYN-001")["location_keys"] == ["CLVAP", "INNSA"]
    assert row(listing, "SHP-SYN-004")["location_keys"] == ["AEJEA", "INMAA"]


def eta_record(eta):
    record = copy.deepcopy(MILESTONES["records"][4])
    record["eta"] = eta
    return record


def test_current_route_exposure_before_arrival_is_at_risk():
    listing = shipment_list(normalized([eta_record("2026-10-12T00:00:00Z")]), now=NOW, snapshot=SNAPSHOT,
                            route_evidence=evidence())
    assert (listing["shipments"][0]["status"], listing["shipments"][0]["signals"]) == ("at_risk", ["route_exposure"])


def test_without_route_evidence_an_early_eta_is_on_time():
    listing = shipment_list(normalized([eta_record("2026-10-12T00:00:00Z")]), now=NOW, snapshot=SNAPSHOT)
    assert (listing["shipments"][0]["status"], listing["shipments"][0]["signals"]) == ("on_time", [])


def test_expired_or_normal_route_evidence_is_not_risk():
    hazards = copy.deepcopy(HAZARDS["records"][0])
    hazards["valid_until"] = "2026-10-08T00:00:00Z"
    normal = copy.deepcopy(HAZARDS["records"][1])
    normal.update(evidence_id="rev-port-normal", facility={"kind": "port", "code": "INMAA", "name": "Chennai (synthetic)"},
                  operating_status="normal")
    listing = shipment_list(normalized([eta_record("2026-10-12T00:00:00Z")]), now=NOW, snapshot=SNAPSHOT,
                            route_evidence=evidence([hazards, normal]))
    assert listing["shipments"][0]["status"] == "on_time"


def test_delivered_shipment_is_delivered():
    record = copy.deepcopy(MILESTONES["records"][1])
    record.update(event_id="evt-001-delivered", type="delivered", occurred_at="2026-10-07T00:00:00Z",
                  observed_at="2026-10-07T01:00:00Z")
    record["evidence"][0].update(evidence_id="ev-001-del", retrieved_at="2026-10-07T01:00:00Z")
    listing = shipment_list(normalized([record]), now=NOW)
    assert listing["shipments"][0]["status"] == "delivered"


def test_shipment_lines_with_different_baselines_are_ambiguous():
    snapshot = copy.deepcopy(SNAPSHOT)
    extra = copy.deepcopy(snapshot["arrival_lines"][1])
    extra.update(line_id="LINE-004-B", arrival_at="2026-10-20T00:00:00Z")
    snapshot["arrival_lines"].append(extra)
    listing = shipment_list(normalized([eta_record("2026-10-15T06:00:00Z")]), now=NOW, snapshot=snapshot)
    assert (listing["shipments"][0]["status"], listing["shipments"][0]["signals"]) == ("unknown", ["baseline_ambiguous"])


def test_old_data_is_stale_but_listed():
    listing = shipment_list(normalized(), now="2026-10-10T13:00:00Z", snapshot=SNAPSHOT, route_evidence=evidence())
    assert listing["status"] == "stale" and listing["reasons"] == ["data_older_than_max_age"]
    assert len(listing["shipments"]) == 6


def test_no_accepted_events_is_empty():
    listing = shipment_list(normalized([]), now=NOW)
    assert (listing["status"], listing["reasons"], listing["shipments"]) == ("empty", ["no_accepted_events"], [])


@pytest.mark.parametrize("kwargs,reason", [
    ({"now": "2026-10-08T13:00:00"}, "invalid_now"),
    ({"now": NOW, "snapshot": {"arrival_lines": "x"}}, "invalid_snapshot"),
    ({"now": NOW, "route_evidence": {"evidence": "x"}}, "invalid_route_evidence"),
])
def test_errors_are_explicit(kwargs, reason):
    listing = shipment_list(normalized(), **kwargs)
    assert (listing["status"], listing["reasons"], listing["shipments"]) == ("error", [reason], [])


def test_inputs_are_not_modified():
    norm, snap, ev = normalized(), copy.deepcopy(SNAPSHOT), evidence()
    before = copy.deepcopy((norm, snap, ev))
    shipment_list(norm, now=NOW, snapshot=snap, route_evidence=ev)
    assert (norm, snap, ev) == before
