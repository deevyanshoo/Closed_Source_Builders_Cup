"""Recovery API checks use real synthetic fixtures and the offline rules extractor."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from procure.demo.app import create_app


@pytest.fixture
def demo():
    with TestClient(create_app(clock=lambda: datetime(2026, 10, 14, tzinfo=timezone.utc)),
                    base_url="https://demo.test") as client:
        session = client.get("/api/demo/session").json()
        headers = {"Origin": "https://demo.test", "X-CSRF-Token": session["csrf_token"]}
        offers = {}
        for fixture in ("late-copper", "express-copper", "eu-copper"):
            response = client.post("/api/demo/extract", headers=headers, json={
                "fixture_id": fixture, "mode": "rules", "idempotency_key": f"extract-{fixture}"})
            assert response.status_code == 200, response.text
            offer = response.json()["offer"]
            if fixture == "late-copper":
                response = client.post(f"/api/demo/offers/{offer['offer_id']}/correct", headers=headers,
                                       json={"field": "settlement_currency", "value": "USD",
                                             "expected_version": offer["version"], "idempotency_key": "currency"})
                assert response.status_code == 200, response.text
                offer = response.json()["offer"]
            response = client.post(f"/api/demo/offers/{offer['offer_id']}/save", headers=headers,
                                   json={"expected_version": offer["version"],
                                         "idempotency_key": f"save-{fixture}"})
            assert response.status_code == 200, response.text
            offers[fixture] = response.json()["offer"]
        plan_request = {"offer_ids": [offer["offer_id"] for offer in offers.values()],
                        "expected_versions": {offer["offer_id"]: offer["version"] for offer in offers.values()},
                        "material": "copper", "quantity_kg": "10000", "current_stock_kg": "2000",
                        "need_by": "2026-11-05", "decision_date": "2026-10-14",
                        "reporting_currency": "USD", "budget": "100000", "additional_cost_per_kg": "0.25"}
        response = client.post("/api/demo/plan", headers=headers, json=plan_request)
        assert response.status_code == 200, response.text
        yield client, headers, offers, plan_request, response.json()


def recovery_body(baseline, request, delays, key="recovery-one"):
    return {"baseline_plan_id": baseline["plan_id"],
            "expected_versions": request["expected_versions"],
            "delay_days_by_offer": delays, "idempotency_key": key}


def error_code(response):
    return response.json()["error"]["code"]


def test_recovery_pairs_original_and_delayed_plan_with_exact_decimal_delta(demo):
    client, headers, offers, request, baseline = demo
    europa, swift = offers["eu-copper"]["offer_id"], offers["express-copper"]["offer_id"]
    response = client.post("/api/demo/recovery", headers=headers,
                           json=recovery_body(baseline, request, {europa: 20}))
    assert response.status_code == 200, response.text
    recovery = response.json()
    assert set(recovery) == {"comparison_id", "synthetic", "baseline", "scenario", "baseline_request",
                             "scenario_request", "cost_delta", "selection_changed", "outcome", "replayed",
                             "baseline_supply_coverage", "scenario_supply_coverage"}
    assert recovery["synthetic"] is True and recovery["replayed"] is False
    assert recovery["baseline"] == baseline and recovery["baseline_request"] == request
    assert recovery["scenario_request"] == {**request, "delay_days_by_offer": {europa: 20}}
    assert recovery["scenario"]["selected_offer_id"] == swift
    assert recovery["scenario"]["total_cost"] == "86000"
    assert recovery["cost_delta"] == "4000"
    assert recovery["selection_changed"] is True and recovery["outcome"] == "changed"
    assert recovery['baseline_supply_coverage']['status'] == 'gap'
    assert recovery['scenario_supply_coverage']['status'] == 'gap'
    assert recovery['scenario_supply_coverage']['uncovered_quantity'] == '2666.62'
    assert {a["offer_id"]: a for a in recovery["scenario"]["alternatives"]}[europa]["arrival_date"] == "2026-11-13"
    session = client.get("/api/demo/session").json()
    assert session["plan"] == recovery["scenario"] and session["plan_request"] == request
    assert session["recovery"] == {**recovery, "replayed": False} and session["rfq"] is None
    exported = client.get("/api/demo/export").json()
    assert exported["plan"] == recovery["scenario"]
    assert exported["baseline_plan"] == baseline and exported["plan_request"] == request
    assert exported["recovery"] == recovery and exported["rfq"] is None


def test_second_delay_infeasible_then_empty_delay_restores_baseline(demo):
    client, headers, offers, request, baseline = demo
    europa, swift = offers["eu-copper"]["offer_id"], offers["express-copper"]["offer_id"]
    infeasible = client.post("/api/demo/recovery", headers=headers,
                             json=recovery_body(baseline, request, {europa: 20, swift: 10}, "both-late"))
    assert infeasible.status_code == 200, infeasible.text
    assert infeasible.json()["scenario"]["selected_offer_id"] is None
    assert infeasible.json()["cost_delta"] is None
    assert infeasible.json()["outcome"] == "infeasible"
    restored = client.post("/api/demo/recovery", headers=headers,
                           json=recovery_body(baseline, request, {}, "restore"))
    assert restored.status_code == 200, restored.text
    assert restored.json()["scenario"]["selected_offer_id"] == europa
    assert restored.json()["scenario"]["total_cost"] == "82000"
    assert restored.json()["cost_delta"] == "0"
    assert restored.json()["outcome"] == "unchanged"
    assert restored.json()["selection_changed"] is False


def test_empty_recovery_preserves_delay_already_in_baseline_plan(demo):
    client, headers, offers, request, _ = demo
    europa, swift = offers["eu-copper"]["offer_id"], offers["express-copper"]["offer_id"]
    delayed_request = {**request, "delay_days_by_offer": {europa: 20}}
    baseline_response = client.post("/api/demo/plan", headers=headers, json=delayed_request)
    assert baseline_response.status_code == 200, baseline_response.text
    baseline = baseline_response.json()
    assert baseline["selected_offer_id"] == swift and baseline["total_cost"] == "86000"

    response = client.post("/api/demo/recovery", headers=headers,
                           json=recovery_body(baseline, delayed_request, {}, "keep-baseline-delay"))
    assert response.status_code == 200, response.text
    comparison = response.json()
    assert comparison["baseline_request"] == delayed_request
    assert comparison["scenario_request"]["delay_days_by_offer"] == {europa: 20}
    assert comparison["scenario"]["selected_offer_id"] == swift
    assert comparison["scenario"]["total_cost"] == "86000"
    assert comparison["cost_delta"] == "0" and comparison["outcome"] == "unchanged"

    replacement = client.post("/api/demo/recovery", headers=headers,
                              json=recovery_body(baseline, delayed_request, {europa: 0}, "replace-delay"))
    assert replacement.status_code == 200, replacement.text
    assert replacement.json()["scenario_request"]["delay_days_by_offer"] == {europa: 0}
    assert replacement.json()["scenario"]["selected_offer_id"] == europa
    assert replacement.json()["scenario"]["total_cost"] == "82000"
    assert replacement.json()["cost_delta"] == "-4000"


def test_recovery_replay_is_current_only_and_new_plan_invalidates_it(demo):
    client, headers, offers, request, baseline = demo
    europa = offers["eu-copper"]["offer_id"]
    body = recovery_body(baseline, request, {europa: 20})
    first = client.post("/api/demo/recovery", headers=headers, json=body)
    assert first.status_code == 200
    replay = client.post("/api/demo/recovery", headers=headers, json=body)
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    assert replay.json()["comparison_id"] == first.json()["comparison_id"]
    newer = client.post("/api/demo/recovery", headers=headers,
                        json=recovery_body(baseline, request, {}, "newer"))
    assert newer.status_code == 200
    stale = client.post("/api/demo/recovery", headers=headers, json=body)
    assert stale.status_code == 409 and error_code(stale) == "stale_plan"
    assert client.get("/api/demo/session").json()["recovery"]["comparison_id"] == newer.json()["comparison_id"]
    replanned = client.post("/api/demo/plan", headers=headers, json=request)
    assert replanned.status_code == 200
    state = client.get("/api/demo/session").json()
    assert state["plan"] == replanned.json() and state["recovery"] is None and state["rfq"] is None
    assert client.post("/api/demo/recovery", headers=headers, json=body).status_code == 409


@pytest.mark.parametrize("delays", [{"foreign": 1}, {"eu": -1}, {"eu": 3651},
                                    {"eu": True}, {"eu": 1.5}, {"eu": "2"}, []])
def test_invalid_delay_cannot_replace_current_comparison(demo, delays):
    client, headers, offers, request, baseline = demo
    europa = offers["eu-copper"]["offer_id"]
    valid = client.post("/api/demo/recovery", headers=headers,
                        json=recovery_body(baseline, request, {europa: 20}))
    assert valid.status_code == 200
    payload = recovery_body(baseline, request, delays, "bad-delay")
    if isinstance(delays, dict) and "eu" in delays:
        payload["delay_days_by_offer"] = {europa: delays["eu"]}
    response = client.post("/api/demo/recovery", headers=headers, json=payload)
    assert response.status_code == 422
    assert client.get("/api/demo/session").json()["recovery"]["comparison_id"] == valid.json()["comparison_id"]


def test_recovery_rejects_foreign_baseline_wrong_versions_and_origin(demo):
    client, headers, offers, request, baseline = demo
    europa = offers["eu-copper"]["offer_id"]
    wrong = recovery_body(baseline, request, {europa: 20}, "bad-id")
    wrong["baseline_plan_id"] = "some-other-plan"
    assert client.post("/api/demo/recovery", headers=headers, json=wrong).status_code == 409
    wrong = recovery_body(baseline, request, {europa: 20}, "bad-version")
    wrong["expected_versions"] = {**request["expected_versions"], europa: 1}
    assert client.post("/api/demo/recovery", headers=headers, json=wrong).status_code == 409
    forged = {**headers, "Origin": "https://attacker.test"}
    assert client.post("/api/demo/recovery", headers=forged,
                       json=recovery_body(baseline, request, {europa: 20}, "forged")).status_code == 403
    with TestClient(client.app, base_url="https://demo.test") as other:
        token = other.get("/api/demo/session").json()["csrf_token"]
        own_headers = {"Origin": "https://demo.test", "X-CSRF-Token": token}
        foreign = other.post("/api/demo/recovery", headers=own_headers,
                             json=recovery_body(baseline, request, {europa: 20}, "foreign"))
        assert foreign.status_code == 409
    assert client.get("/api/demo/session").json()["recovery"] is None


def test_new_extraction_and_quote_change_clear_derived_state(demo):
    client, headers, offers, request, baseline = demo
    europa = offers["eu-copper"]["offer_id"]
    assert client.post("/api/demo/recovery", headers=headers,
                       json=recovery_body(baseline, request, {europa: 20})).status_code == 200
    extracted = client.post("/api/demo/extract", headers=headers, json={
        "fixture_id": "eu-copper", "mode": "rules", "idempotency_key": "new-offer"})
    assert extracted.status_code == 200
    session = client.get("/api/demo/session").json()
    assert session["plan"] is None and session["plan_request"] is None and session["recovery"] is None
    assert session["rfq"] is None
    assert client.get("/api/demo/export").json()["plan"] is None
