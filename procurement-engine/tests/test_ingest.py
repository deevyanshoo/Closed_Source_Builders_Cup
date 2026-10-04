"""Ingestion: parsing with offline fixtures, and the live -> cache -> synthetic fallback order."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pandas as pd
import pytest
from openpyxl import Workbook

from procure.ingest import fx as fxmod
from procure.ingest import news as newsmod
from procure.ingest import prices as pmod
from procure.settings import get_settings


def make_pinksheet(path: Path) -> Path:
    """A tiny workbook with the same quirks as the real Pink Sheet (title rows, units row, '…')."""
    wb = Workbook()
    wb.active.title = "Description"
    ws = wb.create_sheet("Monthly Prices")
    ws.append(["World Bank Commodity Price Data (The Pink Sheet)"])
    ws.append(["monthly prices in nominal US dollars"])
    ws.append([])
    ws.append([None, "Crude oil, Brent", "Aluminum", "Iron ore, cfr spot", "Copper"])
    ws.append([None, "($/bbl)", "($/mt)", "($/dmtu)", "($/mt)"])
    ws.append(["2009M12", 75.0, 2180.0, 100.0, 6982.0])
    ws.append(["2010M01", 76.0, 2235.0, "…", 7386.0])
    ws.append(["2010M02", 74.0, 2048.0, 126.0, 6848.0])
    ws.append(["2010M03", 79.0, 2214.0, 140.0, 7463.0])
    wb.save(path)
    return path


def test_parse_pinksheet(tmp_path):
    df = make_pinksheet(tmp_path / "pink.xlsx")
    out = pmod.parse_pinksheet(df, start="2010-01-01")
    cu = out[out.material == "copper"].set_index("month")["usd_per_unit"]
    assert list(cu.index.strftime("%Y-%m")) == ["2010-01", "2010-02", "2010-03"]
    assert cu.loc["2010-01-01"] == 7386.0
    ore = out[out.material == "iron_ore"]
    assert len(ore) == 2  # the '…' value is dropped, not turned into zero
    assert set(out.material) == {"copper", "aluminium", "iron_ore"}
    assert pmod.sanity_check(out) == []


def test_sanity_check_flags_wrong_scale():
    bad = pd.DataFrame({"material": ["copper"] * 3, "usd_per_unit": [7.0, 8.0, 9.0]})
    assert pmod.sanity_check(bad)


def test_frankfurter_parse_and_monthly():
    payload = {"rates": {"2024-01-02": {"INR": 83.0}, "2024-01-03": {"INR": 85.0}, "2024-02-01": {"INR": 84.0}}}
    daily = fxmod.parse_rates(payload)
    monthly = fxmod.to_monthly(daily)
    assert list(monthly["usdinr"]) == [84.0, 84.0]
    business_days = pd.Series(1.0, index=pd.bdate_range("2024-01-01", periods=40))
    assert fxmod._is_thinned(business_days) is False
    weekly = pd.Series([1.0, 1.0, 1.0, 1.0], index=pd.date_range("2020-01-01", periods=4, freq="7D"))
    assert fxmod._is_thinned(weekly)


def test_prices_fallback_cache_then_synthetic(tmp_path, monkeypatch):
    raw = get_settings().raw_dir
    for f in raw.glob("pinksheet_monthly_*.xlsx"):
        f.unlink()
    res = pmod.fetch_prices()  # OFFLINE=1 and no cache
    assert res.used == "synthetic"
    assert res.df["usd_per_unit"].gt(0).all()
    cached = raw / "pinksheet_monthly_20240101T000000Z.xlsx"
    shutil.copy(make_pinksheet(tmp_path / "p.xlsx"), cached)
    try:
        res = pmod.fetch_prices()
        assert res.used == "cache"
        assert res.df["month"].min() >= pd.Timestamp("2010-01-01")
    finally:
        cached.unlink()


def test_fx_offline_synthetic_is_plausible():
    res = fxmod.fetch_fx()
    assert res.used in {"synthetic", "cache"}
    assert res.df["usdinr"].between(30, 120).all()


def test_news_offline_uses_sample():
    for f in get_settings().raw_dir.glob("news_copper_*.json"):
        f.unlink()
    res = newsmod.fetch_news("copper")
    assert res.used == "sample"
    assert res.df["is_sample"].all()
    assert len(res.df) == 10


def test_google_rss_parse_strips_publisher(monkeypatch):
    xml = b"""<?xml version="1.0"?><rss><channel>
      <item><title>Chile copper mine strike looms - Reuters</title><link>https://news.google.com/a</link>
      <pubDate>Wed, 30 Sep 2026 22:13:02 GMT</pubDate><source url="https://www.reuters.com">Reuters</source></item>
    </channel></rss>"""

    class Resp:
        content = xml

    monkeypatch.setattr(newsmod, "http_get", lambda *a, **k: Resp())
    items = newsmod.fetch_google_rss("copper")
    assert items[0]["title"] == "Chile copper mine strike looms"
    assert items[0]["domain"] == "www.reuters.com"
    assert items[0]["seen_at"].startswith("2026-09-30")


def test_sample_news_file_is_labelled():
    data = json.loads(get_settings().sample_news_path.read_text())
    for mat in ("copper", "aluminium", "iron_ore"):
        assert len(data[mat]) == 10
        assert all(r["is_sample"] for r in data[mat])


def test_refresh_records_provenance(seeded_db):
    used = {s["source"]: s["used"] for s in seeded_db["sources"]}
    assert used["prices"] in {"synthetic", "cache"}
    assert used["news:copper"] in {"sample", "cache"}
    from procure import db
    runs = db.query_df("SELECT * FROM ingest_runs")
    assert {"prices", "fx"} <= set(runs["source"])
    flags = db.query_df("SELECT DISTINCT is_synthetic FROM prices_monthly")
    if used["prices"] == "synthetic":
        assert bool(flags["is_synthetic"].iloc[0]) is True
