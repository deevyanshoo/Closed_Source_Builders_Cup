"""What the gateway's local dispatch connector (#77) relies on from the Python dispatch routes.

The connector proxies these read-only routes and refuses any answer that is not JSON labelled ``synthetic: true``
at the top level, for success and error answers alike. Offline, over the bundled synthetic fixtures.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from procure.dispatch.http import router

CUTOFF = {"as_of": "2026-10-08T12:00:00Z", "now": "2026-10-08T12:00:00Z"}
client = TestClient(FastAPI(routes=router.routes))


@pytest.mark.parametrize("path,params,status", [
    ("/dispatch/shipments", CUTOFF, 200),
    ("/dispatch/shipments/SHP-SYN-002", CUTOFF, 200),
    ("/dispatch/events/evt-002-hold/affected", CUTOFF, 200),
    ("/dispatch/route-evidence", {**CUTOFF, "location": ["INNSA", "INMAA"]}, 200),
    ("/dispatch/replay/samples", {}, 200),
    ("/dispatch/replay", {**CUTOFF, "sample": "innsa-release"}, 200),
    ("/dispatch/hazards", {**CUTOFF, "location": ["INNSA"]}, 200),
    ("/dispatch/shipments", {"as_of": "not-a-time"}, 400),
    ("/dispatch/replay", {**CUTOFF, "sample": "no-such-sample"}, 404),
])
def test_every_proxied_route_answers_json_labelled_synthetic(path, params, status):
    response = client.get(path, params=params)
    assert response.status_code == status
    assert response.headers["content-type"].split(";")[0] == "application/json"
    body = response.json()
    assert isinstance(body, dict) and body["synthetic"] is True
    if status != 200:
        assert body["status"] == "error" and all(isinstance(r, str) for r in body["reasons"])


def test_decimal_quantities_stay_strings_for_the_gateway_to_pass_through():
    replay = client.get("/dispatch/replay", params={**CUTOFF, "sample": "innsa-hold-new-eta"}).json()
    screen = replay["material_screen"]
    assert screen["arrival_changes"][0]["quantity"] == "3000"
    assert screen["affected_commitments"][0]["demands"][1]["changed"]["shortfall"] == "3000"
