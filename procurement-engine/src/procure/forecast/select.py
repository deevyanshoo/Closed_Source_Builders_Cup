"""Walk-forward evaluation of all models and per-material model selection.

At each origin in the last `eval_months` months, every model is fitted on data up to
that origin only, paths are simulated, and P10/P50/P90 of the INR price are compared
with what actually happened 1 to 6 months later.
"""
from __future__ import annotations

import logging
import time

import numpy as np
import pandas as pd

from .. import db
from ..features import load_series
from ..settings import assumptions, material_keys
from .models import MODELS
from .simulate import simulate

log = logging.getLogger(__name__)

EVAL_PATHS = 1000


def walk_forward(df: pd.DataFrame, material: str, model: str, eval_months: int, horizon: int,
                 residual_scale: float = 1.0, end: int | None = None) -> pd.DataFrame:
    """One row per (origin, horizon) with forecast quantiles and the realised price.

    Origins are the `eval_months` months ending at position `end` (default: last row).
    """
    n = len(df) if end is None else end
    rows = []
    for o in range(max(n - eval_months - 1, 24), n - 1):
        hist = df.iloc[: o + 1]
        sim = simulate(hist, material, model, horizon=horizon, n_paths=EVAL_PATHS, seed=o,
                       residual_scale=residual_scale,
                       refit_every=assumptions()["forecast"]["gbm_refit_every"])
        q10, q50, q90 = sim.quantiles()
        p0 = float(hist["inr_per_kg"].iloc[-1])
        for h in range(1, horizon + 1):
            if o + h >= n:
                break
            rows.append({"origin": df.index[o], "h": h, "p0": p0, "p10": q10[h], "p50": q50[h],
                         "p90": q90[h], "actual": float(df["inr_per_kg"].iloc[o + h])})
    return pd.DataFrame(rows)


def metrics_from(wf: pd.DataFrame, model: str) -> pd.DataFrame:
    """MAPE, directional accuracy and P10-P90 coverage per horizon."""
    out = []
    for h, g in wf.groupby("h"):
        mape = float(np.mean(np.abs(g["p50"] - g["actual"]) / g["actual"]))
        if model == "naive":
            dacc = None  # a zero-drift forecast makes no directional call
        else:
            dacc = float(np.mean(np.sign(g["p50"] - g["p0"]) == np.sign(g["actual"] - g["p0"])))
        cov = float(np.mean((g["actual"] >= g["p10"]) & (g["actual"] <= g["p90"])))
        out.append({"horizon": int(h), "mape": mape, "directional_acc": dacc, "coverage": cov, "n_obs": len(g)})
    return pd.DataFrame(out)


def calibrate_scale(df: pd.DataFrame, material: str, eval_months: int, horizon: int) -> float:
    """Residual scale chosen on the window BEFORE the evaluation window (no peeking).

    Picks the scale from a small grid whose naive-model P10-P90 coverage is closest to 80%.
    """
    end = len(df) - eval_months
    if end < 24 + 24:
        return 1.0
    best, best_gap = 1.0, 1e9
    base = walk_forward(df, material, "naive", 24, horizon, residual_scale=1.0, end=end)
    if base.empty:
        return 1.0
    for s in (0.8, 0.9, 1.0, 1.1, 1.2, 1.35, 1.5):
        # Coverage of a zero-drift model scales with the band width, so rescale analytically.
        lo = base["p50"] + (base["p10"] - base["p50"]) * s
        hi = base["p50"] + (base["p90"] - base["p50"]) * s
        cov = float(np.mean((base["actual"] >= lo) & (base["actual"] <= hi)))
        gap = abs(cov - 0.8)
        if gap < best_gap - 1e-9:
            best, best_gap = s, gap
    return best


def evaluate_material(material: str, calibrate: bool = True) -> dict:
    cfg = assumptions()["forecast"]
    horizon = assumptions()["simulation"]["horizon"]
    df = load_series(material)
    t0 = time.perf_counter()
    scale = calibrate_scale(df, material, cfg["eval_months"], horizon) if calibrate else 1.0
    now = db.now_iso()
    all_metrics = []
    for model in MODELS:
        wf = walk_forward(df, material, model, cfg["eval_months"], horizon, residual_scale=scale)
        m = metrics_from(wf, model)
        m.insert(0, "model", model)
        m.insert(0, "material", material)
        m["evaluated_at"] = now
        all_metrics.append(m)
    metrics = pd.concat(all_metrics, ignore_index=True)
    db.replace_rows("model_metrics", metrics[["material", "model", "horizon", "mape", "directional_acc",
                                              "coverage", "n_obs", "evaluated_at"]],
                    "material = ?", [material])
    avg = metrics.groupby("model")["mape"].mean()
    naive_mape = float(avg["naive"])
    challengers = avg.drop("naive")
    best_model = str(challengers.idxmin())
    best_mape = float(challengers.min())
    improvement = 1 - best_mape / naive_mape
    beats = improvement >= cfg["min_improvement_vs_naive"]
    sel = {"material": material, "selected_model": best_model if beats else "naive", "best_model": best_model,
           "naive_mape": naive_mape, "best_mape": best_mape, "improvement": improvement,
           "beats_naive": bool(beats), "residual_scale": scale, "evaluated_at": now}
    db.replace_rows("model_selection", pd.DataFrame([sel]), "material = ?", [material])
    sel["seconds"] = round(time.perf_counter() - t0, 1)
    log.info("Model selection %s: %s", material, sel)
    return sel


def evaluate_all() -> list[dict]:
    return [evaluate_material(m) for m in material_keys()]


def get_selection(material: str) -> dict:
    """Stored selection; evaluates on demand if missing."""
    df = db.query_df("SELECT * FROM model_selection WHERE material = ?", [material])
    if df.empty:
        evaluate_material(material)
        df = db.query_df("SELECT * FROM model_selection WHERE material = ?", [material])
    row = df.iloc[0].to_dict()
    row["beats_naive"] = bool(row["beats_naive"])
    return row


def get_metrics(material: str | None = None) -> pd.DataFrame:
    if material:
        return db.query_df("SELECT * FROM model_metrics WHERE material = ? ORDER BY model, horizon", [material])
    return db.query_df("SELECT * FROM model_metrics ORDER BY material, model, horizon")
