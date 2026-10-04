from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from procure.api import app


@pytest.fixture(scope="module")
def client(seeded_db):
    with TestClient(app) as c:
        yield c


def test_health_and_materials(client):
    h = client.get("/health").json()
    assert h["status"] == "ok" and h["offline"] is True
    assert h["any_synthetic"] or h["any_sample"]
    mats = client.get("/materials").json()
    assert [m["key"] for m in mats] == ["copper", "aluminium", "iron_ore"]


def test_prices_forecast_news(client):
    p = client.get("/prices/copper").json()
    assert p["points"] and p["any_synthetic"] in (True, False)
    f = client.get("/forecast/copper?horizon=6").json()
    assert len(f["points"]) == 7
    assert all(pt["p10"] <= pt["p50"] <= pt["p90"] for pt in f["points"])
    assert f["metrics"]
    n = client.get("/news/copper").json()
    assert -1 <= n["news_index"] <= 1 and n["items"]
    assert client.get("/prices/zinc").status_code == 404


def test_recommend_validation_and_output(client):
    r = client.post("/recommend", json={"material": "copper", "quantity_kg": 50000, "need_by_month": 3})
    assert r.status_code == 200
    rec = r.json()
    assert sum(s["percent"] for s in rec["schedule"]) == pytest.approx(100)
    assert client.post("/recommend", json={"material": "copper", "need_by_month": 9}).status_code == 422


def test_headline_impact_supply_shock_changes_plan(client):
    body = {"headline": "Major copper mine strike halts 10% of global supply",
            "request": {"material": "copper", "current_stock_kg": 60000, "risk_appetite": "medium"}}
    out = client.post("/headline-impact", json=body).json()
    assert out["signal"]["direction"] == "up" and out["signal"]["relevant"]
    assert out["after"]["news_index"] > out["before"]["news_index"]
    before_now = sum(s["percent"] for s in out["before"]["schedule"] if s["month"] == 0)
    after_now = sum(s["percent"] for s in out["after"]["schedule"] if s["month"] == 0)
    assert after_now >= before_now
    assert out["after"]["forecast"]["mean"][2] > out["before"]["forecast"]["mean"][2]
    assert out["diff"]


def test_backtest_endpoint(client):
    b = client.get("/backtest/iron_ore?months=12").json()
    assert b["months"] == 12 and b["rows"]


def test_actions_flow(client):
    rec = client.post("/recommend", json={"material": "iron_ore"}).json()
    a = client.post("/actions/rfq", json={"recommendation_id": rec["id"]}).json()
    assert a["status"] == "draft"
    assert client.post(f"/actions/{a['id']}/reject", json={"reason": "wait for new suppliers"}).json()["status"] == "rejected"
    assert client.post(f"/actions/{a['id']}/approve").status_code == 409
    assert client.post("/actions/rfq", json={"recommendation_id": "nope"}).status_code == 404
    audit = client.get("/audit").json()
    assert any(e["event"] == "rfq_rejected" and e["reason"] == "wait for new suppliers" for e in audit)


def test_overview_and_metrics(client):
    cards = client.get("/overview").json()
    assert len(cards) == 3
    assert all(0 <= c["price_weather"]["probability"] <= 1 for c in cards)
    assert client.get("/metrics").json()


def test_actions_list_handles_null_reason(client):
    rec = client.post("/recommend", json={"material": "copper"}).json()
    a = client.post("/actions/rfq", json={"recommendation_id": rec["id"]}).json()
    assert a["reason"] is None
    listed = client.get("/actions")
    assert listed.status_code == 200
    assert any(x["id"] == a["id"] and x["reason"] is None for x in listed.json())
