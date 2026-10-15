"""Company-fit ordering and bounded official article reading."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from scrapling import Selector

from procure.demo.company import get_company, list_companies
from procure.demo.news import NewsService, _company_item, _gdelt
from procure.demo.news_enrichment import ArticleEnricher, allowed_article, _static_fetch, MAX_ARTICLE_BYTES
from procure.demo.news_ranking import rank_items
from procure.demo.news_snapshots import SnapshotNewsService, catalog_version, validate_bundle


NOW = datetime(2026, 10, 15, 10, tzinfo=timezone.utc)
ATLAS = get_company('atlas-petrochem')


def test_operational_event_precedes_incidental_material_mentions_and_cap():
    titles = ['Farmers weigh propane prices', 'LEGO considers methanol supply',
              'Naphtha cracker shutdown delays petrochemical feedstock production']
    rows = [_company_item({'title': title, 'url': f'https://example.org/{index}',
                           'published_at': (NOW - timedelta(days=index)).isoformat()}, ATLAS)
            for index, title in enumerate(titles)]
    ranked = rank_items(rows, ATLAS, 2, as_of=NOW)
    assert ranked[0]['title'].startswith('Naphtha cracker shutdown')
    assert ranked[0]['relevance_strength'] == 'strong'
    assert all(row['relevance_strength'] == 'background' for row in ranked[1:])
    assert 'operating event' in ranked[0]['ranking_reason']


def test_financial_catalyst_metaphors_do_not_outrank_physical_material_news():
    titles = [
        'Plasma (XPL) Price Movement: BVNK Catalyst and Volatility - CoinMarketCap',
        'BIIB Stock: JPMorgan Sees Increasingly Compelling Catalyst Path For Biogen, Raises Price Target To $300 - Yahoo Finance',
        'Refinery process catalyst supply disrupted after petrochemical plant shutdown',
        'Naphtha price rises as feedstock supply tightens - TradingView',
    ]
    rows = [_company_item({'title': title, 'url': f'https://example.org/{index}'}, ATLAS)
            for index, title in enumerate(titles)]
    ranked = rank_items(rows, ATLAS, as_of=NOW)
    by_title = {row['title']: row for row in ranked}
    assert [by_title[title]['relevance_strength'] for title in titles] == [
        'background', 'background', 'strong', 'related']
    assert ranked[0]['title'] == titles[2]
    assert ranked[1]['title'] == titles[3]


def test_unknown_publication_date_does_not_become_new_from_first_seen():
    rows = [_company_item({'title': 'Naphtha cracker shutdown delays production',
                           'published_at': published, 'first_seen_at': seen,
                           'url': f'https://example.org/{index}'}, ATLAS)
            for index, (published, seen) in enumerate((
                (None, NOW.isoformat()),
                ((NOW - timedelta(days=2)).isoformat(), (NOW - timedelta(days=2)).isoformat()),
                ((NOW + timedelta(days=2)).isoformat(), NOW.isoformat())))]
    ranked = rank_items(rows, ATLAS, as_of=NOW)
    assert ranked[0]['published_at'] is not None
    assert ranked[1]['published_at'] is None
    assert ranked[2]['published_at'].startswith('2026-10-17')


def test_gdelt_missing_or_bad_seen_date_keeps_publication_unknown_and_observation_separate():
    payload = b'{"articles":[{"title":"Naphtha production halted","url":"https://example.org/a"},{"title":"Methanol supply halted","url":"https://example.org/b","seendate":"bad"}]}'
    rows = _gdelt(payload, NOW.isoformat())
    assert len(rows) == 2
    assert all(row['published_at'] is None for row in rows)
    assert all(row['first_seen_at'] == row['retrieved_at'] == NOW.isoformat() for row in rows)


def test_company_discovery_ranks_before_sixty_item_cache_cap():
    from procure.demo.news import MAX_COMPANY_ITEMS
    urls = [f'https://news.google.com/rss/search?q={index}' for index in range(3)]
    def fetch(url, **kwargs):
        group = urls.index(url)
        entries = []
        for index in range(30):
            last = group == 2 and index == 29
            title = ('Naphtha cracker shutdown delays petrochemical production' if last else
                     f'Farmers weigh propane prices {group}-{index}')
            date = 'Mon, 12 Oct 2026 09:00:00 GMT' if last else 'Wed, 14 Oct 2026 09:00:00 GMT'
            entries.append(f'<item><title>{title}</title><link>https://news.google.com/rss/articles/{group}-{index}</link>'
                           f'<pubDate>{date}</pubDate></item>')
        return ('<rss><channel>' + ''.join(entries) + '</channel></rss>').encode()
    service = NewsService(clock=lambda: NOW, transport=fetch, offline=False)
    result = service._refresh_company_news(ATLAS, urls)
    assert len(result['items']) == MAX_COMPANY_ITEMS
    assert result['items'][0]['title'].startswith('Naphtha cracker shutdown')


@pytest.mark.parametrize('url', [
    'http://www.eia.gov/todayinenergy/detail.php?id=1',
    'https://www.eia.gov/todayinenergy/detail.php?id=1&next=2',
    'https://www.eia.gov/todayinenergy/detail.php?id=1&id=2',
    'https://user:pass@www.eia.gov/todayinenergy/detail.php?id=1',
    'https://www.eia.gov:8080/todayinenergy/detail.php?id=1',
    'https://127.0.0.1/todayinenergy/detail.php?id=1',
    'https://www.ecb.europa.eu/press/pr/date/2026/html/ecb.mp260910~a.en.html?next=1',
    'https://www.ecb.europa.eu/press/key/date/2026/html/ecb.sp260910~a.en.html',
])
def test_article_allowlist_rejects_unsafe_or_unreviewed_urls(url):
    assert allowed_article(url) is None


def test_article_reader_caps_excerpt_budget_and_never_guesses_eia_footer_date():
    calls = []
    body = '<html><head><title>Production disruption</title></head><body><p>' + (
        'Naphtha cracker shutdown delays petrochemical feedstock production. ' * 12
    ) + '</p><footer><time datetime="2026-10-15">Today</time></footer></body></html>'
    def fetch(url):
        calls.append(url)
        return SimpleNamespace(status=200, body=body.encode(), css=Selector(content=body).css)
    enricher = ArticleEnricher(fetcher=fetch, clock=lambda: NOW)
    rows = [{'url': f'https://www.eia.gov/todayinenergy/detail.php?id={number}',
             'relevance_strength': 'strong'} for number in (1, 2, 3)]
    first = enricher.attach(rows)
    assert [x['article']['status'] for x in first] == ['enriched', 'enriched', 'unavailable']
    assert first[0]['article']['published_at'] is None
    assert len(first[0]['article']['excerpt']) == 320
    assert first[0]['article']['content_chars'] > 320
    assert len(calls) == 2
    second = enricher.attach(rows)
    assert len(calls) == 2
    assert second[0]['article']['fetched_at'] == first[0]['article']['fetched_at']


def test_ecb_date_uses_official_release_url_and_respects_crawl_delay():
    calls = []
    html = '<html><head><title>Monetary policy decisions</title></head><main><p>' + (
        'Energy inflation and production costs affected the outlook. ' * 8
    ) + '</p></main></html>'
    def fetch(url):
        calls.append(url)
        return SimpleNamespace(status=200, body=html.encode(), css=Selector(content=html).css)
    enricher = ArticleEnricher(fetcher=fetch, clock=lambda: NOW)
    rows = [{'url': f'https://www.ecb.europa.eu/press/pr/date/2026/html/ecb.mp2609{day}~abc.en.html',
             'relevance_strength': 'strong'} for day in ('10', '11')]
    result = enricher.attach(rows)
    assert result[0]['article']['published_at'] == '2026-09-10'
    assert result[1]['article']['reason'] == 'publisher_crawl_delay'
    assert len(calls) == 1


def test_eia_date_only_metadata_is_accepted_without_footer_guess():
    html = '<html><head><meta name="date" content="2026-10-09"><title>Supply</title></head><body><p>' + (
        'Production shutdown reduces naphtha feedstock availability. ' * 8
    ) + '</p><footer><time datetime="2026-10-15">Site update</time></footer></body></html>'
    def fetch(url):
        return SimpleNamespace(status=200, body=html.encode(), css=Selector(content=html).css)
    row = {'url': 'https://www.eia.gov/todayinenergy/detail.php?id=68284', 'relevance_strength': 'strong'}
    article = ArticleEnricher(fetcher=fetch, clock=lambda: NOW).attach([row])[0]['article']
    assert article['published_at'] == '2026-10-09'


def test_static_transport_stops_oversize_response_while_streaming():
    class Response:
        status_code = 200
        headers = {'Content-Type': 'text/html'}
        chunks = 0
    response = Response()
    class Session:
        trust_env = True
        def __init__(self): self.options = None
        def get(self, url, **options):
            self.options = options
            for _ in range(100):
                response.chunks += 1
                if options['content_callback'](b'x' * 8192) != 8192:
                    raise RuntimeError('callback aborted')
            return response
        def close(self): pass
    session = Session()
    with pytest.raises(ValueError, match='article_too_large'):
        _static_fetch('https://www.eia.gov/todayinenergy/detail.php?id=68284', session_factory=lambda: session)
    assert response.chunks <= MAX_ARTICLE_BYTES // 8192 + 1
    assert callable(session.options['content_callback']) and session.options['allow_redirects'] is False
    assert session.trust_env is False


def test_static_transport_curl_callback_parses_bounded_html():
    html = b'<html><title>Energy update</title><p>' + b'Energy production ' * 7 + b'</p></html>'
    class Session:
        def get(self, url, **options):
            assert options['timeout'] == (3, 5)
            assert options['impersonate'] is None
            assert options['headers']['User-Agent'] == 'ScraplingNewsReader/1.0 (bounded public-news enrichment)'
            assert options['content_callback'](html) == len(html)
            return SimpleNamespace(status_code=200, headers={'Content-Type': 'text/html'})
        def close(self): pass
    page = _static_fetch('https://www.eia.gov/todayinenergy/detail.php?id=68284',
                         session_factory=Session)
    assert page.status == 200
    assert page.body == html
    assert page.css('title::text').get() == 'Energy update'


def test_injected_feed_transport_does_not_enable_article_network(monkeypatch):
    monkeypatch.setenv('NEWS_ARTICLE_ENRICHMENT', '1')
    service = NewsService(clock=lambda: NOW, transport=lambda *a, **k: b'', offline=False)
    assert service.article_enricher is None
    assert NewsService(clock=lambda: NOW, offline=True).article_enricher is None


def test_old_snapshot_is_reranked_at_read_without_collection_clock_change():
    profiles = list_companies()
    stamp = NOW.isoformat()
    empty = {'checked_at': stamp, 'cache_state': 'fresh', 'sources': [], 'items': [], 'fx': []}
    bundle = {'schema_version': 1, 'slot': stamp, 'started_at': stamp, 'completed_at': stamp,
              'catalog_version': catalog_version(profiles), 'company_ids': [p['company_id'] for p in profiles],
              'views': {p['company_id']: dict(empty) for p in profiles},
              'collection': {'attempted_at': stamp, 'news_success_at': stamp,
                             'fx_success_at': None, 'status': 'fresh'}}
    bundle['views']['atlas-petrochem']['items'] = [
        {'item_id': str(index), 'title': title, 'url': f'https://example.org/{index}',
         'provider': 'EIA', 'published_at': stamp, 'first_seen_at': stamp, 'retrieved_at': stamp}
        for index, title in enumerate(('Farmers weigh propane prices',
                                       'Naphtha cracker shutdown delays production'))]
    validate_bundle(bundle, profiles)
    class Store:
        def read(self, slot): return bundle
    class Live:
        def get(self, *args, **kwargs): raise AssertionError('live fetch')
    view = SnapshotNewsService(live=Live(), store=Store(), clock=lambda: NOW).get(ATLAS)
    assert view['items'][0]['relevance_strength'] == 'strong'
    assert view['items'][0]['title'].startswith('Naphtha')
    assert view['collection']['news_success_at'] == stamp
    assert 'relevance_strength' not in bundle['views']['atlas-petrochem']['items'][0]


def test_snapshot_nested_article_validation_rejects_unbounded_excerpt():
    profiles = list_companies()
    stamp = NOW.isoformat()
    view = {'checked_at': stamp, 'cache_state': 'fresh', 'sources': [], 'items': [], 'fx': []}
    row = {'schema_version': 1, 'slot': stamp, 'started_at': stamp, 'completed_at': stamp,
           'catalog_version': catalog_version(profiles), 'company_ids': [p['company_id'] for p in profiles],
           'views': {p['company_id']: dict(view) for p in profiles},
           'collection': {'attempted_at': stamp, 'news_success_at': stamp,
                          'fx_success_at': None, 'status': 'fresh'}}
    row['views']['atlas-petrochem']['items'] = [{
        'url': 'https://www.eia.gov/todayinenergy/detail.php?id=68284',
        'article': {'status': 'enriched', 'method': 'scrapling-static', 'fetched_at': stamp,
                    'title': 'Title', 'published_at': None, 'excerpt': 'x' * 321,
                    'content_chars': 321, 'reason': None}}]
    with pytest.raises(ValueError, match='Invalid article text'):
        validate_bundle(row, profiles)


def test_snapshot_reader_enriches_without_advancing_collection_success_clock():
    profiles = list_companies()
    stamp = NOW.isoformat()
    empty = {'checked_at': stamp, 'cache_state': 'fresh', 'sources': [], 'items': [], 'fx': []}
    bundle = {'schema_version': 1, 'slot': stamp, 'started_at': stamp, 'completed_at': stamp,
              'catalog_version': catalog_version(profiles), 'company_ids': [p['company_id'] for p in profiles],
              'views': {p['company_id']: dict(empty) for p in profiles},
              'collection': {'attempted_at': stamp, 'news_success_at': stamp,
                             'fx_success_at': None, 'status': 'fresh'}}
    bundle['views']['atlas-petrochem']['items'] = [{
        'item_id': 'primary', 'title': 'Naphtha cracker shutdown delays petrochemical production',
        'url': 'https://www.eia.gov/todayinenergy/detail.php?id=68284', 'provider': 'EIA',
        'published_at': stamp, 'first_seen_at': stamp, 'retrieved_at': stamp}]
    class Store:
        def read(self, slot): return bundle
    class FakeEnricher:
        def attach(self, items):
            return [{**item, 'article': {'status': 'enriched', 'method': 'scrapling-static',
                     'fetched_at': NOW.isoformat(), 'title': 'Primary', 'published_at': None,
                     'excerpt': 'Short evidence.', 'content_chars': 100, 'reason': None}} for item in items]
    live = NewsService(clock=lambda: NOW, transport=lambda *a, **k: b'',
                       offline=False, article_enricher=FakeEnricher())
    result = SnapshotNewsService(live=live, store=Store(), clock=lambda: NOW).get(ATLAS)
    assert result['items'][0]['article']['status'] == 'enriched'
    assert result['collection']['news_success_at'] == stamp
    assert bundle['views']['atlas-petrochem']['items'][0].get('article') is None


def test_static_transport_explicitly_disables_environment_proxy_and_ca(monkeypatch):
    from curl_cffi import CurlOpt
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:9')
    monkeypatch.setenv('REQUESTS_CA_BUNDLE', '/untrusted/environment-ca.pem')
    class Session:
        verify = '/untrusted/environment-ca.pem'
        curl_options = {}
        def get(self, url, **options):
            assert self.curl_options.get(CurlOpt.PROXY) == ''
            assert self.verify is True
            html = b'<html><p>' + b'Energy production ' * 7 + b'</p></html>'
            options['content_callback'](html)
            return SimpleNamespace(status_code=200, headers={'Content-Type': 'text/html'})
        def close(self): pass
    assert _static_fetch('https://www.eia.gov/todayinenergy/detail.php?id=68284',
                         session_factory=Session).status == 200
