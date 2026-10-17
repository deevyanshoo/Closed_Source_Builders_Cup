"""Real state boundary tests; no provider or account access."""
from datetime import datetime, timezone
import pytest
from procure.demo.sessions import SessionStore, SessionError


def test_expired_cookie_cannot_resurrect_its_offers():
    now = [datetime(2026, 10, 14, tzinfo=timezone.utc)]
    store = SessionStore(clock=lambda: now[0], ttl_seconds=2, max_sessions=2)
    first = store.create()
    first.offers['private'] = {'offer_id': 'private'}
    assert len(first.session_id) >= 40 and first.csrf_token != first.session_id
    now[0] = datetime(2026, 10, 14, 0, 0, 3, tzinfo=timezone.utc)
    with pytest.raises(SessionError):
        store.get(first.session_id)
    second = store.create()
    assert second.session_id != first.session_id and not second.offers


def test_capacity_refuses_new_sessions_without_evicting_active_state():
    store = SessionStore(max_sessions=1)
    first = store.create()
    with pytest.raises(SessionError) as exc:
        store.create()
    assert exc.value.status_code == 503
    assert store.get(first.session_id) is first


def test_reset_invalidates_old_handle_and_preserves_other_session():
    store = SessionStore()
    first, second = store.create(), store.create()
    first.offers['a'] = {'offer_id': 'a'}
    second.offers['b'] = {'offer_id': 'b'}
    store.invalidate(first)
    assert not first.active and not first.offers
    with pytest.raises(SessionError):
        store.get(first.session_id)
    assert store.get(second.session_id).offers['b']['offer_id'] == 'b'


def test_invalidation_clears_plan_recovery_request_and_rfq():
    store = SessionStore()
    value = store.create()
    value.last_plan = {'plan_id': 'baseline'}
    value.plan_request = {'budget': '100000'}
    value.recovery = {'comparison_id': 'changed'}
    value.rfq = {'draft_id': 'unsent'}
    store.invalidate(value)
    assert value.last_plan is None
    assert value.plan_request is None
    assert value.recovery is None
    assert value.rfq is None
