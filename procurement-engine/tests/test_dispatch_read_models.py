"""Dispatch read models over normalised milestones: per-shipment timeline and affected shipments.

Synthetic fixtures only. `now` is always passed explicitly; nothing reads a hidden clock.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from procure.dispatch import affected_shipments, shipment_timeline
from procure.events.ingest import normalize_milestones

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "shipments" / "milestones.json").read_text())
RECORDS = FIXTURE["records"]
NOW = "2026-10-08T13:00:00Z"


@pytest.fixture(scope="module")
def normalized():
    return normalize_milestones(RECORDS, as_of=FIXTURE["as_of"])


# ---------- timeline ----------

def test_timeline_lists_events_in_occurrence_order_with_flags(normalized):
    t = shipment_timeline(normalized, "SHP-SYN-005", now=NOW)
    assert t["status"] == "ok" and t["reasons"] == []
    assert [e["event_id"] for e in t["events"]] == ["evt-005-dep", "evt-005-eta", "evt-005-arr"]
    assert t["events"][0]["flags"] == ["observed_out_of_order"]
    assert t["events"][1]["flags"] == ["superseded_by_arrival"]
    assert t["current"]["last_event_type"] == "arrived"
    assert t["current"]["open_eta"] is None
    assert (t["as_of"], t["age_seconds"]) == ("2026-10-08T12:00:00+00:00", 3600)


def test_timeline_shows_an_open_eta_and_hold(normalized):
    current = shipment_timeline(normalized, "SHP-SYN-002", now=NOW)["current"]
    assert current["open_eta"] == {"eta": "2026-10-12T02:30:00+00:00", "event_id": "evt-002-hold"}
    assert current["on_hold"] is True


def test_timeline_sums_partial_deliveries_exactly_per_unit(normalized):
    current = shipment_timeline(normalized, "SHP-SYN-003", now=NOW)["current"]
    assert current["delivered_quantity"] == {"tonne": "20.000"}
    extra = copy.deepcopy(RECORDS[3])
    extra.update(event_id="evt-003-part-2", occurred_at="2026-10-07T16:00:00+05:30",
                 observed_at="2026-10-07T11:00:00Z", quantity={"amount": "0.125", "unit": "tonne"})
    extra["evidence"][0].update(evidence_id="ev-003-grn-2", retrieved_at="2026-10-07T11:00:00Z")
    two = normalize_milestones([RECORDS[3], extra], as_of=FIXTURE["as_of"])
    assert shipment_timeline(two, "SHP-SYN-003", now=NOW)["current"]["delivered_quantity"] == {"tonne": "20.125"}


def test_eta_without_operating_evidence_is_not_an_open_eta(normalized):
    t = shipment_timeline(normalized, "SHP-SYN-008", now=NOW)
    assert t["events"][0]["operating_evidence"] is False
    assert t["current"]["open_eta"] is None
    assert t["current"]["unusable_event_ids"] == ["evt-008-eta"]


def test_timeline_includes_evidence_and_location(normalized):
    e = shipment_timeline(normalized, "SHP-SYN-001", now=NOW)["events"][0]
    assert e["facility"]["code"] == "CLVAP" and e["lane"]["destination"] == "INNSA"
    assert e["source"]["provider_id"] == "synthetic-carrier-feed"
    assert e["evidence"] == [{"evidence_id": "ev-001-bl", "kind": "bill_of_lading",
                              "locator": "synthetic://docs/SYN-BL-0001#p1", "status": "no_stated_expiry"}]


def test_shipment_with_only_rejected_records_is_empty_and_says_why(normalized):
    t = shipment_timeline(normalized, "SHP-SYN-007", now=NOW, records=RECORDS)
    assert t["status"] == "empty" and t["events"] == []
    assert [(r["record_index"], r["reason"]) for r in t["rejected_records"]] == [
        (12, "conflicting_duplicate"), (13, "conflicting_duplicate"), (14, "invalid_timestamp"),
        (15, "observed_before_occurred")]
    assert t["reasons"] == ["no_accepted_events"]


def test_unknown_shipment_is_empty(normalized):
    t = shipment_timeline(normalized, "SHP-NOT-THERE", now=NOW, records=RECORDS)
    assert (t["status"], t["events"], t["rejected_records"]) == ("empty", [], [])


def test_old_data_is_stale_but_still_shown(normalized):
    t = shipment_timeline(normalized, "SHP-SYN-002", now="2026-10-10T12:00:00Z")
    assert t["status"] == "stale"
    assert t["reasons"] == ["data_older_than_max_age"] and t["age_seconds"] == 172800
    assert [e["event_id"] for e in t["events"]] == ["evt-002-hold"]
    assert shipment_timeline(normalized, "SHP-SYN-002", now="2026-10-10T12:00:00Z",
                             max_age_seconds=3 * 86400)["status"] == "ok"


@pytest.mark.parametrize("kwargs,reason", [
    ({"shipment_id": "SHP-SYN-002", "now": "2026-10-08T13:00:00"}, "invalid_now"),
    ({"shipment_id": "SHP-SYN-002", "now": "2026-10-08T11:00:00Z"}, "as_of_after_now"),
    ({"shipment_id": "", "now": NOW}, "invalid_shipment_id"),
])
def test_timeline_errors_are_explicit(normalized, kwargs, reason):
    t = shipment_timeline(normalized, kwargs.pop("shipment_id"), **kwargs)
    assert (t["status"], t["reasons"], t["events"]) == ("error", [reason], [])


def test_malformed_normalized_input_is_an_error():
    assert shipment_timeline({"events": "x"}, "SHP-SYN-002", now=NOW)["reasons"] == ["invalid_normalized_input"]


# ---------- affected shipments ----------

def test_port_event_affects_shipments_touching_that_port_as_exposure(normalized):
    a = affected_shipments(normalized, "evt-002-hold", now=NOW)
    assert a["status"] == "ok" and a["location_keys"] == ["INNSA"]
    assert [(s["shipment_id"], s["relation"], s["basis"], s["status"]) for s in a["affected"]] == [
        ("SHP-SYN-001", "shares_location", ["facility:INNSA", "lane_destination:INNSA"], "exposure"),
        ("SHP-SYN-002", "self", ["facility:INNSA"], "exposure"),
    ]
    assert a["note"].startswith("Exposure only")


def test_lane_event_uses_both_lane_ends(normalized):
    a = affected_shipments(normalized, "evt-004-eta", now=NOW)
    assert a["location_keys"] == ["AEJEA", "INMAA"]
    assert [s["shipment_id"] for s in a["affected"]] == ["SHP-SYN-004"]


def test_delivered_shipments_are_excluded_and_can_leave_the_list_empty():
    hold = copy.deepcopy(RECORDS[2])
    delivered = copy.deepcopy(RECORDS[2])
    delivered.update(event_id="evt-002-delivered", type="delivered", occurred_at="2026-10-06T00:00:00Z",
                     observed_at="2026-10-06T01:00:00Z")
    delivered.pop("eta")
    delivered["evidence"][0].update(evidence_id="ev-002-del", retrieved_at="2026-10-06T01:00:00Z")
    hold.update(occurred_at="2026-10-05T00:00:00Z", observed_at="2026-10-05T01:00:00Z")
    norm = normalize_milestones([hold, delivered], as_of=FIXTURE["as_of"])
    a = affected_shipments(norm, "evt-002-hold", now=NOW)
    assert a["status"] == "empty" and a["affected"] == []
    assert a["excluded"] == [{"shipment_id": "SHP-SYN-002", "reason": "already_delivered"}]


def test_trigger_without_operating_evidence_is_refused(normalized):
    a = affected_shipments(normalized, "evt-008-eta", now=NOW)
    assert (a["status"], a["reasons"], a["affected"]) == ("error", ["trigger_not_operating_evidence"], [])


def test_unknown_trigger_is_an_error(normalized):
    assert affected_shipments(normalized, "evt-nope", now=NOW)["reasons"] == ["unknown_event"]


def test_affected_list_can_be_stale(normalized):
    a = affected_shipments(normalized, "evt-002-hold", now="2026-10-10T12:00:00Z")
    assert a["status"] == "stale" and [s["shipment_id"] for s in a["affected"]] == ["SHP-SYN-001", "SHP-SYN-002"]


def test_read_models_do_not_modify_input(normalized):
    before = copy.deepcopy(normalized)
    shipment_timeline(normalized, "SHP-SYN-005", now=NOW)
    affected_shipments(normalized, "evt-002-hold", now=NOW)
    assert normalized == before
