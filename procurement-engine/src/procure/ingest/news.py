"""News headlines: GDELT DOC 2.0, then Google News RSS, then cache, then bundled samples."""
from __future__ import annotations

import hashlib
import json
import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

from ..db import now_iso
from ..settings import assumptions, get_settings, material_cfg
from .http import cache_latest, cache_time, cache_write, http_get
from .prices import FetchResult

import pandas as pd

log = logging.getLogger(__name__)

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
RSS_URL = "https://news.google.com/rss/search"
GDELT_MIN_GAP_S = 5.5
_last_gdelt_call = 0.0
_gdelt_blocked_until = 0.0  # after a rate-limit failure, skip GDELT for a while
GDELT_BACKOFF_S = 600


def item_id(material: str, title: str) -> str:
    return hashlib.sha1(f"{material}|{title.strip().lower()}".encode()).hexdigest()[:16]


def _norm_item(material: str, title: str, url: str, seen_at: datetime, source: str,
               is_sample: bool = False) -> dict:
    return {
        "id": item_id(material, title), "material": material, "title": title.strip(),
        "url": url, "domain": urlparse(url).netloc if url else "",
        "seen_at": seen_at.astimezone(timezone.utc).replace(microsecond=0).isoformat(),
        "source": source, "is_sample": is_sample,
    }


def fetch_gdelt(material: str, maxrecords: int = 50) -> list[dict]:
    """Query GDELT DOC API, respecting its one-call-per-5-seconds limit."""
    global _last_gdelt_call, _gdelt_blocked_until
    if time.monotonic() < _gdelt_blocked_until:
        raise RuntimeError("GDELT skipped: rate limited recently")
    wait = GDELT_MIN_GAP_S - (time.monotonic() - _last_gdelt_call)
    if wait > 0:
        time.sleep(wait)
    _last_gdelt_call = time.monotonic()
    query = f"{material_cfg(material)['gdelt_query']} sourcelang:english"
    try:
        resp = http_get(GDELT_URL, params={"query": query, "mode": "artlist", "format": "json",
                                          "maxrecords": maxrecords, "timespan": "14d", "sort": "datedesc"},
                        retries=2, backoff=6.0)
        payload = resp.json()
    except Exception:
        _gdelt_blocked_until = time.monotonic() + GDELT_BACKOFF_S
        raise
    items = []
    for art in payload.get("articles", []):
        seen = datetime.strptime(art["seendate"], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        items.append(_norm_item(material, art.get("title", ""), art.get("url", ""), seen, "gdelt"))
    if not items:
        raise ValueError("GDELT returned no articles")
    return items


def fetch_google_rss(material: str, maxrecords: int = 50) -> list[dict]:
    """Google News RSS search (keyless). Titles end with ' - Publisher', which we strip."""
    q = f"{material_cfg(material)['rss_query']} when:14d"
    resp = http_get(RSS_URL, params={"q": q, "hl": "en-IN", "gl": "IN", "ceid": "IN:en"})
    root = ET.fromstring(resp.content)
    items = []
    for it in root.iter("item"):
        title = it.findtext("title") or ""
        src = it.find("source")
        publisher = src.text if src is not None and src.text else ""
        if publisher and title.endswith(f" - {publisher}"):
            title = title[: -len(publisher) - 3]
        pub = it.findtext("pubDate")
        seen = parsedate_to_datetime(pub) if pub else datetime.now(timezone.utc)
        url = (src.get("url") if src is not None and src.get("url") else it.findtext("link")) or ""
        item = _norm_item(material, title, it.findtext("link") or "", seen, "google_news_rss")
        item["domain"] = urlparse(url).netloc or publisher
        items.append(item)
    if not items:
        raise ValueError("Google News RSS returned no items")
    items.sort(key=lambda d: d["seen_at"], reverse=True)
    return items[:maxrecords]


def load_sample_news(material: str) -> list[dict]:
    """Bundled, clearly labelled sample headlines. Ages are relative to now."""
    path = get_settings().sample_news_path
    data = json.loads(path.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    out = []
    for row in data.get(material, []):
        seen = now - timedelta(days=float(row.get("days_ago", 3)))
        out.append(_norm_item(material, row["title"], row.get("url", ""), seen, "sample", is_sample=True))
    return out


def _dedupe(items: list[dict]) -> list[dict]:
    seen, out = set(), []
    for it in items:
        if it["id"] in seen or not it["title"]:
            continue
        seen.add(it["id"])
        out.append(it)
    return out


def fetch_news(material: str) -> FetchResult:
    """Fresh cache (under cache_hours), then GDELT, then Google RSS, then stale cache, then samples."""
    prefix = f"news_{material}"
    cache_hours = assumptions()["news"]["cache_hours"]
    cached = cache_latest(prefix, ".json")
    if cached is not None and datetime.now(timezone.utc) - cache_time(cached) < timedelta(hours=cache_hours):
        payload = json.loads(cached.read_text())
        return FetchResult(pd.DataFrame(payload["items"]), "cache", payload["source"], payload.get("url"),
                           cache_time(cached).isoformat(), f"Fresh cache (under {cache_hours} h): {cached.name}")
    errors, items, used_sources, urls = [], [], [], []
    # Query both live sources and merge. GDELT matches article bodies (noisy titles);
    # Google News RSS matches titles. Either one alone is enough to count as live.
    for source, fn, url in (("gdelt", fetch_gdelt, GDELT_URL), ("google_news_rss", fetch_google_rss, RSS_URL)):
        try:
            items.extend(fn(material))
            used_sources.append(source)
            urls.append(url)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{source}: {exc}")
            log.warning("News source %s failed for %s: %s", source, material, exc)
    if items:
        items = _dedupe(items)
        source = "+".join(used_sources)
        cache_write(prefix, ".json", json.dumps({"source": source, "url": urls[0], "items": items}).encode())
        return FetchResult(pd.DataFrame(items), "live", source, urls[0], now_iso(),
                           f"{len(items)} headlines from {source}" + (f" ({'; '.join(errors)[:200]})" if errors else ""))
    if cached is not None:
        payload = json.loads(cached.read_text())
        return FetchResult(pd.DataFrame(payload["items"]), "cache", payload["source"], payload.get("url"),
                           cache_time(cached).isoformat(), "Live failed; stale cache used. " + "; ".join(errors)[:300])
    items = load_sample_news(material)
    return FetchResult(pd.DataFrame(items), "sample", "bundled_sample", None, now_iso(),
                       "Live and cache failed; using bundled sample headlines. " + "; ".join(errors)[:300])
