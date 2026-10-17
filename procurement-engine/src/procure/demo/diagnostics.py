"""Small, content-free operational signals for the public demo."""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
import json
import math
import threading
from time import monotonic


_ROUTES = frozenset(('session', 'companies', 'company', 'news', 'risks',
                     'operator', 'voice', 'research', 'extract', 'offers',
                     'plan', 'recovery', 'rfq', 'export'))
_NEWS_STATES = frozenset(('fresh', 'cached', 'stale', 'unavailable', 'offline'))


def _route_bucket(path: str) -> str:
    if path == '/-/health':
        return 'liveness'
    if path == '/-/ready':
        return 'readiness'
    if path.startswith('/api/demo/'):
        first = path[len('/api/demo/'):].split('/', 1)[0]
        return first if first in _ROUTES else 'other'
    return 'other'


def request_outcome(logger, method: str, path: str, status: int, duration_ms: float) -> None:
    """Log only fixed labels and a capped duration; never inspect headers or body."""
    method = method if method in ('GET', 'POST') else 'OTHER'
    status_class = f'{status // 100}xx' if type(status) is int and 100 <= status <= 599 else 'other'
    duration = 0 if not isinstance(duration_ms, (float, int)) or not math.isfinite(duration_ms) else duration_ms
    event = {'event': 'http_request', 'route': _route_bucket(path), 'method': method,
             'status_class': status_class,
             'outcome': ('error' if status_class == '5xx' else
                         'client_error' if status_class == '4xx' else 'ok'),
             'duration_ms': min(180_000, max(0, int(duration)))}
    logger.info(json.dumps(event, separators=(',', ':')))


def news_freshness(logger, state) -> None:
    """Report only a fixed freshness state, never source or headline metadata."""
    safe_state = state if isinstance(state, str) and state in _NEWS_STATES else 'unknown'
    logger.info(json.dumps({'event': 'news_freshness', 'state': safe_state}, separators=(',', ':')))


class DependencyReadiness:
    """Read-only persistence check with one in-flight call and a response deadline."""

    def __init__(self, backend, *, timeout_seconds: float = 3.0, ttl_seconds: float = 5.0):
        self.backend = backend
        self.timeout_seconds = timeout_seconds
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='demo-readiness') if backend else None
        self._future: Future | None = None
        self._cached = False
        self._expires = 0.0

    def check(self) -> bool:
        if self.backend is None:
            return False  # The hosted release requires durable session claims.
        with self._lock:
            if self._future is None and monotonic() < self._expires:
                return self._cached
            if self._future is None:
                self._future = self._executor.submit(self.backend.probe_readiness)
            future = self._future
        try:
            ready = future.result(timeout=self.timeout_seconds) is True
        except TimeoutError:
            return False
        except Exception:
            ready = False
        with self._lock:
            if self._future is future:
                self._future = None
                self._cached = ready
                self._expires = monotonic() + self.ttl_seconds
        return ready

    def close(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
