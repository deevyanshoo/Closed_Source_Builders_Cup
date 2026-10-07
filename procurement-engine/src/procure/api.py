"""FastAPI backend for the procurement engine."""
from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from typing import Any, Literal

import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import actions as act
from . import db
from .backtest import run_backtest
from .decision import PurchaseRequest, headline_impact, price_weather, recommend
from .production.http_comparison import router as recovery_comparison_router
from .features import load_series
from .forecast.models import GBM_BACKEND
from .forecast.simulate import PlanningCalendarUnavailable
from .forecast.select import evaluate_all, get_metrics, get_selection
from .ingest.refresh import refresh_all
from .market import build_context, provenance
from .settings import assumptions, get_settings, material_cfg, material_keys, materials_config

log = logging.getLogger("procure.api")
_REFRESH_LOCK = threading.Lock()


# ----------------------------------------------------------------------------- models

class SourceStatus(BaseModel):
    used: str = Field(description="live, cache, synthetic or sample")
    fetched_at: str
    rows: int
    status: str
    message: str


class HealthResponse(BaseModel):
    status: str
    db_engine: str
    offline: bool
    gemini_enabled: bool
    gbm_backend: str
    latest_price_month: str | None
    latest_fx_month: str | None
    sources: dict[str, SourceStatus]
    any_synthetic: bool
    any_sample: bool


class MaterialInfo(BaseModel):
    key: str
    display_name: str
    source_series: str
    source_unit: str
    keywords: list[str]
    landed_cost: dict[str, float]


class RefreshResponse(BaseModel):
    sources: list[dict[str, Any]]
    price_summary: list[dict[str, Any]]
    timings_s: dict[str, float]
    model_selection: list[dict[str, Any]] | None = None


class PricePoint(BaseModel):
    month: str
    usd_per_unit: float
    usdinr: float
    inr_per_kg: float
    is_synthetic: bool


class PricesResponse(BaseModel):
    material: str
    unit: str
    price_source: str
    fx_source: str
    any_synthetic: bool
    points: list[PricePoint]


class ForecastPoint(BaseModel):
    month: str
    months_ahead: int
    p10: float
    p50: float
    p90: float
    mean: float


class MetricRow(BaseModel):
    model: str
    horizon: int
    mape: float
    directional_acc: float | None
    coverage: float
    n_obs: int


class ForecastResponse(BaseModel):
    material: str
    unit: str = "INR per kg"
    model_used: str
    beats_naive: bool
    selection: dict[str, Any]
    news_index_applied: float
    points: list[ForecastPoint]
    metrics: list[MetricRow]
    note: str
    observation_end: str
    planning_month: str
    bridge_months: int
    planning_offset_zero_basis: str
    publication_at: str | None
    calibration_limit: str


class NewsItemOut(BaseModel):
    title: str
    url: str | None
    domain: str | None
    seen_at: str
    relevant: bool
    direction: str
    severity: float
    horizon_months: int
    reason: str
    extractor: str
    is_sample: bool
    source: str | None
    contribution: float | None = None
    duplicate_of_story: bool | None = None


class NewsResponse(BaseModel):
    material: str
    news_index: float
    news_horizon_months: int
    source_used: str | None
    items: list[NewsItemOut]
    note: str


class ScheduleRow(BaseModel):
    month: int
    month_label: str
    calendar_month: str
    percent: float
    kg: float
    expected_inr_per_kg: float
    expected_landed_inr_per_kg: float
    p10_inr_per_kg: float
    p50_inr_per_kg: float
    p90_inr_per_kg: float


class ComparisonRow(BaseModel):
    name: str
    schedule: str
    percents: list[float]
    expected_cost_inr: float
    p90_cost_inr: float
    p90_regret_inr: float
    stockout_penalty_inr: float
    score: float
    saving_vs_jit_inr: float
    saving_vs_jit_pct: float


class PriceWeather(BaseModel):
    probability: float
    threshold: float
    months: int
    text: str


class WhyItem(BaseModel):
    signal: str
    value: str
    detail: str
    headlines: list[dict[str, Any]] | None = None


class Confidence(BaseModel):
    level: Literal["LOW", "MEDIUM", "HIGH"]
    reasons: list[str]
    coverage: float | None


class RecommendationResponse(BaseModel):
    id: str | None = None
    created_at: str | None = None
    material: str
    display_name: str
    action: Literal["BUY NOW", "SPLIT BUY", "WAIT"]
    summary: str
    feasible_months: list[int]
    schedule: list[ScheduleRow]
    comparison: list[ComparisonRow]
    price_weather: PriceWeather
    why: list[WhyItem]
    confidence: Confidence
    warnings: list[str]
    current_inr_per_kg: float
    latest_data_month: str
    forecast: dict[str, Any]
    news_index: float
    news_horizon_months: int
    model: dict[str, Any]
    provenance: dict[str, Any]


class HeadlineImpactRequest(BaseModel):
    material: str | None = Field(None, description="Optional. Detected from the headline if omitted.")
    headline: str = Field(..., min_length=5)
    request: PurchaseRequest = Field(default_factory=PurchaseRequest)

    model_config = {"json_schema_extra": {"examples": [{
        "material": "copper", "headline": "Major copper mine strike halts 10% of global supply",
        "request": {"material": "copper", "quantity_kg": 50000, "need_by_month": 3, "current_stock_kg": 20000,
                    "monthly_consumption_kg": 15000, "supplier_lead_time_months": 1, "risk_appetite": "medium"}}]}}


class HeadlineImpactResponse(BaseModel):
    id: str
    created_at: str
    headline: str
    material: str
    signal: dict[str, Any]
    before: RecommendationResponse
    after: RecommendationResponse
    diff: str
    note: str


class BacktestResponse(BaseModel):
    material: str
    model: str
    risk_measure: str
    months: int
    period: str
    request: dict[str, Any]
    summary: dict[str, Any]
    rows: list[dict[str, Any]]
    notes: list[str]
    seconds: float


class RFQRequest(BaseModel):
    recommendation_id: str

    model_config = {"json_schema_extra": {"examples": [{"recommendation_id": "a1b2c3d4e5f6"}]}}


class DecisionBody(BaseModel):
    reason: str | None = Field(None, description="Optional reason, stored for future learning")


class ActionResponse(BaseModel):
    id: str
    recommendation_id: str
    type: str
    status: str
    payload: dict[str, Any]
    reason: str | None
    created_at: str
    updated_at: str


class AuditEvent(BaseModel):
    time: str
    event: str
    id: str
    recommendation_id: str | None
    status: str | None
    summary: str | None
    input: dict[str, Any] | None
    reason: str | None


class OverviewCard(BaseModel):
    material: str
    display_name: str
    latest_month: str
    inr_per_kg: float
    usd_per_unit: float
    usdinr: float
    change_1m_pct: float
    change_3m_pct: float
    price_weather: PriceWeather
    news_index: float
    model_used: str
    price_source: str
    news_source: str
    is_synthetic: bool
    is_sample_news: bool


# ----------------------------------------------------------------------------- app

def _ensure_data() -> None:
    """On startup: ingest if the database is empty, evaluate models if never evaluated."""
    if db.query_df("SELECT COUNT(*) AS n FROM prices_monthly")["n"].iloc[0] == 0:
        log.info("No data yet; running initial ingestion")
        refresh_all()
    elif db.query_df("SELECT COUNT(*) AS n FROM model_selection")["n"].iloc[0] < len(material_keys()):
        evaluate_all()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    _ensure_data()
    yield


app = FastAPI(title="AI Procurement Engine", version="0.1.0", lifespan=lifespan,
              description="When to buy imported raw materials, how much, and why. Prototype.")
app.include_router(recovery_comparison_router)


@app.exception_handler(PlanningCalendarUnavailable)
async def planning_calendar_unavailable(_request, exc: PlanningCalendarUnavailable):
    return JSONResponse(status_code=503, content={"detail": {"code": "planning_calendar_unavailable",
                                                            "message": str(exc)}})


def _check_material(material: str) -> None:
    try:
        material_cfg(material)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    prov = provenance()
    pm = db.query_df("SELECT MAX(month) AS m FROM prices_monthly")["m"].iloc[0]
    fm = db.query_df("SELECT MAX(month) AS m FROM fx_monthly")["m"].iloc[0]
    sources = {k: SourceStatus(**v) for k, v in prov.items() if isinstance(v, dict)}
    s = get_settings()
    return HealthResponse(status="ok", db_engine=db.ENGINE, offline=s.offline, gemini_enabled=bool(s.gemini_api_key),
                          gbm_backend=GBM_BACKEND, latest_price_month=pm, latest_fx_month=fm, sources=sources,
                          any_synthetic=prov.get("any_synthetic", False), any_sample=prov.get("any_sample", False))


@app.get("/materials", response_model=list[MaterialInfo])
def materials() -> list[MaterialInfo]:
    lc = assumptions()["landed_cost"]
    out = []
    for k, c in materials_config()["materials"].items():
        out.append(MaterialInfo(key=k, display_name=c["display_name"], source_series=c["source_series"],
                                source_unit=c["source_unit"], keywords=c["keywords"],
                                landed_cost={**lc["materials"].get(k, {}),
                                             "holding_cost_pct_per_month": lc["holding_cost_pct_per_month"]}))
    return out


@app.post("/ingest/refresh", response_model=RefreshResponse)
def ingest_refresh(evaluate_models: bool = True) -> RefreshResponse:
    if not _REFRESH_LOCK.acquire(blocking=False):
        raise HTTPException(409, "A refresh is already running")
    try:
        from .backtest import clear_cache
        clear_cache()
        rep = refresh_all(evaluate_models=evaluate_models)
    finally:
        _REFRESH_LOCK.release()
    return RefreshResponse(sources=rep["sources"], price_summary=rep["price_summary"], timings_s=rep["timings_s"],
                           model_selection=rep.get("model_selection"))


@app.get("/prices/{material}", response_model=PricesResponse)
def prices(material: str) -> PricesResponse:
    _check_material(material)
    df = load_series(material)
    pts = [PricePoint(month=m.strftime("%Y-%m-%d"), usd_per_unit=float(r.usd_per_unit), usdinr=float(r.usdinr),
                      inr_per_kg=round(float(r.inr_per_kg), 4), is_synthetic=bool(r.is_synthetic))
           for m, r in df.iterrows()]
    return PricesResponse(material=material, unit=material_cfg(material)["source_unit"],
                          price_source=str(df["price_source"].iloc[-1]), fx_source=str(df["fx_source"].iloc[-1]),
                          any_synthetic=bool(df["is_synthetic"].any()), points=pts)


@app.get("/forecast/{material}", response_model=ForecastResponse)
def forecast(material: str, horizon: int = Query(6, ge=1, le=6), include_news: bool = True) -> ForecastResponse:
    _check_material(material)
    ctx = build_context(material, use_news=include_news)
    paths = ctx.sim.inr_paths
    q10, q50, q90 = np.quantile(paths, [0.1, 0.5, 0.9], axis=0)
    mean = paths.mean(axis=0)
    pts = [ForecastPoint(month=ctx.sim.months[k].strftime("%Y-%m-%d"), months_ahead=k, p10=round(q10[k], 2),
                         p50=round(q50[k], 2), p90=round(q90[k], 2), mean=round(mean[k], 2))
           for k in range(horizon + 1)]
    met = get_metrics(material)
    rows = [MetricRow(model=r["model"], horizon=int(r["horizon"]), mape=float(r["mape"]),
                      directional_acc=None if r["directional_acc"] != r["directional_acc"] or r["directional_acc"] is None
                      else float(r["directional_acc"]), coverage=float(r["coverage"]), n_obs=int(r["n_obs"]))
            for r in met.to_dict(orient="records")]
    sel = ctx.selection
    note = ("No model beat the naive random walk by 2% in walk-forward testing, so the forecast assumes no "
            "price direction; the band shows the range of likely outcomes." if not sel["beats_naive"] else
            f"{sel['selected_model']} beat naive by {sel['improvement'] * 100:.1f}% in walk-forward testing.")
    if include_news and ctx.news_index:
        note += f" A heuristic news overlay (index {ctx.news_index:+.2f}) is applied."
    return ForecastResponse(material=material, model_used=sel["selected_model"], beats_naive=bool(sel["beats_naive"]),
                            selection=sel, news_index_applied=round(ctx.news_index if include_news else 0.0, 4),
                            points=pts, metrics=rows, note=note,
                            **{key: ctx.sim.meta[key] for key in ("observation_end", "planning_month", "bridge_months",
                                                              "planning_offset_zero_basis", "publication_at", "calibration_limit")})


@app.get("/news/{material}", response_model=NewsResponse)
def news(material: str) -> NewsResponse:
    _check_material(material)
    ctx = build_context(material)
    scored = {s["id"]: s for s in ctx.news_scored}
    items = []
    for it in ctx.news_items:
        s = scored.get(it["id"], {})
        items.append(NewsItemOut(title=it["title"], url=it.get("url"), domain=it.get("domain"), seen_at=str(it["seen_at"]),
                                 relevant=bool(it["relevant"]), direction=it["direction"], severity=float(it["severity"]),
                                 horizon_months=int(it["horizon_months"]), reason=it["reason"], extractor=it["extractor"],
                                 is_sample=bool(it["is_sample"]), source=it.get("source"),
                                 contribution=s.get("contribution"), duplicate_of_story=s.get("duplicate_of_story")))
    used = ctx.provenance.get(f"news:{material}", {}).get("used")
    return NewsResponse(material=material, news_index=round(ctx.news_index, 4), news_horizon_months=ctx.news_horizon,
                        source_used=used, items=items,
                        note="Signals are extracted by rules unless a Gemini key is set. The index is a heuristic.")


@app.post("/recommend", response_model=RecommendationResponse)
def recommend_endpoint(req: PurchaseRequest) -> dict:
    _check_material(req.material)
    return recommend(req)


@app.post("/headline-impact", response_model=HeadlineImpactResponse)
def headline_impact_endpoint(body: HeadlineImpactRequest) -> dict:
    if body.material:
        _check_material(body.material)
    return headline_impact(body.headline, body.request, body.material)


@app.get("/backtest/{material}", response_model=BacktestResponse)
def backtest(material: str, model: Literal["selected", "naive", "ets", "gbm"] = "selected",
             risk_measure: Literal["regret", "overshoot"] | None = None,
             months: int = Query(36, ge=6, le=120)) -> dict:
    _check_material(material)
    return run_backtest(material, model=model, months=months, risk_measure=risk_measure)


@app.post("/actions/rfq", response_model=ActionResponse)
def create_rfq(body: RFQRequest) -> dict:
    try:
        return act.create_rfq(body.recommendation_id)
    except act.ActionError as exc:
        raise HTTPException(404 if "not found" in str(exc) else 400, str(exc)) from exc


def _decide(action_id: str, status: str, body: DecisionBody | None) -> dict:
    try:
        return act.set_status(action_id, status, body.reason if body else None)
    except act.ActionError as exc:
        raise HTTPException(404 if "not found" in str(exc) else 409, str(exc)) from exc


@app.post("/actions/{action_id}/approve", response_model=ActionResponse)
def approve(action_id: str, body: DecisionBody | None = None) -> dict:
    return _decide(action_id, "approved", body)


@app.post("/actions/{action_id}/reject", response_model=ActionResponse)
def reject(action_id: str, body: DecisionBody | None = None) -> dict:
    return _decide(action_id, "rejected", body)


@app.get("/actions", response_model=list[ActionResponse])
def list_actions() -> list[dict]:
    return act.list_actions()


@app.get("/audit", response_model=list[AuditEvent])
def audit(limit: int = Query(200, ge=1, le=2000)) -> list[dict]:
    return act.audit_log(limit)


@app.get("/metrics", response_model=list[dict[str, Any]])
def metrics() -> list[dict]:
    met = get_metrics()
    met = met.astype(object).where(met.notna(), None)
    sel = {m: get_selection(m) for m in material_keys()}
    out = met.to_dict(orient="records")
    for r in out:
        r["selected"] = sel[r["material"]]["selected_model"] == r["model"]
    return out


@app.get("/overview", response_model=list[OverviewCard])
def overview() -> list[OverviewCard]:
    cards = []
    thr = float(assumptions()["risk"]["price_rise_threshold"])
    for m in material_keys():
        ctx = build_context(m)
        s = ctx.series
        p = s["inr_per_kg"]
        news_used = ctx.provenance.get(f"news:{m}", {}).get("used", "none")
        cards.append(OverviewCard(
            material=m, display_name=material_cfg(m)["display_name"], latest_month=s.index[-1].strftime("%b %Y"),
            inr_per_kg=round(float(p.iloc[-1]), 2), usd_per_unit=round(float(s["usd_per_unit"].iloc[-1]), 2),
            usdinr=round(float(s["usdinr"].iloc[-1]), 3),
            change_1m_pct=round(float(p.iloc[-1] / p.iloc[-2] - 1) * 100, 2),
            change_3m_pct=round(float(p.iloc[-1] / p.iloc[-4] - 1) * 100, 2),
            price_weather=PriceWeather(**price_weather(ctx.sim.inr_paths, 3, thr)),
            news_index=round(ctx.news_index, 3), model_used=ctx.selection["selected_model"],
            price_source=ctx.provenance.get("prices", {}).get("used", "none"), news_source=news_used,
            is_synthetic=bool(s["is_synthetic"].any()), is_sample_news=news_used == "sample"))
    return cards
