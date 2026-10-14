from datetime import datetime, timezone
import pytest
from procure.demo.company import list_companies
from procure.demo.news_snapshots import SnapshotNewsService, validate_bundle, catalog_version

NOW = datetime(2026, 10, 15, 10, tzinfo=timezone.utc)

def bundle():
    profiles = list_companies()
    stamp = NOW.isoformat()
    view = {'checked_at': stamp, 'cache_state': 'fresh', 'sources': [], 'items': [], 'fx': [], 'limitations': []}
    return {'schema_version': 1, 'slot': stamp, 'started_at': stamp, 'completed_at': stamp,
            'catalog_version': catalog_version(profiles), 'company_ids': [p['company_id'] for p in profiles],
            'views': {p['company_id']: dict(view) for p in profiles},
            'collection': {'attempted_at': stamp, 'news_success_at': stamp, 'fx_success_at': None, 'status': 'fresh'}}

class Store:
    def __init__(self, row): self.row, self.reads = row, 0
    def read(self, slot): self.reads += 1; return self.row

class Live:
    def get(self, company, force=False): raise AssertionError('unexpected live fetch')

def test_reader_validates_and_memoizes():
    store = Store(bundle())
    service = SnapshotNewsService(live=Live(), store=store, clock=lambda: NOW)
    for profile in list_companies():
        assert service.get(profile)['collection']['slot'] == NOW.isoformat()
    assert store.reads == 1

def test_rejects_private_oversize_and_catalog_incompatible():
    profiles = list_companies()
    for kind in ('private', 'oversize', 'catalog'):
        row = bundle()
        if kind == 'private': row['views'][profiles[0]['company_id']]['email'] = 'secret'
        if kind == 'oversize': row['junk'] = 'x' * (2*1024*1024)
        if kind == 'catalog': row['company_ids'][0] = 'wrong'
        with pytest.raises(ValueError): validate_bundle(row, profiles)

def test_reader_keeps_last_valid_snapshot_when_storage_transport_fails():
    import requests
    store = Store(bundle())
    from datetime import timedelta
    clock = {'now': NOW}
    service = SnapshotNewsService(live=Live(), store=store, clock=lambda: clock['now'])
    profile = list_companies()[0]
    assert service.get(profile)['collection']['slot'] == NOW.isoformat()
    def failed_read(slot): raise requests.ConnectionError('quota or transport unavailable')
    store.read = failed_read
    clock['now'] += timedelta(minutes=16)
    assert service.get(profile)['collection']['slot'] == NOW.isoformat()

def test_store_uses_generation_zero_and_deterministic_hourly_name():
    from procure.demo.news_snapshots import GcsSnapshotStore
    class Response:
        status_code = 412
        def raise_for_status(self): raise AssertionError('precondition loser must be handled')
    class Session:
        def __init__(self): self.calls = []
        def post(self, url, **kwargs): self.calls.append((url, kwargs)); return Response()
    store = GcsSnapshotStore('private-news-bucket')
    session = Session()
    store._session = session
    assert store.create(NOW, bundle()) is False
    assert store.create(NOW.replace(minute=59), bundle()) is False
    assert len(session.calls) == 2
    assert session.calls[0][1]['params']['name'] == session.calls[1][1]['params']['name']
    assert session.calls[0][1]['params']['ifGenerationMatch'] == '0'

def test_force_runs_live_manual_refresh_without_publishing_or_using_bundle():
    class ManualLive:
        def __init__(self): self.calls = []
        def get(self, company, force=False):
            self.calls.append(force)
            return {'checked_at': NOW.isoformat(), 'cache_state': 'fresh', 'sources': [],
                    'items': [], 'fx': [], 'limitations': []}
    store, live = Store(bundle()), ManualLive()
    service = SnapshotNewsService(live=live, store=store, clock=lambda: NOW)
    result = service.get(list_companies()[0], force=True)
    assert live.calls == [True]
    assert store.reads == 0
    assert result['collection']['status'] == 'manual'
    assert result['collection']['mode'] == 'manual'
    assert result['collection']['published'] is False


def test_old_provider_is_stale_despite_recent_company_news_success():
    from datetime import timedelta
    row = bundle()
    old = (NOW-timedelta(hours=3)).isoformat()
    row['views']['solara-cables']['sources'] = [
        {'source_id': 'eia', 'name': 'EIA', 'state': 'fresh', 'last_checked_at': old, 'reason': 'Old observation'},
        {'source_id': 'gdelt', 'name': 'GDELT', 'state': 'fresh', 'last_checked_at': NOW.isoformat(), 'reason': 'New observation'}]
    row['views']['solara-cables']['collection'] = {'attempted_at': NOW.isoformat(),
        'news_success_at': NOW.isoformat(), 'fx_success_at': None, 'status': 'fresh'}
    result = SnapshotNewsService(live=Live(), store=Store(row), clock=lambda: NOW).get(list_companies()[0])
    assert result['sources'][0]['state'] == 'stale'
    assert result['sources'][0]['last_checked_at'] == old
    assert result['sources'][1]['state'] == 'fresh'
    assert result['cache_state'] == 'stale'


def test_adc_failure_falls_back_to_live_without_crashing():
    from google.auth.exceptions import DefaultCredentialsError
    class AuthStore:
        def read(self, slot): raise DefaultCredentialsError('no ADC')
    class FallbackLive:
        def get(self, company, force=False):
            return {'checked_at': NOW.isoformat(), 'cache_state': 'unavailable',
                    'sources': [], 'items': [], 'fx': [], 'limitations': []}
    result = SnapshotNewsService(live=FallbackLive(), store=AuthStore(), clock=lambda: NOW).get(list_companies()[0])
    assert result['collection']['status'] == 'live_fallback'
    assert result['collection']['mode'] == 'live_fallback'

def test_reader_rejects_older_bundle_returned_for_newer_slot():
    from datetime import timedelta
    old = bundle()
    old_slot = (NOW-timedelta(hours=1)).isoformat()
    old['slot'] = old_slot
    old['started_at'] = old_slot
    old['completed_at'] = old_slot
    store = Store(old)
    class FallbackLive:
        def get(self, company, force=False):
            return {'checked_at': NOW.isoformat(), 'cache_state': 'unavailable',
                    'sources': [], 'items': [], 'fx': [], 'limitations': []}
    result = SnapshotNewsService(live=FallbackLive(), store=store, clock=lambda: NOW).get(list_companies()[0])
    assert result['collection']['slot'] == old_slot
    assert store.reads == 2

def test_cold_reader_uses_seven_day_fallback_and_skips_corrupt_newer_slot():
    from datetime import timedelta
    old_slot = NOW - timedelta(hours=48)
    bad_slot = NOW - timedelta(hours=30)
    old, bad = bundle(), bundle()
    for row, slot in ((old, old_slot), (bad, bad_slot)):
        row.update(slot=slot.isoformat(), started_at=slot.isoformat(), completed_at=slot.isoformat())
    old['collection']['news_success_at'] = old_slot.isoformat()
    old['views']['solara-cables']['sources'] = [
        {'source_id': 'eia', 'name': 'EIA', 'state': 'fresh',
         'last_checked_at': old_slot.isoformat()}]
    old['views']['solara-cables']['items'] = [
        {'item_id': 'old', 'provider': 'EIA', 'url': 'https://example.org/old',
         'title': 'Old headline', 'published_at': old_slot.isoformat(),
         'first_seen_at': old_slot.isoformat(), 'retrieved_at': old_slot.isoformat()}]
    bad['views'].pop('solara-cables')
    class HistoricalStore:
        def __init__(self): self.reads, self.lists = [], 0
        def read(self, slot):
            self.reads.append(slot)
            return {old_slot: old, bad_slot: bad}.get(slot)
        def recent_slots(self, before):
            self.lists += 1
            return [bad_slot, old_slot]
    store = HistoricalStore()
    result = SnapshotNewsService(live=Live(), store=store, clock=lambda: NOW).get(list_companies()[0])
    assert result['collection']['slot'] == old_slot.isoformat()
    assert result['cache_state'] == 'stale'
    assert result['sources'][0]['state'] == 'stale'
    assert result['sources'][0]['last_checked_at'] == old_slot.isoformat()
    assert result['items'][0]['first_seen_at'] == old_slot.isoformat()
    assert store.lists == 1 and len(store.reads) == 10


def test_gcs_recent_slots_is_one_bounded_metadata_request():
    from datetime import timedelta
    from procure.demo.news_snapshots import GcsSnapshotStore
    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {'items': [
                {'name': GcsSnapshotStore._name(NOW-timedelta(hours=48))},
                {'name': 'attempts/irrelevant.json'},
                {'name': GcsSnapshotStore._name(NOW-timedelta(days=8))},
                {'name': GcsSnapshotStore._name(NOW+timedelta(hours=1))}]}
    class Session:
        def __init__(self): self.calls = []
        def get(self, url, **kwargs): self.calls.append((url, kwargs)); return Response()
    session = Session()
    store = GcsSnapshotStore('private-news-bucket')
    store._session = session
    assert store.recent_slots(NOW) == [NOW-timedelta(hours=48)]
    assert len(session.calls) == 1
    url, options = session.calls[0]
    assert url.endswith('/b/private-news-bucket/o')
    assert options['params']['prefix'] == 'snapshots/'
    assert options['params']['maxResults'] == 200
    assert options['params']['startOffset'].startswith('snapshots/')
    assert options['params']['endOffset'].startswith('snapshots/')


def test_failed_current_attempt_updates_visible_attempt_without_renewing_success():
    from datetime import timedelta
    prior_slot = NOW - timedelta(hours=1)
    old = bundle()
    old.update(slot=prior_slot.isoformat(), started_at=prior_slot.isoformat(),
               completed_at=prior_slot.isoformat())
    old['collection'].update(attempted_at=prior_slot.isoformat(),
                             news_success_at=prior_slot.isoformat(),
                             fx_success_at=prior_slot.isoformat())
    old['views']['solara-cables']['sources'] = [
        {'source_id': 'eia', 'name': 'EIA', 'state': 'fresh',
         'last_checked_at': prior_slot.isoformat()}]
    old['views']['solara-cables']['items'] = [
        {'item_id': 'old', 'provider': 'EIA', 'url': 'https://example.org/old',
         'title': 'Old headline', 'published_at': prior_slot.isoformat(),
         'first_seen_at': prior_slot.isoformat(), 'retrieved_at': prior_slot.isoformat()}]
    attempt_time = NOW.replace(minute=5).isoformat()
    receipt = {'slot': NOW.isoformat(), 'attempted_at': attempt_time,
               'completed_at': NOW.replace(minute=6).isoformat(),
               'status': 'news_unavailable', 'news_success': False, 'fx_success': True}
    class AttemptStore:
        def __init__(self): self.attempt_reads = []
        def read(self, slot): return old if slot == prior_slot else None
        def read_attempt(self, slot):
            self.attempt_reads.append(slot)
            return receipt if slot == NOW else None
    store = AttemptStore()
    service = SnapshotNewsService(live=Live(), store=store, clock=lambda: NOW.replace(minute=10))
    result = service.get(list_companies()[0])
    assert result['collection']['mode'] == 'scheduled'
    assert 'mode' not in old['collection']
    assert result['collection']['attempted_at'] == attempt_time
    assert result['collection']['status'] == 'news_unavailable'
    assert result['collection']['news_success_at'] == prior_slot.isoformat()
    assert result['collection']['fx_success_at'] == prior_slot.isoformat()
    assert result['cache_state'] == 'stale'
    assert result['sources'][0]['state'] == 'stale'
    assert result['items'][0]['first_seen_at'] == prior_slot.isoformat()
    service.get(list_companies()[1])
    assert store.attempt_reads == [NOW, prior_slot]


def test_attempt_receipt_is_deterministic_generation_zero_and_bounded():
    from procure.demo.news_snapshots import GcsSnapshotStore, validate_attempt_receipt
    receipt = {'slot': NOW.isoformat(), 'attempted_at': NOW.isoformat(),
               'completed_at': NOW.isoformat(), 'status': 'news_unavailable',
               'news_success': False, 'fx_success': True}
    class Response:
        status_code = 412
        def raise_for_status(self): raise AssertionError('first attempt already won')
    class Session:
        def __init__(self): self.calls = []
        def post(self, url, **kwargs): self.calls.append(kwargs); return Response()
    store = GcsSnapshotStore('private-news-bucket')
    session = Session()
    store._session = session
    assert store.create_attempt(NOW, receipt) is False
    assert store.create_attempt(NOW.replace(minute=59), receipt) is False
    assert session.calls[0]['params']['name'] == session.calls[1]['params']['name']
    assert session.calls[0]['params']['ifGenerationMatch'] == '0'
    with pytest.raises(ValueError): validate_attempt_receipt({**receipt, 'private_email': 'secret'})
    with pytest.raises(ValueError): validate_attempt_receipt({**receipt, 'news_success': 'false'})
    with pytest.raises(ValueError): validate_attempt_receipt({**receipt, 'status': 'fresh'})


def test_attempt_read_rejects_oversize_and_wrong_slot():
    from procure.demo.news_snapshots import GcsSnapshotStore
    class Response:
        status_code = 200
        def __init__(self, payload): self.payload = payload
        def raise_for_status(self): pass
        def iter_content(self, chunk_size): yield self.payload
        def close(self): pass
    class Session:
        def __init__(self, payload): self.payload = payload
        def get(self, url, **kwargs): return Response(self.payload)
    store = GcsSnapshotStore('private-news-bucket')
    store._session = Session(b'x' * 8193)
    with pytest.raises(ValueError, match='8 KiB'):
        store.read_attempt(NOW)
    wrong = {'slot': '2026-10-15T09:00:00+00:00', 'attempted_at': NOW.isoformat(),
             'completed_at': NOW.isoformat(), 'status': 'news_unavailable',
             'news_success': False, 'fx_success': False}
    import json
    store._session = Session(json.dumps(wrong).encode())
    with pytest.raises(ValueError): store.read_attempt(NOW)
