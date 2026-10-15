"""Scheduled cleanup removes expired documents without reading message payloads."""
from datetime import datetime, timedelta, timezone
import secrets

from procure.demo.session_persistence import MemorySessionPersistence


def test_cleanup_bounds_work_and_preserves_unexpired_history():
    from procure.demo.history_cleanup import run_cleanup
    now = datetime(2026, 10, 15, tzinfo=timezone.utc)
    backend = MemorySessionPersistence()
    expired = [secrets.token_urlsafe(32) for _ in range(17)]
    for sid in expired:
        backend.save(sid, {'expires_at': (now - timedelta(hours=1)).isoformat()}, None)
    live = secrets.token_urlsafe(32)
    backend.save(live, {'expires_at': (now + timedelta(days=1)).isoformat()}, None)
    result = run_cleanup(backend, now=now, page_size=8, max_pages=2)
    assert result['deleted'] == 16 and result['limit'] == 16
    assert backend.load(expired[-1]) is not None
    assert backend.load(live) is not None
    assert run_cleanup(backend, now=now)['deleted'] == 1
    assert backend.load(live) is not None
