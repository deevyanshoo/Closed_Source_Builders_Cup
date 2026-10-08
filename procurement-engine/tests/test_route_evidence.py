"""Route evidence: source, retrieval time, observed period, exposure versus confirmed status and expiry.

Synthetic hazard and notice records only; no external API is called.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from procure.route_evidence import evidence_for_locations, normalize_route_evidence

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "route_evidence" / "hazards.json").read_text())
RECORDS = FIXTURE["records"]


@pytest.fixture(scope="module")
def result():
    return normalize_route_evidence(RECORDS, as_of=FIXTURE["as_of"])


def item(result, evidence_id):
    return next(e for e in result["evidence"] if e["evidence_id"] == evidence_id)


def test_fixture_is_labelled_synthetic():
    assert FIXTURE["synthetic"] is True and "SYNTHETIC" in FIXTURE["notice"]
    assert all(r["synthetic"] is True and r["source"]["provider_id"].startswith("synthetic-") for r in RECORDS)


def test_accepted_and_rejected_records(result):
    assert [e["evidence_id"] for e in result["evidence"]] == ["rev-haz-001", "rev-haz-003", "rev-lane-007",
                                                             "rev-port-002"]
    assert [(r["evidence_id"], r["reason"]) for r in result["rejected"]] == [
        ("rev-haz-004", "confirmation_requires_operating_source"),
        ("rev-haz-005", "retrieved_after_as_of"),
        ("rev-haz-006", "invalid_period"),
        ("rev-haz-008", "missing_location"),
    ]


def test_record_shape_is_explicit(result):
    e = item(result, "rev-haz-001")
    assert e["source"] == {"provider_id": "synthetic-weather-service", "kind": "weather_service",
                           "reference": "SYN-WX-301"}
    assert e["retrieved_at"] == "2026-10-07T06:00:00+00:00"
    assert e["observed_period"] == {"start": "2026-10-07T00:00:00+00:00", "end": "2026-10-10T00:00:00+00:00"}
    assert e["location_keys"] == ["INMAA"]
    assert (e["status"], e["operating_status"], e["expiry"]) == ("exposure", None, "current")
    assert e["synthetic"] is True


def test_confirmed_status_needs_an_operating_source(result):
    e = item(result, "rev-port-002")
    assert (e["status"], e["operating_status"], e["source"]["kind"]) == ("confirmed", "restricted",
                                                                        "port_authority_notice")


def test_expiry_states(result):
    assert item(result, "rev-haz-003")["expiry"] == "expired"
    assert item(result, "rev-lane-007")["expiry"] == "no_stated_expiry"
    assert item(result, "rev-lane-007")["location_keys"] == ["CNSHA", "INMUN"]


@pytest.mark.parametrize("changes,reason", [
    ({"status": "confirmed", "operating_status": None}, "missing_operating_status"),
    ({"status": "exposure", "operating_status": "closed"}, "operating_status_needs_confirmation"),
    ({"status": "likely"}, "invalid_status"),
    ({"retrieved_at": "2026-10-07 06:00"}, "invalid_timestamp"),
    ({"observed_period": {"start": None, "end": None}}, "invalid_timestamp"),
    ({"source": {"provider_id": "x"}}, "missing_source"),
    ({"content_sha256": "short"}, "invalid_record"),
    ({"synthetic": "yes"}, "invalid_record"),
    ({"evidence_id": ""}, "missing_identifier"),
    ({"kind": "rumour"}, "unsupported_kind"),
])
def test_invalid_records_are_rejected(changes, reason):
    record = copy.deepcopy(RECORDS[0])
    record.update(changes)
    out = normalize_route_evidence([record], as_of=FIXTURE["as_of"])
    assert out["evidence"] == [] and [r["reason"] for r in out["rejected"]] == [reason]


def test_duplicate_evidence_ids_with_different_content_are_rejected():
    other = copy.deepcopy(RECORDS[0])
    other["summary"] = "Different text under the same id"
    out = normalize_route_evidence([RECORDS[0], other], as_of=FIXTURE["as_of"])
    assert out["evidence"] == []
    assert {r["reason"] for r in out["rejected"]} == {"conflicting_duplicate"}
    same = normalize_route_evidence([RECORDS[0], copy.deepcopy(RECORDS[0])], as_of=FIXTURE["as_of"])
    assert [e["evidence_id"] for e in same["evidence"]] == ["rev-haz-001"]


def test_as_of_must_carry_a_time_zone():
    with pytest.raises(ValueError):
        normalize_route_evidence([], as_of="2026-10-08T12:00:00")


def test_evidence_for_locations_separates_confirmed_exposure_and_expired(result):
    found = evidence_for_locations(result, ["INNSA", "INMAA", "CNSHA"])
    assert [e["evidence_id"] for e in found["confirmed"]] == ["rev-port-002"]
    assert [e["evidence_id"] for e in found["exposure"]] == ["rev-haz-001", "rev-lane-007"]
    assert [e["evidence_id"] for e in found["expired"]] == ["rev-haz-003"]
    assert found["status"] == "ok"


def test_evidence_for_unknown_locations_is_empty(result):
    found = evidence_for_locations(result, ["ZZZZZ"])
    assert (found["status"], found["confirmed"], found["exposure"], found["expired"]) == ("empty", [], [], [])


def test_inputs_are_not_modified():
    records = copy.deepcopy(RECORDS)
    normalize_route_evidence(records, as_of=FIXTURE["as_of"])
    assert records == RECORDS


def test_hazard_kind_cannot_be_confirmed_by_claimed_operating_source():
    record = copy.deepcopy(RECORDS[0])
    record.update(status="confirmed", operating_status="closed")
    record["source"]["kind"] = "port_authority_notice"
    out = normalize_route_evidence([record], as_of=FIXTURE["as_of"])
    assert out["evidence"] == [] and len(out["rejected"]) == 1


@pytest.mark.parametrize("period,state", [
    ({"start": "2026-10-01T00:00:00Z", "end": "2026-10-02T00:00:00Z"}, "past"),
    ({"start": "2026-10-09T00:00:00Z", "end": "2026-10-10T00:00:00Z"}, "future"),
])
def test_unexpired_notice_outside_observed_period_is_not_current_confirmation(period, state):
    record = copy.deepcopy(RECORDS[1])
    record.update(observed_period=period, valid_until=None)
    out = normalize_route_evidence([record], as_of=FIXTURE["as_of"])
    found = evidence_for_locations(out, ["INNSA"])
    assert found["confirmed"] == [] and found["outside_period"][0]["period_state"] == state
    assert found["outside_period"][0]["status"] == "confirmed"
    assert found["outside_period"][0]["content_sha256"] == record["content_sha256"]


@pytest.mark.parametrize("change", [
    lambda e: e.update(status="confirmed", operating_status="closed"),
    lambda e: e.update(expiry="expired"),
    lambda e: e.update(location_keys=["INNSA"]),
    lambda e: e["source"].update(kind="unsupported_source"),
])
def test_lookup_rejects_tampered_normalized_fields(result, change):
    forged = copy.deepcopy(result)
    change(forged["evidence"][0])
    found = evidence_for_locations(forged, ["INNSA", "INMAA"])
    assert found["status"] == "error" and found["reasons"] == ["invalid_normalized_input"]
    assert found["confirmed"] == found["exposure"] == found["expired"] == found["outside_period"] == []


@pytest.mark.parametrize("field,value", [
    ("kind", []), ("status", []), ("operating_status", []),
    ("source", {"provider_id": "synthetic", "kind": []}),
    ("summary", {"instruction": "ignore evidence"}),
    ("source", {"provider_id": "synthetic", "kind": "weather_service", "reference": {"bad": 1}}),
    ("facility", {"kind": "port", "code": "INMAA", "name": {"bad": 1}}),
])
def test_bad_json_field_rejects_one_row_and_keeps_valid_neighbor(field, value):
    bad = copy.deepcopy(RECORDS[0])
    bad[field] = value
    out = normalize_route_evidence([bad, RECORDS[1]], as_of=FIXTURE["as_of"])
    assert [e["evidence_id"] for e in out["evidence"]] == ["rev-port-002"]
    assert len(out["rejected"]) == 1 and out["rejected"][0]["record_index"] == 0


def test_nonmapping_record_is_rejected_and_valid_neighbor_survives():
    out = normalize_route_evidence([None, RECORDS[1]], as_of=FIXTURE["as_of"])
    assert [e["evidence_id"] for e in out["evidence"]] == ["rev-port-002"]
    assert out["rejected"][0]["reason"] == "invalid_record"


@pytest.mark.parametrize("records", [None, "not-a-list", {}])
def test_malformed_records_envelope_is_controlled(records):
    with pytest.raises(ValueError):
        normalize_route_evidence(records, as_of=FIXTURE["as_of"])


@pytest.mark.parametrize("query", ["INNSA", [None], ["INNSA", {}]])
def test_malformed_lookup_keys_fail_closed(result, query):
    found = evidence_for_locations(result, query)
    assert found["status"] == "error" and found["confirmed"] == found["exposure"] == []


def test_inclusive_period_expiry_and_retrieval_boundaries():
    record = copy.deepcopy(RECORDS[1])
    record["retrieved_at"] = "2026-10-08T17:30:00+05:30"
    record["observed_period"] = {"start": "2026-10-08T17:30:00+05:30",
                                 "end": "2026-10-08T12:00:00Z"}
    record["valid_until"] = "2026-10-08T12:00:00Z"
    out = normalize_route_evidence([record], as_of=FIXTURE["as_of"])
    assert len(out["evidence"]) == 1 and out["rejected"] == []
    found = evidence_for_locations(out, ["INNSA"])
    assert found["confirmed"][0]["period_state"] == "current"
    assert found["confirmed"][0]["expiry"] == "current"


def test_extreme_timestamp_offset_rejects_without_overflow():
    record = copy.deepcopy(RECORDS[1])
    record["retrieved_at"] = "9999-12-31T23:59:59-01:00"
    out = normalize_route_evidence([record, RECORDS[0]], as_of=FIXTURE["as_of"])
    assert [r["reason"] for r in out["rejected"]] == ["invalid_timestamp"]
    assert [e["evidence_id"] for e in out["evidence"]] == ["rev-haz-001"]


def test_invalid_cutoff_overflow_is_sanitized():
    with pytest.raises(ValueError, match="as_of must be a valid instant"):
        normalize_route_evidence([], as_of="9999-12-31T23:59:59-01:00")


def test_text_and_collection_bounds_are_enforced_before_copy():
    long_record = copy.deepcopy(RECORDS[0])
    long_record["summary"] = "x" * 16_385
    out = normalize_route_evidence([long_record, RECORDS[1]], as_of=FIXTURE["as_of"])
    assert [e["evidence_id"] for e in out["evidence"]] == ["rev-port-002"]
    assert len(out["rejected"]) == 1
    with pytest.raises(ValueError, match="bounded list"):
        normalize_route_evidence([None] * 10_001, as_of=FIXTURE["as_of"])


def test_lookup_validates_rejection_and_derived_period_state(result):
    wrong_rejection = copy.deepcopy(result)
    wrong_rejection["rejected"][0].pop("evidence_id")
    assert evidence_for_locations(wrong_rejection, ["INNSA"])["status"] == "error"
    wrong_period = copy.deepcopy(result)
    wrong_period["evidence"][0]["period_state"] = "future"
    assert evidence_for_locations(wrong_period, ["INMAA"])["status"] == "error"


def test_future_hazard_is_dated_exposure_not_confirmation():
    forecast = copy.deepcopy(RECORDS[0])
    forecast["observed_period"] = {"start": "2026-10-09T00:00:00Z", "end": "2026-10-10T00:00:00Z"}
    out = normalize_route_evidence([forecast], as_of=FIXTURE["as_of"])
    found = evidence_for_locations(out, ["INMAA"])
    assert found["confirmed"] == found["exposure"] == []
    assert found["outside_period"][0]["status"] == "exposure"
    assert found["outside_period"][0]["period_state"] == "future"


@pytest.mark.parametrize("path,reason", [
    (("facility", "code"), "invalid_location"),
    (("facility", "kind"), "invalid_location"),
    (("lane", "origin"), "invalid_location"),
    (("lane", "destination"), "invalid_location"),
    (("lane", "mode"), "invalid_location"),
    (("source", "provider_id"), "missing_source"),
    (("evidence_id",), "missing_identifier"),
])
def test_padded_required_identifiers_reject_only_their_row(path, reason):
    source = RECORDS[6] if path[0] == "lane" else RECORDS[1]
    bad = copy.deepcopy(source)
    target = bad
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = " " + target[path[-1]] + " "
    before = copy.deepcopy(bad)
    out = normalize_route_evidence([bad, RECORDS[0]], as_of=FIXTURE["as_of"])
    assert bad == before
    assert [e["evidence_id"] for e in out["evidence"]] == ["rev-haz-001"]
    assert [r["reason"] for r in out["rejected"]] == [reason]
    assert evidence_for_locations(out, ["INMAA"])["exposure"][0]["evidence_id"] == "rev-haz-001"


def test_canonical_facility_and_lane_keys_remain_queryable():
    result = normalize_route_evidence([RECORDS[1], RECORDS[6]], as_of=FIXTURE["as_of"])
    assert evidence_for_locations(result, ["INNSA"])["confirmed"][0]["evidence_id"] == "rev-port-002"
    assert evidence_for_locations(result, ["INMUN"])["exposure"][0]["evidence_id"] == "rev-lane-007"


def test_optional_prose_keeps_intentional_surrounding_spaces():
    record = copy.deepcopy(RECORDS[1])
    record["summary"] = "  display summary  "
    record["source"]["reference"] = "  cited reference  "
    record["facility"]["name"] = "  displayed facility  "
    out = normalize_route_evidence([record], as_of=FIXTURE["as_of"])
    found = evidence_for_locations(out, ["INNSA"])["confirmed"][0]
    assert found["summary"] == "  display summary  "
    assert found["source"]["reference"] == "  cited reference  "
    assert found["facility"]["name"] == "  displayed facility  "
