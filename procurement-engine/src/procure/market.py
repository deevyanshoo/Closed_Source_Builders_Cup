"""Market context for one material: data, selected model, news index and simulated paths."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import db
from .features import load_series
from .forecast.select import get_selection
from .forecast.simulate import SimResult, simulate
from .news_signal import HeadlineSignal, news_index


@dataclass
class MarketContext:
    material: str
    series: pd.DataFrame
    selection: dict
    news_items: list[dict]
    news_index: float
    news_horizon: int
    news_scored: list[dict]
    sim: SimResult
    provenance: dict = field(default_factory=dict)

    @property
    def current_price(self) -> float:
        return float(self.series["inr_per_kg"].iloc[-1])


def load_news_items(material: str) -> list[dict]:
    df = db.query_df("SELECT * FROM news_items WHERE material = ? ORDER BY seen_at DESC", [material])
    return df.to_dict(orient="records")


def news_horizon_of(scored: list[dict], default: int = 2) -> int:
    """Contribution-weighted average horizon of the distinct stories driving the index."""
    use = [s for s in scored if not s.get("duplicate_of_story")]
    w = np.array([abs(s["contribution"]) for s in use])
    if not len(use) or w.sum() == 0:
        return default
    h = np.array([int(s.get("horizon_months") or default) for s in use])
    return int(np.clip(round(float((w * h).sum() / w.sum())), 1, 6))


def provenance() -> dict:
    """Which source (live, cache, synthetic, sample) was used most recently for each input."""
    runs = db.query_df("SELECT source, used, started_at, rows, status, message FROM ingest_runs "
                       "ORDER BY started_at DESC")
    latest = runs.drop_duplicates("source") if not runs.empty else runs
    out = {r["source"]: {"used": r["used"], "fetched_at": r["started_at"], "rows": int(r["rows"]),
                         "status": r["status"], "message": r["message"]}
           for r in latest.to_dict(orient="records")}
    out["any_synthetic"] = any(v["used"] == "synthetic" for k, v in out.items() if isinstance(v, dict))
    out["any_sample"] = any(v["used"] == "sample" for k, v in out.items() if isinstance(v, dict))
    return out


def headline_as_item(material: str, headline: str, sig: HeadlineSignal) -> dict:
    """A pasted headline as a news item at full freshness."""
    return {"id": "user_headline", "material": material, "title": headline, "url": "", "domain": "user input",
            "seen_at": datetime.now(timezone.utc).isoformat(), "relevant": sig.relevant,
            "direction": sig.direction, "severity": sig.severity, "horizon_months": sig.horizon_months,
            "reason": sig.reason, "extractor": sig.extractor, "is_sample": False, "source": "user"}


def build_context(material: str, extra_items: list[dict] | None = None, use_news: bool = True,
                  series: pd.DataFrame | None = None, model: str | None = None) -> MarketContext:
    """Assemble everything the decision engine needs for one material."""
    df = series if series is not None else load_series(material)
    sel = get_selection(material)
    items = load_news_items(material) if use_news else []
    if extra_items:
        items = list(extra_items) + items
    idx, scored = news_index(items) if use_news else (0.0, [])
    horizon = news_horizon_of(scored)
    sim = simulate(df, material, model or sel["selected_model"], news_index=idx, news_horizon=horizon,
                   residual_scale=float(sel.get("residual_scale") or 1.0))
    return MarketContext(material, df, sel, items, idx, horizon, scored, sim, provenance())
