"""Bounded static article excerpts from two reviewed primary publishers."""
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
import re
import threading
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import requests

_EIA = re.compile(r'/todayinenergy/detail\.php$')
_ECB = re.compile(r'/press/pr/date/\d{4}/html/ecb\.[a-z0-9._~-]+\.en\.html$')
MAX_ARTICLE_BYTES = 350_000
MAX_EXCERPT = 320
_CACHE_TTL = timedelta(hours=6)
_default = None
_default_lock = threading.Lock()


def allowed_article(url):
    """Exact public hosts/paths; reject redirects and all user-controlled destinations."""
    if not isinstance(url, str) or len(url) > 2048 or any(ord(c) < 33 for c in url):
        return None
    try:
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443) or parsed.fragment:
            return None
        if parsed.hostname == 'www.eia.gov' and _EIA.fullmatch(parsed.path):
            query = parse_qs(parsed.query, strict_parsing=True)
            if set(query) == {'id'} and len(query['id']) == 1 and re.fullmatch(r'\d{1,8}', query['id'][0]):
                return 'eia'
        if parsed.hostname == 'www.ecb.europa.eu' and not parsed.query and _ECB.fullmatch(parsed.path):
            return 'ecb'
    except (ValueError, TypeError):
        return None
    return None


def _clean(value):
    return re.sub(r'\s+', ' ', str(value or '')).strip()


def _date(page, provider, url):
    selectors = ["meta[property='article:published_time']", "meta[name='date']"]
    if provider == 'ecb':
        selectors.append('main time[datetime]')
    for selector in selectors:
        for node in page.css(selector)[:2]:
            raw = _clean(node.attrib.get('content') or node.attrib.get('datetime'))
            try:
                parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
                if parsed.tzinfo is not None:
                    return parsed.astimezone(timezone.utc).isoformat()
                if re.fullmatch(r'\d{4}-\d{2}-\d{2}', raw):
                    return raw
            except ValueError:
                continue
    if provider == 'ecb':
        match = re.search(r'/ecb\.[a-z]+(\d{2})(\d{2})(\d{2})~', url)
        if match:
            try:
                return datetime.strptime('20' + ''.join(match.groups()), '%Y%m%d').date().isoformat()
            except ValueError:
                pass
    # EIA page footers contain dates unrelated to publication; never guess from them.
    return None


def _static_fetch(url, *, session_factory=requests.Session):
    # Reviewed 2026-10-15: eia.gov/robots.txt allows /todayinenergy/;
    # ecb.europa.eu/robots.txt does not disallow /press/pr/ and asks for a
    # five-second crawl delay. The process budget is at most two requests/hour.
    # EIA's reuse page and the ECB's disclaimer permit attributed use of their
    # own information; this reader retains only a short excerpt, not images.
    if not allowed_article(url):
        raise ValueError('publisher_or_path_not_allowlisted')
    from scrapling import Selector
    deadline = time.monotonic() + 4
    session = session_factory()
    session.trust_env = False  # No ambient proxy or netrc credentials.
    try:
        with session.get(url, headers={'User-Agent': 'ProcureNewsArticleReader/1.0'},
                         timeout=(2, 2), allow_redirects=False, stream=True) as response:
            if response.status_code != 200:
                return SimpleNamespace(status=response.status_code, body=b'', css=None)
            media = response.headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
            if media and media not in ('text/html', 'application/xhtml+xml'):
                raise ValueError('article_not_html')
            length = response.headers.get('Content-Length')
            if length and length.isdecimal() and int(length) > MAX_ARTICLE_BYTES:
                raise ValueError('article_too_large')
            body = bytearray()
            for chunk in response.iter_content(chunk_size=8192):
                if time.monotonic() > deadline:
                    raise TimeoutError('article_deadline')
                if len(body) + len(chunk) > MAX_ARTICLE_BYTES:
                    raise ValueError('article_too_large')
                body.extend(chunk)
            if time.monotonic() > deadline:
                raise TimeoutError('article_deadline')
            content = bytes(body)
            parsed = Selector(content=content)
            return SimpleNamespace(status=200, body=content, css=parsed.css)
    finally:
        session.close()


class ArticleEnricher:
    def __init__(self, *, fetcher=None, clock=None):
        self.fetcher = fetcher or _static_fetch
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        self._cache = OrderedDict()
        self._hour = None
        self._used = 0
        self._last_ecb_fetch = None

    def _read(self, url, provider, now):
        try:
            page = self.fetcher(url)
            if page.status != 200 or not isinstance(page.body, bytes) or len(page.body) > MAX_ARTICLE_BYTES:
                raise ValueError('http_or_size')
            selector = 'main p' if provider == 'ecb' else 'p'
            paragraphs = [_clean(node.text) for node in page.css(selector)]
            body = [part for part in paragraphs if len(part) >= 80]
            if not body:
                raise ValueError('body_missing')
            title = _clean(page.css('title::text').get())[:240] or None
            return {'status': 'enriched', 'method': 'scrapling-static', 'fetched_at': now.isoformat(),
                    'title': title, 'published_at': _date(page, provider, url),
                    'excerpt': body[0][:MAX_EXCERPT], 'content_chars': sum(map(len, body)), 'reason': None}
        except Exception as exc:
            return {'status': 'unavailable', 'method': 'scrapling-static', 'fetched_at': now.isoformat(),
                    'title': None, 'published_at': None, 'excerpt': None, 'content_chars': 0,
                    'reason': type(exc).__name__ if not isinstance(exc, ValueError) else str(exc)[:60]}

    def attach(self, items, *, max_new=2):
        """Enrich ranked selected stories; process-wide hourly budget and six-hour cache."""
        now = self.clock().astimezone(timezone.utc)
        hour = now.replace(minute=0, second=0, microsecond=0)
        output, new = [], 0
        with self._lock:
            if self._hour != hour:
                self._hour, self._used = hour, 0
            for item in items:
                row = dict(item)
                prior = row.get('article') if isinstance(row.get('article'), dict) else None
                provider = allowed_article(row.get('url'))
                if row.get('relevance_strength') not in ('strong', 'related'):
                    output.append(row)
                    continue
                if not provider:
                    row['article'] = {'status': 'unsupported', 'method': 'scrapling-static',
                                      'fetched_at': now.isoformat(), 'title': None, 'published_at': None,
                                      'excerpt': None, 'content_chars': 0, 'reason': 'publisher_or_path_not_allowlisted'}
                else:
                    cached = self._cache.get(row['url'])
                    if cached and now - cached[0] < _CACHE_TTL:
                        row['article'] = dict(cached[1])
                        self._cache.move_to_end(row['url'])
                    elif provider == 'ecb' and self._last_ecb_fetch is not None and time.monotonic() - self._last_ecb_fetch < 5:
                        row['article'] = {'status': 'unavailable', 'method': 'scrapling-static',
                                          'fetched_at': now.isoformat(), 'title': None, 'published_at': None,
                                          'excerpt': None, 'content_chars': 0, 'reason': 'publisher_crawl_delay'}
                    elif self._used < 2 and new < max_new:
                        self._used += 1
                        new += 1
                        if provider == 'ecb':
                            self._last_ecb_fetch = time.monotonic()
                        row['article'] = self._read(row['url'], provider, now)
                        self._cache[row['url']] = (now, dict(row['article']))
                        self._cache.move_to_end(row['url'])
                        while len(self._cache) > 64:
                            self._cache.popitem(last=False)
                    else:
                        row['article'] = {'status': 'unavailable', 'method': 'scrapling-static',
                                          'fetched_at': now.isoformat(), 'title': None, 'published_at': None,
                                          'excerpt': None, 'content_chars': 0, 'reason': 'hourly_fetch_budget'}
                    if row['article']['status'] == 'unavailable' and prior and prior.get('status') == 'enriched':
                        # Keep dated prior evidence when an enrichment attempt fails or the budget is spent.
                        row['article'] = dict(prior)
                output.append(row)
        return output


def default_enricher():
    global _default
    with _default_lock:
        if _default is None:
            _default = ArticleEnricher()
        return _default
