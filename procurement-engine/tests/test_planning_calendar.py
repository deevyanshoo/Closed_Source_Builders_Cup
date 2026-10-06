"""Calendar alignment uses synthetic dated paths; no provider data or account calls."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from procure import api, decision
from procure import market
from procure.forecast import select
from procure.decision import PurchaseRequest, recommend
from procure.forecast.simulate import SimResult, simulate


def dated_series(last="2026-08-01", n=80):
    rng = np.random.default_rng(21)
    dates = pd.date_range(end=last, periods=n, freq="MS")
    usd = 7000 * np.exp(np.cumsum(rng.normal(0, 0.025, n)))
    fx = 75 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    return pd.DataFrame({"usd_per_unit": usd, "usdinr": fx, "inr_per_kg": usd * fx / 1000}, index=dates)


def supplied_context(months, values):
    dates = [pd.Timestamp(m) for m in months]
    paths = np.tile(np.asarray(values, dtype=float), (3, 1))
    return SimpleNamespace(
        sim=SimResult(paths, dates, "naive"),
        series=pd.DataFrame({"inr_per_kg": [100.0]}, index=[pd.Timestamp("2026-08-01")]),
        current_price=100.0, news_index=0.0, news_horizon=0, selection={},
        provenance={"prices": {"used": "synthetic", "status": "test"}},
    )


def recommendation(ctx, stub_why=True):
    req = PurchaseRequest(material="copper", quantity_kg=1000, need_by_month=2,
                          supplier_lead_time_months=0, current_stock_kg=10000,
                          monthly_consumption_kg=1000)
    if stub_why:
        with patch.object(decision, "why_panel", return_value=([], {"level": "LOW"})):
            return recommend(req, ctx=ctx, store=False, planning_month=pd.Timestamp("2026-10-15"))
    return recommend(req, ctx=ctx, store=False, planning_month=pd.Timestamp("2026-10-15"))


def test_live_bridge_slices_actual_draws_and_retains_uncertainty():
    df = dated_series()
    original = simulate(df, "copper", "naive", horizon=8, n_paths=400, seed=17)
    bridged = simulate(df, "copper", "naive", horizon=6, n_paths=400, seed=17,
                       planning_month=pd.Timestamp("2026-10-15"))
    np.testing.assert_array_equal(bridged.inr_paths, original.inr_paths[:, 2:9])
    assert bridged.months[0] == pd.Timestamp("2026-10-01")
    assert np.std(bridged.inr_paths[:, 0]) > 0
    assert bridged.meta["observation_end"] == "2026-08-01"
    assert bridged.meta["planning_month"] == "2026-10-01"
    assert bridged.meta["bridge_months"] == 2
    assert bridged.meta["planning_offset_zero_basis"] == "forecast"
    assert bridged.meta["publication_at"] is None
    assert original.months[0] == pd.Timestamp("2026-08-01")
    assert np.allclose(original.inr_paths[:, 0], df["inr_per_kg"].iloc[-1])


@pytest.mark.parametrize("last,planning,bridge", [
    ("2026-10-01", "2026-10-15", 0),
    ("2026-11-01", "2027-02-15", 3),
])
def test_zero_lag_and_year_rollover(last, planning, bridge):
    df = dated_series(last)
    sim = simulate(df, "copper", "naive", horizon=6, n_paths=100, seed=5,
                   planning_month=pd.Timestamp(planning))
    assert sim.meta["bridge_months"] == bridge
    assert sim.months[0] == pd.Timestamp(planning).replace(day=1)
    assert sim.months[-1] == sim.months[0] + pd.DateOffset(months=6)
    assert sim.meta["planning_offset_zero_basis"] == ("observed" if bridge == 0 else "forecast")


def test_news_overlay_starts_at_transition_into_planning_month():
    sim = simulate(dated_series(), "copper", "naive", horizon=6, n_paths=20, seed=5,
                   news_index=0.5, news_horizon=2, planning_month=pd.Timestamp("2026-10-15"))
    drift = sim.meta["drift_c"]
    assert drift[0] == 0  # August to September predates planning
    assert drift[1] > 0   # September to October enters planning
    assert drift[2] > 0
    assert drift[3] == 0


def test_supplied_context_values_follow_dates_not_old_offsets():
    dates = pd.date_range("2026-08-01", periods=7, freq="MS")
    ctx = supplied_context(dates, np.arange(100, 170, 10))
    rec = recommendation(ctx)
    assert rec["forecast"]["months"][0] == "2026-10-01"
    assert rec["forecast"]["mean"][0] == 120
    assert rec["current_inr_per_kg"] == 100
    assert rec["latest_data_month"] == "Aug 2026"
    assert rec["forecast"]["bridge_months"] == 2
    assert any("forecast" in w.lower() and "Aug 2026" in w for w in rec["warnings"])
    for row in rec["schedule"]:
        k = row["month"]
        assert row["calendar_month"] == pd.Timestamp(rec["forecast"]["months"][k]).strftime("%b %Y")
        assert row["expected_inr_per_kg"] == rec["forecast"]["mean"][k]


@pytest.mark.parametrize("months,values", [
    (pd.date_range("2026-08-01", periods=4, freq="MS"), np.arange(100, 140, 10)),  # short for req need_by=2
    (["2026-08-01", "2026-10-01"] + list(pd.date_range("2026-11-01", periods=7, freq="MS")), np.arange(9)),
    (pd.date_range("2026-11-01", periods=9, freq="MS"), np.arange(9)),  # future observation axis
])
def test_supplied_context_rejects_short_gapped_or_future_axis(months, values):
    from procure.forecast.simulate import PlanningCalendarUnavailable
    with pytest.raises(PlanningCalendarUnavailable):
        recommendation(supplied_context(months, values))


def test_supplied_context_rejects_empty_path_batch_before_scoring():
    from procure.forecast.simulate import PlanningCalendarUnavailable
    dates = pd.date_range("2026-10-01", periods=7, freq="MS")
    ctx = supplied_context(dates, np.arange(100, 170, 10))
    ctx.series.index = pd.DatetimeIndex([pd.Timestamp("2026-10-01")])
    ctx.sim.inr_paths = np.empty((0, 7))
    with pytest.raises(PlanningCalendarUnavailable, match="Malformed dated planning paths"):
        recommendation(ctx)


def test_api_forecast_metadata_and_semantic_unavailability(monkeypatch):
    from procure.forecast.simulate import PlanningCalendarUnavailable
    sim = simulate(dated_series(), "copper", "naive", horizon=6, n_paths=20, seed=2,
                   planning_month=pd.Timestamp("2026-10-15"))
    ctx = SimpleNamespace(sim=sim, selection={"selected_model": "naive", "beats_naive": False}, news_index=0)
    monkeypatch.setattr(api, "build_context", lambda *a, **k: ctx)
    monkeypatch.setattr(api, "get_metrics", lambda material: pd.DataFrame(columns=["model", "horizon", "mape", "directional_acc", "coverage", "n_obs"]))
    client = TestClient(api.app)
    ok = client.get("/forecast/copper?horizon=6")
    assert ok.status_code == 200
    body = ok.json()
    assert body["observation_end"] == "2026-08-01"
    assert body["planning_month"] == "2026-10-01"
    assert body["bridge_months"] == 2
    assert body["publication_at"] is None
    assert body["points"][0]["month"] == "2026-10-01"
    def unavailable(*a, **k):
        raise PlanningCalendarUnavailable("calendar window unavailable")
    monkeypatch.setattr(api, "build_context", unavailable)
    denied = client.get("/forecast/copper")
    assert denied.status_code == 503
    assert denied.json()["detail"]["code"] == "planning_calendar_unavailable"
    monkeypatch.setattr(market, "build_context", unavailable)
    denied_recommendation = client.post("/recommend", json={"material": "copper"})
    assert denied_recommendation.status_code == 503
    assert denied_recommendation.json()["detail"]["code"] == "planning_calendar_unavailable"


def test_live_context_uses_explicit_anchor_without_relabelling(seeded_db):
    df = dated_series()
    df["is_synthetic"] = True
    ctx = market.build_context("copper", series=df, model="naive", use_news=False,
                               planning_month=pd.Timestamp("2026-10-15"))
    req = PurchaseRequest(material="copper", quantity_kg=1000, need_by_month=2,
                          supplier_lead_time_months=0, current_stock_kg=10000,
                          monthly_consumption_kg=1000)
    rec = recommend(req, ctx=ctx, store=False, planning_month=pd.Timestamp("2026-10-15"))
    assert rec["forecast"]["months"][0] == "2026-10-01"
    assert rec["forecast"]["planning_offset_zero_basis"] == "forecast"
    assert rec["forecast"]["bridge_months"] == 2
    assert rec["current_inr_per_kg"] == round(df["inr_per_kg"].iloc[-1], 2)
    assert rec["latest_data_month"] == "Aug 2026"
    assert rec["forecast"]["mean"][0] == round(float(ctx.sim.inr_paths[:, 0].mean()), 2)
    assert any("forecast" in warning.lower() for warning in rec["warnings"])


def test_bridged_high_confidence_is_downgraded_and_coverage_dated(monkeypatch):
    dates = pd.date_range("2026-08-01", periods=7, freq="MS")
    ctx = supplied_context(dates, np.arange(100, 170, 10))
    ctx.series = dated_series()
    ctx.series["is_synthetic"] = False
    ctx.selection = {"selected_model": "gbm", "best_model": "gbm", "naive_mape": 0.1,
                     "best_mape": 0.08, "improvement": 0.2, "beats_naive": True, "residual_scale": 1.0}
    ctx.news_scored, ctx.news_items = [], []
    monkeypatch.setattr(select, "get_metrics", lambda material: pd.DataFrame(
        [{"model": "gbm", "horizon": 1, "coverage": 0.8},
         {"model": "gbm", "horizon": 2, "coverage": 0.8}]))
    rec = recommendation(ctx, stub_why=False)
    assert rec["confidence"]["coverage"] == pytest.approx(0.8)
    assert rec["confidence"]["level"] != "HIGH"
    assert any("bridge" in reason.lower() or "calibrat" in reason.lower() for reason in rec["confidence"]["reasons"])
    reliability = next(item for item in rec["why"] if item["signal"] == "Forecast band reliability")
    assert "last observation" in reliability["detail"].lower()
    assert "not validated" in reliability["detail"].lower()


def test_extended_gbm_window_without_targets_is_unavailable():
    from procure.forecast.simulate import PlanningCalendarUnavailable
    with pytest.raises(PlanningCalendarUnavailable, match="targets"):
        simulate(dated_series(n=16), "copper", "gbm", horizon=6,
                 planning_month=pd.Timestamp("2027-02-15"))


@pytest.mark.parametrize("damage", ["empty", "gap", "duplicate", "mid_month"])
def test_live_simulation_rejects_malformed_observation_axis(damage):
    from procure.forecast.simulate import PlanningCalendarUnavailable
    df = dated_series()
    if damage == "empty":
        df = df.iloc[:0]
    elif damage == "gap":
        df = df.drop(df.index[-2])  # ordered and unique, but missing a whole month
    else:
        dates = list(df.index)
        if damage == "duplicate":
            dates[-2] = dates[-1]
        else:
            dates[-1] = dates[-1] + pd.DateOffset(days=1)
        df.index = dates
    with pytest.raises(PlanningCalendarUnavailable):
        simulate(df, "copper", "naive", horizon=6, planning_month=pd.Timestamp("2026-10-15"))


@pytest.mark.parametrize("horizon", [0, -1, 2.5])
def test_explicit_simulation_horizon_must_be_positive_integer(horizon):
    with pytest.raises(ValueError):
        simulate(dated_series(), "copper", "naive", horizon=horizon)


def test_historical_default_keeps_original_observation_anchor():
    df = dated_series()
    df.index = df.index + pd.DateOffset(days=14)
    sim = simulate(df, "copper", "naive", horizon=2, n_paths=10, seed=2)
    assert sim.months[0] == df.index[-1]
    assert sim.months[1] == df.index[-1] + pd.DateOffset(months=1)
