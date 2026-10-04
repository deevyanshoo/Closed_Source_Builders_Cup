from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from procure.forecast.models import fit_ets, fit_gbm, fit_naive
from procure.forecast.select import get_metrics, get_selection, metrics_from, walk_forward
from procure.forecast.simulate import news_overlay, simulate


def _series(n=150, seed=1, rho=0.6):
    rng = np.random.default_rng(seed)
    z = rng.standard_normal((n, 2))
    e_c = 0.05 * z[:, 0]
    e_fx = 0.015 * (rho * z[:, 0] + np.sqrt(1 - rho ** 2) * z[:, 1])
    idx = pd.date_range("2012-01-01", periods=n, freq="MS")
    usd = 7000 * np.exp(np.cumsum(e_c))
    fx = 60 * np.exp(np.cumsum(e_fx))
    return pd.DataFrame({"usd_per_unit": usd, "usdinr": fx, "inr_per_kg": usd * fx / 1000}, index=idx)


@pytest.mark.parametrize("fitter", [fit_naive, fit_ets, fit_gbm])
def test_models_return_drift_path(fitter):
    df = _series()
    fc = fitter(np.log(df["usd_per_unit"]), 6)
    assert fc.drift.shape == (6,)
    assert np.isfinite(fc.drift).all()
    assert len(fc.residuals) >= 24
    assert fc.noise_scale > 0


def test_naive_has_zero_drift():
    fc = fit_naive(np.log(_series()["usd_per_unit"]), 6)
    assert np.allclose(fc.drift, 0)


def test_simulation_shape_start_and_correlation():
    df = _series(rho=0.6)
    sim = simulate(df, "copper", "naive", horizon=6, n_paths=4000, seed=3)
    assert sim.inr_paths.shape == (4000, 7)
    assert np.allclose(sim.inr_paths[:, 0], df["inr_per_kg"].iloc[-1])
    # the bootstrapped residual pairs keep the commodity/FX correlation of the data
    r = np.log(df["usd_per_unit"]).diff().dropna()
    f = np.log(df["usdinr"]).diff().dropna()
    assert sim.meta["corr_c_fx"] == pytest.approx(np.corrcoef(r.iloc[-120:], f.iloc[-120:])[0, 1], abs=0.05)
    q10, q50, q90 = sim.quantiles()
    assert (q10[1:] < q50[1:]).all() and (q50[1:] < q90[1:]).all()
    # bands widen with horizon
    assert (q90 - q10)[6] > (q90 - q10)[1]


def test_simulation_is_reproducible():
    df = _series()
    a = simulate(df, "copper", "naive", n_paths=500, seed=7).inr_paths
    b = simulate(df, "copper", "naive", n_paths=500, seed=7).inr_paths
    np.testing.assert_array_equal(a, b)


def test_news_overlay_shifts_and_widens():
    drift, widen = news_overlay(np.zeros(6), 0.5, 2)
    assert drift[0] > 0 and drift[1] > 0 and drift[2] == 0
    assert widen[0] > 1 and widen[2] == 1


def test_walk_forward_metrics_are_computed():
    df = _series(n=120)
    wf = walk_forward(df, "copper", "naive", eval_months=12, horizon=3)
    m = metrics_from(wf, "naive")
    assert list(m["horizon"]) == [1, 2, 3]
    assert m["coverage"].between(0, 1).all()
    assert (m["mape"] > 0).all()
    assert m["directional_acc"].isna().all()  # naive makes no directional call


def test_selection_follows_two_percent_rule(seeded_db):
    for mat in ("copper", "aluminium", "iron_ore"):
        sel = get_selection(mat)
        if sel["improvement"] >= 0.02:
            assert sel["selected_model"] == sel["best_model"]
        else:
            assert sel["selected_model"] == "naive"
        met = get_metrics(mat)
        assert set(met["model"]) == {"naive", "ets", "gbm"}
        assert set(met["horizon"]) == set(range(1, 7))
