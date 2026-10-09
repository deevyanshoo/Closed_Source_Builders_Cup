"""Read-only /dispatch/replay routes over the labelled synthetic replay samples."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from procure.api import app
from procure.dispatch import samples

AS_OF = "2026-10-08T12:00:00Z"
NOW = "2026-10-08T13:00:00Z"
Q = f"as_of={AS_OF}&now={NOW}"


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def test_samples_are_listed_and_labelled(client):
    body = client.get("/dispatch/replay/samples").json()
    assert body["status"] == "ok" and body["synthetic"] is True
    assert [s["sample_id"] for s in body["samples"]] == [
        "innsa-hold-new-eta", "innsa-release", "inmaa-hold-no-eta", "eta-after-cutoff"]
    first = body["samples"][0]
    assert (first["event_id"], first["shipment_id"], first["event_type"]) == ("rpl-innsa-hold", "SHP-SYN-002", "port_hold")
    assert "SYNTHETIC" in body["notice"]


def test_replay_route_returns_before_and_after(client):
    r = client.get(f"/dispatch/replay?sample=innsa-release&{Q}")
    assert r.status_code == 200
    body = r.json()
    assert (body["status"], body["synthetic"], body["now_source"]) == ("ok", True, "request")
    assert body["sample"]["sample_id"] == "innsa-release"
    change = body["changes"][0]
    assert (change["shipment_id"], change["before"]["status"], change["after"]["status"]) == (
        "SHP-SYN-002", "on_hold", "delayed")
    assert body["recovery_link"] == "/shipment-recovery"


def test_event_after_cutoff_is_400(client):
    r = client.get(f"/dispatch/replay?sample=eta-after-cutoff&{Q}")
    assert r.status_code == 400
    assert (r.json()["status"], r.json()["reasons"]) == ("error", ["event_after_as_of"])


@pytest.mark.parametrize("query,code,reason", [
    (f"sample=nope&{Q}", 404, "unknown_sample"),
    (Q, 400, "missing_sample"),
    ("sample=innsa-release", 400, "invalid_as_of"),
])
def test_bad_requests(client, query, code, reason):
    r = client.get(f"/dispatch/replay?{query}")
    assert r.status_code == code and r.json()["reasons"] == [reason]


def test_stale_replay(client):
    body = client.get(f"/dispatch/replay?sample=innsa-hold-new-eta&as_of={AS_OF}&now=2026-10-10T13:00:00Z").json()
    assert body["status"] == "stale"


def test_replay_routes_are_read_only(client):
    assert client.post(f"/dispatch/replay?sample=innsa-release&{Q}").status_code == 405
    assert client.put("/dispatch/replay/samples").status_code == 405


def test_missing_replay_samples_are_503(client, monkeypatch, tmp_path):
    monkeypatch.setattr(samples, "REPLAY_FILE", tmp_path / "absent.json")
    assert client.get("/dispatch/replay/samples").status_code == 503
    r = client.get(f"/dispatch/replay?sample=innsa-release&{Q}")
    assert r.status_code == 503 and r.json()["reasons"] == ["sample_data_unavailable"]
