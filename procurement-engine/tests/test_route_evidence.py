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
