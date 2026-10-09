"""Working-day lead time for a shipment timeline, over the synthetic holiday table."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from procure.api import app
from procure.calendars import SyntheticHolidayProvider
from procure.dispatch import shipment_timeline
from procure.dispatch.lead_time import shipment_lead_time
from procure.events.ingest import normalize_milestones

HERE = Path(__file__).parent / "fixtures" / "shipments"
RECORDS = json.loads((HERE / "milestones.json").read_text())["records"]
AS_OF = "2026-10-08T12:00:00Z"
NOW = "2026-10-08T13:00:00Z"
NORMALIZED = normalize_milestones(RECORDS, as_of=AS_OF)
PROVIDER = SyntheticHolidayProvider()


def lead(shipment_id, provider=PROVIDER):
    return shipment_lead_time(shipment_timeline(NORMALIZED, shipment_id, now=NOW), provider)


def test_open_eta_counts_from_the_cutoff_in_the_destination_country():
    r = lead("SHP-SYN-004")
    assert (r["status"], r["basis"], r["country"]) == ("ok", "cutoff_to_expected_arrival", "IN")
    assert (r["start"], r["end"]) == ("2026-10-08", "2026-10-15")
    assert (r["working_days"], r["weekend_days"]) == (4, 2)
    assert r["holidays"] == [{"date": "2026-10-14", "name": "Synthetic closure day IN-A"}]
    assert r["synthetic"] is True
    assert r["location"] == "lane_destination:INMAA"


def test_hold_location_gives_the_country_when_there_is_no_lane():
    r = lead("SHP-SYN-002")
    assert (r["status"], r["country"], r["location"], r["working_days"]) == ("ok", "IN", "facility:INNSA", 2)


def test_arrived_shipment_counts_departure_to_arrival():
    r = lead("SHP-SYN-001")
    assert (r["status"], r["basis"], r["start"], r["end"], r["working_days"]) == (
        "ok", "departure_to_arrival", "2026-09-20", "2026-10-06", 11)
    r = lead("SHP-SYN-005")
    assert (r["start"], r["end"], r["working_days"]) == ("2026-09-25", "2026-10-07", 7)


def test_no_dates_is_not_available():
    for shipment_id in ("SHP-SYN-003", "SHP-SYN-008"):
        r = lead(shipment_id)
        assert (r["status"], r["reason"], r["working_days"]) == ("not_available", "no_dates", None)


def test_unsupported_states_pass_through(tmp_path):
    missing = SyntheticHolidayProvider(tmp_path / "absent.json")
    assert lead("SHP-SYN-004", missing)["status"] == "source_unavailable"


def test_dates_are_utc_and_stated():
    r = lead("SHP-SYN-002")
    assert "UTC" in r["note"]


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def test_timeline_route_includes_lead_time(client):
    body = client.get(f"/dispatch/shipments/SHP-SYN-004?as_of={AS_OF}&now={NOW}").json()
    assert body["lead_time"]["working_days"] == 4
    assert body["lead_time"]["synthetic"] is True
    empty = client.get(f"/dispatch/shipments/SHP-SYN-007?as_of={AS_OF}&now={NOW}").json()
    assert empty["lead_time"]["status"] == "not_available"
