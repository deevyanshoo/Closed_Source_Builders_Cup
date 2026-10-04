"""Decision engine: enumerate buy schedules, score them on simulated paths, explain the choice.

score = E[total landed cost] + lambda * (P90[cost] - E[cost]) + stockout_penalty
"""
from __future__ import annotations

import itertools
import json
from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from . import db
from .features import fx_trend, volatility_summary
from .landed_cost import LandedAssumptions, get_landed_assumptions, landed_inr_per_kg
from .settings import assumptions, material_cfg

RiskAppetite = Literal["low", "medium", "high"]
UNIT_PCT = 20  # schedules are built from 20% blocks


class PurchaseRequest(BaseModel):
    material: str = Field("copper", description="Material key, e.g. copper, aluminium, iron_ore")
    quantity_kg: float = Field(50000, gt=0)
    need_by_month: int = Field(3, ge=1, le=6, description="Month the material must be on hand (1 to 6)")
    current_stock_kg: float = Field(20000, ge=0)
    monthly_consumption_kg: float = Field(15000, gt=0)
    supplier_lead_time_months: int = Field(1, ge=0, le=3)
    risk_appetite: RiskAppetite = "medium"
    max_budget_inr: float | None = Field(None, gt=0)
    risk_measure: Literal["regret", "overshoot"] | None = Field(
        None, description="Override the configured risk measure (default from assumptions.yaml)")

    model_config = {"json_schema_extra": {"examples": [{
        "material": "copper", "quantity_kg": 50000, "need_by_month": 3, "current_stock_kg": 20000,
        "monthly_consumption_kg": 15000, "supplier_lead_time_months": 1, "risk_appetite": "medium"}]}}


@dataclass
class ScoreInputs:
    """Everything needed to score schedules, independent of where the paths came from."""

    paths: np.ndarray            # (n_paths, H+1) INR per kg, column k = month k
    feasible_months: list[int]
    quantity_kg: float
    need_by_month: int
    lead_time: int
    lam: float
    current_stock_kg: float
    monthly_consumption_kg: float
    landed: LandedAssumptions
    stockout_multiplier: float
    risk_measure: str = "regret"


# ----------------------------------------------------------------------------- schedules

def feasible_months(need_by: int, lead: int) -> tuple[list[int], str | None]:
    last = need_by - lead
    if last < 0:
        return [0], (f"Lead time ({lead} months) is longer than the need-by window ({need_by} months). "
                     f"Even buying now, material arrives about {-last} month(s) late. Buy now and expedite.")
    return list(range(0, last + 1)), None


def enumerate_schedules(n_months: int, units: int = 100 // UNIT_PCT) -> np.ndarray:
    """All ways to split `units` equal blocks across n_months (stars and bars). Rows sum to 1."""
    rows = []
    for bars in itertools.combinations(range(units + n_months - 1), n_months - 1):
        parts, prev = [], -1
        for b in bars + (units + n_months - 1,):
            parts.append(b - prev - 1)
            prev = b
        rows.append(parts)
    return np.array(rows, dtype=float) / units


def baseline_schedules(n_months: int) -> dict[str, np.ndarray]:
    now, jit = np.zeros(n_months), np.zeros(n_months)
    now[0], jit[-1] = 1.0, 1.0
    return {"Buy all now": now, "Just in time": jit, "Even split": np.full(n_months, 1.0 / n_months)}


# ----------------------------------------------------------------------------- scoring

def month_costs(inp: ScoreInputs) -> np.ndarray:
    """Landed INR per kg on every path for every feasible buy month. Shape (n_paths, F)."""
    jit_month = inp.need_by_month - inp.lead_time
    cols = []
    for m in inp.feasible_months:
        held = max(jit_month - m, 0)
        cols.append(landed_inr_per_kg(inp.paths[:, m], held, inp.landed))
    return np.column_stack(cols)


def stockout_penalty(schedules: np.ndarray, inp: ScoreInputs) -> np.ndarray:
    """Penalty if stock runs out before the first delivery arrives."""
    runway = inp.current_stock_kg / inp.monthly_consumption_kg
    months = np.array(inp.feasible_months)
    first_buy = np.array([months[np.argmax(s > 1e-9)] for s in schedules])
    gap = np.maximum(first_buy + inp.lead_time - runway, 0.0)
    shortfall_kg = gap * inp.monthly_consumption_kg
    ref_price = float(landed_inr_per_kg(inp.paths[:, 0].mean(), 0, inp.landed))
    return shortfall_kg * ref_price * inp.stockout_multiplier


def score_schedules(schedules: np.ndarray, inp: ScoreInputs) -> pd.DataFrame:
    """Expected cost, P90 cost, risk, penalty and score for each schedule (rows of fractions).

    risk_measure 'overshoot' is the literal P90 - E. 'regret' is the P90 of the gap between the
    plan's cost and the cheapest single-month plan on the same path (what hindsight would have chosen).
    """
    costs = month_costs(inp)                                  # (n, F)
    totals = inp.quantity_kg * schedules @ costs.T            # (k, n)
    exp = totals.mean(axis=1)
    p90 = np.quantile(totals, 0.9, axis=1)
    regret = totals - inp.quantity_kg * costs.min(axis=1)[None, :]
    p90_regret = np.quantile(regret, 0.9, axis=1)
    risk = p90_regret if inp.risk_measure == "regret" else (p90 - exp)
    pen = stockout_penalty(schedules, inp)
    score = exp + inp.lam * risk + pen
    return pd.DataFrame({"expected_cost": exp, "p90_cost": p90, "p10_cost": np.quantile(totals, 0.1, axis=1),
                         "p90_regret": p90_regret, "risk": risk, "stockout_penalty": pen, "score": score})


def choose(inp: ScoreInputs, max_budget: float | None = None) -> dict:
    """Score all candidates plus baselines and pick the lowest score."""
    F = len(inp.feasible_months)
    cands = enumerate_schedules(F)
    base = baseline_schedules(F)
    all_s = np.vstack([cands] + list(base.values()))
    scores = score_schedules(all_s, inp)
    warnings = []
    eligible = np.ones(len(all_s), dtype=bool)
    if max_budget is not None:
        eligible = scores["p90_cost"].values <= max_budget
        if not eligible[: len(cands)].any():
            warnings.append(f"Every schedule's P90 cost is above the budget of INR {max_budget:,.0f}. "
                            "Showing the best schedule anyway.")
            eligible[:] = True
    s = scores["score"].values.copy()
    s[~eligible] = np.inf
    s[len(cands):] = np.inf  # pick among the 20% grid; baselines are reported separately
    best = int(np.argmin(s))
    base_idx = {name: len(cands) + i for i, name in enumerate(base)}
    return {"schedules": all_s, "scores": scores, "best": best, "baselines": base_idx, "warnings": warnings}


def action_label(fractions: np.ndarray, months: list[int]) -> str:
    now_share = fractions[months.index(0)] if 0 in months else 0.0
    if now_share >= 0.999:
        return "BUY NOW"
    if now_share <= 1e-9:
        return "WAIT"
    return "SPLIT BUY"


def lambda_for(risk: str) -> float:
    return float(assumptions()["risk"]["lambda"][risk])


# ----------------------------------------------------------------------------- recommendation

def _month_label(k: int) -> str:
    return "Now" if k == 0 else f"In {k} month{'s' if k > 1 else ''}"


def describe_schedule(fr: np.ndarray, months: list[int]) -> str:
    parts = [f"{round(f * 100):.0f}% {_month_label(m).lower()}" for f, m in zip(fr, months) if f > 1e-9]
    return "Buy " + ", ".join(parts[:-1]) + (" and " if len(parts) > 1 else "") + parts[-1]


def price_weather(paths: np.ndarray, months: int, threshold: float) -> dict:
    p = float(np.mean(paths[:, months] / paths[:, 0] - 1 > threshold))
    return {"probability": p, "threshold": threshold, "months": months,
            "text": f"{p * 100:.0f}% chance of a price rise above {threshold * 100:.0f}% in "
                    f"{months} month{'s' if months > 1 else ''}"}


def score_inputs_for(req: PurchaseRequest, paths: np.ndarray) -> tuple[ScoreInputs, str | None]:
    months, warn = feasible_months(req.need_by_month, req.supplier_lead_time_months)
    risk = assumptions()["risk"]
    inp = ScoreInputs(paths=paths, feasible_months=months, quantity_kg=req.quantity_kg,
                      need_by_month=req.need_by_month, lead_time=req.supplier_lead_time_months,
                      lam=lambda_for(req.risk_appetite), current_stock_kg=req.current_stock_kg,
                      monthly_consumption_kg=req.monthly_consumption_kg,
                      landed=get_landed_assumptions(req.material),
                      stockout_multiplier=float(risk["stockout_penalty_multiplier"]),
                      risk_measure=req.risk_measure or str(risk.get("measure", "regret")))
    return inp, warn


def recommend(req: PurchaseRequest, ctx=None, extra_items: list[dict] | None = None, store: bool = True,
              kind: str = "recommend") -> dict:
    """Full recommendation with baselines, price weather, why-panel and confidence."""
    from .market import build_context

    material_cfg(req.material)  # validates the key
    ctx = ctx or build_context(req.material, extra_items=extra_items)
    paths = ctx.sim.inr_paths
    inp, warn = score_inputs_for(req, paths)
    res = choose(inp, req.max_budget_inr)
    warnings = ([warn] if warn else []) + res["warnings"]
    months = inp.feasible_months
    sc, sched = res["scores"], res["schedules"]
    b = res["best"]
    jit_cost = float(sc["expected_cost"].iloc[res["baselines"]["Just in time"]])

    q10, q50, q90 = np.quantile(paths, [0.1, 0.5, 0.9], axis=0)
    mean_p = paths.mean(axis=0)
    jit_month = req.need_by_month - req.supplier_lead_time_months
    schedule = []
    for f, m in zip(sched[b], months):
        if f <= 1e-9:
            continue
        held = max(jit_month - m, 0)
        schedule.append({
            "month": m, "month_label": _month_label(m),
            "calendar_month": (pd.Timestamp.today().normalize().replace(day=1) + pd.DateOffset(months=m)).strftime("%b %Y"),
            "percent": round(float(f) * 100, 1), "kg": round(float(f) * req.quantity_kg, 1),
            "expected_inr_per_kg": round(float(mean_p[m]), 2),
            "expected_landed_inr_per_kg": round(float(landed_inr_per_kg(mean_p[m], held, inp.landed)), 2),
            "p10_inr_per_kg": round(float(q10[m]), 2), "p50_inr_per_kg": round(float(q50[m]), 2),
            "p90_inr_per_kg": round(float(q90[m]), 2),
        })

    def row(name: str, i: int) -> dict:
        e = float(sc["expected_cost"].iloc[i])
        return {"name": name, "schedule": describe_schedule(sched[i], months),
                "percents": [round(float(x) * 100, 1) for x in sched[i]],
                "expected_cost_inr": round(e), "p90_cost_inr": round(float(sc["p90_cost"].iloc[i])),
                "p90_regret_inr": round(float(sc["p90_regret"].iloc[i])),
                "stockout_penalty_inr": round(float(sc["stockout_penalty"].iloc[i])),
                "score": round(float(sc["score"].iloc[i])),
                "saving_vs_jit_inr": round(jit_cost - e),
                "saving_vs_jit_pct": round((jit_cost - e) / jit_cost * 100, 2)}

    comparison = [row("Recommended", b)] + [row(n, i) for n, i in res["baselines"].items()]
    thr = float(assumptions()["risk"]["price_rise_threshold"])
    weather = price_weather(paths, req.need_by_month, thr)
    why, confidence = why_panel(req, ctx, inp, comparison[0], weather)

    result = {
        "material": req.material, "display_name": material_cfg(req.material)["display_name"],
        "action": action_label(sched[b], months), "summary": describe_schedule(sched[b], months),
        "feasible_months": months, "schedule": schedule, "comparison": comparison,
        "price_weather": weather, "why": why, "confidence": confidence, "warnings": warnings,
        "current_inr_per_kg": round(ctx.current_price, 2),
        "latest_data_month": ctx.series.index[-1].strftime("%b %Y"),
        "forecast": forecast_payload(ctx),
        "news_index": round(ctx.news_index, 3), "news_horizon_months": ctx.news_horizon,
        "model": {k: ctx.selection.get(k) for k in ("selected_model", "best_model", "naive_mape", "best_mape",
                                                     "improvement", "beats_naive", "residual_scale")},
        "provenance": ctx.provenance,
    }
    if store:
        rid = db.new_id()
        result["id"] = rid
        result["created_at"] = db.now_iso()
        db.insert_row("recommendations", {"id": rid, "created_at": result["created_at"], "kind": kind,
                                          "request_json": req.model_dump_json(),
                                          "result_json": json.dumps(result, default=str)})
    return result


def forecast_payload(ctx) -> dict:
    q10, q50, q90 = np.quantile(ctx.sim.inr_paths, [0.1, 0.5, 0.9], axis=0)
    hist = ctx.series["inr_per_kg"].iloc[-36:]
    return {"months": [m.strftime("%Y-%m-%d") for m in ctx.sim.months],
            "p10": np.round(q10, 2).tolist(), "p50": np.round(q50, 2).tolist(), "p90": np.round(q90, 2).tolist(),
            "mean": np.round(ctx.sim.inr_paths.mean(axis=0), 2).tolist(),
            "history_months": [m.strftime("%Y-%m-%d") for m in hist.index],
            "history_inr_per_kg": np.round(hist.values, 2).tolist(), "model": ctx.sim.model}


MODEL_NAMES = {"naive": "Naive random walk", "ets": "Exponential smoothing (ETS)", "gbm": "Quantile gradient boosting"}


def why_panel(req: PurchaseRequest, ctx, inp: ScoreInputs, best: dict, weather: dict) -> tuple[list[dict], dict]:
    """Signals and numbers behind the recommendation. Every value is computed, none hardcoded."""
    from .forecast.select import get_metrics

    sel = ctx.selection
    met = get_metrics(req.material)
    horizon_met = met[(met["model"] == sel["selected_model"]) & (met["horizon"] <= req.need_by_month)]
    coverage = float(horizon_met["coverage"].mean()) if not horizon_met.empty else float("nan")
    vol = volatility_summary(ctx.series)
    fx3 = fx_trend(ctx.series, 3)
    runway = req.current_stock_kg / req.monthly_consumption_kg
    first_delivery = min(s for s in [m for m, f in zip(inp.feasible_months, best["percents"]) if f > 0]) + inp.lead_time
    distinct = [s for s in ctx.news_scored if not s.get("duplicate_of_story")]
    top = [{"title": s["title"], "direction": s["direction"], "severity": s["severity"],
            "age_days": s["age_days"], "contribution": s["contribution"], "is_sample": bool(s.get("is_sample")),
            "url": s.get("url", "")} for s in distinct[:3]]
    synthetic = bool(ctx.series["is_synthetic"].any())
    sample_news = any(bool(s.get("is_sample")) for s in ctx.news_items)
    a = inp.landed

    why = [
        {"signal": "Forecast model", "value": MODEL_NAMES[sel["selected_model"]],
         "detail": (f"Backtest error over the last 36 months (average 1 to 6 months ahead): naive {sel['naive_mape'] * 100:.1f}%, "
                    f"best challenger {MODEL_NAMES[sel['best_model']]} {sel['best_mape'] * 100:.1f}% "
                    f"({sel['improvement'] * 100:+.1f}% vs naive). "
                    + ("It beats naive by at least 2%, so it is used." if sel["beats_naive"] else
                       "No model beat naive by 2%, so the honest random walk is used: no price direction is assumed."))},
        {"signal": "Forecast band reliability", "value": f"{coverage * 100:.0f}% of past outcomes inside P10 to P90",
         "detail": f"Target is about 80%. Measured on horizons 1 to {req.need_by_month} months, residual scale {sel['residual_scale']:.2f}."},
        {"signal": "Price volatility (12 month, annualised)", "value": f"{vol['current_annual_vol'] * 100:.1f}%",
         "detail": f"5-year average {vol['avg_5y_annual_vol'] * 100:.1f}% (now {vol['ratio']:.2f}x the average)."},
        {"signal": "Rupee trend (USD/INR, 3 months)", "value": f"{fx3 * 100:+.2f}%",
         "detail": f"USD/INR at {ctx.series['usdinr'].iloc[-1]:.2f}. A weaker rupee raises import cost."},
        {"signal": "News risk index", "value": f"{ctx.news_index:+.2f}",
         "detail": (f"Range -1 (prices likely to ease) to +1 (supply shock). Applied as a heuristic drift of "
                    f"{ctx.news_index * assumptions()['news']['max_news_drift'] * 100:+.2f}% per month for "
                    f"{ctx.news_horizon} month(s). Not a trained effect."), "headlines": top},
        {"signal": "Price weather", "value": f"{weather['probability'] * 100:.0f}%",
         "detail": weather["text"] + " (INR price, from 2,000 simulated paths)."},
        {"signal": "Stock runway", "value": f"{runway:.1f} months",
         "detail": f"First delivery of this plan arrives in month {first_delivery}. "
                   + ("Stock lasts until then." if runway >= first_delivery else "Stock runs out before it arrives.")},
        {"signal": "Expected saving vs just in time", "value": f"INR {best['saving_vs_jit_inr']:,.0f}",
         "detail": f"{best['saving_vs_jit_pct']:+.2f}% of expected landed cost. P90 cost INR {best['p90_cost_inr']:,.0f}."},
        {"signal": "Assumptions used", "value": f"duty {a.duty_pct * 100:.1f}%, freight INR {a.freight_inr_per_kg:.2f}/kg",
         "detail": f"Holding cost {a.holding_cost_pct_per_month * 100:.1f}% per month, risk weight lambda {inp.lam} "
                   f"({req.risk_appetite} risk appetite), risk measured as "
                   + ("P90 regret versus the best single month in hindsight" if inp.risk_measure == "regret"
                      else "P90 cost minus expected cost") + ". Demo assumptions, not official rates."},
        {"signal": "Data used", "value": "Synthetic" if synthetic else "Real",
         "detail": f"Monthly prices to {ctx.series.index[-1]:%b %Y}. "
                   + ("Some price or FX data is synthetic. " if synthetic else "")
                   + ("News is bundled sample headlines." if sample_news else "")},
    ]
    reasons = []
    if not sel["beats_naive"]:
        reasons.append("No forecast model beats the naive random walk, so price direction is not predicted.")
    if synthetic:
        reasons.append("Synthetic data in use.")
    if reasons:
        level = "LOW"
    elif 0.7 <= coverage <= 0.9:
        level, reasons = "HIGH", [f"Model beats naive and forecast bands are well calibrated ({coverage * 100:.0f}% coverage)."]
    else:
        level, reasons = "MEDIUM", [f"Model beats naive but band coverage is {coverage * 100:.0f}% (target 80%)."]
    return why, {"level": level, "reasons": reasons, "coverage": coverage}


# ----------------------------------------------------------------------------- headline to impact

def _now_share(rec: dict) -> float:
    return sum(r["percent"] for r in rec["schedule"] if r["month"] == 0)


def _weighted_month(rec: dict) -> float:
    return sum(r["percent"] * r["month"] for r in rec["schedule"]) / 100.0


def impact_diff(before: dict, after: dict, signal: dict) -> str:
    """Plain-language explanation of how the recommendation changed."""
    nb = len(before["forecast"]["mean"]) - 1
    k = min(int(before["price_weather"]["months"]), nb)
    move = after["forecast"]["mean"][k] / before["forecast"]["mean"][k] - 1
    if not signal.get("relevant"):
        return ("The headline was not recognised as price-moving for this material, so the plan is unchanged. "
                f"({signal.get('reason', '')})")
    kind = {"up": "a supply-side shock", "down": "easing supply or weaker demand"}.get(signal["direction"], "neutral news")
    b_now, a_now = _now_share(before), _now_share(after)
    cause = (f"because {kind} moved the expected price in {k} month{'s' if k > 1 else ''} by about {move * 100:+.1f}% "
             f"(news index {before['news_index']:+.2f} to {after['news_index']:+.2f})")
    if abs(a_now - b_now) >= 0.5:
        verb = "rose" if a_now > b_now else "fell"
        text = f"Buy-now share {verb} from {b_now:.0f}% to {a_now:.0f}% {cause}."
    elif before["summary"] != after["summary"]:
        text = f"Plan changed from '{before['summary']}' to '{after['summary']}' {cause}."
    else:
        text = (f"Plan unchanged ({after['summary']}). The headline moved the expected price by about "
                f"{move * 100:+.1f}%, which is not enough to change the timing.")
        same_side = (before["news_index"] > 0) == (signal["direction"] == "up")
        if abs(before["news_index"]) >= 0.85 and same_side:
            text += (f" Recent news had already pushed the news index to {before['news_index']:+.2f}, close to its "
                     "cap of 1, so this risk is largely in the forecast already.")
    pw_b, pw_a = before["price_weather"]["probability"], after["price_weather"]["probability"]
    text += f" Chance of a rise above {before['price_weather']['threshold'] * 100:.0f}%: {pw_b * 100:.0f}% to {pw_a * 100:.0f}%."
    return text


def headline_impact(headline: str, req: PurchaseRequest, material: str | None = None) -> dict:
    """Extract the headline's signal, add it at full freshness, and compare recommendations."""
    from .market import build_context, headline_as_item
    from .news_signal import extract_one

    sig = extract_one(headline, material)
    mat = material or sig.material or req.material
    if sig.material is None or (material and sig.material != material):
        sig = sig.model_copy(update={"material": mat})
    req = req.model_copy(update={"material": mat})
    before = recommend(req, ctx=build_context(mat), store=False)
    after = recommend(req, ctx=build_context(mat, extra_items=[headline_as_item(mat, headline, sig)]), store=False)
    signal = sig.model_dump()
    out = {"headline": headline, "material": mat, "signal": signal, "before": before, "after": after,
           "diff": impact_diff(before, after, signal),
           "note": "The news effect is a heuristic overlay (drift shift and wider bands), not a trained model."}
    rid = db.new_id()
    out["id"], out["created_at"] = rid, db.now_iso()
    db.insert_row("recommendations", {"id": rid, "created_at": out["created_at"], "kind": "headline_impact",
                                      "request_json": json.dumps({"headline": headline, "material": mat,
                                                                  "request": req.model_dump()}),
                                      "result_json": json.dumps({k: out[k] for k in ("signal", "diff", "note")}
                                                                | {"before_summary": before["summary"],
                                                                   "after_summary": after["summary"]}, default=str)})
    return out
