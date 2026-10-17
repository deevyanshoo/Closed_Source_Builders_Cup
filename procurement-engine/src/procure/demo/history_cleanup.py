"""Bounded scheduled expiry cleanup. Never log private session contents."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os

from .session_persistence import FirestoreSessionPersistence


def run_cleanup(backend, *, now=None, page_size=32, max_pages=4):
    if type(page_size) is not int or not 1 <= page_size <= 32:
        raise ValueError('Invalid cleanup page size')
    if type(max_pages) is not int or not 1 <= max_pages <= 4:
        raise ValueError('Invalid cleanup page count')
    now = now or datetime.now(timezone.utc)
    deleted = 0
    for _ in range(max_pages):
        count = backend.cleanup(now, limit=page_size)
        deleted += count
        if count < page_size:
            break
    return {'deleted': deleted, 'limit': page_size * max_pages,
            'checked_at': now.isoformat()}


def main():
    try:
        backend = FirestoreSessionPersistence(os.environ.get('GOOGLE_CLOUD_PROJECT', ''))
        print(json.dumps({'status': 'completed', **run_cleanup(backend)}))
        return 0
    except Exception:
        print(json.dumps({'status': 'unavailable', 'message': 'History cleanup could not complete.'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
