"""World Bank Pink Sheet (monthly commodity prices, keyless)."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from ..db import now_iso
from ..settings import assumptions, materials_config
from .http import cache_latest, cache_time, cache_write, http_get
from . import synthetic

log = logging.getLogger(__name__)

LANDING_URL = "https://www.worldbank.org/en/research/commodity-markets"
KNOWN_URL = ("https://thedocs.worldbank.org/en/doc/5d903e848db1d1b83e0ec8f744e55570-0350012021/"
             "related/CMO-Historical-Data-Monthly.xlsx")
CACHE_PREFIX = "pinksheet_monthly"


@dataclass
class FetchResult:
    """Outcome of one ingestion source, including provenance."""

    df: pd.DataFrame
    used: str  # live, cache, synthetic, sample
    source: str
    url: str | None
    fetched_at: str
    message: str = ""
    extra: dict = field(default_factory=dict)


def discover_monthly_url() -> str:
    """Find the monthly xlsx link on the World Bank landing page."""
    html = http_get(LANDING_URL).text
    links = re.findall(r'href="([^"]+\.xlsx)"', html, flags=re.IGNORECASE)
    monthly = [l for l in links if "monthly" in l.lower()]
    if not monthly:
        raise ValueError("No monthly xlsx link found on landing page")
    url = monthly[0]
    if url.startswith("/"):
        url = "https://www.worldbank.org" + url
    return url


def parse_pinksheet(path: Path, start: str | None = None) -> pd.DataFrame:
    """Parse the 'Monthly Prices' sheet into long format.

    Returns columns: material, month (Timestamp, month start), usd_per_unit, unit.
    The header row is detected as the first row containing 'Copper'.
    """
    xls = pd.ExcelFile(path)
    sheet = next((s for s in xls.sheet_names if "monthly prices" in s.lower()), None)
    if sheet is None:
        raise ValueError(f"No 'Monthly Prices' sheet in {path.name}: {xls.sheet_names}")
    raw = pd.read_excel(xls, sheet_name=sheet, header=None)
    header_idx = None
    for i in range(min(len(raw), 40)):
        if any(isinstance(v, str) and v.strip() == "Copper" for v in raw.iloc[i].tolist()):
            header_idx = i
            break
    if header_idx is None:
        raise ValueError("Could not find header row containing 'Copper'")
    headers = [str(v).strip() if isinstance(v, str) else "" for v in raw.iloc[header_idx]]
    units = [str(v).strip() if isinstance(v, str) else "" for v in raw.iloc[header_idx + 1]]
    body = raw.iloc[header_idx + 1:].copy()
    date_col = body.iloc[:, 0].astype(str).str.strip()
    is_date = date_col.str.match(r"^\d{4}M\d{2}$")
    body = body[is_date.values]
    months = pd.to_datetime(date_col[is_date].str.replace("M", "-", regex=False) + "-01")

    frames = []
    for key, cfg in materials_config()["materials"].items():
        series = cfg["source_series"]
        if series not in headers:
            raise ValueError(f"Series '{series}' for {key} not found in Pink Sheet headers")
        col = headers.index(series)
        values = pd.to_numeric(body.iloc[:, col], errors="coerce").values
        frames.append(pd.DataFrame({
            "material": key,
            "month": months.values,
            "usd_per_unit": values,
            "unit": units[col] or cfg["source_unit"],
        }))
    out = pd.concat(frames, ignore_index=True).dropna(subset=["usd_per_unit"])
    start = start or assumptions()["forecast"]["history_start"]
    out = out[out["month"] >= pd.Timestamp(start)].reset_index(drop=True)
    return out


def fetch_prices() -> FetchResult:
    """Live download, then the latest cached xlsx, then synthetic."""
    fetched_at = now_iso()
    errors: list[str] = []
    # 1) live
    for url_getter in (discover_monthly_url, lambda: KNOWN_URL):
        try:
            url = url_getter()
            content = http_get(url, timeout=60).content
            path = cache_write(CACHE_PREFIX, ".xlsx", content)
            df = parse_pinksheet(path)
            return FetchResult(df, "live", "worldbank_pinksheet", url, fetched_at,
                               f"Downloaded {path.name}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"live: {exc}")
    # 2) cache
    cached = cache_latest(CACHE_PREFIX, ".xlsx")
    if cached is not None:
        try:
            df = parse_pinksheet(cached)
            return FetchResult(df, "cache", "worldbank_pinksheet", str(cached.name),
                               cache_time(cached).isoformat(),
                               f"Live failed; used cache {cached.name}. " + "; ".join(errors)[:300])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"cache: {exc}")
    # 3) synthetic
    df = synthetic.synthetic_prices()
    return FetchResult(df, "synthetic", "synthetic_gbm", None, fetched_at,
                       "Live and cache failed; generated synthetic prices. " + "; ".join(errors)[:300])


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    """Rows, date range and last value per material, for the ingest report."""
    g = df.sort_values("month").groupby("material")
    return pd.DataFrame({
        "rows": g.size(),
        "first": g["month"].min().dt.strftime("%Y-%m"),
        "last": g["month"].max().dt.strftime("%Y-%m"),
        "last_usd": g["usd_per_unit"].last().round(2),
        "min_usd": g["usd_per_unit"].min().round(2),
        "max_usd": g["usd_per_unit"].max().round(2),
    })


def sanity_check(df: pd.DataFrame) -> list[str]:
    """Warn when parsed values look implausible (e.g. wrong column or unit)."""
    ranges = {"copper": (1000, 50000), "aluminium": (500, 10000), "iron_ore": (10, 1000)}
    warnings = []
    for mat, (lo, hi) in ranges.items():
        vals = df.loc[df["material"] == mat, "usd_per_unit"]
        if len(vals) and not (lo <= float(np.median(vals)) <= hi):
            warnings.append(f"{mat}: median {np.median(vals):.1f} outside plausible range {lo}-{hi}")
    return warnings
