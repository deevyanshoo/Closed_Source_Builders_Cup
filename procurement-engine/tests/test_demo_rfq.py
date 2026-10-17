"""Unsent RFQ API checks against accepted synthetic quotes, without provider calls."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from procure.demo.app import create_app


@pytest.fixture
def demo():
    with TestClient(create_app(clock=lambda: datetime(2026, 10, 14, tzinfo=timezone.utc)),
                    base_url="https://demo.test") as client:
        token = client.get("/api/demo/session").json()["csrf_token"]
        headers = {"Origin": "https://demo.test", "X-CSRF-Token": token}
        offers = {}
        for fixture in ("express-copper", "eu-copper"):
            response = client.post("/api/demo/extract", headers=headers, json={
                "fixture_id": fixture, "mode": "rules", "idempotency_key": "extract-" + fixture})
            assert response.status_code == 200
            offer = response.json()["offer"]
            response = client.post(f"/api/demo/offers/{offer['offer_id']}/save", headers=headers,
                                   json={"expected_version": offer["version"], "idempotency_key": "save-" + fixture})
            assert response.status_code == 200
            offers[fixture] = response.json()["offer"]
        request = {"offer_ids": [offer["offer_id"] for offer in offers.values()],
                   "expected_versions": {offer["offer_id"]: offer["version"] for offer in offers.values()},
                   "material": "copper", "quantity_kg": "10000", "current_stock_kg": "2000",
                   "need_by": "2026-11-05", "decision_date": "2026-10-14",
                   "reporting_currency": "USD", "budget": "100000", "additional_cost_per_kg": "0.25"}
        response = client.post("/api/demo/plan", headers=headers, json=request)
        assert response.status_code == 200
        yield client, headers, offers, request, response.json()


def rfq_body(plan, key="draft-one", **changes):
    return {"plan_id": plan["plan_id"], "expected_versions": plan["offer_versions"],
            "reviewed": True, "idempotency_key": key, **changes}


def test_reviewed_rfq_from_delayed_active_plan_preserves_quote_and_assumption_boundaries(demo):
    client, headers, offers, request, baseline = demo
    europa, swift = offers["eu-copper"]["offer_id"], offers["express-copper"]["offer_id"]
    comparison = client.post("/api/demo/recovery", headers=headers, json={
        "baseline_plan_id": baseline["plan_id"], "expected_versions": request["expected_versions"],
        "delay_days_by_offer": {europa: 20}, "idempotency_key": "delay"})
    assert comparison.status_code == 200, comparison.text
    scenario = comparison.json()["scenario"]
    assert scenario["selected_offer_id"] == swift
    response = client.post("/api/demo/rfq", headers=headers, json=rfq_body(scenario))
    assert response.status_code == 200, response.text
    draft = response.json()
    assert set(draft) == {"draft_id", "plan_id", "offer_versions", "synthetic", "status", "supplier",
                          "company_id", "material", "quantity", "quantity_unit", "quantity_kg", "need_by", "reporting_currency", "total_cost",
                          "created_at", "text"}
    assert draft["plan_id"] == scenario["plan_id"]
    assert draft["offer_versions"] == request["expected_versions"]
    assert draft["synthetic"] is True and draft["status"] == "draft_unsent"
    assert draft["supplier"].startswith("Swift Copper") and draft["material"] == "copper"
    assert draft["quantity_kg"] == "8000" and draft["need_by"] == "2026-11-05"
    assert draft["reporting_currency"] == "USD" and draft["total_cost"] == "86000"
    assert draft["created_at"].startswith("2026-10-14T")
    text = draft["text"].lower()
    for fact in ("synthetic", "unsent", "request for quotation", "no order commitment", "8000",
                 "2026-11-05", "10.50", "usd", "per kg", "settlement", "incoterm", "payment",
                 "0.25", "synthetic fx", "20", "document", "scenario"):
        assert fact in text
    assert "86000" in text and "supplier quote total: 86000" not in text
    download = client.get(f"/api/demo/rfq/{draft['draft_id']}/download")
    assert download.status_code == 200
    assert "attachment" in download.headers["content-disposition"]
    assert "synthetic-rfq-draft.txt" in download.headers["content-disposition"]
    assert download.text == draft["text"]
    state = client.get("/api/demo/session").json()
    assert state["rfq"] == draft and state["plan"] == scenario
    assert client.get("/api/demo/export").json()["rfq"] == draft


@pytest.mark.parametrize("reviewed", [False, None, 1, "true"])
def test_review_gate_requires_literal_true(demo, reviewed):
    client, headers, _, _, plan = demo
    response = client.post("/api/demo/rfq", headers=headers, json=rfq_body(plan, reviewed=reviewed))
    assert response.status_code == 422 and response.json()["error"]["code"] == "review_required"
    assert client.get("/api/demo/session").json()["rfq"] is None


def test_rfq_requires_current_selected_plan_and_versions(demo):
    client, headers, _, request, plan = demo
    stale = client.post("/api/demo/rfq", headers=headers,
                        json=rfq_body(plan, "wrong-id", plan_id="other-plan"))
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "stale_plan"
    wrong_versions = {**request["expected_versions"]}
    wrong_versions[next(iter(wrong_versions))] = 1
    response = client.post("/api/demo/rfq", headers=headers,
                           json=rfq_body(plan, "wrong-versions", expected_versions=wrong_versions))
    assert response.status_code == 409
    no_selection = client.post("/api/demo/plan", headers=headers, json={**request, "budget": "80000"})
    assert no_selection.status_code == 200 and no_selection.json()["selected_offer_id"] is None
    blocked = client.post("/api/demo/rfq", headers=headers, json=rfq_body(no_selection.json(), "no-selection"))
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "draft_unavailable"
    stock_only = client.post("/api/demo/plan", headers=headers,
                             json={**request, "current_stock_kg": "10000"})
    assert stock_only.status_code == 200 and stock_only.json()["required_purchase_kg"] == "0"
    blocked = client.post("/api/demo/rfq", headers=headers, json=rfq_body(stock_only.json(), "stock-only"))
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "draft_unavailable"


def test_rfq_replay_cannot_restore_stale_draft_and_download_checks_ownership(demo):
    client, headers, offers, request, plan = demo
    body = rfq_body(plan)
    first = client.post("/api/demo/rfq", headers=headers, json=body)
    assert first.status_code == 200
    draft = first.json()
    assert client.post("/api/demo/rfq", headers=headers, json=body).json() == draft
    with TestClient(client.app, base_url="https://demo.test") as other:
        other.get("/api/demo/session")
        foreign = other.get(f"/api/demo/rfq/{draft['draft_id']}/download")
        assert foreign.status_code == 404 and foreign.json()["error"]["code"] == "draft_not_found"
    new_plan = client.post("/api/demo/plan", headers=headers, json=request)
    assert new_plan.status_code == 200
    assert client.post("/api/demo/rfq", headers=headers, json=body).status_code == 409
    assert client.get(f"/api/demo/rfq/{draft['draft_id']}/download").status_code == 404
    assert client.get("/api/demo/session").json()["rfq"] is None
    assert client.get("/api/demo/export").json()["rfq"] is None
    assert client.post("/api/demo/reset", headers=headers, json={}).status_code == 200
    assert client.get(f"/api/demo/rfq/{draft['draft_id']}/download").status_code == 404


def test_failed_recovery_keeps_prior_draft_and_valid_comparison(demo):
    client, headers, offers, request, plan = demo
    europa = offers["eu-copper"]["offer_id"]
    comparison = client.post("/api/demo/recovery", headers=headers, json={
        "baseline_plan_id": plan["plan_id"], "expected_versions": request["expected_versions"],
        "delay_days_by_offer": {europa: 20}, "idempotency_key": "comparison"})
    assert comparison.status_code == 200
    draft = client.post("/api/demo/rfq", headers=headers,
                        json=rfq_body(comparison.json()["scenario"]))
    assert draft.status_code == 200
    failed = client.post("/api/demo/recovery", headers=headers, json={
        "baseline_plan_id": plan["plan_id"], "expected_versions": request["expected_versions"],
        "delay_days_by_offer": {europa: -1}, "idempotency_key": "invalid-delay"})
    assert failed.status_code == 422
    state = client.get("/api/demo/session").json()
    assert state["recovery"]["comparison_id"] == comparison.json()["comparison_id"]
    assert state["rfq"]["draft_id"] == draft.json()["draft_id"]
    assert client.get(f"/api/demo/rfq/{draft.json()['draft_id']}/download").status_code == 200


@pytest.mark.parametrize("amounts,total", [(["11.00"], "90000"), (["11.00", "12.00"], "98000")])
def test_corrected_price_rfq_keeps_original_quote_separate_from_current_plan(amounts, total):
    with TestClient(create_app(clock=lambda: datetime(2026, 10, 14, tzinfo=timezone.utc)),
                    base_url="https://demo.test") as client:
        token = client.get("/api/demo/session").json()["csrf_token"]
        headers = {"Origin": "https://demo.test", "X-CSRF-Token": token}
        response = client.post("/api/demo/extract", headers=headers, json={
            "fixture_id": "express-copper", "mode": "rules", "idempotency_key": "price-extract"})
        assert response.status_code == 200
        offer = response.json()["offer"]
        original_locators = [e["locator"] for e in offer["items"][0]["pricing"]["evidence"]]
        assert original_locators
        for index, amount in enumerate(amounts):
            response = client.post(f"/api/demo/offers/{offer['offer_id']}/correct", headers=headers, json={
                "field": "items[0].pricing",
                "value": {"type": "fixed", "unit_price": {"amount": amount, "currency": "USD"},
                          "per_unit": "kg"},
                "expected_version": offer["version"], "idempotency_key": f"price-correct-{index}"})
            assert response.status_code == 200, response.text
            offer = response.json()["offer"]
        saved = client.post(f"/api/demo/offers/{offer['offer_id']}/save", headers=headers, json={
            "expected_version": offer["version"], "idempotency_key": "price-save"})
        assert saved.status_code == 200, saved.text
        offer = saved.json()["offer"]
        request = {"offer_ids": [offer["offer_id"]], "expected_versions": {offer["offer_id"]: offer["version"]},
                   "material": "copper", "quantity_kg": "10000", "current_stock_kg": "2000",
                   "need_by": "2026-11-05", "decision_date": "2026-10-14", "reporting_currency": "USD",
                   "budget": "100000", "additional_cost_per_kg": "0.25"}
        planned = client.post("/api/demo/plan", headers=headers, json=request)
        assert planned.status_code == 200, planned.text
        assert planned.json()["total_cost"] == total
        drafted = client.post("/api/demo/rfq", headers=headers, json=rfq_body(planned.json()))
        assert drafted.status_code == 200, drafted.text
        text = drafted.json()["text"]
        assert "Original quoted price: 10.50 USD per kg" in text
        assert f"Current reviewed price: {amounts[-1]} USD per kg" in text
        assert f"Scenario comparison total: {total} USD" in text
        assert f"Original quoted price: {amounts[-1]} USD per kg" not in text
        assert all(locator in text for locator in original_locators)


@pytest.mark.parametrize("original_status", ["unverified", "missing"])
def test_unverified_or_missing_original_price_is_explicitly_unavailable_in_rfq(original_status):
    from datetime import date
    from procure.demo import planner, quote_service, rfq

    today = date(2026, 10, 14)
    offer = quote_service.new_offer(
        quote_service.extract_fixture("express-copper", mode="rules", as_of=today),
        offer_id="reviewed-only", today=today)
    offer["items"][0]["pricing"].update(status=original_status, value=None, evidence=[])
    corrected = quote_service.correct_offer(offer, field="items[0].pricing", value={
        "type": "fixed", "unit_price": {"amount": "11.00", "currency": "USD"}, "per_unit": "kg"},
        today=today)
    saved = quote_service.save_offer(corrected, today=today)
    request = {"offer_ids": [saved["offer_id"]],
               "expected_versions": {saved["offer_id"]: saved["version"]},
               "material": "copper", "quantity_kg": "10000", "current_stock_kg": "2000",
               "need_by": "2026-11-05", "decision_date": "2026-10-14",
               "reporting_currency": "USD", "budget": "100000", "additional_cost_per_kg": "0.25"}
    plan = planner.build_plan([saved], request, today=today)
    draft = rfq.build_draft(plan, saved, all_offers=[saved],
                            created_at=datetime(2026, 10, 14, tzinfo=timezone.utc))
    assert f"Original quoted price: unavailable ({original_status} document field)" in draft["text"]
    assert "Current reviewed price: 11.00 USD per kg" in draft["text"]
    assert "Original quoted price: 11.00" not in draft["text"]
