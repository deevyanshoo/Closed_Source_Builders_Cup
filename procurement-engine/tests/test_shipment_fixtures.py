"""The synthetic shipment milestone fixtures contain every case the normaliser must handle."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "shipments" / "milestones.json").read_text())
RECORDS = FIXTURE["records"]


def by_shipment(shipment_id):
    return [r for r in RECORDS if r.get("shipment_id") == shipment_id]


def test_fixture_is_labelled_synthetic_everywhere():
    assert FIXTURE["synthetic"] is True and "SYNTHETIC" in FIXTURE["notice"]
    assert all(r["synthetic"] is True for r in RECORDS)
    assert all("(synthetic)" in r["facility"]["name"] for r in RECORDS if "facility" in r)
    assert all(r["source"]["provider_id"].startswith("synthetic-") for r in RECORDS)


def test_six_to_ten_shipments_each_with_a_described_case():
    shipments = {r["shipment_id"] for r in RECORDS if r.get("shipment_id")}
    assert 6 <= len(shipments) <= 10
    assert shipments <= set(FIXTURE["cases"])


def test_departed_arrived_hold_and_partial_delivery_are_present():
    types = {r["type"].lower() for r in RECORDS}
    assert {"departed", "arrived", "port_hold", "partial_delivery"} <= types
    partial = next(r for r in RECORDS if r["type"] == "partial_delivery")
    assert partial["quantity"] == {"amount": "20.000", "unit": "tonne"}


def test_duplicates_include_identical_and_re_observed_copies():
    copies = [r for r in RECORDS if r["event_id"] == "evt-004-eta"]
    assert len(copies) == 3
    assert copies[0] == copies[1]
    assert copies[2]["observed_at"] > copies[0]["observed_at"]
    assert {k: v for k, v in copies[2].items() if k not in ("observed_at", "evidence")} == \
           {k: v for k, v in copies[0].items() if k not in ("observed_at", "evidence")}


def test_out_of_order_arrival_is_received_before_departure():
    rows = by_shipment("SHP-SYN-005")
    order = [r["event_id"] for r in rows]
    assert order.index("evt-005-arr") < order.index("evt-005-dep")


def test_missing_identifiers_are_really_missing():
    assert any(r["shipment_id"] is None for r in RECORDS)
    assert any(r["event_id"] == "" for r in RECORDS)


def test_conflicting_and_invalid_timestamps_are_present():
    deps = [r for r in RECORDS if r["event_id"] == "evt-007-dep"]
    assert len({r["occurred_at"] for r in deps}) == 2
    no_zone = next(r for r in RECORDS if r["event_id"] == "evt-007-arr")
    assert datetime.fromisoformat(no_zone["occurred_at"]).tzinfo is None
    backwards = next(r for r in RECORDS if r["event_id"] == "evt-007-eta")
    assert datetime.fromisoformat(backwards["observed_at"].replace("Z", "+00:00")) < \
           datetime.fromisoformat(backwards["occurred_at"].replace("Z", "+00:00"))


def test_exactly_one_evidence_record_is_expired_at_as_of():
    as_of = datetime.fromisoformat(FIXTURE["as_of"].replace("Z", "+00:00"))
    expired = [e["evidence_id"] for r in RECORDS for e in r["evidence"]
               if e["valid_until"] and datetime.fromisoformat(e["valid_until"].replace("Z", "+00:00")) < as_of]
    assert expired == ["ev-008-notice"]
