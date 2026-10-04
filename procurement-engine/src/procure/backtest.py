"""Regret meter: replay the decision policy on past months and price it with realised prices.

At each origin month t the engine sees only data up to t (no leakage), chooses a schedule
for a standard request, and the schedule is costed at the prices that actually happened.
The news overlay is off: there is no historical news archive in this prototype.
"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd

from .decision import PurchaseRequest, choose, describe_schedule, score_inputs_for
from .features import load_series
from .forecast.select import get_selection
from .forecast.simulate import simulate
from .landed_cost import landed_inr_per_kg
from .settings import assumptions

_CACHE: dict[tuple, dict] = {}


def standard_request(material: str, risk_measure: str | None = None) -> PurchaseRequest:
    b = assumptions()["backtest"]
    return PurchaseRequest(material=material, quantity_kg=b["quantity_kg"], need_by_month=b["need_by_month"],
                           current_stock_kg=b["current_stock_kg"], monthly_consumption_kg=b["monthly_consumption_kg"],
                           supplier_lead_time_months=b["lead_time_months"], risk_appetite=b["risk_appetite"],
                           risk_measure=risk_measure)


def decide_at(df: pd.DataFrame, t: int, req: PurchaseRequest, model: str, residual_scale: float) -> dict:
    """Run the engine at origin position t using df.iloc[:t+1] only."""
    hist = df.iloc[: t + 1]
    sim = simulate(hist, req.material, model, news_index=0.0, residual_scale=residual_scale, seed=t,
                   refit_every=assumptions()["forecast"]["gbm_refit_every"])
    inp, _ = score_inputs_for(req, sim.inr_paths)
    res = choose(inp)
    return {"inputs": inp, "result": res, "fractions": res["schedules"][res["best"]],
            "data_end": hist.index[-1]}


def realised_cost(fractions: np.ndarray, actual_prices: np.ndarray, req: PurchaseRequest, inp) -> float:
    """Total landed cost of a schedule at realised prices (actual_prices[m] = INR/kg in month t+m)."""
    jit = req.need_by_month - req.supplier_lead_time_months
    total = 0.0
    for f, m in zip(fractions, inp.feasible_months):
        if f > 0:
            total += f * req.quantity_kg * float(landed_inr_per_kg(actual_prices[m], max(jit - m, 0), inp.landed))
    return total


def run_backtest(material: str, model: str = "selected", months: int | None = None,
                 risk_measure: str | None = None) -> dict:
    df = load_series(material)
    sel = get_selection(material)
    use_model = sel["selected_model"] if model == "selected" else model
    months = int(months or assumptions()["backtest"]["months"])
    req = standard_request(material, risk_measure)
    key = (material, use_model, months, req.risk_measure, df.index[-1], len(df), float(df["inr_per_kg"].iloc[-1]))
    if key in _CACHE:
        return _CACHE[key]

    t0 = time.perf_counter()
    last_buy = req.need_by_month - req.supplier_lead_time_months
    n = len(df)
    origins = list(range(max(60, n - 1 - last_buy - months + 1), n - last_buy))
    prices = df["inr_per_kg"].values
    rows = []
    for t in origins:
        d = decide_at(df, t, req, use_model, float(sel.get("residual_scale") or 1.0))
        assert d["data_end"] == df.index[t], "leakage guard: engine saw data beyond the origin"
        inp, res = d["inputs"], d["result"]
        actual = prices[t: t + last_buy + 1]
        costs = {"policy": realised_cost(d["fractions"], actual, req, inp)}
        for name, idx in res["baselines"].items():
            costs[name] = realised_cost(res["schedules"][idx], actual, req, inp)
        jit = costs["Just in time"]
        onehot = np.eye(len(inp.feasible_months))
        hindsight = min(realised_cost(onehot[i], actual, req, inp) for i in range(len(onehot)))
        rows.append({
            "origin": df.index[t].strftime("%Y-%m"), "decision": describe_schedule(d["fractions"], inp.feasible_months),
            "buy_now_pct": round(float(d["fractions"][0]) * 100, 1),
            "policy_cost": round(costs["policy"]), "jit_cost": round(jit),
            "buy_now_cost": round(costs["Buy all now"]), "even_split_cost": round(costs["Even split"]),
            "saving_vs_jit": round(jit - costs["policy"]),
            "saving_vs_jit_pct": round((jit - costs["policy"]) / jit * 100, 3),
            "saving_vs_buy_now": round(costs["Buy all now"] - costs["policy"]),
            "saving_vs_even": round(costs["Even split"] - costs["policy"]),
            "price_change_pct": round((actual[-1] / actual[0] - 1) * 100, 2),
            "hindsight_best_cost": round(hindsight),
            "regret_policy_pct": round((costs["policy"] / hindsight - 1) * 100, 3),
            "regret_jit_pct": round((jit / hindsight - 1) * 100, 3),
            "regret_buy_now_pct": round((costs["Buy all now"] / hindsight - 1) * 100, 3),
            "regret_even_pct": round((costs["Even split"] / hindsight - 1) * 100, 3),
        })
    per = pd.DataFrame(rows)
    per["cumulative_saving_vs_jit"] = per["saving_vs_jit"].cumsum()

    def stats(col: str) -> dict:
        s = per[col]
        return {"total_inr": int(s.sum()), "win_rate": float((s > 0).mean()), "loss_rate": float((s < 0).mean()),
                "tie_rate": float((s == 0).mean())}

    worst = per.loc[per["saving_vs_jit"].idxmin()]
    best = per.loc[per["saving_vs_jit"].idxmax()]
    out = {
        "material": material, "model": use_model, "risk_measure": req.risk_measure or assumptions()["risk"]["measure"],
        "months": len(per), "period": f"{per['origin'].iloc[0]} to {per['origin'].iloc[-1]}",
        "request": req.model_dump(),
        "summary": {
            "total_saved_vs_jit_inr": int(per["saving_vs_jit"].sum()),
            "avg_saving_vs_jit_pct": float(per["saving_vs_jit_pct"].mean()),
            "share_months_beat_jit": float((per["saving_vs_jit"] > 0).mean()),
            "worst_month": {"origin": worst["origin"], "saving_inr": int(worst["saving_vs_jit"]),
                            "decision": worst["decision"]},
            "best_month": {"origin": best["origin"], "saving_inr": int(best["saving_vs_jit"]),
                           "decision": best["decision"]},
            "vs_jit": stats("saving_vs_jit"), "vs_buy_now": stats("saving_vs_buy_now"),
            "vs_even_split": stats("saving_vs_even"),
            "total_policy_cost_inr": int(per["policy_cost"].sum()),
            "avg_buy_now_pct": float(per["buy_now_pct"].mean()),
            "regret_vs_hindsight": {
                name: {"avg_pct": float(per[col].mean()), "worst_pct": float(per[col].max())}
                for name, col in (("policy", "regret_policy_pct"), ("just_in_time", "regret_jit_pct"),
                                  ("buy_all_now", "regret_buy_now_pct"), ("even_split", "regret_even_pct"))},
        },
        "rows": per.to_dict(orient="records"),
        "notes": ["News overlay is off in backtests because there is no historical news archive.",
                  "Each month the engine sees only data up to that month (asserted in code and tests).",
                  f"Standard request: {req.quantity_kg:,.0f} kg needed {req.need_by_month} months ahead, "
                  f"lead time {req.supplier_lead_time_months} month, {req.risk_appetite} risk."],
        "seconds": round(time.perf_counter() - t0, 2),
    }
    _CACHE[key] = out
    return out


def clear_cache() -> None:
    _CACHE.clear()
