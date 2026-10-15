"""Behavioral contract for the public demo's bounded news metadata service."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import threading
import time
import pytest

from procure.demo.news import NewsService, _default_transport


NOW = datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc)
RSS = b'''<rss><channel><item><title>Copper demand rises</title><link>https://www.eia.gov/todayinenergy/detail.php?id=1</link><pubDate>Wed, 14 Oct 2026 09:00:00 GMT</pubDate></item><item><title>Bad</title><link>javascript:alert(1)</link></item></channel></rss>'''
ECB = b'''<rss><channel><item><title>Euro area exchange rates update</title><link>https://www.ecb.europa.eu/press/pr/date/2026/html/x.html</link></item></channel></rss>'''
FX = b'''<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01" xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref"><Cube><Cube time="2026-10-13"><Cube currency="USD" rate="1.15"/><Cube currency="INR" rate="107.1"/></Cube></Cube></gesmes:Envelope>'''
GDELT = b'''{"articles":[{"url":"https://example.org/copper-story?utm_source=x","title":"Copper supply concern","domain":"example.org","seendate":"20261014T083000Z"},{"url":"https://example.org/copper-story","title":"Copper supply concern","domain":"example.org","seendate":"20261014T083000Z"},{"url":"http://127.0.0.1/private","title":"Copper leak"}]}'''
COMPANY = {"materials": [{"material_id": "copper", "name": "Copper", "keywords": ["copper"]}], "routes": [{"route_id": "r1", "origin": "Chile", "destination": "India", "via": ["Suez Canal"], "currency": "USD"}], "currencies": ["USD", "INR"]}


class Clock:
    def __init__(self):
        self.value = NOW

    def __call__(self):
        return self.value


def transport(url, *, timeout, max_bytes):
    if 'todayinenergy' in url:
        return RSS
    if '/rss/press.html' in url:
        return ECB
    if 'eurofxref-daily.xml' in url:
        return FX
    if 'gdeltproject.org' in url:
        return GDELT
    raise AssertionError(url)


def test_parses_metadata_matches_company_and_rejects_malicious_links():
    result = NewsService(clock=lambda: NOW, transport=transport, offline=False).get(COMPANY)
    assert result['cache_state'] == 'fresh'
    assert {x['source_id']: x['state'] for x in result['sources']} == {'eia': 'fresh', 'ecb': 'fresh', 'gdelt': 'fresh', 'ecb_fx': 'fresh'}
    assert all(x['url'].startswith('https://') and x['synthetic'] is False for x in result['items'])
    assert len([x for x in result['items'] if x['publisher'] == 'example.org']) == 1
    copper = next(x for x in result['items'] if x['provider'] == 'EIA')
    assert copper['matched_materials'] == ['copper']
    assert copper['implication_confidence'] == 'conditional'
    assert copper['published_at'] == '2026-10-14T09:00:00+00:00'
    assert result['fx'] == [{'base': 'EUR', 'quote': 'USD', 'rate': '1.15', 'observation_date': '2026-10-13', 'provider': 'ECB', 'retrieved_at': NOW.isoformat(), 'informational': True}, {'base': 'EUR', 'quote': 'INR', 'rate': '107.1', 'observation_date': '2026-10-13', 'provider': 'ECB', 'retrieved_at': NOW.isoformat(), 'informational': True}]


def test_shared_cache_recomputes_relevance_without_refetching_and_expires():
    clock = Clock()
    calls = []
    def fetch(url, **limits):
        calls.append(url)
        return transport(url, **limits)
    service = NewsService(clock=clock, transport=fetch, offline=False)
    first = service.get(COMPANY)
    other = service.get({"materials": [{"material_id": "steel", "name": "Steel", "keywords": ["steel"]}], "routes": [], "currencies": []})
    assert len(calls) == 4
    assert other['cache_state'] == 'cached'
    assert next(x for x in other['sources'] if x['source_id'] == 'eia')['state'] == 'cached'
    assert next(x for x in other['items'] if x['provider'] == 'EIA')['matched_materials'] == []
    clock.value += timedelta(minutes=16)
    service.get(COMPANY)
    assert len(calls) == 8
    assert first['items'][0]['first_seen_at'] == service.get(COMPANY)['items'][0]['first_seen_at']


def test_offline_and_outage_have_explicit_states_without_synthetic_fallback():
    offline = NewsService(clock=lambda: NOW, offline=True).get(COMPANY)
    assert offline['cache_state'] == 'offline' and offline['items'] == []
    def fail(url, **limits):
        raise TimeoutError('private diagnostic')
    failed = NewsService(clock=lambda: NOW, transport=fail, offline=False).get(COMPANY)
    assert failed['cache_state'] == 'unavailable' and failed['items'] == []
    assert all(x['state'] == 'unavailable' and 'private diagnostic' not in x['reason'] for x in failed['sources'])


def test_all_source_outage_stays_unavailable_during_refresh_cap():
    calls = []
    def fail(url, **limits):
        calls.append(url)
        raise TimeoutError()
    service = NewsService(clock=lambda: NOW, transport=fail, offline=False)
    assert service.get(COMPANY)['cache_state'] == 'unavailable'
    repeated = service.get(COMPANY, force=True)
    assert repeated['cache_state'] == 'unavailable'
    assert all(source['state'] == 'unavailable' for source in repeated['sources'])
    assert len(calls) == 4
    assert any('2026-10-14T10:15:00+00:00' in note for note in repeated['limitations'])


def test_standard_offline_switch_does_not_fetch(monkeypatch):
    monkeypatch.setenv('OFFLINE', '1')
    service = NewsService(clock=lambda: NOW, transport=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('network')))
    assert service.get(COMPANY)['cache_state'] == 'offline'


def test_stale_cache_retains_observed_items_after_provider_failure():
    clock = Clock()
    broken = False
    def fetch(url, **limits):
        if broken:
            raise TimeoutError()
        return transport(url, **limits)
    service = NewsService(clock=clock, transport=fetch, offline=False)
    service.get(COMPANY)
    clock.value += timedelta(minutes=16)
    broken = True
    result = service.get(COMPANY)
    assert result['cache_state'] == 'stale' and result['items']
    assert all(x['state'] == 'stale' for x in result['sources'])


def test_one_failed_source_keeps_only_its_old_items_and_original_retrieval_time():
    clock = Clock()
    fail_eia = False
    def fetch(url, **limits):
        if fail_eia and 'todayinenergy' in url:
            raise TimeoutError()
        return transport(url, **limits)
    service = NewsService(clock=clock, transport=fetch, offline=False)
    first = service.get(COMPANY)
    old = next(x for x in first['items'] if x['provider'] == 'EIA')
    clock.value += timedelta(minutes=16)
    fail_eia = True
    result = service.get(COMPANY)
    retained = next(x for x in result['items'] if x['provider'] == 'EIA')
    assert retained['retrieved_at'] == old['retrieved_at']
    assert next(x for x in result['sources'] if x['source_id'] == 'eia')['state'] == 'stale'
    assert next(x for x in result['sources'] if x['source_id'] == 'ecb')['state'] == 'fresh'
    repeated = service.get(COMPANY, force=True)
    assert repeated['cache_state'] == 'stale'
    assert next(x for x in repeated['sources'] if x['source_id'] == 'eia')['state'] == 'stale'


def test_simultaneous_calls_share_one_refresh():
    calls = []
    gate = threading.Barrier(2)
    def fetch(url, **limits):
        calls.append(url)
        if len(calls) == 1:
            gate.wait(timeout=2)
        return transport(url, **limits)
    service = NewsService(clock=lambda: NOW, transport=fetch, offline=False)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(service.get, COMPANY)
        gate.wait(timeout=2)
        b = pool.submit(service.get, COMPANY)
        assert a.result()['items'] and b.result()['items']
    assert len(calls) == 4


def test_force_refresh_respects_shared_cache_cap_and_oversized_source_is_isolated():
    calls = []
    def fetch(url, **limits):
        calls.append(url)
        if 'gdeltproject.org' in url:
            return b'x' * (limits['max_bytes'] + 1)
        return transport(url, **limits)
    service = NewsService(clock=lambda: NOW, transport=fetch, offline=False)
    initial = service.get(COMPANY)
    assert next(x for x in initial['sources'] if x['source_id'] == 'gdelt')['state'] == 'unavailable'
    assert any(x['provider'] == 'EIA' for x in initial['items'])
    forced = service.get(COMPANY, force=True)
    assert forced['cache_state'] == 'cached'
    assert len(calls) == 4


def test_default_transport_disables_redirects_and_environment_credentials(monkeypatch):
    settings = {}
    class Response:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def iter_content(self, chunk_size): return iter([b'ok'])
    class Session:
        trust_env = True
        def get(self, url, **kwargs):
            settings.update(kwargs)
            settings['trust_env'] = self.trust_env
            return Response()
        def close(self): pass
    monkeypatch.setattr('procure.demo.news.requests.Session', Session)
    assert _default_transport('https://www.eia.gov/rss/todayinenergy.xml', timeout=(2, 4), max_bytes=4) == b'ok'
    assert settings['allow_redirects'] is False and settings['stream'] is True
    assert settings['trust_env'] is False
    assert 'headers' not in settings or settings['headers'].get('Accept') == '*/*'


def test_provider_rate_limit_is_reported_without_echoing_response_body(monkeypatch):
    class Response:
        status_code = 429
        def __enter__(self): return self
        def __exit__(self, *args): return False
    class Session:
        trust_env = True
        def get(self, url, **kwargs): return Response()
        def close(self): pass
    monkeypatch.setattr('procure.demo.news.requests.Session', Session)
    with pytest.raises(ValueError, match='rate limit') as error:
        _default_transport('https://api.gdeltproject.org/api/v2/doc/doc', timeout=(2, 8), max_bytes=350_000)
    assert '429' not in str(error.value)

    def fetch(url, **limits):
        if 'gdeltproject.org' in url:
            return _default_transport(url, **limits)
        return transport(url, **limits)
    result = NewsService(clock=lambda: NOW, transport=fetch, offline=False).get(COMPANY)
    source = next(x for x in result['sources'] if x['source_id'] == 'gdelt')
    assert source['state'] == 'unavailable'
    assert 'rate limit' in source['reason'].lower()
    assert any(item['provider'] == 'EIA' for item in result['items'])


def test_fixed_sources_are_fetched_concurrently():
    gate = threading.Barrier(4)
    def fetch(url, **limits):
        gate.wait(timeout=1)
        return transport(url, **limits)
    result = NewsService(clock=lambda: NOW, transport=fetch, offline=False).get(COMPANY)
    assert all(source['state'] == 'fresh' for source in result['sources'])


def test_refresh_deadline_marks_slow_source_unavailable_without_delaying_other_sources(monkeypatch):
    monkeypatch.setattr('procure.demo.news.REFRESH_DEADLINE_SECONDS', 0.08)
    release = threading.Event()
    def fetch(url, **limits):
        if 'todayinenergy' in url:
            release.wait(timeout=0.5)
            return RSS
        return transport(url, **limits)
    start = time.monotonic()
    try:
        result = NewsService(clock=lambda: NOW, transport=fetch, offline=False).get(COMPANY)
        assert time.monotonic() - start < 0.3
        assert next(x for x in result['sources'] if x['source_id'] == 'eia')['state'] == 'unavailable'
        assert next(x for x in result['sources'] if x['source_id'] == 'ecb')['state'] == 'fresh'
        assert not any(item['provider'] == 'EIA' for item in result['items'])
    finally:
        release.set()


def test_partial_stale_refresh_still_respects_total_item_cap():
    from procure.demo.news import MAX_ITEMS
    clock = Clock()
    broken_eia = False
    def many_rss(host):
        return ('<rss><channel>' + ''.join(
            f'<item><title>Copper {i}</title><link>https://{host}/article/{i}</link></item>'
            for i in range(30)) + '</channel></rss>').encode()
    many_gdelt = ('{"articles":[' + ','.join(
        f'{{"url":"https://example.org/article/{i}","title":"Copper {i}"}}' for i in range(30)) + ']}').encode()
    def fetch(url, **limits):
        if 'todayinenergy' in url:
            if broken_eia:
                raise TimeoutError()
            return many_rss('www.eia.gov')
        if '/rss/press.html' in url:
            return many_rss('www.ecb.europa.eu')
        if 'eurofxref-daily.xml' in url:
            return FX
        return many_gdelt
    service = NewsService(clock=clock, transport=fetch, offline=False)
    service.get(COMPANY)
    broken_eia = True
    clock.value += timedelta(minutes=16)
    result = service.get(COMPANY)
    assert len(result['items']) == MAX_ITEMS
    assert next(x for x in result['sources'] if x['source_id'] == 'eia')['state'] == 'stale'


def test_latin1_declared_rss_feed_preserves_headline():
    rss = '<?xml version="1.0" encoding="ISO-8859-1"?><rss><channel><item><title>Crude oil café</title><link>https://www.eia.gov/article/1</link></item></channel></rss>'.encode('latin-1')
    def fetch(url, **limits):
        return rss if 'todayinenergy' in url else transport(url, **limits)
    result = NewsService(clock=lambda: NOW, transport=fetch, offline=False).get(COMPANY)
    assert any(item['title'] == 'Crude oil café' for item in result['items'])


def test_company_discovery_isolated_and_sector_does_not_invent_material_match():
    from urllib.parse import parse_qs, urlsplit
    from procure.demo.company import get_company
    copper = get_company('solara-cables')
    textile = get_company('loomworks')
    calls = []
    def fetch(url, **limits):
        if urlsplit(url).hostname == 'news.google.com':
            calls.append(url)
            query = parse_qs(urlsplit(url).query)['q'][0]
            if 'textile' in query.lower() or 'cotton' in query.lower():
                title = 'Bangladesh garment exports face shipping delays'
            else:
                title = 'Copper cathode supply delays cable production'
            return (f'<rss><channel><item><title>{title}</title><link>https://news.google.com/rss/articles/story</link><pubDate>Wed, 14 Oct 2026 09:00:00 GMT</pubDate><source url="https://example.org">Example publisher</source></item></channel></rss>').encode()
        return transport(url, **limits)
    service = NewsService(clock=lambda: NOW, transport=fetch, offline=False)
    first = service.get(copper)
    second = service.get(textile)
    assert 1 <= len(calls) <= 6
    assert len(set(calls)) == len(calls)
    assert any(item['relevance_kind'] == 'direct_material' and item['matched_materials'] == ['copper'] for item in first['items'])
    assert any(item['relevance_kind'] in ('sector', 'route') and not item['matched_materials'] for item in second['items'])
    assert not any('copper' in item['title'].lower() and item['relevance_kind'] != 'unmatched' for item in second['items'])
    before = len(calls)
    assert service.get(copper)['cache_state'] == 'cached'
    assert len(calls) == before


def test_sector_aliases_and_financial_headlines_do_not_claim_material_supply():
    from procure.demo.company import get_company
    from procure.demo.news import _company_item
    def item(title):
        return {'title': title}
    pharma = _company_item(item('Pharmaceutical plant expands production'), get_company('meridian-pharma'))
    assert pharma['relevance_kind'] == 'sector' and pharma['matched_materials'] == []
    textile = _company_item(item('Textile stock gets target price upgrade'), get_company('loomworks'))
    assert textile['relevance_kind'] == 'unmatched' and textile['matched_materials'] == []
    energy = _company_item(item('Crude oil demand rises'), get_company('harvest-foods'))
    assert energy['relevance_kind'] == 'unmatched' and energy['matched_materials'] == []
    cement = _company_item(item('Cement production rises'), get_company('stonebridge-materials'))
    assert cement['relevance_kind'] == 'sector' and cement['matched_materials'] == []
    deal = _company_item(item('EnergyX inks lithium deal'), get_company('terrapack'))
    assert deal['relevance_kind'] == 'unmatched' and deal['matched_materials'] == []
    currency = _company_item(item('USD financing backs unrelated project'), get_company('solara-cables'))
    assert currency['relevance_kind'] == 'unmatched' and currency['matched_currencies'] == []


def test_discovery_queries_use_canonical_server_catalogue_only():
    from procure.demo.company import get_company
    from procure.demo.news import _google_queries
    hostile = get_company('solara-cables')
    hostile['sector'] = 'secret account'
    hostile['materials'][0]['keywords'] = ['confidential value']
    urls = _google_queries(hostile)
    assert urls and all('confidential' not in url and 'secret' not in url for url in urls)
    assert _google_queries({'company_id': 'unknown', 'sector': 'copper', 'materials': []}) == []


def test_simultaneous_company_news_reads_share_one_provider_refresh():
    from procure.demo.company import get_company
    company = get_company('solara-cables')
    entered = threading.Event()
    release = threading.Event()
    calls = []
    lock = threading.Lock()
    google = b'<rss><channel><item><title>Copper cathode supply update</title><link>https://news.google.com/rss/articles/one</link><pubDate>Wed, 14 Oct 2026 09:00:00 GMT</pubDate></item></channel></rss>'
    def fetch(url, **limits):
        if 'news.google.com' in url:
            with lock:
                calls.append(url)
            entered.set()
            release.wait(timeout=2)
            return google
        return transport(url, **limits)
    service = NewsService(clock=lambda: NOW, transport=fetch, offline=False)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(service.get, company)
        assert entered.wait(timeout=2)
        second = pool.submit(service.get, company)
        time.sleep(0.05)
        release.set()
        assert first.result(timeout=2)['items']
        assert second.result(timeout=2)['items']
    assert len(calls) == 3


def test_waiting_company_news_read_returns_unavailable_at_refresh_deadline(monkeypatch):
    from procure.demo.company import get_company
    monkeypatch.setattr('procure.demo.news.REFRESH_DEADLINE_SECONDS', 0.08)
    company = get_company('solara-cables')
    entered = threading.Event()
    release = threading.Event()
    def fetch(url, **limits):
        if 'news.google.com' in url:
            entered.set()
            release.wait(timeout=0.5)
            return RSS
        return transport(url, **limits)
    service = NewsService(clock=lambda: NOW, transport=fetch, offline=False)
    service._get_global(company)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(service.get, company)
        assert entered.wait(timeout=1)
        started = time.monotonic()
        try:
            waiting = service.get(company)
            assert time.monotonic() - started < 0.3
            assert all(source['state'] == 'unavailable' for source in waiting['sources']
                       if source['source_id'].startswith('google_news_'))
        finally:
            release.set()
        first.result(timeout=2)


def test_public_discovery_source_id_binds_headline_to_exact_source_health():
    from procure.demo.company import get_company
    from procure.demo.news import _company_item
    raw = {'title': 'Copper supply shortage', 'provider': 'Google News RSS',
           '_discovery_source_id': 'google_news_2'}
    mapped = _company_item(raw, get_company('solara-cables'))
    assert mapped.get('source_id') == 'google_news_2'
    assert '_discovery_source_id' not in mapped
    assert raw['_discovery_source_id'] == 'google_news_2'


def test_crypto_catalyst_and_chip_packaging_do_not_map_to_unrelated_industries():
    from procure.demo.company import get_company
    from procure.demo.news import _company_item
    title = 'Litecoin Price Prediction: Remittix Has the Catalyst Buyers Are Watching'
    assert _company_item({'title': title}, get_company('atlas-petrochem'))['relevance_kind'] == 'unmatched'
    chip = 'GlobalFoundries and TSMC establish a silicon interposer supply chain for AI packaging'
    assert _company_item({'title': chip}, get_company('terrapack'))['relevance_kind'] == 'unmatched'
    assert _company_item({'title': chip}, get_company('circuitpeak-systems'))['relevance_kind'] != 'unmatched'
    flex = 'Flexible packaging supply chain adapts to new manufacturing standards'
    assert _company_item({'title': flex}, get_company('terrapack'))['relevance_kind'] == 'sector'
    direct = 'PET resin supply for semiconductor packaging shifts'
    assert _company_item({'title': direct}, get_company('terrapack'))['relevance_kind'] == 'direct_material'


@pytest.mark.parametrize(('company_id', 'title', 'expected_material'), [
    ('atlas-petrochem', 'UBS keeps AUD 9.25 target on Catalyst Metals stock', None),
    ('atlas-petrochem', 'Home Depot Stock: One Big Catalyst', None),
    ('atlas-petrochem', 'AMD stock catalyst after analyst upgrade', None),
    ('atlas-petrochem', 'Catalyst Metals prices jump after quarterly report', None),
    ('stonebridge-materials', 'Aggregate offer price rises in public offering', None),
    ('atlas-petrochem', 'Refinery process catalyst supply disrupted after plant shutdown', 'catalyst'),
    ('atlas-petrochem', 'Catalyst stock levels fall after refinery shipment delay', 'catalyst'),
    ('atlas-petrochem', 'Catalyst warehouse stock falls after supply disruption', 'catalyst'),
    ('atlas-petrochem', 'Catalyst feedstock shortage at chemical plant', 'catalyst'),
    ('atlas-petrochem', 'Analyst reports Catalyst feedstock shortage at chemical plant', 'catalyst'),
    ('stonebridge-materials', 'Crushed aggregate supply price rises after quarry shutdown', 'aggregate'),
    ('stonebridge-materials', 'Crushed aggregate offer price rises after quarry shutdown', 'aggregate'),
])
def test_material_homonyms_require_physical_procurement_context(company_id, title, expected_material):
    from procure.demo.company import get_company
    from procure.demo.news import _company_item
    item = _company_item({'title': title}, get_company(company_id))
    assert item['matched_materials'] == ([expected_material] if expected_material else [])
    assert (item['relevance_kind'] == 'direct_material') == bool(expected_material)
