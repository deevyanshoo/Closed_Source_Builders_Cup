"""Bounded public news metadata for the fictional company demo.

Only server-owned endpoints are fetched. Article bodies and feed descriptions are
never stored or returned; links take readers to the original publisher.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, wait
import hashlib
import ipaddress
import json
import os
import re
import threading
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import xml.etree.ElementTree as ET

import requests

from .news_ranking import rank_items


TTL_SECONDS = 900
MAX_BYTES = 350_000
MAX_ITEMS_PER_SOURCE = 30
MAX_ITEMS = 80
MAX_COMPANY_ITEMS = 60
TIMEOUT = (2, 8)
REFRESH_DEADLINE_SECONDS = 10
GOOGLE_NEWS_HOST = 'news.google.com'
SECTOR_TERMS = {
    'electrical cable manufacturing': ('cable', 'electrical wire'),
    'flexible packaging': ('packaging', 'plastic film'),
    'textile production': ('textile', 'fabric', 'garment'),
    'food processing': ('food processing', 'food production', 'grain'),
    'pharmaceutical manufacturing': ('pharmaceutical', 'medicine', 'drug manufacturing'),
    'automotive components': ('automotive', 'auto parts', 'vehicle production'),
    'battery assembly': ('battery', 'energy storage'),
    'electronics assembly': ('electronics', 'semiconductor', 'circuit board', 'silicon interposer'),
    'petrochemical blending': ('petrochemical', 'refinery', 'chemical plant'),
    'construction materials': ('construction', 'cement', 'concrete'),
}
OPERATIONS = {
    'supply': ('supply', 'shortage', 'scarcity', 'import', 'export', 'production', 'output', 'factory', 'plant', 'inventory'),
    'pricing': ('price', 'cost', 'tariff', 'duty', 'inflation'),
    'logistics': ('shipping', 'port', 'freight', 'cargo', 'route', 'transit', 'delay', 'disruption'),
    'demand': ('demand', 'orders', 'sales', 'market'),
}
AMBIGUOUS_MATERIAL_TERMS = {'oil', 'gas', 'cell', 'glass', 'packaging', 'chemical', 'polymer', 'film', 'sand', 'steel', 'resin', 'grain', 'fibre', 'electronics', 'battery', 'aluminium', 'inks'}
FINANCIAL_ONLY_TERMS = ('shares', 'share price', 'stock price', 'stock gets', 'buy rating',
                        'sell rating', 'target price', 'fair value', 'dividend', 'earnings')
FINANCIAL_RATING_TERMS = ('price target', 'analyst rating', 'stock rating')
SOURCES = (
    ('eia', 'U.S. Energy Information Administration', 'https://www.eia.gov/rss/todayinenergy.xml'),
    ('ecb', 'European Central Bank', 'https://www.ecb.europa.eu/rss/press.html'),
    ('ecb_fx', 'ECB euro reference rates', 'https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml'),
    ('gdelt', 'GDELT article discovery', 'https://api.gdeltproject.org/api/v2/doc/doc?query=%28copper%20OR%20steel%20OR%20oil%20OR%20shipping%20OR%20semiconductor%20OR%20pharmaceutical%20OR%20food%20OR%20battery%29&mode=artlist&format=json&timespan=24h&maxrecords=75'),
)


class _RateLimited(ValueError):
    """A fixed public provider declined the bounded request."""


def _unavailable_reason(error):
    if isinstance(error, (requests.Timeout, TimeoutError)):
        return 'Provider request timed out.'
    if isinstance(error, requests.ConnectionError):
        return 'Provider connection failed.'
    if isinstance(error, ValueError) and str(error) == 'Response too large':
        return 'Provider response exceeded the size limit.'
    return 'Source could not be read or validated.'


def _now(clock):
    value = clock()
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _safe_url(raw, *, primary_host=None):
    if not isinstance(raw, str) or len(raw) > 2048 or any(ord(c) < 32 for c in raw):
        return None
    try:
        parts = urlsplit(raw.strip())
        host = parts.hostname
        if parts.scheme != 'https' or not host or parts.username or parts.password or parts.port not in (None, 443):
            return None
        host = host.lower().rstrip('.')
        if primary_host and host != primary_host:
            return None
        if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')) or '.' not in host:
            return None
        try:
            ipaddress.ip_address(host)
            return None
        except ValueError:
            pass
        query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                           if not k.lower().startswith(('utm_', 'fbclid', 'gclid'))])
        return urlunsplit(('https', host, parts.path or '/', query, ''))
    except ValueError:
        return None


def _iso(raw):
    if not raw:
        return None
    try:
        if re.fullmatch(r'\d{8}T\d{6}Z', raw):
            value = datetime.strptime(raw, '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
        else:
            try:
                value = datetime.fromisoformat(raw.replace('Z', '+00:00'))
            except ValueError:
                value = parsedate_to_datetime(raw)
        return value.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError):
        return None


def _xml(payload):
    if len(payload) > MAX_BYTES or re.search(br'<!\s*(?:DOCTYPE|ENTITY)', payload, re.I):
        raise ValueError('Unsafe XML')
    return ET.fromstring(payload)


def _local_name(tag):
    return tag.rsplit('}', 1)[-1].lower()


def _child_text(node, name):
    for child in node:
        if _local_name(child.tag) == name:
            return (child.text or '').strip()
    return ''


def _metadata(title, url, provider, publisher, published_at, seen_at, retrieved_at, tier, reason):
    title = re.sub(r'\s+', ' ', str(title or '')).strip()[:240]
    url = _safe_url(url, primary_host={'EIA': 'www.eia.gov', 'ECB': 'www.ecb.europa.eu'}.get(provider))
    if not title or not url:
        return None
    return {
        'item_id': hashlib.sha256(url.encode()).hexdigest()[:20],
        'title': title, 'url': url, 'publisher': publisher, 'provider': provider,
        'published_at': published_at, 'first_seen_at': seen_at,
        'retrieved_at': retrieved_at, 'source_tier': tier,
        'source_reliability': 'high' if tier == 'primary' else 'unverified',
        'reliability_reason': reason, 'synthetic': False,
    }


def _rss(payload, provider, retrieved_at):
    root = _xml(payload)
    if _local_name(root.tag) not in ('rss', 'feed', 'rdf'):
        raise ValueError('Unexpected feed')
    out = []
    for node in root.iter():
        if _local_name(node.tag) not in ('item', 'entry'):
            continue
        link = _child_text(node, 'link')
        if not link:
            for child in node:
                if _local_name(child.tag) == 'link':
                    link = child.attrib.get('href', '')
        publisher = ('U.S. Energy Information Administration' if provider == 'EIA' else
                     'European Central Bank' if provider == 'ECB' else _child_text(node, 'source') or 'Google News discovery')
        item = _metadata(_child_text(node, 'title'), link, provider, publisher,
                         _iso(_child_text(node, 'pubdate') or _child_text(node, 'published') or _child_text(node, 'updated')),
                         retrieved_at, retrieved_at, 'discovery' if provider == 'Google News RSS' else 'primary',
                         ('Google News RSS discovered publisher metadata; the underlying claim is unverified.'
                          if provider == 'Google News RSS' else 'Official publisher feed; the headline is evidence of a publication, not an exposure outcome.'))
        if item:
            out.append(item)
        if len(out) >= MAX_ITEMS_PER_SOURCE:
            break
    return out


def _gdelt(payload, retrieved_at):
    if len(payload) > MAX_BYTES:
        raise ValueError('Response too large')
    data = json.loads(payload)
    if not isinstance(data, dict) or not isinstance(data.get('articles'), list):
        raise ValueError('Unexpected discovery response')
    out = []
    for row in data['articles'][:MAX_ITEMS_PER_SOURCE * 2]:
        if not isinstance(row, dict):
            continue
        url = _safe_url(row.get('url'))
        if not url:
            continue
        publisher = urlsplit(url).hostname
        item = _metadata(row.get('title'), url, 'GDELT', publisher, None,
                         _iso(row.get('seendate')) or retrieved_at, retrieved_at,
                         'discovery', 'GDELT discovered this publisher link; the underlying claim has not been independently verified.')
        if item:
            out.append(item)
        if len(out) >= MAX_ITEMS_PER_SOURCE:
            break
    return out


def _fx(payload, retrieved_at):
    root = _xml(payload)
    day = None
    rates = []
    for node in root.iter():
        if _local_name(node.tag) != 'cube':
            continue
        if 'time' in node.attrib:
            day = node.attrib['time']
        currency, rate = node.attrib.get('currency'), node.attrib.get('rate')
        if currency and rate and day and re.fullmatch(r'[A-Z]{3}', currency) and re.fullmatch(r'\d+(?:\.\d+)?', rate):
            rates.append({'base': 'EUR', 'quote': currency, 'rate': rate,
                          'observation_date': day, 'provider': 'ECB',
                          'retrieved_at': retrieved_at, 'informational': True})
    if not rates:
        raise ValueError('No reference rates')
    return rates[:40]


def _default_transport(url, *, timeout, max_bytes):
    # Source URLs are fixed above; requests never follows a provider redirect.
    deadline = time.monotonic() + REFRESH_DEADLINE_SECONDS
    session = requests.Session()
    session.trust_env = False  # Ignore netrc credentials and ambient proxy settings.
    try:
        with session.get(url, timeout=timeout, allow_redirects=False, stream=True) as response:
            if response.status_code == 429:
                raise _RateLimited('Provider rate limit reached.')
            if response.status_code != 200:
                raise ValueError('Source unavailable')
            body = bytearray()
            for chunk in response.iter_content(chunk_size=8192):
                if time.monotonic() > deadline:
                    raise TimeoutError('Source read deadline exceeded')
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise ValueError('Response too large')
            return bytes(body)
    finally:
        session.close()


def _matches(text, company):
    lowered = text.casefold()
    materials = []
    sector_terms = {term for aliases in SECTOR_TERMS.values() for term in aliases}
    for material in company.get('materials', []):
        words = [material.get('name', '')] + [word for word in material.get('keywords', [])
                                                 if word.casefold() not in AMBIGUOUS_MATERIAL_TERMS
                                                 and word.casefold() not in sector_terms
                                                 and word.casefold() != 'api']
        if any(word and re.search(r'(?<!\w)' + re.escape(word.casefold()) + r'(?!\w)', lowered) for word in words):
            materials.append(material['material_id'])
    routes = []
    for route in company.get('routes', []):
        places = [route.get('origin', ''), route.get('destination', '')] + list(route.get('via', []))
        if any(place and re.search(r'(?<!\w)' + re.escape(place.casefold()) + r'(?!\w)', lowered) for place in places):
            routes.append(route['route_id'])
    currencies = [code for code in company.get('currencies', [])
                  if re.search(r'(?<!\w)' + re.escape(code.casefold()) + r'(?!\w)', lowered)]
    return materials, routes, currencies


def _company_item(raw, company):
    item = dict(raw)
    source_id = item.pop('_discovery_source_id', None)
    if source_id in {'google_news_1', 'google_news_2', 'google_news_3'}:
        item['source_id'] = source_id
    materials, routes, currencies = _matches(item['title'], company)
    if currencies and not any(_term_in(item['title'].casefold(), term) for term in
                              ('exchange', 'currency', 'forex', 'fx', 'dollar', 'rate', 'devaluation')):
        currencies = []
    financial_only = any(_term_in(item['title'].casefold(), term) for term in FINANCIAL_ONLY_TERMS +
                         ('bitcoin', 'litecoin', 'ethereum', 'dogecoin', 'remittix',
                          'cryptocurrency', 'crypto', 'memecoin', 'altcoin'))
    if financial_only:
        materials = []
        routes = []
        currencies = []
    item['matched_materials'], item['matched_routes'], item['matched_currencies'] = materials, routes, currencies
    title = item['title'].casefold()
    sector = company.get('sector', '').casefold()
    operations = [name for name, terms in OPERATIONS.items() if any(_term_in(title, term) for term in terms)]
    # Analyst price targets describe securities, even when the issuer's sector
    # and the generic word "price" appear, unless the headline also names a
    # recorded exposure or a physical supply/logistics operation.
    # A geographic mention alone does not establish a route disruption.
    route_match = bool(routes and 'logistics' in operations)
    sector_only_rating = (any(_term_in(title, term) for term in FINANCIAL_RATING_TERMS)
                          and not (materials or route_match or currencies)
                          and not any(name in operations for name in ('supply', 'logistics')))
    sector_match = (not financial_only and not sector_only_rating
                    and any(_term_in(title, term) for term in SECTOR_TERMS.get(sector, ())))
    # Semiconductor package assembly is a different business from flexible films.
    # Retain explicit physical material matches, but reject the ambiguous sector word.
    if sector == 'flexible packaging' and not materials and any(_term_in(title, term) for term in
            ('chip', 'chips', 'semiconductor', 'wafer', 'silicon interposer',
             'tsmc', 'globalfoundries', 'amat', 'avgo')):
        sector_match = False
    item['matched_operations'] = operations if materials or route_match or sector_match else []
    item['relevance_kind'] = ('direct_material' if materials else 'route' if route_match else
                              'sector' if sector_match and operations else 'currency' if currencies else 'unmatched')
    item['relevance_score'] = {'direct_material': 0.9, 'route': 0.72, 'sector': 0.62,
                               'currency': 0.45, 'unmatched': 0.0}[item['relevance_kind']]
    if not route_match:
        routes = []
        item['matched_routes'] = []
    item['category'] = 'material' if materials else 'route' if route_match else 'sector' if item['relevance_kind'] == 'sector' else 'currency' if currencies else 'general'
    refs = materials + routes + currencies
    item['relevance_reason'] = (('Headline names a recorded material: ' + ', '.join(materials)) if materials else
                                ('Headline names a recorded route and a logistics term: ' + ', '.join(routes)) if route_match else
                                ('Headline connects the company sector to operations: ' + ', '.join(operations)) if item['relevance_kind'] == 'sector' else
                                ('Headline names a recorded currency: ' + ', '.join(currencies)) if currencies else
                                'No recorded material, operational sector, route disruption, or currency match.')
    item['implication'] = ('This publication may affect the matched exposure; check the original source and current supplier or route facts before changing a plan.'
                           if item['relevance_kind'] != 'unmatched' else 'No company-specific operational implication established from this headline.')
    item['implication_confidence'] = 'conditional' if item['relevance_kind'] != 'unmatched' else 'none'
    item['next_steps'] = ['Read the original source', 'Check current supplier, route, and price evidence'] if item['relevance_kind'] != 'unmatched' else ['Read the original source if relevant']
    return item


def _term_in(text, term):
    return bool(term and re.search(r'(?<!\w)' + re.escape(term.casefold()) + r'(?!\w)', text))


def _google_queries(company):
    """At most three bounded queries from server-owned fictional exposure metadata."""
    if not company.get('company_id'):
        return []
    from .company import get_company
    company = get_company(company['company_id'])
    if not company or not company.get('sector'):
        return []
    terms = []
    for material in company.get('materials', [])[:20]:
        name = re.sub(r'[^a-zA-Z0-9 -]', '', str(material.get('name', ''))).strip().lower()[:55]
        keywords = [re.sub(r'[^a-zA-Z0-9 -]', '', str(x)).strip().lower()[:40]
                    for x in material.get('keywords', [])[:4]]
        distinctive = next((x for x in keywords if len(x) >= 4 and x not in AMBIGUOUS_MATERIAL_TERMS), name)
        if distinctive and distinctive not in terms:
            terms.append(distinctive)
    sector = re.sub(r'[^a-zA-Z0-9 -]', '', str(company['sector'])).strip().lower()[:60]
    queries = []
    for group in (terms[:2], terms[2:6]):
        if group:
            queries.append('(' + ' OR '.join('"' + x + '"' for x in group) + ') (supply OR shortage OR price OR production)')
    aliases = SECTOR_TERMS.get(sector, ())
    if aliases:
        queries.append('(' + ' OR '.join('"' + x + '"' for x in aliases[:2]) + ') (supply chain OR production OR shipping)')
    return [urlunsplit(('https', GOOGLE_NEWS_HOST, '/rss/search',
                       urlencode({'q': q[:220], 'hl': 'en-US', 'gl': 'US', 'ceid': 'US:en'}), ''))
            for q in queries[:3]]


class NewsService:
    """One process-wide, company-neutral refresh shared by all company views."""
    def __init__(self, *, clock=None, transport=None, offline=None, article_enricher=None):
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.transport = transport or _default_transport
        self.offline = (any(os.getenv(name, '').lower() in ('1', 'true', 'yes')
                            for name in ('OFFLINE', 'PROCURE_OFFLINE')) if offline is None else bool(offline))
        from .global_watch import GlobalWatch
        self.global_watch = GlobalWatch(clock=self.clock, transport=self.transport, offline=self.offline)
        # A test/injected feed transport never causes implicit article network calls.
        enabled = os.getenv('NEWS_ARTICLE_ENRICHMENT', '').lower() in ('1', 'true', 'yes')
        if article_enricher is not None:
            self.article_enricher = article_enricher
        elif enabled and transport is None and not self.offline:
            from .news_enrichment import default_enricher
            self.article_enricher = default_enricher()
        else:
            self.article_enricher = None
        self._lock = threading.Condition(threading.RLock())
        self._refreshing = False
        self._snapshot = None
        self._seen = OrderedDict()
        self._company_cache = OrderedDict()
        self._company_refreshing = set()

    def get(self, company: dict, force: bool = False) -> dict:
        view = self._get_global(company, force)
        if self.offline:
            return view
        urls = _google_queries(company)
        if not urls:
            return self.present_view(view, company)
        extra = self._company_news(company, urls)
        view['checked_at'] = max(view['checked_at'], extra['checked_at'])
        view['sources'].extend(extra['sources'])
        view['items'] = list({item['url']: item for item in view['items'] + extra['items']}.values())
        if extra['cache_state'] == 'stale':
            view['cache_state'] = 'stale'
        elif view['cache_state'] == 'stale':
            pass
        elif extra['items']:
            view['cache_state'] = 'fresh' if extra['cache_state'] == 'fresh' else 'cached'
        return self.present_view(view, company)

    def present_view(self, view, company, *, enrich=True):
        view['items'] = rank_items(view.get('items', []), company, MAX_ITEMS, as_of=_now(self.clock))
        if enrich and self.article_enricher and not self.offline:
            view['items'] = self.article_enricher.attach(view['items'])
        return view

    def _get_global(self, company: dict, force: bool = False) -> dict:
        now = _now(self.clock)
        if self.offline:
            snapshot = {'checked_at': now.isoformat(), 'sources': [
                {'source_id': sid, 'name': name, 'state': 'offline', 'last_checked_at': None, 'reason': 'Live public sources are disabled.'}
                for sid, name, _ in SOURCES], 'items': [], 'fx': [], 'cache_state': 'offline'}
            return self._view(snapshot, company)
        with self._lock:
            wait_until = time.monotonic() + REFRESH_DEADLINE_SECONDS + 2
            while self._refreshing:
                remaining = wait_until - time.monotonic()
                if remaining <= 0:
                    if self._snapshot:
                        return self._view({**self._snapshot, 'cache_state': 'stale'}, company)
                    return self._view({'checked_at': now.isoformat(), 'cache_state': 'unavailable',
                                       'sources': [{'source_id': sid, 'name': name, 'state': 'unavailable',
                                                    'last_checked_at': now.isoformat(), 'reason': 'Refresh is still in progress.'}
                                                   for sid, name, _ in SOURCES], 'items': [], 'fx': []}, company)
                self._lock.wait(timeout=remaining)
            if self._snapshot and (now - self._snapshot['time']).total_seconds() < TTL_SECONDS:
                state = 'cached' if self._snapshot['cache_state'] == 'fresh' else self._snapshot['cache_state']
                return self._view({**self._snapshot, 'cache_state': state}, company)
            self._refreshing = True
        try:
            fresh = self._refresh(now)
            with self._lock:
                prior = self._snapshot
                if prior:
                    provider_for = {'eia': 'EIA', 'ecb': 'ECB', 'gdelt': 'GDELT'}
                    old_sources = {source['source_id']: source for source in prior['sources']}
                    for source in fresh['sources']:
                        sid = source['source_id']
                        if source['state'] != 'unavailable':
                            continue
                        old = old_sources.get(sid)
                        if not old or old['state'] not in ('fresh', 'cached', 'stale'):
                            continue
                        if sid == 'ecb_fx' and prior['fx']:
                            fresh['fx'] = prior['fx']
                        elif sid in provider_for:
                            fresh['items'].extend(item for item in prior['items']
                                                  if item['provider'] == provider_for[sid])
                        else:
                            continue
                        source['state'] = 'stale'
                        source['last_checked_at'] = old['last_checked_at']
                        source['reason'] = (source['reason'] + ' Showing previously observed metadata.'
                                            if source['reason'] == 'Provider rate limit reached.'
                                            else 'Refresh failed; showing previously observed metadata.')
                if any(source['state'] == 'stale' for source in fresh['sources']):
                    fresh['cache_state'] = 'stale'
                fresh['items'] = list({item['url']: item for item in fresh['items']}.values())
                self._snapshot = {**fresh, 'time': now}
                return self._view(self._snapshot, company)
        finally:
            with self._lock:
                self._refreshing = False
                self._lock.notify_all()

    def _company_news(self, company, urls):
        key = (company['company_id'], hashlib.sha256('\n'.join(urls).encode()).hexdigest())
        with self._lock:
            deadline = time.monotonic() + REFRESH_DEADLINE_SECONDS
            while key in self._company_refreshing:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    old = self._company_cache.get(key)
                    if old:
                        return {'cache_state': 'stale', 'checked_at': old['checked_at'],
                                'sources': [{**source, 'state': 'stale' if source['state'] in ('fresh', 'cached') else source['state'],
                                             'reason': 'Refresh still in progress; showing previously observed metadata.'}
                                            for source in old['sources']],
                                'items': [_company_item(item, company) for item in old['items']]}
                    stamp = _now(self.clock).isoformat()
                    return {'cache_state': 'unavailable', 'checked_at': stamp,
                            'sources': [{'source_id': f'google_news_{index}',
                                         'name': 'Google News RSS discovery', 'state': 'unavailable',
                                         'last_checked_at': stamp, 'reason': 'Refresh is still in progress.'}
                                        for index in range(1, len(urls) + 1)], 'items': []}
                self._lock.wait(timeout=remaining)
            now = _now(self.clock)
            old = self._company_cache.get(key)
            if old and (now - old['time']).total_seconds() < TTL_SECONDS:
                return {'cache_state': 'cached' if old['cache_state'] == 'fresh' else old['cache_state'],
                        'checked_at': old['checked_at'],
                        'sources': [{**source, 'state': 'cached' if source['state'] == 'fresh' else source['state']}
                                    for source in old['sources']],
                        'items': [_company_item(item, company) for item in old['items']]}
            self._company_refreshing.add(key)
        try:
            return self._refresh_company_news(company, urls)
        finally:
            with self._lock:
                self._company_refreshing.discard(key)
                self._lock.notify_all()

    def _refresh_company_news(self, company, urls):
        now = _now(self.clock)
        key = (company['company_id'], hashlib.sha256('\n'.join(urls).encode()).hexdigest())
        with self._lock:
            old = self._company_cache.get(key)
            if old and (now - old['time']).total_seconds() < TTL_SECONDS:
                return {'cache_state': 'cached' if old['cache_state'] == 'fresh' else old['cache_state'],
                        'checked_at': old['checked_at'],
                        'sources': [{**source, 'state': 'cached' if source['state'] == 'fresh' else source['state']}
                                    for source in old['sources']],
                        'items': [_company_item(item, company) for item in old['items']]}
        stamp = now.isoformat()
        sources, items = [], []
        pool = ThreadPoolExecutor(max_workers=min(3, len(urls)), thread_name_prefix='demo-company-news')
        try:
            futures = [pool.submit(self.transport, url, timeout=TIMEOUT, max_bytes=MAX_BYTES) for url in urls]
            done, _ = wait(futures, timeout=REFRESH_DEADLINE_SECONDS)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        for index, future in enumerate(futures, 1):
            sid = f'google_news_{index}'
            try:
                if future not in done:
                    raise TimeoutError('Refresh deadline exceeded')
                payload = future.result()
                if not isinstance(payload, bytes) or len(payload) > MAX_BYTES:
                    raise ValueError('Invalid response size')
                found = _rss(payload, 'Google News RSS', stamp)
                for item in found:
                    item['_discovery_source_id'] = sid
                items.extend(found)
                sources.append({'source_id': sid, 'name': 'Google News RSS discovery',
                                'state': 'fresh' if found else 'empty', 'last_checked_at': stamp,
                                'reason': 'Public headline metadata received.' if found else 'No valid items in source response.'})
            except _RateLimited:
                sources.append({'source_id': sid, 'name': 'Google News RSS discovery',
                                'state': 'unavailable', 'last_checked_at': stamp,
                                'reason': 'Provider rate limit reached.'})
            except (requests.RequestException, ValueError, TypeError, ET.ParseError, TimeoutError) as error:
                sources.append({'source_id': sid, 'name': 'Google News RSS discovery',
                                'state': 'unavailable', 'last_checked_at': stamp,
                                'reason': _unavailable_reason(error)})
        unique = {item['url']: item for item in items}
        with self._lock:
            for item in unique.values():
                first = self._seen.setdefault(item['url'], item['first_seen_at'])
                item['first_seen_at'] = first
                self._seen.move_to_end(item['url'])
            while len(self._seen) > 1024:
                self._seen.popitem(last=False)
            if old:
                old_sources = {source['source_id']: source for source in old['sources']}
                for source in sources:
                    if source['state'] != 'unavailable' or source['source_id'] not in old_sources:
                        continue
                    prior = old_sources[source['source_id']]
                    if prior['state'] not in ('fresh', 'cached', 'stale'):
                        continue
                    unique.update((item['url'], item) for item in old['items']
                                  if item.get('_discovery_source_id') == source['source_id'])
                    source['state'] = 'stale'
                    source['last_checked_at'] = prior['last_checked_at']
                    source['reason'] += ' Showing previously observed metadata.'
            # Rank this company's complete discovery set before its storage cap.
            by_url = {item['url']: item for item in unique.values()}
            ranked = rank_items((_company_item(item, company) for item in by_url.values()),
                                company, MAX_COMPANY_ITEMS, as_of=now)
            ordered = [by_url[item['url']] for item in ranked]
            state = ('stale' if any(source['state'] == 'stale' for source in sources) else
                     'fresh' if ordered else 'unavailable' if all(source['state'] == 'unavailable' for source in sources) else 'empty')
            self._company_cache[key] = {'time': now, 'checked_at': stamp, 'cache_state': state,
                                        'sources': sources, 'items': ordered}
            self._company_cache.move_to_end(key)
            while len(self._company_cache) > 12:
                self._company_cache.popitem(last=False)
        return {'cache_state': state, 'checked_at': stamp, 'sources': sources,
                'items': [_company_item(item, company) for item in ordered]}

    def _refresh(self, now):
        stamp = now.isoformat()
        sources, items, fx = [], [], []
        pool = ThreadPoolExecutor(max_workers=len(SOURCES), thread_name_prefix='demo-news')
        try:
            futures = {source_id: pool.submit(self.transport, url, timeout=TIMEOUT, max_bytes=MAX_BYTES)
                       for source_id, _, url in SOURCES}
            done, _ = wait(futures.values(), timeout=REFRESH_DEADLINE_SECONDS)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        for source_id, name, _ in SOURCES:
            try:
                if futures[source_id] not in done:
                    raise TimeoutError('Refresh deadline exceeded')
                payload = futures[source_id].result()
                if not isinstance(payload, bytes) or len(payload) > MAX_BYTES:
                    raise ValueError('Invalid response size')
                if source_id == 'eia':
                    found = _rss(payload, 'EIA', stamp)
                elif source_id == 'ecb':
                    found = _rss(payload, 'ECB', stamp)
                elif source_id == 'ecb_fx':
                    found = _fx(payload, stamp)
                    fx.extend(found)
                    found = []
                else:
                    found = _gdelt(payload, stamp)
                items.extend(found)
                sources.append({'source_id': source_id, 'name': name, 'state': 'fresh' if found or source_id == 'ecb_fx' else 'empty',
                                'last_checked_at': stamp, 'reason': 'Official reference data received.' if source_id == 'ecb_fx' else ('Metadata received.' if found else 'No valid items in source response.')})
            except _RateLimited:
                sources.append({'source_id': source_id, 'name': name, 'state': 'unavailable',
                                'last_checked_at': stamp, 'reason': 'Provider rate limit reached.'})
            except (requests.RequestException, ValueError, TypeError, ET.ParseError, TimeoutError, json.JSONDecodeError) as error:
                sources.append({'source_id': source_id, 'name': name, 'state': 'unavailable',
                                'last_checked_at': stamp, 'reason': _unavailable_reason(error)})
        unique = {}
        for item in items:
            if item['url'] not in unique:
                first = self._seen.setdefault(item['url'], item['first_seen_at'])
                self._seen.move_to_end(item['url'])
                if len(self._seen) > 1024:
                    self._seen.popitem(last=False)
                item['first_seen_at'] = first
                unique[item['url']] = item
        ordered = list(unique.values())
        state = 'fresh' if ordered or fx else ('unavailable' if all(x['state'] == 'unavailable' for x in sources) else 'empty')
        return {'checked_at': stamp, 'cache_state': state, 'sources': sources, 'items': ordered, 'fx': fx}

    def _view(self, snapshot, company):
        limitations = ['Headlines and discovery links are metadata, not verified claims or forecasts.',
                       'Company exposure matches are conditional; FX reference rates are informational, not executable offers.',
                       'Relevance score is a fixed title-term heuristic, not a calibrated probability or verified company impact.']
        if snapshot.get('time'):
            limitations.append('Refresh cannot fetch providers again until ' +
                               (snapshot['time'] + timedelta(seconds=TTL_SECONDS)).isoformat() + '.')
        return {'checked_at': snapshot['checked_at'], 'cache_state': snapshot['cache_state'],
                'sources': [{**x, 'state': 'cached' if snapshot['cache_state'] == 'cached' and x['state'] == 'fresh' else x['state']}
                            for x in snapshot['sources']],
                'items': rank_items((_company_item(item, company) for item in snapshot['items']),
                                    company, as_of=_now(self.clock)),
                'fx': [dict(rate) for rate in snapshot['fx'] if rate['quote'] in company.get('currencies', [])],
                'limitations': limitations}
