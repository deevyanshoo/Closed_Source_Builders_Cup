"""Parse GDELT and Google News RSS headlines into provider items, with explicit states.

Public live entrypoints preserve endpoint and per-material query metadata but are disabled until
a source-specific rights approval and bounded transport are reviewed. ``recorded_fetches``
replays hand-written synthetic responses through the same parsers.

Fetch states: ok, empty, offline, rate_limited (HTTP 429), provider_failure, malformed_payload.
Headline text is untrusted data: it is parsed, never followed.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

from ..ingest.news import GDELT_URL, RSS_URL
from ..settings import PROJECT_ROOT, material_cfg

MAX_BODY = 2 << 20
MAX_TITLE = 500
_SEENDATE = re.compile(r"^\d{8}T\d{6}Z$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$")
ENDPOINTS = {"gdelt": GDELT_URL, "google_news_rss": RSS_URL}
NEWS_SOURCES_FILE = PROJECT_ROOT / "config" / "news_sources_v1.json"


class NewsMalformed(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def utc(text: str) -> str:
    if not isinstance(text, str) or not _TIMESTAMP.fullmatch(text):
        raise ValueError("instants need seconds and a time zone")
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()


def host(url: str | None) -> str | None:
    if not url:
        return None
    name = (urlparse(url).hostname or "").lower()
    return (name[4:] if name.startswith("www.") else name) or None


def canonical_url(url: str | None) -> str | None:
    """Lower-case host without www, no fragment, tracking (utm_*) parameters dropped."""
    if not isinstance(url, str) or not url.strip():
        return None
    parts = urlparse(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    query = "&".join(p for p in parts.query.split("&") if p and not p.lower().startswith("utm_"))
    path = parts.path.rstrip("/") or "/"
    return f"https://{host(url)}{path}" + (f"?{query}" if query else "")


def clean_title(text: object) -> str:
    return re.sub(r"\s+", " ", text).strip()[:MAX_TITLE] if isinstance(text, str) else ""


def _item(provider: str, *, title: str, url: str | None, source_id: str | None, publisher: str | None, language: str,
          published_at: str | None, provider_first_seen_at: str | None, retrieved_at: str, guid: str | None) -> dict:
    return {"provider": provider, "title": title, "url": canonical_url(url), "source_id": source_id,
            "publisher": publisher, "language": language or "unknown", "published_at": published_at,
            "provider_first_seen_at": provider_first_seen_at, "retrieved_at": utc(retrieved_at), "guid": guid}


def parse_gdelt(raw: bytes, *, retrieved_at: str) -> list[dict]:
    """GDELT DOC 2.0 ``mode=artlist&format=json``: {"articles": [{url, title, seendate, domain, language}]}."""
    if len(raw) > MAX_BODY:
        raise NewsMalformed("body_too_large")
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, UnicodeDecodeError):
        raise NewsMalformed("invalid_json") from None
    if not isinstance(payload, dict):
        raise NewsMalformed("invalid_json")
    articles = payload.get("articles", [])
    if not isinstance(articles, list):
        raise NewsMalformed("invalid_articles")
    out = []
    for art in articles:
        if not isinstance(art, dict) or not isinstance(art.get("url"), str) or not clean_title(art.get("title")) \
                or not isinstance(art.get("seendate"), str) or not _SEENDATE.fullmatch(art["seendate"]):
            raise NewsMalformed("invalid_article")
        try:
            seen = datetime.strptime(art["seendate"], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).isoformat()
            domain = art.get("domain") if isinstance(art.get("domain"), str) and art["domain"].strip() else None
            if canonical_url(art["url"]) is None:
                raise ValueError("invalid article URL")
            out.append(_item("gdelt", title=clean_title(art["title"]), url=art["url"],
                             source_id=host(f"https://{domain}") if domain else host(art["url"]), publisher=domain,
                             language=art.get("language") if isinstance(art.get("language"), str) else "unknown",
                             published_at=None, provider_first_seen_at=seen, retrieved_at=retrieved_at, guid=None))
        except ValueError:
            raise NewsMalformed("invalid_article") from None
    return out


def parse_rss(raw: bytes, *, retrieved_at: str) -> list[dict]:
    """Google News RSS: <item> with title ending ' - Publisher', link, guid, pubDate, <source url>."""
    if len(raw) > MAX_BODY:
        raise NewsMalformed("body_too_large")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise NewsMalformed("invalid_xml") from None
    channel = root.find("channel")
    if root.tag != "rss" or channel is None:
        raise NewsMalformed("not_rss")
    language = clean_title(channel.findtext("language")) or "unknown"
    out = []
    for node in channel.iter("item"):
        title = clean_title(node.findtext("title"))
        source = node.find("source")
        publisher = clean_title(source.text) if source is not None and source.text else None
        if publisher and title.endswith(f" - {publisher}"):
            title = title[: -len(publisher) - 3].strip()
        if not title:
            raise NewsMalformed("invalid_item")
        published = None
        if node.findtext("pubDate"):
            try:
                published = parsedate_to_datetime(node.findtext("pubDate")).astimezone(timezone.utc).isoformat()
            except (TypeError, ValueError):
                raise NewsMalformed("invalid_pubdate") from None
        try:
            source_url = source.get("url") if source is not None else None
            out.append(_item("google_news_rss", title=title, url=None, source_id=host(source_url), publisher=publisher,
                             language=language, published_at=published, provider_first_seen_at=None,
                             retrieved_at=retrieved_at, guid=clean_title(node.findtext("guid")) or
                             clean_title(node.findtext("link")) or None))
        except ValueError:
            raise NewsMalformed("invalid_item") from None
    return out


PARSERS = {"gdelt": parse_gdelt, "google_news_rss": parse_rss}


def outcome(provider: str, material: str, *, state: str, reason: str | None, retrieved_at: str, items: list[dict],
            synthetic: bool) -> dict:
    return {"provider": provider, "material": material, "state": state, "reason": reason,
            "retrieved_at": utc(retrieved_at), "items": items, "synthetic": synthetic,
            "request": ENDPOINTS[provider]}


def _from_body(provider: str, material: str, status: int, raw: bytes, retrieved_at: str, synthetic: bool) -> dict:
    if status == 429:
        return outcome(provider, material, state="rate_limited", reason="http_429", retrieved_at=retrieved_at,
                       items=[], synthetic=synthetic)
    if status != 200:
        return outcome(provider, material, state="provider_failure", reason=f"http_{status}",
                       retrieved_at=retrieved_at, items=[], synthetic=synthetic)
    try:
        items = PARSERS[provider](raw, retrieved_at=retrieved_at)
    except NewsMalformed as exc:
        return outcome(provider, material, state="malformed_payload", reason=exc.reason, retrieved_at=retrieved_at,
                       items=[], synthetic=synthetic)
    return outcome(provider, material, state="ok" if items else "empty", reason=None if items else "no_items",
                   retrieved_at=retrieved_at, items=items, synthetic=synthetic)


def _live(provider: str, material: str, url: str, params: dict, retrieved_at: str) -> dict:
    # No bounded, rights-reviewed transport exists for this feature yet. The
    # source-specific default is recorded, but changing it alone cannot fetch.
    try:
        source = json.loads(NEWS_SOURCES_FILE.read_text(encoding="utf-8"))["providers"][provider]
        allowed = source.get("fetch_allowed") is True and source.get("rights_status") != "unreviewed"
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        allowed = False
    return outcome(provider, material, state="offline",
                   reason="bounded_transport_unavailable" if allowed else "fetch_not_approved",
                   retrieved_at=retrieved_at, items=[], synthetic=False)


def fetch_gdelt_items(material: str, *, retrieved_at: str, maxrecords: int = 50) -> dict:
    query = f"{material_cfg(material)['gdelt_query']} sourcelang:english"
    return _live("gdelt", material, GDELT_URL, {"query": query, "mode": "artlist", "format": "json",
                                                "maxrecords": maxrecords, "timespan": "14d", "sort": "datedesc"},
                 retrieved_at)


def fetch_rss_items(material: str, *, retrieved_at: str) -> dict:
    query = f"{material_cfg(material)['rss_query']} when:14d"
    return _live("google_news_rss", material, RSS_URL, {"q": query, "hl": "en-IN", "gl": "IN", "ceid": "IN:en"},
                 retrieved_at)


def recorded_fetches(manifest: Path) -> list[dict]:
    """Replay recorded responses (here hand-written and labelled synthetic) through the same parsers."""
    data = json.loads(Path(manifest).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("synthetic") is not True:
        raise ValueError("recorded responses must be labelled synthetic")
    folder = Path(manifest).parent
    return [_from_body(row["provider"], row["material"], row["http_status"],
                       b"" if row.get("file") is None else (folder / row["file"]).read_bytes(),
                       row["retrieved_at"], True) for row in data["responses"]]
