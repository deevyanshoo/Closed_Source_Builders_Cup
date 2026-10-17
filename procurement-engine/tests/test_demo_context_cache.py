"""Prepared operator context stays current, scoped, and bounded."""
from copy import deepcopy

import pytest

from procure.demo.context_cache import BoundedContextCache
from procure.demo.operator import _prepare_context, run_turn
from test_demo_operator import Provider, snapshot


SCOPE = 'a' * 64


def prepared(value):
    return {'compact': {'plan': deepcopy(value.get('plan'))}, 'facts': {'x': '1'},
            'encoded': '{}', 'encoded_facts': '{}'}


def test_hit_returns_immutable_preparation_and_mutations_invalidate_without_version_bump():
    cache = BoundedContextCache()
    value = snapshot()
    calls = []

    def build():
        calls.append(1)
        return prepared(value)

    first = cache.get_or_build(SCOPE, value, build)
    with pytest.raises(TypeError):
        first['facts']['x'] = 'tampered'
    first['facts'] = {'x': 'outer replacement'}
    assert cache.get_or_build(SCOPE, value, build)['facts']['x'] == '1'
    assert len(calls) == 1
    for key, update in [('plan', {'plan_id': 'changed'}),
                        ('news', {'items': []})]:
        value[key] = update
        cache.get_or_build(SCOPE, value, build)
    assert len(calls) == 3


def test_nested_preparation_and_bypass_are_read_only():
    cache = BoundedContextCache()
    value = snapshot()
    def build():
        return {'compact': {'news': {'items': [{'title': 'Current'}]}},
                'facts': {'x': '1'}, 'encoded': '{}', 'encoded_facts': '{}'}
    for scope in (SCOPE, None):
        result = cache.get_or_build(scope, value, build)
        with pytest.raises(TypeError):
            result['compact']['news']['items'][0]['title'] = 'changed'
        with pytest.raises(AttributeError):
            result['compact']['news']['items'].append({'title': 'changed'})


def test_session_company_and_context_version_are_distinct():
    cache = BoundedContextCache()
    value = snapshot()
    builds = []

    def build():
        builds.append(1)
        return prepared(value)

    cache.get_or_build(SCOPE, value, build)
    cache.get_or_build('b' * 64, value, build)
    value['company_id'] = 'other-company'
    cache.get_or_build(SCOPE, value, build)
    value['context_version'] = 2
    cache.get_or_build(SCOPE, value, build)
    assert len(builds) == 4


def test_derived_risk_check_clock_does_not_bust_cache_but_risk_and_news_changes_do():
    cache = BoundedContextCache()
    value = snapshot()
    value['risk_report'] = {'checked_at': '2026-10-15T00:00:00+00:00',
                            'alerts': [], 'coverage': {'source_state': 'current'}}
    value['portfolio_risks'] = [{'company_id': value['company_id'],
                                 'company_name': value['company']['name'],
                                 'checked_at': '2026-10-15T00:00:00+00:00',
                                 'counts': {'high': 0}}]
    builds = []
    def build():
        builds.append(1)
        return _prepare_context(value)

    first = cache.get_or_build(SCOPE, value, build)
    assert 'checked_at' not in first['compact']['risk_report']
    assert 'checked_at' not in first['compact']['portfolio_risks'][0]
    value['risk_report']['checked_at'] = '2026-10-15T00:00:01+00:00'
    value['portfolio_risks'][0]['checked_at'] = '2026-10-15T00:00:01+00:00'
    second = cache.get_or_build(SCOPE, value, build)
    assert second['encoded'] == first['encoded'] and len(builds) == 1
    value['risk_report']['coverage']['source_state'] = 'stale'
    cache.get_or_build(SCOPE, value, build)
    value['news']['items'][0]['title'] = 'New public headline'
    cache.get_or_build(SCOPE, value, build)
    assert len(builds) == 3


def test_ttl_lru_and_total_byte_bounds():
    now = [0.0]
    cache = BoundedContextCache(max_entries=2, max_bytes=420, ttl_seconds=60,
                                clock=lambda: now[0])
    builds = []

    def fetch(n):
        value = {'company_id': 'c', 'context_version': 1, 'n': n}
        def build():
            builds.append(n)
            return {'compact': {'n': n}, 'facts': {'x': 'x' * 40},
                    'encoded': '{}', 'encoded_facts': '{}'}
        return cache.get_or_build(SCOPE, value, build)

    fetch(1); fetch(2); fetch(1); fetch(3); fetch(2)
    assert builds == [1, 2, 3, 2]
    now[0] = 61.0
    fetch(2)
    assert builds == [1, 2, 3, 2, 2]
    assert cache.entry_count <= 2 and cache.total_bytes <= 420


def test_invalid_scope_snapshot_oversize_and_failed_build_are_not_cached():
    cache = BoundedContextCache(max_bytes=100)
    value = {'company_id': 'c', 'context_version': 1}
    calls = []
    def build():
        calls.append(1)
        return {'compact': {'text': 'x' * 200}, 'facts': {},
                'encoded': '{}', 'encoded_facts': '{}'}
    for scope, current in [(None, value), ('raw-session-id', value),
                           (SCOPE, {'company_id': 'c', 'context_version': 1,
                                    'invalid': object()}), (SCOPE, value), (SCOPE, value)]:
        cache.get_or_build(scope, current, build)
    assert len(calls) == 5 and cache.entry_count == 0
    failures = []
    def fail():
        failures.append(1)
        raise ValueError('failed')
    for _ in range(2):
        with pytest.raises(ValueError):
            cache.get_or_build(SCOPE, value, fail)
    assert len(failures) == 2 and cache.entry_count == 0


def test_operator_reuses_preparation_but_keeps_each_message_dynamic():
    cache = BoundedContextCache()
    value = snapshot()
    provider = Provider(intent='inspect', action='none', evidence=())
    run_turn(value, 'Inspect inventory', provider, model='test', provider_call='mocked',
             context_cache=cache, cache_scope=SCOPE)
    run_turn(value, 'Inspect copper inventory', provider, model='test', provider_call='mocked',
             context_cache=cache, cache_scope=SCOPE)
    assert cache.hits == 1 and cache.misses == 1
    assert 'Inspect inventory' in provider.calls[0][1]
    assert 'Inspect copper inventory' in provider.calls[3][1]
