from datetime import datetime, timedelta, timezone
from copy import deepcopy
import pytest

from procure.demo.company import list_companies
from procure.demo.global_watch import GlobalWatch
from procure.demo.news_snapshots import validate_bundle, catalog_version


NOW = datetime(2026, 10, 15, 10, tzinfo=timezone.utc)


def feed(title, url, date='Wed, 14 Oct 2026 09:00:00 GMT'):
    return (f'<rss><channel><item><title>{title}</title><link>{url}</link>'
            f'<pubDate>{date}</pubDate></item></channel></rss>').encode()


def test_global_collection_is_shared_bounded_and_classifies_exposure():
    calls = []
    def transport(url, **limits):
        calls.append((url, limits))
        return feed('Copper cathode shipping via Suez Canal', 'https://example.org/event')
    watch = GlobalWatch(clock=lambda: NOW, transport=transport, offline=False)
    views = [watch.get(profile) for profile in list_companies()]
    assert len(calls) == 4
    assert all(call[1]['max_bytes'] <= 350_000 for call in calls)
    assert all(len(view['events']) == 1 for view in views)
    assert views[0]['events'][0]['exposure']['kind'] == 'direct'
    assert any(view['events'][0]['exposure']['kind'] == 'background' for view in views)
    assert all('confirmed' not in view['events'][0]['exposure']['reason'].lower() for view in views)


def test_fixed_port_query_is_collected_once_for_the_portfolio():
    calls = []
    def transport(url, **limits):
        calls.append(url)
        return b'<rss><channel></channel></rss>'
    watch = GlobalWatch(clock=lambda: NOW, transport=transport, offline=False)
    for profile in list_companies():
        watch.get(profile)
    assert len(calls) == 4
    assert any('port+congestion' in url or 'port%20congestion' in url for url in calls)


def test_unknown_and_old_publication_dates_remain_explicit():
    rows = [feed('Port congestion reported', 'https://example.org/unknown', ''),
            feed('Trade restriction proposal', 'https://example.org/old', 'Wed, 01 Jan 2020 09:00:00 GMT'),
            b'<rss><channel></channel></rss>', b'<rss><channel></channel></rss>']
    iterator = iter(rows)
    watch = GlobalWatch(clock=lambda: NOW, transport=lambda *_args, **_kwargs: next(iterator), offline=False)
    result = watch.get(list_companies()[0])
    assert result['events'][0]['published_at'] is None
    assert result['events'][1]['published_at'].startswith('2020-01-01')
    assert result['events'][0]['first_seen_at'] == NOW.isoformat()


def test_event_cap_keeps_each_topic_and_newest_publications():
    def many(prefix, count):
        rows = ''.join(f'<item><title>{prefix} shipping update {i}</title>'
                       f'<link>https://example.org/{prefix}/{i}</link>'
                       f'<pubDate>Wed, 14 Oct 2026 {i % 24:02d}:00:00 GMT</pubDate></item>'
                       for i in range(count))
        return f'<rss><channel>{rows}</channel></rss>'.encode()
    def transport(url, **limits):
        if 'conflict' in url:
            return many('conflict', 30)
        if 'Suez' in url:
            return many('chokepoint', 2)
        if 'port+congestion' in url:
            return many('port', 2)
        return many('restriction', 2)
    events = GlobalWatch(clock=lambda: NOW, transport=transport, offline=False).get(list_companies()[0])['events']
    assert len(events) <= 18
    assert {'conflict', 'chokepoint', 'port', 'restriction'} <= {event['topic'] for event in events}
    assert any(event['url'].endswith('/23') for event in events)


def test_recorded_hormuz_route_and_steel_material_are_direct_matches():
    from procure.demo.global_watch import exposure
    company = {'materials': [{'material_id': 'steel', 'name': 'Steel', 'keywords': ['steel']}],
               'routes': [{'route_id': 'hormuz-route', 'origin': 'Dubai', 'destination': 'India',
                           'via': ['Strait of Hormuz']}], 'suppliers': [], 'locations': []}
    assert exposure({'title': 'Hormuz shipping delays reported'}, company)['matched_routes'] == ['hormuz-route']
    assert exposure({'title': 'Steel export restriction proposed'}, company)['matched_materials'] == ['steel']
    assert exposure({'title': 'India shipping update'}, company)['kind'] == 'background'


def test_country_only_match_is_indirect_and_malformed_feed_marks_source_unavailable():
    from procure.demo.global_watch import exposure
    company = {'materials': [], 'routes': [], 'suppliers': [{'country': 'Chile'}], 'locations': []}
    assert exposure({'title': 'Chile export restrictions announced'}, company)['kind'] == 'indirect'
    def transport(url, **limits):
        return b'<rss><channel><item>' if 'conflict' in url else b'<rss><channel></channel></rss>'
    watch = GlobalWatch(clock=lambda: NOW, transport=transport, offline=False).get(company)
    assert watch['status'] == 'partial'
    assert watch['sources'][0]['state'] == 'unavailable'
    assert watch['success_at'] == NOW.isoformat()


def test_outage_keeps_old_events_and_original_success_clock():
    calls = {'fail': False}
    clock = {'now': NOW}
    def transport(url, **limits):
        if calls['fail']:
            raise TimeoutError('offline')
        return feed('Copper cathode shipping via Suez Canal', 'https://example.org/event')
    watch = GlobalWatch(clock=lambda: clock['now'], transport=transport, offline=False)
    first = watch.get(list_companies()[0])
    calls['fail'] = True
    clock['now'] += timedelta(hours=1)
    later = watch.get(list_companies()[0])
    assert later['status'] == 'stale'
    assert later['success_at'] == first['success_at']
    assert later['events'][0]['retrieved_at'] == first['events'][0]['retrieved_at']
    assert later['sources'][0]['last_checked_at'] == first['sources'][0]['last_checked_at']


def test_partial_outage_keeps_failed_source_evidence_when_fresh_events_fill_cap():
    from procure.demo.global_watch import carry_watch, MAX_EVENTS
    old = (NOW - timedelta(hours=1)).isoformat()
    current = {'attempted_at': NOW.isoformat(), 'success_at': NOW.isoformat(),
               'status': 'partial', 'sources': [
                   {'source_id': 'global_conflict', 'name': 'Conflict', 'state': 'unavailable',
                    'last_checked_at': NOW.isoformat(), 'reason': 'Timeout'},
                   {'source_id': 'global_port', 'name': 'Port', 'state': 'fresh',
                    'last_checked_at': NOW.isoformat(), 'reason': 'Metadata received'}],
               'events': [{'item_id': str(i), 'title': f'Port event {i}',
                           'url': f'https://example.org/port/{i}', 'publisher': 'Example',
                           'provider': 'Google News RSS', 'published_at': NOW.isoformat(),
                           'first_seen_at': NOW.isoformat(), 'retrieved_at': NOW.isoformat(),
                           'source_id': 'global_port', 'topic': 'port'} for i in range(MAX_EVENTS)]}
    previous = {'attempted_at': old, 'success_at': old, 'status': 'fresh',
                'sources': [{'source_id': 'global_conflict', 'name': 'Conflict', 'state': 'fresh',
                             'last_checked_at': old, 'reason': 'Metadata received'}],
                'events': [{'item_id': 'old-conflict', 'title': 'Conflict shipping event',
                            'url': 'https://example.org/old-conflict', 'publisher': 'Example',
                            'provider': 'Google News RSS', 'published_at': old,
                            'first_seen_at': old, 'retrieved_at': old,
                            'source_id': 'global_conflict', 'topic': 'conflict'}]}
    merged = carry_watch(current, previous)
    assert merged['status'] == 'partial'
    assert len(merged['events']) == MAX_EVENTS
    assert merged['sources'][0]['state'] == 'stale'
    assert merged['sources'][0]['last_checked_at'] == old
    assert any(row['item_id'] == 'old-conflict' and
               (row['published_at'], row['first_seen_at'], row['retrieved_at']) == (old, old, old)
               for row in merged['events'])


def test_scheduled_force_rechecks_once_inside_read_cache_ttl():
    calls = []
    def transport(url, **limits):
        calls.append(url)
        return b'<rss><channel></channel></rss>'
    watch = GlobalWatch(clock=lambda: NOW, transport=transport, offline=False)
    watch.get(list_companies()[0])
    watch.get(list_companies()[1])
    assert len(calls) == 4
    watch.get(list_companies()[0], force=True)
    assert len(calls) == 8


def test_v1_snapshot_accepts_optional_global_watch_but_rejects_invalid_fields():
    profiles = list_companies()
    stamp = NOW.isoformat()
    view = {'checked_at': stamp, 'cache_state': 'fresh', 'sources': [], 'items': [], 'fx': []}
    bundle = {'schema_version': 1, 'slot': stamp, 'started_at': stamp, 'completed_at': stamp,
              'catalog_version': catalog_version(profiles), 'company_ids': [p['company_id'] for p in profiles],
              'views': {p['company_id']: dict(view) for p in profiles},
              'collection': {'attempted_at': stamp, 'news_success_at': stamp, 'fx_success_at': None, 'status': 'fresh'}}
    validate_bundle(bundle)
    bundle['views'][profiles[0]['company_id']]['global_watch'] = {
        'attempted_at': stamp, 'success_at': stamp, 'status': 'fresh', 'sources': [], 'events': []}
    validate_bundle(bundle)
    bundle['views'][profiles[0]['company_id']]['global_watch']['secret'] = 'x'
    with pytest.raises(ValueError):
        validate_bundle(bundle)


def test_snapshot_rejects_unrecognized_watch_topic_and_private_url():
    profiles = list_companies()
    stamp = NOW.isoformat()
    view = {'checked_at': stamp, 'cache_state': 'fresh', 'sources': [], 'items': [], 'fx': [],
            'global_watch': {'attempted_at': stamp, 'success_at': stamp, 'status': 'fresh',
                             'sources': [], 'events': [{'item_id': 'one', 'title': 'Port story',
                             'url': 'https://example.org/story', 'publisher': 'Example',
                             'provider': 'Google News RSS', 'published_at': None,
                             'first_seen_at': stamp, 'retrieved_at': stamp,
                             'source_id': 'global_chokepoint', 'topic': 'chokepoint',
                             'exposure': {'kind': 'background', 'reason': 'Context only.',
                                          'matched_materials': [], 'matched_routes': [],
                                          'next_action': 'Read source.'}}]}}
    row = {'schema_version': 1, 'slot': stamp, 'started_at': stamp, 'completed_at': stamp,
           'catalog_version': catalog_version(profiles), 'company_ids': [p['company_id'] for p in profiles],
           'views': {p['company_id']: deepcopy(view) for p in profiles},
           'collection': {'attempted_at': stamp, 'news_success_at': stamp, 'fx_success_at': None, 'status': 'fresh'}}
    validate_bundle(row)
    row['views'][profiles[0]['company_id']]['global_watch']['events'][0]['topic'] = 'secret'
    with pytest.raises(ValueError):
        validate_bundle(row)
    row['views'][profiles[0]['company_id']]['global_watch']['events'][0]['topic'] = 'chokepoint'
    row['views'][profiles[0]['company_id']]['global_watch']['events'][0]['url'] = 'https://127.0.0.1/private'
    with pytest.raises(ValueError):
        validate_bundle(row)
    row['views'][profiles[0]['company_id']]['global_watch']['events'][0]['url'] = 'https://example.org/story'
    row['views'][profiles[0]['company_id']]['global_watch']['sources'] = [
        {'source_id': 'global_port', 'name': {'private': 'data'}, 'state': 'fresh',
         'last_checked_at': stamp}]
    with pytest.raises(ValueError):
        validate_bundle(row)
