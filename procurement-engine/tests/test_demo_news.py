"""Behavioral contract for the public demo's bounded news metadata service."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import threading
import time

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
