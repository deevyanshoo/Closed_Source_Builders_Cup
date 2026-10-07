"""Shipment milestone normalisation: explicit IDs, UTC instants, location, source and evidence; no guessing."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from procure.events.ingest import normalize_milestones

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "shipments" / "milestones.json").read_text())
AS_OF = FIXTURE["as_of"]


@pytest.fixture(scope="module")
def result():
    return normalize_milestones(FIXTURE["records"], as_of=AS_OF)


def event(result, event_id):
    return next(e for e in result["events"] if e["event_id"] == event_id)


def test_accepted_events_are_exactly_the_valid_ones(result):
    assert sorted(e["event_id"] for e in result["events"]) == sorted([
        "evt-001-dep", "evt-001-arr", "evt-002-hold", "evt-003-part", "evt-004-eta",
        "evt-005-arr", "evt-005-dep", "evt-005-eta", "evt-008-eta"])
    assert result["as_of"] == "2026-10-08T12:00:00+00:00"


def test_each_rejection_has_a_record_index_and_reason(result):
    got = {(r["record_index"], r["reason"], tuple(r["details"])) for r in result["rejected"]}
    assert got == {
        (10, "missing_identifier", ("shipment_id",)),
        (11, "missing_identifier", ("event_id",)),
        (12, "conflicting_duplicate", ("evt-007-dep",)),
        (13, "conflicting_duplicate", ("evt-007-dep",)),
        (14, "invalid_timestamp", ("occurred_at",)),
        (15, "observed_before_occurred", ()),
        (17, "observed_after_as_of", ()),
        (18, "unsupported_event_type", ("teleported",)),
        (19, "missing_location", ()),
    }


def test_instants_are_utc_in_the_propagation_format_and_keep_the_local_offset(result):
    dep = event(result, "evt-001-dep")
    assert dep["occurred_at"] == "2026-09-20T17:00:00+00:00"
    assert dep["occurred_at_offset"] == "-03:00"
    assert dep["observed_at"] == "2026-09-20T17:30:00+00:00"
    assert event(result, "evt-002-hold")["eta"] == "2026-10-12T02:30:00+00:00"


def test_types_are_canonical_with_the_raw_label_kept(result):
    assert (event(result, "evt-001-dep")["event_type"], event(result, "evt-001-dep")["raw_type"]) == \
           ("departed", "DEPARTED")
    assert event(result, "evt-004-eta")["event_type"] == "eta_changed"
    assert event(result, "evt-003-part")["event_type"] == "partially_delivered"


def test_location_source_and_evidence_are_explicit(result):
    dep = event(result, "evt-001-dep")
    assert dep["facility"] == {"kind": "port", "code": "CLVAP", "name": "Valparaiso (synthetic)"}
    assert dep["lane"] == {"origin": "CLVAP", "destination": "INNSA", "mode": "ocean"}
    assert event(result, "evt-004-eta")["facility"] is None
    assert dep["source"] == {"provider_id": "synthetic-carrier-feed", "kind": "carrier_feed",
                             "reference": "SYN-BL-0001"}
    assert dep["evidence"][0]["evidence_id"] == "ev-001-bl"
    assert dep["evidence"][0]["status"] == "no_stated_expiry"
    assert dep["synthetic"] is True and dep["record_index"] == 0


def test_partial_quantity_is_kept_as_an_exact_decimal_string(result):
    assert event(result, "evt-003-part")["quantity"] == {"amount": "20.000", "unit": "tonne"}


def test_duplicates_keep_the_earliest_observation(result):
    assert result["duplicates"] == [{"event_id": "evt-004-eta", "record_indexes": [4, 5, 6], "kept_record_index": 4}]
    assert event(result, "evt-004-eta")["observed_at"] == "2026-10-04T10:00:00+00:00"


def test_out_of_order_arrival_is_sorted_and_flagged(result):
    shipment = result["shipments"]["SHP-SYN-005"]
    assert shipment["event_ids"] == ["evt-005-dep", "evt-005-eta", "evt-005-arr"]
    assert "observed_out_of_order" in event(result, "evt-005-dep")["flags"]
    assert "observed_out_of_order" not in event(result, "evt-005-arr")["flags"]
    assert "superseded_by_arrival" in event(result, "evt-005-eta")["flags"]


def test_expired_evidence_is_kept_but_not_operating_evidence(result):
    eta = event(result, "evt-008-eta")
    assert eta["evidence"][0]["status"] == "expired"
    assert "evidence_expired" in eta["flags"]
    assert eta["operating_evidence"] is False
    assert event(result, "evt-002-hold")["evidence"][0]["status"] == "current"
    assert event(result, "evt-002-hold")["operating_evidence"] is True


def test_events_are_sorted_by_shipment_then_occurrence(result):
    keys = [(e["shipment_id"], e["occurred_at"], e["event_id"]) for e in result["events"]]
    assert keys == sorted(keys)


def test_input_records_are_not_modified():
    records = copy.deepcopy(FIXTURE["records"])
    normalize_milestones(records, as_of=AS_OF)
    assert records == FIXTURE["records"]


# ---------- rules exercised with small inline records ----------

def base(**changes):
    record = copy.deepcopy(FIXTURE["records"][0])
    record.update(changes)
    return record


@pytest.mark.parametrize("changes,reason,details", [
    ({"type": "eta_changed"}, "missing_eta", ()),
    ({"type": "partial_delivery"}, "invalid_quantity", ("quantity",)),
    ({"type": "partial_delivery", "quantity": {"amount": "1e3", "unit": "tonne"}}, "invalid_quantity", ("quantity",)),
    ({"type": "partial_delivery", "quantity": {"amount": "-5", "unit": "tonne"}}, "invalid_quantity", ("quantity",)),
    ({"source": None}, "missing_source", ()),
    ({"evidence": []}, "missing_evidence", ()),
    ({"evidence": [{"evidence_id": "x", "kind": "k", "locator": "l", "content_sha256": "short",
                    "retrieved_at": "2026-09-20T17:30:00Z", "valid_until": None}]}, "invalid_evidence", ("ev:x",)),
    ({"observed_at": "2026-09-20 17:30"}, "invalid_timestamp", ("observed_at",)),
    ({"occurred_at": "2026-09-20T14:00:00.1234567-03:00"}, "invalid_timestamp", ("occurred_at",)),
    ({"eta": "soon", "type": "eta_changed"}, "invalid_timestamp", ("eta",)),
    ({"synthetic": "yes"}, "invalid_record", ("synthetic",)),
    ({"shipment_id": " SHP-SYN-001"}, "missing_identifier", ("shipment_id",)),
    ({"facility": {"kind": "port"}, "lane": None}, "invalid_location", ("facility",)),
    ({"facility": None, "lane": {"origin": "CLVAP", "mode": "ocean"}}, "invalid_location", ("lane",)),
])
def test_invalid_records_are_rejected_with_a_reason(changes, reason, details):
    out = normalize_milestones([base(**changes)], as_of=AS_OF)
    assert out["events"] == []
    assert [(r["reason"], tuple(r["details"])) for r in out["rejected"]] == [(reason, details)]


def test_non_mapping_record_is_rejected():
    out = normalize_milestones(["not a record"], as_of=AS_OF)
    assert [r["reason"] for r in out["rejected"]] == ["invalid_record"]


def test_as_of_must_carry_a_time_zone():
    with pytest.raises(ValueError):
        normalize_milestones([], as_of="2026-10-08T12:00:00")


def test_arrival_before_departure_is_a_sequence_conflict():
    dep = base()
    arr = base(event_id="evt-001-early-arr", type="arrived", occurred_at="2026-09-19T00:00:00Z",
               observed_at="2026-09-19T01:00:00Z")
    out = normalize_milestones([dep, arr], as_of=AS_OF)
    assert "sequence_conflict" in out["shipments"]["SHP-SYN-001"]["flags"]
