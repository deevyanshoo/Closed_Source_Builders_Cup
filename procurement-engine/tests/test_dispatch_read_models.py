"""Dispatch read models over normalised milestones: per-shipment timeline and affected shipments.

Synthetic fixtures only. `now` is always passed explicitly; nothing reads a hidden clock.
"""
from __future__ import annotations

import copy
import json
from decimal import localcontext
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
    assert e["evidence"] == normalized["events"][0]["evidence"]
    assert set(e["evidence"][0]) == {"evidence_id", "kind", "locator", "content_sha256",
                                     "retrieved_at", "valid_until", "status"}


@pytest.mark.parametrize("precision", [4, 28])
def test_quantity_sum_is_exact_under_low_ambient_precision(normalized, precision):
    data = copy.deepcopy(normalized)
    part = next(e for e in data["events"] if e["event_type"] == "partially_delivered")
    part["quantity"] = {"amount": "12345678901234567890.12345678901234567890", "unit": "tonne"}
    tiny = copy.deepcopy(part)
    tiny["event_id"] = "tiny-tonne"
    tiny["quantity"] = {"amount": "0.00000000000000000001", "unit": "tonne"}
    other = copy.deepcopy(part)
    other["event_id"] = "tiny-kg"
    other["quantity"] = {"amount": "0.00000000000000000001", "unit": "kg"}
    data["events"].extend((tiny, other))
    with localcontext() as ctx:
        ctx.prec = precision
        got = shipment_timeline(data, part["shipment_id"], now=NOW)["current"]["delivered_quantity"]
    assert got == {"kg": "0.00000000000000000001",
                   "tonne": "12345678901234567890.12345678901234567891"}


@pytest.mark.parametrize("precision", [4, 28])
def test_two_allowed_long_amounts_sum_without_combined_integer_string_conversion(precision):
    first = copy.deepcopy(RECORDS[3])
    first["quantity"] = {"amount": "9" * 3999, "unit": "tonne"}
    second = copy.deepcopy(first)
    second.update(event_id="long-fraction", occurred_at="2026-10-07T16:00:00+05:30",
                  observed_at="2026-10-07T11:00:00Z",
                  quantity={"amount": "0." + "0" * 3997 + "1", "unit": "tonne"})
    second["evidence"][0].update(evidence_id="long-fraction-evidence", retrieved_at="2026-10-07T11:00:00Z")
    other_unit = copy.deepcopy(second)
    other_unit.update(event_id="long-other-unit", quantity={"amount": "0.125", "unit": "kg"})
    other_unit["evidence"][0]["evidence_id"] = "long-other-unit-evidence"
    data = normalize_milestones([first, second, other_unit], as_of=FIXTURE["as_of"])
    assert len(data["events"]) == 3 and data["rejected"] == []
    with localcontext() as ctx:
        ctx.prec = precision
        got = shipment_timeline(data, "SHP-SYN-003", now=NOW)
    assert got["status"] == "ok"
    assert got["current"]["delivered_quantity"] == {
        "kg": "0.125", "tonne": "9" * 3999 + "." + "0" * 3997 + "1"}


def test_missing_rejection_event_id_is_controlled_for_both_views(normalized):
    data = copy.deepcopy(normalized)
    data["rejected"] = [{"record_index": 0, "reason": "missing_identifier", "details": ["event_id"]}]
    for got in (shipment_timeline(data, "SHP-SYN-002", now=NOW, records=RECORDS),
                affected_shipments(data, "evt-002-hold", now=NOW)):
        assert got["status"] == "error" and got["reasons"] == ["invalid_normalized_input"]
        assert got.get("events", got.get("affected")) == []
    data["rejected"][0]["event_id"] = None
    assert shipment_timeline(data, "SHP-SYN-002", now=NOW)["status"] == "ok"
    assert affected_shipments(data, "evt-002-hold", now=NOW)["status"] == "ok"


def test_timeline_retains_caller_claimed_synthetic_label(normalized):
    data = copy.deepcopy(normalized)
    own = next(e for e in data["events"] if e["event_id"] == "evt-002-hold")
    own["synthetic"] = False
    entry = shipment_timeline(data, "SHP-SYN-002", now=NOW)["events"][0]
    assert entry["synthetic"] is False
    for bad in (None, "false"):
        own["synthetic"] = bad
        got = shipment_timeline(data, "SHP-SYN-002", now=NOW)
        assert got["status"] == "error" and got["reasons"] == ["invalid_normalized_input"]
    own.pop("synthetic")
    assert shipment_timeline(data, "SHP-SYN-002", now=NOW)["status"] == "error"


def test_nonoperating_arrival_and_release_do_not_close_operating_eta_or_hold(normalized):
    data = copy.deepcopy(normalized)
    hold = next(e for e in data["events"] if e["event_id"] == "evt-002-hold")
    hold["flags"].append("superseded_by_arrival")
    for event_type in ("arrived", "port_released"):
        later = copy.deepcopy(hold)
        later.update(event_id=f"unusable-{event_type}", event_type=event_type,
                     occurred_at="2026-10-08T11:00:00+00:00", operating_evidence=False,
                     flags=["evidence_expired"])
        data["events"].append(later)
    current = shipment_timeline(data, "SHP-SYN-002", now=NOW)["current"]
    assert current["open_eta"]["event_id"] == "evt-002-hold"
    assert current["on_hold"] is True
    assert current["unusable_event_ids"] == ["unusable-arrived", "unusable-port_released"]


@pytest.mark.parametrize("kind", ["location", "delivery"])
def test_nonoperating_peer_does_not_create_or_remove_exposure(normalized, kind):
    data = copy.deepcopy(normalized)
    data["events"] = [e for e in data["events"] if e["shipment_id"] == "SHP-SYN-002"]
    trigger = data["events"][0]
    peer = copy.deepcopy(trigger)
    peer.update(event_id=f"peer-{kind}", shipment_id="SHP-SYN-FOREIGN",
                event_type="departed", operating_evidence=kind == "delivery")
    if kind == "delivery":
        terminal = copy.deepcopy(peer)
        terminal.update(event_id="expired-delivery", event_type="delivered", operating_evidence=False)
        data["events"].extend((peer, terminal))
    else:
        data["events"].append(peer)
    got = affected_shipments(data, trigger["event_id"], now=NOW)
    foreign = [row for row in got["affected"] if row["shipment_id"] == "SHP-SYN-FOREIGN"]
    assert (len(foreign) == 1) == (kind == "delivery")
    assert not any(row["shipment_id"] == "SHP-SYN-FOREIGN" for row in got["excluded"])
    if foreign:
        assert foreign[0]["latest_event_type"] == "departed"


@pytest.mark.parametrize("mutation", [
    lambda d: d["events"].append("broken"),
    lambda d: d["events"][0].pop("occurred_at"),
    lambda d: d["events"][0].pop("operating_evidence"),
    lambda d: d["events"][0].update(event_type=[]),
    lambda d: d["events"][0].update(evidence=[None]),
    lambda d: d["events"][0]["evidence"][0].update(status=[]),
    lambda d: d["events"][0]["evidence"][0].pop("valid_until"),
    lambda d: d["events"][0].update(facility={"kind": "port"}),
    lambda d: d["events"][0].update(quantity={"amount": "NaN", "unit": "tonne"}),
    lambda d: d["events"][0].update(quantity={"amount": "9" * 4_001, "unit": "tonne"}),
    lambda d: d["shipments"].update({"SHP-SYN-001": None}),
    lambda d: d["rejected"].append({"event_id": "bad"}),
    lambda d: d["rejected"].append({"record_index": True, "event_id": None,
                                     "reason": "bad", "details": []}),
])
def test_malformed_consumed_nested_input_returns_controlled_error(normalized, mutation):
    data = copy.deepcopy(normalized)
    mutation(data)
    for result in (shipment_timeline(data, "SHP-SYN-001", now=NOW, records=RECORDS),
                   affected_shipments(data, "evt-002-hold", now=NOW)):
        assert result["status"] == "error"
        assert result["reasons"] == ["invalid_normalized_input"]
        assert result.get("events", result.get("affected")) == []


@pytest.mark.parametrize("limit", [-1, True, "86400", None])
def test_invalid_age_limit_is_controlled(normalized, limit):
    assert shipment_timeline(normalized, "SHP-SYN-002", now=NOW,
                             max_age_seconds=limit)["status"] == "error"
    assert affected_shipments(normalized, "evt-002-hold", now=NOW,
                              max_age_seconds=limit)["status"] == "error"


def test_age_limit_uses_full_precision(normalized):
    assert shipment_timeline(normalized, "SHP-SYN-002", now="2026-10-09T12:00:00Z")["status"] == "ok"
    assert shipment_timeline(normalized, "SHP-SYN-002", now="2026-10-09T12:00:00.000001Z")["status"] == "stale"


@pytest.mark.parametrize("extreme", ["9999-12-31T23:59:59-01:00", "0001-01-01T00:00:00+01:00"])
@pytest.mark.parametrize("field", ["as_of", "now", "occurred_at", "observed_at", "eta",
                                   "retrieved_at", "valid_until"])
def test_utc_conversion_overflow_is_controlled_in_both_views(normalized, extreme, field):
    data = copy.deepcopy(normalized)
    now = NOW
    if field == "as_of":
        data["as_of"] = extreme
    elif field == "now":
        now = extreme
    elif field in {"retrieved_at", "valid_until"}:
        data["events"][0]["evidence"][0][field] = extreme
    else:
        data["events"][0][field] = extreme
    reason = "invalid_now" if field == "now" else "invalid_normalized_input"
    for got in (shipment_timeline(data, "SHP-SYN-002", now=now),
                affected_shipments(data, "evt-002-hold", now=now)):
        assert got["status"] == "error" and got["reasons"] == [reason]
        assert got.get("events", got.get("affected")) == []


def test_valid_utc_boundary_and_equivalent_offsets_remain_accepted(normalized):
    data = copy.deepcopy(normalized)
    data["as_of"] = "2026-10-08T17:30:00+05:30"
    assert shipment_timeline(data, "SHP-SYN-002", now="2026-10-08T13:00:00Z")["status"] == "ok"
    assert affected_shipments(data, "evt-002-hold", now="2026-10-08T18:30:00+05:30")["status"] == "ok"


def test_records_shape_is_controlled(normalized):
    got = shipment_timeline(normalized, "SHP-SYN-002", now=NOW, records="not-a-list")
    assert got["status"] == "error" and got["reasons"] == ["invalid_records"] and got["events"] == []


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
