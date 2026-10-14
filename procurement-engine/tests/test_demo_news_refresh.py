from datetime import datetime, timedelta, timezone
from procure.demo.company import list_companies
from procure.demo.news_refresh import refresh_portfolio
from test_demo_news_snapshots import bundle

NOW = datetime(2026, 10, 15, 10, tzinfo=timezone.utc)

class Store:
    def __init__(self, prior=None): self.prior, self.created, self.attempts = prior, {}, []
    def read(self, slot): return self.prior
    def create(self, slot, row):
        if slot in self.created: return False
        self.created[slot] = row
        return True
    def create_attempt(self, slot, row): self.attempts.append(row)

class Live:
    def __init__(self, fail=False): self.calls, self.fail = [], fail
    def get(self, company, force=False):
        self.calls.append(company['company_id'])
        stamp = NOW.isoformat()
        return {'checked_at': stamp, 'cache_state': 'unavailable' if self.fail else 'fresh',
                'sources': [{'source_id': 'eia', 'state': 'unavailable' if self.fail else 'fresh', 'last_checked_at': stamp},
                            {'source_id': 'ecb_fx', 'state': 'fresh', 'last_checked_at': stamp}],
                'items': [], 'fx': [{'base': 'EUR', 'quote': 'USD', 'rate': '1.2', 'retrieved_at': stamp}], 'limitations': []}


def test_cli_duplicate_is_successful_noop_but_news_outage_is_failure(monkeypatch, capsys):
    from procure.demo import news_refresh
    monkeypatch.setenv('NEWS_SNAPSHOT_BUCKET', 'private-news')
    monkeypatch.setattr(news_refresh, 'GcsSnapshotStore', lambda bucket: Store())
    for status, expected in [('partial', 0), ('fresh', 0), ('news_unavailable', 1)]:
        monkeypatch.setattr(news_refresh, 'refresh_portfolio', lambda **kwargs:
            {'slot': NOW.isoformat(), 'collection': {'status': status, 'published': False}})
        assert news_refresh.main() == expected
        assert '"published": false' in capsys.readouterr().out

def test_ten_profiles_and_first_writer_wins():
    store, live = Store(), Live()
    result = refresh_portfolio(live=live, store=store, clock=lambda: NOW)
    assert sorted(live.calls) == sorted(p['company_id'] for p in list_companies())
    assert len(store.created) == 1 and result['collection']['published']
    again = refresh_portfolio(live=Live(), store=store, clock=lambda: NOW)
    assert not again['collection']['published']
    assert len(store.created) == 1

def test_fx_only_success_preserves_old_news_clock_and_attempt_receipt():
    prior = bundle()
    prior['slot'] = (NOW-timedelta(hours=1)).isoformat()
    prior['started_at'] = prior['slot']
    prior['completed_at'] = prior['slot']
    prior['collection']['attempted_at'] = prior['slot']
    prior['collection']['news_success_at'] = prior['slot']
    store = Store(prior)
    result = refresh_portfolio(live=Live(fail=True), store=store, clock=lambda: NOW)
    assert result['collection']['news_success_at'] == prior['collection']['news_success_at']
    assert result['collection']['fx_success_at'] == NOW.isoformat()
    assert not store.created and store.attempts

def test_real_news_service_reuses_four_global_feeds_across_portfolio():
    from procure.demo.news import NewsService
    from test_demo_news import transport
    calls = []
    def fetch(url, **limits):
        calls.append(url)
        if 'news.google.com' in url:
            return b'<rss><channel></channel></rss>'
        return transport(url, **limits)
    live = NewsService(clock=lambda: NOW, transport=fetch, offline=False)
    result = refresh_portfolio(live=live, store=Store(), clock=lambda: NOW)
    assert len(result['views']) == 10
    assert sum('news.google.com' not in url for url in calls) == 4
    assert len(calls) == 4 + sum(len(__import__('procure.demo.news', fromlist=['_google_queries'])._google_queries(p)) for p in list_companies())


def test_current_run_shared_global_sources_count_for_every_company_during_query_outage():
    import requests
    from procure.demo.news import NewsService
    from test_demo_news import transport

    def fetch(url, **limits):
        if 'news.google.com' in url:
            raise requests.ConnectionError('Discovery unavailable')
        return transport(url, **limits)

    result = refresh_portfolio(live=NewsService(clock=lambda: NOW, transport=fetch, offline=False),
                               store=Store(), clock=lambda: NOW)
    assert result['collection']['published'] is True
    assert result['collection']['status'] == 'partial'
    assert all(view['collection']['news_success_at'] == NOW.isoformat()
               for view in result['views'].values())
    assert all(view['collection']['fx_success_at'] == NOW.isoformat()
               for view in result['views'].values())


def test_failed_news_carries_source_and_item_observation_clocks():
    prior = bundle()
    previous = (NOW-timedelta(hours=1)).isoformat()
    prior['slot'] = previous
    prior['started_at'] = previous
    prior['completed_at'] = previous
    prior['collection']['attempted_at'] = previous
    prior['collection']['news_success_at'] = previous
    for company_view in prior['views'].values():
        company_view['sources'] = [{'source_id': 'eia', 'state': 'fresh', 'last_checked_at': previous}]
        company_view['items'] = [{'item_id': 'old', 'provider': 'EIA', 'url': 'https://example.org/old',
                                  'title': 'Old item', 'published_at': previous,
                                  'first_seen_at': previous, 'retrieved_at': previous}]
    result = refresh_portfolio(live=Live(fail=True), store=Store(prior), clock=lambda: NOW)
    saved = result['views']['solara-cables']
    assert saved['sources'][0]['last_checked_at'] == previous
    assert saved['items'][0]['published_at'] == previous
    assert saved['items'][0]['retrieved_at'] == previous
    assert result['collection']['news_success_at'] == previous

def test_success_accepts_source_check_after_run_start():
    class LaterLive(Live):
        def get(self, company, force=False):
            row = super().get(company, force=force)
            checked = (NOW+timedelta(milliseconds=1)).isoformat()
            for source in row['sources']: source['last_checked_at'] = checked
            return row
    result = refresh_portfolio(live=LaterLive(), store=Store(), clock=lambda: NOW)
    assert result['collection']['news_success_at'] == NOW.isoformat()
    assert result['collection']['published']

def test_one_company_failure_does_not_renew_its_news_success():
    prior = bundle()
    prior['slot'] = (NOW-timedelta(hours=1)).isoformat()
    prior['started_at'] = prior['slot']
    prior['completed_at'] = prior['slot']
    prior['collection']['attempted_at'] = prior['slot']
    prior['collection']['news_success_at'] = prior['slot']
    class OneFailure(Live):
        def get(self, company, force=False):
            row = super().get(company, force=force)
            if company['company_id'] == 'solara-cables':
                row['sources'][0]['state'] = 'unavailable'
                row['cache_state'] = 'unavailable'
            return row
    result = refresh_portfolio(live=OneFailure(), store=Store(prior), clock=lambda: NOW)
    assert result['views']['solara-cables']['collection']['news_success_at'] == prior['slot']
    assert result['views']['terrapack']['collection']['news_success_at'] == NOW.isoformat()


def test_raised_company_refresh_carries_prior_news_fx_and_provider_clocks():
    prior = bundle()
    old = (NOW-timedelta(hours=1)).isoformat()
    prior.update(slot=old, started_at=old, completed_at=old)
    prior['collection'].update(attempted_at=old, news_success_at=old, fx_success_at=old)
    original = prior['views']['solara-cables']
    original.update(checked_at=old, sources=[
        {'source_id': 'eia', 'name': 'EIA', 'state': 'fresh', 'last_checked_at': old,
         'reason': 'Metadata received.'},
        {'source_id': 'ecb_fx', 'name': 'ECB FX', 'state': 'fresh', 'last_checked_at': old,
         'reason': 'Official reference data received.'}],
        items=[{'item_id': 'old', 'provider': 'EIA', 'url': 'https://example.org/old',
                'title': 'Old headline', 'published_at': old, 'first_seen_at': old,
                'retrieved_at': old}],
        fx=[{'base': 'EUR', 'quote': 'USD', 'rate': '1.1', 'retrieved_at': old}],
        collection={'attempted_at': old, 'news_success_at': old, 'fx_success_at': old,
                    'status': 'fresh'})
    class RaisesForOne(Live):
        def get(self, company, force=False):
            if company['company_id'] == 'solara-cables':
                raise TimeoutError('source deadline')
            return super().get(company, force=force)
    result = refresh_portfolio(live=RaisesForOne(), store=Store(prior), clock=lambda: NOW)
    failed = result['views']['solara-cables']
    assert failed['cache_state'] == 'stale'
    assert [(s['source_id'], s['name'], s['state'], s['last_checked_at']) for s in failed['sources']] == [
        ('eia', 'EIA', 'stale', old), ('ecb_fx', 'ECB FX', 'stale', old)]
    assert failed['items'] == original['items']
    assert failed['fx'] == original['fx']
    assert failed['collection']['news_success_at'] == old
    assert failed['collection']['fx_success_at'] == old
    assert result['views']['terrapack']['collection']['news_success_at'] == NOW.isoformat()
    assert result['collection']['published'] is True


def test_repeated_url_keeps_first_seen_from_prior_hour():
    prior = bundle()
    old = (NOW-timedelta(hours=1)).isoformat()
    prior.update(slot=old, started_at=old, completed_at=old)
    prior['collection']['attempted_at'] = old
    prior['views']['solara-cables']['items'] = [
        {'item_id': 'repeat', 'provider': 'EIA', 'url': 'https://example.org/repeat',
         'title': 'Repeat', 'published_at': old, 'first_seen_at': old, 'retrieved_at': old}]
    class RepeatsOne(Live):
        def get(self, company, force=False):
            row = super().get(company, force=force)
            if company['company_id'] == 'solara-cables':
                row['items'] = [{'item_id': 'repeat', 'provider': 'EIA',
                                 'url': 'https://example.org/repeat', 'title': 'Repeat',
                                 'published_at': old, 'first_seen_at': NOW.isoformat(),
                                 'retrieved_at': NOW.isoformat()}]
            return row
    result = refresh_portfolio(live=RepeatsOne(), store=Store(prior), clock=lambda: NOW)
    item = result['views']['solara-cables']['items'][0]
    assert item['first_seen_at'] == old
    assert item['retrieved_at'] == NOW.isoformat()


def test_cold_worker_uses_old_valid_bundle_after_48_hour_news_outage():
    old_slot = NOW - timedelta(hours=48)
    bad_slot = NOW - timedelta(hours=30)
    old, bad = bundle(), bundle()
    for row, slot in ((old, old_slot), (bad, bad_slot)):
        row.update(slot=slot.isoformat(), started_at=slot.isoformat(), completed_at=slot.isoformat())
        row['collection'].update(attempted_at=slot.isoformat(), news_success_at=slot.isoformat(),
                                 fx_success_at=slot.isoformat())
    old_view = old['views']['solara-cables']
    old_view['sources'] = [{'source_id': 'eia', 'name': 'EIA', 'state': 'fresh',
                            'last_checked_at': old_slot.isoformat()}]
    old_view['items'] = [{'item_id': 'old', 'provider': 'EIA', 'url': 'https://example.org/old',
                          'title': 'Old headline', 'published_at': old_slot.isoformat(),
                          'first_seen_at': old_slot.isoformat(), 'retrieved_at': old_slot.isoformat()}]
    old_view['fx'] = [{'base': 'EUR', 'quote': 'USD', 'rate': '1.1', 'retrieved_at': old_slot.isoformat()}]
    bad['views'].pop('solara-cables')
    class HistoricalStore(Store):
        def __init__(self): super().__init__(); self.reads = []
        def read(self, slot):
            self.reads.append(slot)
            return {old_slot: old, bad_slot: bad}.get(slot)
        def recent_slots(self, before): return [bad_slot, old_slot]
    class AllNewsFails(Live):
        def get(self, company, force=False):
            row = super().get(company, force=force)
            row['sources'][0]['state'] = 'unavailable'
            row['items'] = []
            return row
    store = HistoricalStore()
    result = refresh_portfolio(live=AllNewsFails(), store=store, clock=lambda: NOW)
    saved = result['views']['solara-cables']
    assert saved['cache_state'] == 'stale'
    assert saved['sources'][0]['last_checked_at'] == old_slot.isoformat()
    assert saved['items'] == old_view['items']
    assert saved['fx'][0]['rate'] == '1.2'  # current FX succeeds independently
    assert saved['collection']['fx_success_at'] == NOW.isoformat()
    assert saved['collection']['news_success_at'] == old_slot.isoformat()
    assert result['collection']['news_success_at'] == old_slot.isoformat()
    assert not result['collection']['published'] and store.attempts
    assert len(store.reads) == 10
