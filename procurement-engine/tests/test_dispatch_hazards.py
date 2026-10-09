"""Read-only /dispatch/hazards over the recorded (hand-written, synthetic) hazard responses."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from procure.api import app
from procure.dispatch import samples

AS_OF = "2026-10-08T12:00:00Z"
NOW = "2026-10-08T13:00:00Z"


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def test_hazards_route_returns_exposure_only(client):
    r = client.get(f"/dispatch/hazards?location=INNSA&location=INPRT&as_of={AS_OF}&now={NOW}")
    assert r.status_code == 200
    body = r.json()
    assert (body["status"], body["synthetic"], body["thresholds_version"]) == ("ok", True, "hazard-thresholds-v1")
    assert body["note"].startswith("Exposure only, not confirmed")
    assert body["evidence"]["confirmed"] == []
    assert sorted(e["evidence_id"] for e in body["evidence"]["exposure"]) == [
        "haz-gdacs-INPRT-9100001-4", "haz-open-meteo-INNSA-99acbf09e031"]
    assert [e["evidence_id"] for e in body["evidence"]["expired"]] == ["haz-open-meteo-INPRT-7a639b3d1492"]
    assert body["source"]["kind"] == "synthetic_fixture"


def test_cutoff_rejects_later_retrievals(client):
    body = client.get(f"/dispatch/hazards?location=INNSA&as_of=2026-10-08T08:30:00Z&now={NOW}").json()
    states = {(a["provider_id"], a["state"]) for a in body["assessments"]}
    assert ("gdacs", "no_exposure_found") in states
    assert all(a["provider_id"] != "open-meteo" for a in body["assessments"])
    assert {r["provider_id"] for r in body["rejected"]} == {"open-meteo"}


def test_unsupported_location_is_reported_not_guessed(client):
    body = client.get(f"/dispatch/hazards?location=SYN-PLANT-CHAKAN&as_of={AS_OF}&now={NOW}").json()
    assert {(a["provider_id"], a["state"]) for a in body["assessments"]} == {
        ("open-meteo", "unsupported_location"), ("gdacs", "unsupported_location")}


@pytest.mark.parametrize("query,reason", [(f"as_of={AS_OF}", "invalid_location_keys"),
                                          ("location=INNSA", "invalid_as_of")])
def test_bad_requests_are_400(client, query, reason):
    r = client.get(f"/dispatch/hazards?{query}")
    assert r.status_code == 400 and r.json()["reasons"] == [reason]


def test_hazards_route_is_read_only(client):
    assert client.post(f"/dispatch/hazards?location=INNSA&as_of={AS_OF}").status_code == 405


def test_missing_recordings_are_503(client, monkeypatch, tmp_path):
    monkeypatch.setattr(samples, "HAZARD_RECORDINGS", tmp_path / "absent.json")
    r = client.get(f"/dispatch/hazards?location=INNSA&as_of={AS_OF}&now={NOW}")
    assert r.status_code == 503 and r.json()["reasons"] == ["sample_data_unavailable"]
