from __future__ import annotations

import numpy as np
import pytest

from procure.backtest import decide_at, realised_cost, run_backtest, standard_request
from procure.features import load_series


def test_no_leakage_future_data_does_not_change_decision(seeded_db):
    """The decision at origin t must be identical whatever happens after t."""
    df = load_series("copper")
    t = len(df) - 10
    req = standard_request("copper")
    base = decide_at(df, t, req, "naive", 1.0)
    poisoned = df.copy()
    poisoned.iloc[t + 1:, poisoned.columns.get_loc("usd_per_unit")] *= 5.0
    poisoned.iloc[t + 1:, poisoned.columns.get_loc("inr_per_kg")] *= 5.0
    again = decide_at(poisoned, t, req, "naive", 1.0)
    np.testing.assert_array_equal(base["fractions"], again["fractions"])
    assert base["data_end"] == df.index[t]
    for model in ("ets", "gbm"):
        a = decide_at(df, t, req, model, 1.0)
        b = decide_at(poisoned, t, req, model, 1.0)
        np.testing.assert_array_equal(a["fractions"], b["fractions"])


def test_realised_cost_hand_checked(seeded_db):
    df = load_series("copper")
    req = standard_request("copper")
    d = decide_at(df, len(df) - 10, req, "naive", 1.0)
    inp = d["inputs"]
    prices = np.array([100.0, 110.0, 120.0])
    a = inp.landed
    cost_now = realised_cost(np.array([1.0, 0, 0]), prices, req, inp)
    assert cost_now == pytest.approx(req.quantity_kg * (100 * (1 + a.duty_pct) + a.freight_inr_per_kg
                                                         + 100 * a.holding_cost_pct_per_month * 2))


def test_backtest_report_shape(seeded_db):
    out = run_backtest("aluminium", months=12)
    assert out["months"] == 12
    s = out["summary"]
    for k in ("total_saved_vs_jit_inr", "avg_saving_vs_jit_pct", "share_months_beat_jit", "worst_month",
              "best_month", "vs_buy_now", "vs_even_split", "regret_vs_hindsight"):
        assert k in s
    rows = out["rows"]
    assert rows[-1]["cumulative_saving_vs_jit"] == pytest.approx(sum(r["saving_vs_jit"] for r in rows))
    assert all(r["regret_policy_pct"] >= -1e-9 for r in rows)  # nothing beats hindsight
    assert any("news" in n.lower() for n in out["notes"])
