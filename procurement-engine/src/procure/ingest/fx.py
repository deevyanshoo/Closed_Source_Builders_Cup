"""USD/INR from Frankfurter (ECB reference rates, keyless). Daily, aggregated to monthly."""
from __future__ import annotations

import json
import logging

import pandas as pd

from ..db import now_iso
from ..settings import assumptions
from . import synthetic
from .http import cache_latest, cache_time, cache_write, http_get
from .prices import FetchResult

log = logging.getLogger(__name__)

CACHE_PREFIX = "frankfurter_usdinr"
ENDPOINTS = [
    ("https://api.frankfurter.dev/v1/{start}..{end}", {"base": "USD", "symbols": "INR"}),
    ("https://api.frankfurter.app/{start}..{end}", {"from": "USD", "to": "INR"}),
]


def parse_rates(payload: dict) -> pd.Series:
    """Frankfurter JSON -> daily Series of USDINR indexed by date."""
    rates = payload.get("rates", {})
    s = pd.Series({pd.Timestamp(d): float(v["INR"]) for d, v in rates.items() if "INR" in v})
    return s.sort_index()


def to_monthly(daily: pd.Series) -> pd.DataFrame:
    monthly = daily.groupby(daily.index.to_period("M")).mean()
    return pd.DataFrame({"month": monthly.index.to_timestamp(), "usdinr": monthly.values})


def _is_thinned(daily: pd.Series) -> bool:
    """Frankfurter thins long ranges to weekly points. Detect a median gap above 3 days."""
    if len(daily) < 3:
        return True
    gaps = pd.Series(daily.index).diff().dt.days.dropna()
    return float(gaps.median()) > 3


def _fetch_range(base: str, params: dict, start: str, end: str) -> dict:
    return http_get(base.format(start=start, end=end), params=params).json()


def fetch_daily_live() -> tuple[pd.Series, str, dict]:
    """Probe endpoints; fetch full range, fall back to year-by-year if thinned."""
    start_year = pd.Timestamp(assumptions()["forecast"]["history_start"]).year
    today = pd.Timestamp.today().normalize()
    last_err: Exception | None = None
    for base, params in ENDPOINTS:
        try:
            payload = _fetch_range(base, params, f"{start_year}-01-01", today.strftime("%Y-%m-%d"))
            daily = parse_rates(payload)
            if _is_thinned(daily):
                log.info("Frankfurter range response thinned; fetching year by year")
                parts, merged = [], {"rates": {}}
                for y in range(start_year, today.year + 1):
                    p = _fetch_range(base, params, f"{y}-01-01", f"{y}-12-31")
                    parts.append(parse_rates(p))
                    merged["rates"].update(p.get("rates", {}))
                daily = pd.concat(parts).sort_index()
                daily = daily[~daily.index.duplicated()]
                payload = merged
            return daily, base.split("/{")[0], payload
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            log.warning("Frankfurter endpoint %s failed: %s", base, exc)
    raise RuntimeError(f"All Frankfurter endpoints failed: {last_err}")


def fetch_fx() -> FetchResult:
    """Live, then cache, then synthetic."""
    errors = []
    try:
        daily, url, payload = fetch_daily_live()
        path = cache_write(CACHE_PREFIX, ".json", json.dumps(payload).encode())
        return FetchResult(to_monthly(daily), "live", "frankfurter_ecb", url, now_iso(),
                           f"{len(daily)} daily rates, cached {path.name}")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"live: {exc}")
    cached = cache_latest(CACHE_PREFIX, ".json")
    if cached is not None:
        try:
            daily = parse_rates(json.loads(cached.read_text()))
            return FetchResult(to_monthly(daily), "cache", "frankfurter_ecb", cached.name,
                               cache_time(cached).isoformat(), "Live failed; used cache. " + "; ".join(errors)[:300])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"cache: {exc}")
    return FetchResult(synthetic.synthetic_fx(), "synthetic", "synthetic_gbm", None, now_iso(),
                       "Live and cache failed; generated synthetic USD/INR. " + "; ".join(errors)[:300])
