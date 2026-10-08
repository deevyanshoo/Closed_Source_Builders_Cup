"""Read-only /dispatch routes over the labelled synthetic shipment and route-evidence fixtures."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from procure.dispatch import samples
from procure.dispatch.http import router

AS_OF = "2026-10-08T12:00:00Z"
NOW = "2026-10-08T13:00:00Z"
Q = f"as_of={AS_OF}&now={NOW}"


@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)  # isolated read-only router; shared mount is root integration scope


def test_shipment_list_is_synthetic_and_evidence_based(client):
    r = client.get(f"/dispatch/shipments?{Q}")
    assert r.status_code == 200
    body = r.json()
    assert (body["status"], body["synthetic"], body["source"]["kind"]) == ("ok", True, "synthetic_fixture")
    assert [(s["shipment_id"], s["status"]) for s in body["shipments"]] == [
        ("SHP-SYN-001", "unknown"), ("SHP-SYN-002", "on_hold"), ("SHP-SYN-003", "unknown"),
        ("SHP-SYN-004", "delayed"), ("SHP-SYN-005", "on_time"), ("SHP-SYN-008", "unknown")]
    assert (body["as_of"], body["now"], body["now_source"]) == ("2026-10-08T12:00:00+00:00", NOW, "request")


def test_as_of_is_point_in_time(client):
    body = client.get(f"/dispatch/shipments?as_of=2026-10-02T00:00:00Z&now={NOW}").json()
    assert [s["shipment_id"] for s in body["shipments"]] == ["SHP-SYN-001", "SHP-SYN-005", "SHP-SYN-008"]
    assert body["status"] == "stale"


def test_now_defaults_to_the_server_clock_and_is_reported(client):
    body = client.get(f"/dispatch/shipments?as_of={AS_OF}").json()
    assert body["now_source"] == "server_clock" and body["now"].endswith("Z")


@pytest.mark.parametrize("query,reason", [
    ("", "invalid_as_of"),
    ("as_of=2026-10-08", "invalid_as_of"),
    (f"as_of={AS_OF}&now=2026-10-08T13:00:00", "invalid_now"),
])
def test_bad_parameters_are_400_with_a_reason(client, query, reason):
    r = client.get(f"/dispatch/shipments?{query}")
    assert r.status_code == 400
    assert (r.json()["status"], r.json()["reasons"]) == ("error", [reason])


def test_shipment_timeline_route(client):
    body = client.get(f"/dispatch/shipments/SHP-SYN-002?{Q}").json()
    assert body["status"] == "ok" and body["synthetic"] is True
    assert body["current"]["on_hold"] is True
    assert body["current"]["open_eta"]["eta"] == "2026-10-12T02:30:00+00:00"


def test_timeline_lists_rejected_records_for_a_shipment(client):
    body = client.get(f"/dispatch/shipments/SHP-SYN-007?{Q}").json()
    assert body["status"] == "empty"
    assert [r["reason"] for r in body["rejected_records"]] == [
        "conflicting_duplicate", "conflicting_duplicate", "invalid_timestamp", "observed_before_occurred"]


def test_timeline_can_be_stale(client):
    body = client.get(f"/dispatch/shipments/SHP-SYN-002?as_of={AS_OF}&now=2026-10-10T13:00:00Z").json()
    assert body["status"] == "stale" and body["reasons"] == ["data_older_than_max_age"]


def test_affected_shipments_route_is_exposure(client):
    body = client.get(f"/dispatch/events/evt-002-hold/affected?{Q}").json()
    assert body["status"] == "ok"
    assert [(s["shipment_id"], s["status"]) for s in body["affected"]] == [
        ("SHP-SYN-001", "exposure"), ("SHP-SYN-002", "exposure")]
    assert body["note"].startswith("Exposure only")


def test_unknown_event_is_404(client):
    r = client.get(f"/dispatch/events/evt-nope/affected?{Q}")
    assert r.status_code == 404 and r.json()["reasons"] == ["unknown_event"]


def test_route_evidence_route_separates_confirmed_and_exposure(client):
    body = client.get(f"/dispatch/route-evidence?location=INNSA&location=INMAA&as_of={AS_OF}").json()
    assert body["status"] == "ok" and body["synthetic"] is True
    assert [e["evidence_id"] for e in body["confirmed"]] == ["rev-port-002"]
    assert [e["evidence_id"] for e in body["exposure"]] == ["rev-haz-001"]
    assert body["confirmed"][0]["retrieved_at"] == "2026-10-05T05:00:00+00:00"


def test_route_evidence_needs_a_location(client):
    r = client.get(f"/dispatch/route-evidence?as_of={AS_OF}")
    assert r.status_code == 400 and r.json()["reasons"] == ["invalid_location_keys"]


def test_routes_are_read_only(client):
    assert client.post(f"/dispatch/shipments?{Q}").status_code == 405
    assert client.delete(f"/dispatch/shipments/SHP-SYN-002?{Q}").status_code == 405


def test_missing_or_non_synthetic_sample_data_is_503(client, monkeypatch, tmp_path):
    monkeypatch.setitem(samples.SAMPLE_FILES, "milestones", tmp_path / "absent.json")
    r = client.get(f"/dispatch/shipments?{Q}")
    assert r.status_code == 503 and r.json()["reasons"] == ["sample_data_unavailable"]
    real = Path(samples.PROJECT_ROOT / "tests/fixtures/shipments/snapshot.json").read_text()
    labelled_real = tmp_path / "snapshot.json"
    labelled_real.write_text(real.replace('"synthetic": true', '"synthetic": false'))
    monkeypatch.setitem(samples.SAMPLE_FILES, "milestones", samples.PROJECT_ROOT / "tests/fixtures/shipments/milestones.json")
    monkeypatch.setitem(samples.SAMPLE_FILES, "snapshot", labelled_real)
    r = client.get(f"/dispatch/shipments?{Q}")
    assert r.status_code == 503 and r.json()["reasons"] == ["sample_data_unavailable"]


@pytest.mark.parametrize("stamp", ["2026-99-99T99:99:99Z", "2026-02-30T12:00:00Z", "2026-10-08T12:00:00+25:00"])
def test_impossible_as_of_is_400_before_sample_loading(client, monkeypatch, stamp):
    def forbidden_load():
        raise AssertionError("sample opened before calendar validation")
    monkeypatch.setattr(samples, "load_samples", forbidden_load)
    for path in ("/dispatch/shipments", "/dispatch/shipments/SHP-SYN-002", "/dispatch/events/evt-002-hold/affected", "/dispatch/route-evidence?location=INNSA"):
        sep = "&" if "?" in path else "?"
        response = client.get(f"{path}{sep}as_of={stamp}")
        assert response.status_code == 400
        assert response.json()["reasons"] == ["invalid_as_of"]


@pytest.mark.parametrize("stamp", ["2026-02-30T12:00:00Z", "2026-10-08T12:00:00+25:00"])
def test_impossible_now_is_400_before_sample_loading(client, monkeypatch, stamp):
    def forbidden_load():
        raise AssertionError("sample opened before calendar validation")
    monkeypatch.setattr(samples, "load_samples", forbidden_load)
    response = client.get(f"/dispatch/shipments?as_of={AS_OF}&now={stamp}")
    assert response.status_code == 400
    assert response.json()["reasons"] == ["invalid_now"]


def test_historical_http_list_rejects_malformed_future_snapshot(client, monkeypatch):
    import copy

    data = copy.deepcopy(samples.load_samples())
    data["snapshot"]["arrival_lines"] = [{"shipment_id": "SHP-SYN-004", "arrival_at": "2026-99-99T00:00:00Z"}]
    monkeypatch.setattr(samples, "load_samples", lambda: data)
    response = client.get("/dispatch/shipments?as_of=2026-10-05T12:00:00Z&now=2026-10-05T12:01:00Z")
    assert response.status_code == 400
    assert response.json()["status"] == "error"
    assert response.json()["reasons"] == ["invalid_snapshot"]
    assert response.json()["shipments"] == []
