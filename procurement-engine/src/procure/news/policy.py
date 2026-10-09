"""Official policy-notice listings (DGFT, CBIC, PIB, a port authority) parsed from SAVED HTML only.

There is deliberately no fetch function: robots.txt and terms are recorded as unchecked in
``config/policy_sources_v1.json`` and ``fetch_allowed`` is false for every source. Parsing uses the
standard library (``html.parser``); Scrapling is a proposed swap that needs approval as a new
dependency.

Each source has a versioned layout: the expected container, header labels and row shape. A page
without them is ``layout_changed``, never an empty listing. Page states: ok, empty,
layout_changed, malformed_payload, provider_failure, rate_limited. Rows with an invalid date or
shape are rejected individually with a reason. Notice text is untrusted data.

Output items use the news item shape, so ``news.build_snapshots`` builds the same snapshots and
deduplicates a notice against the same story indexed by a news provider (same canonical URL,
publisher and headline). A stated listing date without a time is kept as ``published_date``;
no time of day is invented.
"""
from __future__ import annotations

import json
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..settings import PROJECT_ROOT
from .fetch import _item, canonical_url, clean_title, utc

POLICY_FILE = PROJECT_ROOT / "config" / "policy_sources_v1.json"
MAX_BODY = 2 << 20
MAX_NODES = 20_000
MAX_DEPTH = 128


class _Node:
    __slots__ = ("tag", "attrs", "children", "parts")

    def __init__(self, tag: str, attrs: dict):
        self.tag, self.attrs, self.children, self.parts = tag, attrs, [], []

    def text(self) -> str:
        return clean_title(" ".join([*self.parts, *(c.text() for c in self.children)]))

    def find_all(self, tag: str, cls: str | None = None, ident: str | None = None) -> list["_Node"]:
        found = []
        pending = list(reversed(self.children))
        while pending:
            child = pending.pop()
            if child.tag == tag and (cls is None or cls in child.attrs.get("class", "").split()) and \
                    (ident is None or child.attrs.get("id") == ident):
                found.append(child)
            pending.extend(reversed(child.children))
        return found

    def kids(self, tag: str) -> list["_Node"]:
        return [c for c in self.children if c.tag == tag]


class _Builder(HTMLParser):
    VOID = {"br", "img", "meta", "link", "input", "hr", "source", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", {})
        self.stack = [self.root]
        self.nodes = 0

    def handle_starttag(self, tag, attrs):
        self.nodes += 1
        if self.nodes > MAX_NODES or len(self.stack) >= MAX_DEPTH:
            raise _Layout("html_structure_bound")
        node = _Node(tag, {k: v or "" for k, v in attrs})
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if data.strip():
            self.stack[-1].parts.append(data)


class _Layout(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _date(text: str, fmt: str) -> str | None:
    try:
        return datetime.strptime(text.strip().title() if "%b" in fmt else text.strip(), fmt).date().isoformat()
    except ValueError:
        return None


def _rows_dgft(root: _Node):
    tables = root.find_all("table", cls="notices-table")
    if len(tables) != 1:
        raise _Layout("missing_table")
    headers = [th.text() for th in tables[0].find_all("th")]
    if headers != ["Notice No.", "Subject", "Date", "Download"]:
        raise _Layout("header_mismatch")
    body = tables[0].kids("tbody")
    if len(body) != 1:
        raise _Layout("missing_table_body")
    direct_rows = body[0].kids("tr")
    direct_ids = {id(tr) for tr in direct_rows}
    cells_in_rows = {id(td) for tr in direct_rows for td in tr.find_all("td")}
    if any(id(tr) not in direct_ids and tr.find_all("td") for tr in tables[0].find_all("tr")) or \
            any(id(td) not in cells_in_rows for td in tables[0].find_all("td")):
        raise _Layout("row_outside_table_body")
    for tr in direct_rows:
        cells = tr.kids("td")
        links = cells[3].find_all("a") if len(cells) == 4 else []
        if len(cells) != 4 or not links:
            yield None, None, None, None, "unexpected_row_shape", tr.text()
            continue
        yield cells[0].text(), cells[1].text(), cells[2].text(), links[0].attrs.get("href"), "%d/%m/%Y", None


def _rows_cbic(root: _Node):
    lists = root.find_all("div", cls="notification-list")
    if len(lists) != 1 or not lists[0].find_all("ul"):
        raise _Layout("missing_list")
    for li in lists[0].find_all("li"):
        number, date, link = li.find_all("span", cls="notif-no"), li.find_all("span", cls="notif-date"), li.find_all("a")
        if not (number and date and link):
            yield None, None, None, None, "unexpected_row_shape", li.text()
            continue
        yield number[0].text(), link[0].text(), date[0].text(), link[0].attrs.get("href"), "%d-%m-%Y", None


def _rows_pib(root: _Node):
    lists = root.find_all("ul", cls="releases")
    if len(lists) != 1:
        raise _Layout("missing_list")
    for li in lists[0].kids("li"):
        date, link = li.find_all("span", cls="release-date"), li.find_all("a")
        if not (date and link):
            yield None, None, None, None, "unexpected_row_shape", li.text()
            continue
        href = link[0].attrs.get("href", "")
        yield (href.rsplit("PRID=", 1)[-1] if "PRID=" in href else None), link[0].text(), date[0].text(), href, \
            "%d %b %Y", None


def _rows_jnpa(root: _Node):
    tables = root.find_all("table", ident="circulars")
    if len(tables) != 1:
        raise _Layout("missing_table")
    rows = tables[0].find_all("tr")
    if not rows or [th.text() for th in rows[0].kids("th")] != ["Sr", "Circular", "Date"]:
        raise _Layout("header_mismatch")
    for tr in rows[1:]:
        cells = tr.kids("td")
        links = cells[1].find_all("a") if len(cells) == 3 else []
        if len(cells) != 3 or not links:
            yield None, None, None, None, "unexpected_row_shape", tr.text()
            continue
        yield f"Circular {cells[0].text()}", links[0].text(), cells[2].text(), links[0].attrs.get("href"), "%Y-%m-%d", \
            None


PARSERS = {"dgft_table_v1": _rows_dgft, "cbic_list_v1": _rows_cbic, "pib_releases_v1": _rows_pib,
           "jnpa_circulars_v1": _rows_jnpa}


def load_policy_sources(path: Path | None = None) -> dict:
    content = json.loads(Path(path or POLICY_FILE).read_text(encoding="utf-8"))
    for key, source in content.get("providers", {}).items():
        if source.get("parser") not in PARSERS or source.get("fetch_allowed") is not False \
                or not isinstance(source.get("permitted_uses"), list) \
                or source.get("rights_status") not in ("unreviewed", "reviewed") \
                or (source["rights_status"] == "unreviewed" and source["permitted_uses"]):
            raise ValueError(f"invalid policy source {key}")
        hosts = source.get("document_hosts")
        if not isinstance(hosts, list) or not hosts or any(not isinstance(host, str) or not host
                or host != host.lower() or "/" in host for host in hosts) or source.get("source_id") not in hosts:
            raise ValueError(f"invalid policy document hosts {key}")
        try:
            if source.get("publication_timezone") != "Asia/Kolkata":
                raise ValueError("unsupported policy publication timezone")
            ZoneInfo(source["publication_timezone"])
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError(f"invalid policy publication timezone {key}") from exc
    return content


def combined_sources(news: dict, policy: dict) -> dict:
    """The news registry with the policy providers added, for ``build_snapshots``."""
    clash = set(news["providers"]) & set(policy["providers"])
    if clash:
        raise ValueError(f"provider ids collide: {sorted(clash)}")
    return {**news, "version": f"{news['version']}+{policy['version']}",
            "providers": {**news["providers"], **policy["providers"]}}


def _page(provider: str, source: dict, *, state: str, reason: str | None, retrieved_at: str, items: list[dict],
          rejected: list[dict], synthetic: bool) -> dict:
    return {"provider": provider, "material": None, "state": state, "reason": reason, "retrieved_at": utc(retrieved_at),
            "items": items, "rejected_rows": rejected, "synthetic": synthetic, "request": source["listing_url"],
            "layout": source["parser"]}


def _document_url(source: dict, href: str | None) -> str | None:
    if not isinstance(href, str) or not href.strip():
        return None
    try:
        url = urljoin(source["listing_url"], href.strip())
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.port not in (None, 443)):
            return None
        host = parsed.hostname.lower().removeprefix("www.")
        if host not in source["document_hosts"]:
            return None
        return canonical_url(url)
    except ValueError:
        return None


def parse_saved_page(provider: str, raw: bytes, *, http_status: int, retrieved_at: str, registry: dict | None = None,
                     synthetic: bool = False) -> dict:
    registry = registry or load_policy_sources()
    source = registry["providers"][provider]
    base = dict(retrieved_at=retrieved_at, items=[], rejected=[], synthetic=synthetic)
    if http_status == 429:
        return _page(provider, source, state="rate_limited", reason="http_429", **base)
    if http_status != 200:
        return _page(provider, source, state="provider_failure", reason=f"http_{http_status}", **base)
    try:
        if len(raw) > MAX_BODY:
            raise UnicodeDecodeError("utf-8", b"", 0, 1, "too large")
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return _page(provider, source, state="malformed_payload", reason="not_html", **base)
    if "<html" not in text[:2048].lower():
        return _page(provider, source, state="malformed_payload", reason="not_html", **base)
    builder = _Builder()
    try:
        builder.feed(text)
        builder.close()
    except _Layout:
        return _page(provider, source, state="malformed_payload", reason="html_structure_bound", **base)
    root = builder.root
    html = root.find_all("html")
    language = (html[0].attrs.get("lang") if html else None) or source.get("language") or "unknown"
    items, rejected = [], []
    try:
        for index, (number, title, date_text, href, fmt, raw_row) in enumerate(PARSERS[source["parser"]](root), 1):
            if fmt == "unexpected_row_shape":
                rejected.append({"row": index, "reason": "unexpected_row_shape", "text": (raw_row or "")[:200]})
                continue
            stated = _date(date_text or "", fmt)
            if stated is None:
                rejected.append({"row": index, "reason": "invalid_date", "text": (date_text or "")[:40]})
                continue
            if not clean_title(title) or not href:
                rejected.append({"row": index, "reason": "missing_title_or_link", "text": clean_title(title)[:200]})
                continue
            local_day = datetime.fromisoformat(utc(retrieved_at)).astimezone(
                ZoneInfo(source["publication_timezone"])).date().isoformat()
            if stated > local_day:
                rejected.append({"row": index, "reason": "future_publication_date", "text": stated})
                continue
            url = _document_url(source, href)
            if url is None:
                rejected.append({"row": index, "reason": "untrusted_document_url", "text": (href or "")[:200]})
                continue
            item = _item(provider, title=clean_title(title), url=url, source_id=source["source_id"],
                         publisher=source["publisher"], language=language, published_at=None,
                         provider_first_seen_at=None, retrieved_at=retrieved_at, guid=number)
            items.append({**item, "published_date": stated})
    except _Layout as exc:
        return _page(provider, source, state="layout_changed", reason=exc.reason, **base)
    state = "ok" if items else ("layout_changed" if rejected else "empty")
    return _page(provider, source, state=state, reason=None if items else ("all_rows_rejected" if rejected
                                                                           else "no_notices"),
                 retrieved_at=retrieved_at, items=items, rejected=rejected, synthetic=synthetic)


def recorded_policy_fetches(manifest: Path, *, registry: dict | None = None) -> list[dict]:
    data = json.loads(Path(manifest).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("synthetic") is not True:
        raise ValueError("saved pages must be labelled synthetic")
    folder = Path(manifest).parent
    return [parse_saved_page(row["provider"], b"" if row.get("file") is None else (folder / row["file"]).read_bytes(),
                             http_status=row["http_status"], retrieved_at=row["retrieved_at"], registry=registry,
                             synthetic=True) for row in data["pages"]]
