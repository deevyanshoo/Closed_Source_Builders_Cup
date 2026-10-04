"""Run every ingestion source, store results with provenance, log an ingest_runs row each."""
from __future__ import annotations

import logging
import time

import pandas as pd

from .. import db
from ..news_signal import extract_many
from ..settings import material_keys
from .fx import fetch_fx
from .news import fetch_news
from .prices import FetchResult, fetch_prices, sanity_check, summarize

log = logging.getLogger(__name__)


def _record_run(source: str, res: FetchResult | None, started: str, status: str, rows: int,
                message: str) -> dict:
    row = {"id": db.new_id(), "source": source, "status": status, "rows": rows,
           "used": res.used if res else "none", "message": message[:1000], "started_at": started,
           "finished_at": db.now_iso(), "url": res.url if res else None}
    db.insert_row("ingest_runs", row)
    print(f"[ingest] {source:<16} used={row['used']:<9} rows={rows:<5} {message[:140]}")
    return row


def store_prices(res: FetchResult) -> int:
    df = res.df.copy()
    df["month"] = pd.to_datetime(df["month"]).dt.strftime("%Y-%m-%d")
    df["source"] = res.source
    df["is_synthetic"] = res.used == "synthetic"
    df["fetched_at"] = res.fetched_at
    df["url"] = res.url
    cols = ["material", "month", "usd_per_unit", "unit", "source", "is_synthetic", "fetched_at", "url"]
    return db.replace_rows("prices_monthly", df[cols], "1=1", [])


def store_fx(res: FetchResult) -> int:
    df = res.df.copy()
    df["month"] = pd.to_datetime(df["month"]).dt.strftime("%Y-%m-%d")
    df["source"] = res.source
    df["is_synthetic"] = res.used == "synthetic"
    df["fetched_at"] = res.fetched_at
    df["url"] = res.url
    return db.replace_rows("fx_monthly", df[["month", "usdinr", "source", "is_synthetic", "fetched_at", "url"]],
                           "1=1", [])


def store_news(material: str, res: FetchResult) -> int:
    df = res.df.copy()
    if df.empty:
        return 0
    signals = extract_many(df["title"].tolist(), material)
    df["relevant"] = [s.relevant and s.material == material for s in signals]
    df["direction"] = [s.direction for s in signals]
    df["severity"] = [s.severity for s in signals]
    df["horizon_months"] = [s.horizon_months for s in signals]
    df["reason"] = [s.reason for s in signals]
    df["extractor"] = [s.extractor for s in signals]
    df["is_sample"] = res.used == "sample"
    df["source"] = res.source
    df["material"] = material
    cols = ["id", "material", "title", "url", "domain", "seen_at", "relevant", "direction", "severity",
            "horizon_months", "reason", "extractor", "is_sample", "source"]
    return db.replace_rows("news_items", df[cols], "material = ?", [material])


def refresh_all(include_news: bool = True, evaluate_models: bool = True) -> dict:
    """Ingest prices, FX and news; then (optionally) re-run model evaluation."""
    report: dict = {"sources": [], "timings_s": {}}
    t0 = time.perf_counter()

    started = db.now_iso()
    res = fetch_prices()
    n = store_prices(res)
    warn = sanity_check(res.df)
    msg = res.message + (" | WARN: " + "; ".join(warn) if warn else "")
    report["sources"].append(_record_run("prices", res, started, "ok" if not warn else "warn", n, msg))
    report["price_summary"] = summarize(res.df).reset_index().to_dict(orient="records")

    started = db.now_iso()
    res = fetch_fx()
    n = store_fx(res)
    report["sources"].append(_record_run("fx", res, started, "ok", n, res.message))
    report["timings_s"]["prices_fx"] = round(time.perf_counter() - t0, 2)

    if include_news:
        t1 = time.perf_counter()
        for mat in material_keys():
            started = db.now_iso()
            try:
                res = fetch_news(mat)
                n = store_news(mat, res)
                report["sources"].append(_record_run(f"news:{mat}", res, started, "ok", n, res.message))
            except Exception as exc:  # noqa: BLE001
                report["sources"].append(_record_run(f"news:{mat}", None, started, "error", 0, str(exc)))
        report["timings_s"]["news"] = round(time.perf_counter() - t1, 2)

    # clear in-memory forecast caches that depend on data
    from .. import forecast  # local import to avoid cycles
    forecast.clear_caches()

    if evaluate_models:
        t2 = time.perf_counter()
        from ..forecast.select import evaluate_all
        report["model_selection"] = evaluate_all()
        report["timings_s"]["model_eval"] = round(time.perf_counter() - t2, 2)
    report["timings_s"]["total"] = round(time.perf_counter() - t0, 2)
    return report


if __name__ == "__main__":  # pragma: no cover
    import json
    logging.basicConfig(level=logging.INFO)
    out = refresh_all()
    print(json.dumps({k: v for k, v in out.items() if k != "sources"}, indent=2, default=str))
