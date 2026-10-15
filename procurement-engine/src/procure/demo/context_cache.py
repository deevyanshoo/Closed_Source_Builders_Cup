"""Bounded, session-scoped reuse of immutable operator prompt preparation."""
from __future__ import annotations

from collections import OrderedDict
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import json
import logging
import re
from threading import RLock
from time import monotonic
from types import MappingProxyType


logger = logging.getLogger(__name__)
SCHEMA_VERSION = 1
_PREPARED_KEYS = frozenset({'compact', 'facts', 'encoded', 'encoded_facts'})
_OPAQUE_SCOPE = re.compile(r'[a-f0-9]{32,128}\Z')


def _json_default(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError('Unsupported snapshot value')


def _canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False,
                      default=_json_default).encode('ascii')


def without_derived_check_times(snapshot):
    """Exclude only risk evaluation clocks that prepared context omits."""
    projected = dict(snapshot)
    risk = snapshot.get('risk_report')
    if isinstance(risk, dict) and 'checked_at' in risk:
        projected['risk_report'] = {key: value for key, value in risk.items()
                                    if key != 'checked_at'}
    portfolio = snapshot.get('portfolio_risks')
    if isinstance(portfolio, list) and any(isinstance(row, dict) and 'checked_at' in row
                                           for row in portfolio):
        projected['portfolio_risks'] = [
            {key: value for key, value in row.items() if key != 'checked_at'}
            if isinstance(row, dict) else row for row in portfolio]
    return projected


def _freeze(value):
    """Copy JSON-shaped preparation into recursively read-only containers."""
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool, Decimal, date, datetime)):
        return value
    raise TypeError('Unsupported prepared context value')


class BoundedContextCache:
    """A short-lived LRU of trusted immutable preparation, never model output."""

    def __init__(self, *, max_entries=64, max_bytes=2 * 1024 * 1024,
                 ttl_seconds=60, clock=monotonic):
        if not 0 < max_entries <= 64 or not 0 < max_bytes <= 2 * 1024 * 1024 or not 0 < ttl_seconds <= 60:
            raise ValueError('Context cache bounds exceed the allowed maximum.')
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._entries = OrderedDict()
        self._bytes = 0
        self._lock = RLock()
        self.hits = 0
        self.misses = 0

    @property
    def entry_count(self):
        with self._lock:
            return len(self._entries)

    @property
    def total_bytes(self):
        with self._lock:
            return self._bytes

    def _expire(self, now):
        for key, (created, size, _) in list(self._entries.items()):
            if now - created >= self.ttl_seconds:
                self._bytes -= size
                del self._entries[key]

    def get_or_build(self, scope, snapshot, build):
        """Return a fresh outer dict with read-only contents; invalid inputs bypass storage."""
        started = self.clock()
        try:
            if not isinstance(scope, str) or not _OPAQUE_SCOPE.fullmatch(scope):
                raise ValueError('Invalid scope')
            if not isinstance(snapshot, dict) or not isinstance(snapshot.get('company_id'), str) or \
                    not snapshot['company_id'] or type(snapshot.get('context_version')) is not int or \
                    snapshot['context_version'] < 0:
                raise ValueError('Invalid snapshot')
            digest = sha256(_canonical_bytes(without_derived_check_times(snapshot))).hexdigest()
            key = (SCHEMA_VERSION, scope, snapshot['company_id'], snapshot['context_version'], digest)
        except (TypeError, ValueError, OverflowError):
            result = dict(_freeze(build()))
            logger.info('operator_context_cache status=bypass bytes=0 build_ms=%d',
                        int((self.clock() - started) * 1000))
            return result

        with self._lock:
            self._expire(self.clock())
            entry = self._entries.get(key)
            if entry is not None:
                self._entries.move_to_end(key)
                self.hits += 1
                logger.info('operator_context_cache status=hit bytes=%d build_ms=0', entry[1])
                return dict(entry[2])
            self.misses += 1

        built = build()
        build_ms = int((self.clock() - started) * 1000)
        frozen = _freeze(built)
        result = dict(frozen)
        try:
            if not isinstance(built, dict) or frozenset(built) != _PREPARED_KEYS:
                raise ValueError('Invalid prepared context')
            size = len(_canonical_bytes(built))
        except (TypeError, ValueError, OverflowError):
            logger.info('operator_context_cache status=uncacheable bytes=0 build_ms=%d', build_ms)
            return result
        if size > self.max_bytes:
            logger.info('operator_context_cache status=oversize bytes=%d build_ms=%d', size, build_ms)
            return result
        with self._lock:
            now = self.clock()
            self._expire(now)
            previous = self._entries.pop(key, None)
            if previous:
                self._bytes -= previous[1]
            while self._entries and (len(self._entries) >= self.max_entries or
                                     self._bytes + size > self.max_bytes):
                _, (_, removed_size, _) = self._entries.popitem(last=False)
                self._bytes -= removed_size
            self._entries[key] = (now, size, frozen)
            self._bytes += size
        logger.info('operator_context_cache status=miss bytes=%d build_ms=%d', size, build_ms)
        return result
