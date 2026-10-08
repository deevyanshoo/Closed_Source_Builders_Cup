"""HTTP routes for quote intelligence. Existing routes are untouched; these are new /offers routes."""
from __future__ import annotations

import base64
import datetime as dt
import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from procure.api import app

FIXTURES = Path(__file__).parent / "fixtures" / "offers"
QUOTES = {q["id"]: q for q in json.loads((FIXTURES / "manifest.json").read_text())["quotes"]}
AS_OF = "2026-10-07"
BUYER = {"need_by_month": 3, "current_stock_kg": "20000", "monthly_consumption_kg": "15000", "risk_appetite": "medium"}
_n = iter(range(100_000))


def key():
    return f"api-key-{next(_n):06d}"


@pytest.fixture(scope="module")
def client(seeded_db):
    with TestClient(app) as c:
        yield c


def upload(client, quote_id, **extra):
    path = FIXTURES / QUOTES[quote_id]["file"]
    body = {"filename": path.name, "content_base64": base64.b64encode(path.read_bytes()).decode(), "mode": "rules",
            "as_of": AS_OF, "actor": "api-tester", "allow_duplicate": True, **extra}
    return client.post("/offers/extractions", json=body)


def test_upload_and_extract_returns_offer_with_evidence(client):
    r = upload(client, "Q01")
    assert r.status_code == 201
    body = r.json()
    offer = body["offer"]
    assert offer["status"] == "needs_review" and offer["synthetic"] is True
    assert offer["items"][0]["pricing"]["evidence"][0]["locator"] == "p1:l10"
    ex = client.get(f"/offers/extractions/{body['extraction_id']}").json()
    assert {"locator": "p1:l10", "text": "Unit price: USD 9,412.50 per metric tonne"}.items() <= next(
        s for s in ex["segments"] if s["locator"] == "p1:l10").items()


def test_full_review_flow_over_http(client):
    offer = upload(client, "Q01").json()["offer"]
    oid = offer["offer_id"]
    blocked = client.post(f"/offers/{oid}/save", json={"expected_version": 1, "actor": "r", "idempotency_key": key()})
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "save_blocked"
    # Since #33 a price-line currency is not settlement evidence, so citing it is refused...
    refused = client.post(f"/offers/{oid}/corrections", json={
        "field": "settlement_currency", "value": "USD", "evidence": [{"locator": "p1:l10", "quote": "USD"}],
        "expected_version": 1, "actor": "r", "idempotency_key": key()})
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "evidence_not_verified"
    # ...and the reviewer records USD as their own assertion, labelled as having no document evidence.
    corrected = client.post(f"/offers/{oid}/corrections", json={
        "field": "settlement_currency", "value": "USD", "expected_version": 1, "actor": "r",
        "idempotency_key": key()})
    assert corrected.status_code == 200 and corrected.json()["status"] == "corrected"
    settlement = corrected.json()["fields"]["settlement_currency"]
    assert (settlement["evidence"], settlement["evidence_status"]) == ([], "user_entered_without_document_evidence")
    saved = client.post(f"/offers/{oid}/save", json={"expected_version": 2, "actor": "r", "idempotency_key": key()})
    assert saved.json()["status"] == "saved"
    detail = client.get(f"/offers/{oid}?include_events=true").json()
    assert [e["action"] for e in detail["events"]] == ["created", "correct", "save"]
    assert oid in {o["offer_id"] for o in client.get("/offers?status=saved").json()["offers"]}
    plan = client.post("/offers/planning-input", json={"offer_ids": [oid], "buyer": BUYER, "actor": "planner",
                                                       "idempotency_key": key(), "as_of": AS_OF})
    assert plan.status_code == 200
    assert plan.json()["offers"][0]["items"][0]["quantity_kg"] == "250000"


def test_idempotency_key_can_come_from_the_header(client):
    oid = upload(client, "Q01").json()["offer"]["offer_id"]
    body = {"field": "settlement_currency", "value": "USD", "expected_version": 1, "actor": "r"}
    first = client.post(f"/offers/{oid}/corrections", json=body, headers={"Idempotency-Key": "header-key-0001"})
    again = client.post(f"/offers/{oid}/corrections", json=body, headers={"Idempotency-Key": "header-key-0001"})
    assert first.status_code == again.status_code == 200
    assert again.json()["replayed"] is True and again.json()["version"] == first.json()["version"]


def test_concurrent_identical_requests_apply_once(client):
    oid = upload(client, "Q04").json()["offer"]["offer_id"]
    k, results = key(), []

    def send():
        results.append(client.post(f"/offers/{oid}/reject", json={
            "expected_version": 1, "actor": "r", "reason": "duplicate supplier", "idempotency_key": k}))

    threads = [threading.Thread(target=send) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert {r.status_code for r in results} == {200}
    assert sorted(r.json()["replayed"] for r in results) == [False, True, True, True]
    events = client.get(f"/offers/{oid}?include_events=true").json()["events"]
    assert [e["action"] for e in events].count("reject") == 1


@pytest.mark.parametrize("path,body,status,code", [
    ("/offers/nope00000000/save", {"expected_version": 1, "actor": "r", "idempotency_key": "k" * 10}, 404, "not_found"),
    ("/offers/extractions", {"filename": "x.pdf", "content_base64": "%%%not base64%%%", "actor": "r"}, 400,
     "invalid_request"),
    ("/offers/extractions", {"filename": "x.docx", "content_base64": base64.b64encode(b"PK\x03\x04").decode(),
                             "actor": "r"}, 422, "document_unreadable"),
])
def test_errors_have_explicit_codes(client, path, body, status, code):
    r = client.post(path, json=body)
    assert r.status_code == status
    assert r.json()["error"]["code"] == code


def test_missing_idempotency_key_is_rejected(client):
    oid = upload(client, "Q01").json()["offer"]["offer_id"]
    r = client.post(f"/offers/{oid}/save", json={"expected_version": 1, "actor": "r"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_request"


def test_existing_routes_are_unchanged(client):
    assert client.get("/health").status_code == 200
    assert [m["key"] for m in client.get("/materials").json()] == ["copper", "aluminium", "iron_ore"]


def test_future_and_past_get_are_preview_only(client):
    offer = upload(client, "Q01").json()["offer"]
    oid = offer["offer_id"]
    future = client.get(f"/offers/{oid}?as_of=2099-01-01&include_events=true").json()
    assert future["status"] == "expired" and future["version"] == 1
    assert [e["action"] for e in future["events"]] == ["created"]
    past = client.get(f"/offers/{oid}?as_of={AS_OF}&include_events=true").json()
    assert past["status"] == "needs_review" and past["version"] == 1
    assert [e["action"] for e in past["events"]] == ["created"]


def test_mutation_body_as_of_cannot_expire_an_offer(client, monkeypatch):
    from procure.offers import store

    monkeypatch.setattr(store, "_today", lambda: dt.date.fromisoformat(AS_OF), raising=False)
    offer = upload(client, "Q01").json()["offer"]
    oid = offer["offer_id"]
    corrected = client.post(f"/offers/{oid}/corrections", json={
        "field": "settlement_currency", "value": "USD", "expected_version": 1,
        "actor": "reviewer", "idempotency_key": key(), "as_of": "2099-01-01"})
    assert corrected.status_code == 200 and corrected.json()["status"] == "corrected"
    saved = client.post(f"/offers/{oid}/save", json={
        "expected_version": 2, "actor": "reviewer", "idempotency_key": key(), "as_of": "2099-01-01"})
    assert saved.status_code == 200 and saved.json()["status"] == "saved"


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "1e400"])
def test_nonfinite_buyer_returns_structured_error_without_mutation(client, bad):
    offer = upload(client, "Q01").json()["offer"]
    oid = offer["offer_id"]
    corrected = client.post(f"/offers/{oid}/corrections", json={
        "field": "settlement_currency", "value": "USD", "expected_version": 1,
        "actor": "reviewer", "idempotency_key": key()}).json()
    client.post(f"/offers/{oid}/save", json={"expected_version": corrected["version"],
             "actor": "reviewer", "idempotency_key": key()})
    response = client.post("/offers/planning-input", json={
        "offer_ids": [oid], "buyer": {**BUYER, "current_stock_kg": bad},
        "actor": "planner", "idempotency_key": key()})
    assert response.status_code == 422 and response.json()["error"]["code"] == "invalid_value"
    assert client.get(f"/offers/{oid}").json()["status"] == "saved"
