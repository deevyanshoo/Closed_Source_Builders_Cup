"""Decision engine with fixed, hand-checkable price paths."""
from __future__ import annotations

from math import comb

import numpy as np
import pytest

from procure.decision import (PurchaseRequest, ScoreInputs, action_label, choose, enumerate_schedules,
                              feasible_months, recommend)
from procure.landed_cost import LandedAssumptions

A = LandedAssumptions(duty_pct=0.05, freight_inr_per_kg=6.0, holding_cost_pct_per_month=0.01)


def paths(monthly_change: float, n: int = 200, H: int = 6, noise: float = 0.0, seed: int = 0) -> np.ndarray:
    base = 100 * (1 + monthly_change) ** np.arange(H + 1)
    rng = np.random.default_rng(seed)
    p = np.tile(base, (n, 1))
    if noise:
        p[:, 1:] *= np.exp(np.cumsum(rng.normal(0, noise, (n, H)), axis=1))
    return p


def inputs(p, need_by=3, lead=1, stock=1e9, cons=1000.0, lam=0.5, measure="regret") -> ScoreInputs:
    months, _ = feasible_months(need_by, lead)
    return ScoreInputs(paths=p, feasible_months=months, quantity_kg=10000, need_by_month=need_by, lead_time=lead,
                       lam=lam, current_stock_kg=stock, monthly_consumption_kg=cons, landed=A,
                       stockout_multiplier=10.0, risk_measure=measure)


def best_fractions(inp) -> np.ndarray:
    res = choose(inp)
    return res["schedules"][res["best"]]


@pytest.mark.parametrize("measure", ["regret", "overshoot"])
def test_certain_rise_buys_now(measure):
    fr = best_fractions(inputs(paths(0.05), measure=measure))
    assert fr[0] == pytest.approx(1.0)
    assert action_label(fr, [0, 1, 2]) == "BUY NOW"


@pytest.mark.parametrize("measure", ["regret", "overshoot"])
def test_certain_fall_buys_as_late_as_feasible(measure):
    inp = inputs(paths(-0.05), measure=measure)
    fr = best_fractions(inp)
    assert inp.feasible_months == [0, 1, 2]
    assert fr[-1] == pytest.approx(1.0)
    assert action_label(fr, inp.feasible_months) == "WAIT"


def test_stockout_forces_buying_now():
    # flat prices favour waiting (holding cost), but stock runs out before any later delivery
    inp = inputs(paths(0.0, noise=0.03), stock=0.0, lead=1)
    fr = best_fractions(inp)
    assert fr[0] > 0
    assert action_label(fr, inp.feasible_months) != "WAIT"


def test_flat_prices_without_stock_pressure_wait_for_holding_cost():
    fr = best_fractions(inputs(paths(0.0)))
    assert fr[-1] == pytest.approx(1.0)


def test_enumeration_counts_and_sums():
    for F in range(1, 7):
        s = enumerate_schedules(F)
        assert len(s) == comb(5 + F - 1, F - 1)
        np.testing.assert_allclose(s.sum(axis=1), 1.0)
        assert ((s * 100) % 20 < 1e-9).all()


def test_infeasible_lead_time_warns_and_buys_now():
    months, warn = feasible_months(need_by=1, lead=3)
    assert months == [0] and warn and "late" in warn


def test_budget_filter_warns_when_nothing_fits():
    inp = inputs(paths(0.0, noise=0.05))
    res = choose(inp, max_budget=1.0)
    assert res["warnings"]


def test_regret_measure_can_split_under_uncertainty():
    """With noisy, driftless paths the regret measure hedges instead of going all-in."""
    inp = inputs(paths(0.004, noise=0.06, n=2000), lam=1.0, stock=1e9)
    fr = best_fractions(inp)
    assert (fr > 0).sum() >= 2


def test_recommend_end_to_end(seeded_db):
    rec = recommend(PurchaseRequest(material="copper"))
    assert sum(r["percent"] for r in rec["schedule"]) == pytest.approx(100.0)
    assert rec["action"] in {"BUY NOW", "SPLIT BUY", "WAIT"}
    names = [c["name"] for c in rec["comparison"]]
    assert names == ["Recommended", "Buy all now", "Just in time", "Even split"]
    jit = next(c for c in rec["comparison"] if c["name"] == "Just in time")
    assert jit["saving_vs_jit_inr"] == 0
    assert 0 <= rec["price_weather"]["probability"] <= 1
    signals = {w["signal"] for w in rec["why"]}
    assert {"Forecast model", "News risk index", "Stock runway", "Assumptions used"} <= signals
    # offline test data is synthetic, so confidence must be LOW
    assert rec["confidence"]["level"] == "LOW"
    assert rec["id"]
