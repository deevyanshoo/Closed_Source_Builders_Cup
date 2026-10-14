"""Private, bounded hourly public-news snapshot storage and reader."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import threading
from urllib.parse import quote

import google.auth
from google.auth.exceptions import GoogleAuthError
import requests
from google.auth.transport.requests import AuthorizedSession

from .company import list_companies
from .news import _now

MAX_BUNDLE_BYTES = 2 * 1024 * 1024
MAX_ATTEMPT_BYTES = 8 * 1024
SCHEMA_VERSION = 1
_ALLOWED_VIEW = {'checked_at', 'cache_state', 'sources', 'items', 'fx', 'limitations', 'collection'}
_ALLOWED_ITEM = {'item_id', 'title', 'url', 'publisher', 'provider', 'published_at', 'first_seen_at',
                 'retrieved_at', 'source_tier', 'source_reliability', 'reliability_reason', 'synthetic',
                 'source_id', 'matched_materials', 'matched_routes', 'matched_currencies',
                 'matched_operations', 'relevance_kind', 'relevance_score', 'category',
                 'relevance_reason', 'implication', 'implication_confidence', 'next_steps'}
_ALLOWED_SOURCE = {'source_id', 'name', 'state', 'last_checked_at', 'reason'}
_ALLOWED_FX = {'base', 'quote', 'rate', 'observation_date', 'provider', 'retrieved_at', 'informational'}
_ALLOWED_COLLECTION = {'attempted_at', 'news_success_at', 'fx_success_at', 'status', 'published'}


def _slot(value):
    return _now(lambda: value).replace(minute=0, second=0, microsecond=0)


def catalog_version(profiles):
    public = [(p['company_id'], [(m['material_id'], m['name']) for m in p['materials']],
               [r['route_id'] for r in p['routes']], p['currencies']) for p in profiles]
    return hashlib.sha256(json.dumps(public, sort_keys=True).encode()).hexdigest()[:20]


def _stamp(value):
    if not isinstance(value, str):
        raise ValueError('Invalid timestamp')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Timezone required')
    return parsed.astimezone(timezone.utc)


def validate_bundle(bundle, profiles=None):
    profiles = profiles or list_companies()
    if not isinstance(bundle, dict) or set(bundle) != {'schema_version', 'slot', 'started_at', 'completed_at',
                                                        'catalog_version', 'company_ids', 'views', 'collection'}:
        raise ValueError('Invalid bundle fields')
    if bundle['schema_version'] != SCHEMA_VERSION:
        raise ValueError('Unsupported schema')
    ids = [p['company_id'] for p in profiles]
    if bundle['catalog_version'] != catalog_version(profiles) or bundle['company_ids'] != ids:
        raise ValueError('Incompatible catalog')
    if not isinstance(bundle['views'], dict) or set(bundle['views']) != set(ids):
        raise ValueError('Incomplete company views')
    slot = _stamp(bundle['slot'])
    if slot != _slot(slot):
        raise ValueError('Invalid slot')
    if not slot <= _stamp(bundle['started_at']) <= _stamp(bundle['completed_at']):
        raise ValueError('Invalid run timestamps')
    collection = bundle['collection']
    if not isinstance(collection, dict) or not set(collection) <= _ALLOWED_COLLECTION:
        raise ValueError('Invalid collection')
    _stamp(collection['attempted_at'])
    for key in ('news_success_at', 'fx_success_at'):
        if collection.get(key) is not None:
            _stamp(collection[key])
    for view in bundle['views'].values():
        if not isinstance(view, dict) or not set(view) <= _ALLOWED_VIEW or not {'checked_at', 'cache_state', 'sources', 'items', 'fx'} <= set(view):
            raise ValueError('Invalid view')
        _stamp(view['checked_at'])
        if 'collection' in view:
            vc = view['collection']
            if not isinstance(vc, dict) or not set(vc) <= _ALLOWED_COLLECTION:
                raise ValueError('Invalid company collection')
            for timestamp in ('attempted_at', 'news_success_at', 'fx_success_at'):
                if vc.get(timestamp): _stamp(vc[timestamp])
        for key, allowed, cap in (('sources', _ALLOWED_SOURCE, 8), ('items', _ALLOWED_ITEM, 80), ('fx', _ALLOWED_FX, 40)):
            rows = view[key]
            if not isinstance(rows, list) or len(rows) > cap:
                raise ValueError('Invalid view rows')
            for row in rows:
                if not isinstance(row, dict) or not set(row) <= allowed:
                    raise ValueError('Private or invalid fields')
        for source in view['sources']:
            if source.get('last_checked_at'):
                _stamp(source['last_checked_at'])
        for item in view['items']:
            for key in ('published_at', 'first_seen_at', 'retrieved_at'):
                if item.get(key): _stamp(item[key])
            if not isinstance(item.get('url'), str) or not item['url'].startswith('https://'):
                raise ValueError('Invalid article URL')
        for fx in view['fx']:
            if fx.get('retrieved_at'): _stamp(fx['retrieved_at'])
    encoded = json.dumps(bundle, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
    if len(encoded) > MAX_BUNDLE_BYTES:
        raise ValueError('Bundle exceeds 2 MiB')
    return encoded


def validate_attempt_receipt(receipt, slot=None):
    if not isinstance(receipt, dict) or set(receipt) != {
            'slot', 'attempted_at', 'completed_at', 'status', 'news_success', 'fx_success'}:
        raise ValueError('Invalid attempt receipt fields')
    receipt_slot = _stamp(receipt['slot'])
    attempted = _stamp(receipt['attempted_at'])
    completed = _stamp(receipt['completed_at'])
    if receipt_slot != _slot(receipt_slot) or (slot is not None and receipt_slot != _slot(slot)):
        raise ValueError('Invalid attempt slot')
    if not receipt_slot <= attempted <= completed:
        raise ValueError('Invalid attempt timestamps')
    if _slot(attempted) != receipt_slot:
        raise ValueError('Attempt outside slot')
    if type(receipt['news_success']) is not bool or type(receipt['fx_success']) is not bool:
        raise ValueError('Invalid attempt success flags')
    if receipt['status'] not in ('fresh', 'partial', 'news_unavailable'):
        raise ValueError('Invalid attempt status')
    if (receipt['status'] == 'news_unavailable') == receipt['news_success']:
        raise ValueError('Inconsistent attempt status')
    encoded = json.dumps(receipt, separators=(',', ':'), allow_nan=False).encode()
    if len(encoded) > MAX_ATTEMPT_BYTES:
        raise ValueError('Attempt receipt exceeds 8 KiB')
    return encoded


class GcsSnapshotStore:
    """ADC-backed JSON API; deterministic object names and create-only writes."""
    def __init__(self, bucket: str):
        if not bucket or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-._' for c in bucket):
            raise ValueError('Invalid bucket')
        self.bucket = bucket
        self._session = None
        self._lock = threading.Lock()

    def _client(self):
        with self._lock:
            if self._session is None:
                credentials, _ = google.auth.default(scopes=['https://www.googleapis.com/auth/devstorage.read_write'])
                self._session = AuthorizedSession(credentials)
            return self._session

    @staticmethod
    def _name(slot):
        return 'snapshots/' + _slot(slot).strftime('%Y-%m-%dT%H:00:00Z') + '.json'

    @staticmethod
    def _attempt_name(slot):
        return 'attempts/' + _slot(slot).strftime('%Y-%m-%dT%H:00:00Z') + '.json'

    def read(self, slot):
        name = quote(self._name(slot), safe='')
        response = self._client().get(f'https://storage.googleapis.com/storage/v1/b/{self.bucket}/o/{name}',
                                      params={'alt': 'media'}, timeout=(2, 8), stream=True)
        if response.status_code == 404:
            response.close()
            return None
        response.raise_for_status()
        body = bytearray()
        try:
            for chunk in response.iter_content(65536):
                body.extend(chunk)
                if len(body) > MAX_BUNDLE_BYTES:
                    raise ValueError('Bundle exceeds 2 MiB')
            return json.loads(body)
        finally:
            response.close()

    def recent_slots(self, before):
        """List names only, once, for the seven-day recovery window."""
        before = _slot(before)
        oldest = before - timedelta(days=7)
        response = self._client().get(
            f'https://storage.googleapis.com/storage/v1/b/{self.bucket}/o',
            params={'prefix': 'snapshots/',
                    'startOffset': 'snapshots/' + oldest.strftime('%Y-%m-%dT%H:00:00Z'),
                    'endOffset': 'snapshots/' + (before + timedelta(hours=1)).strftime('%Y-%m-%dT%H:00:00Z'),
                    'maxResults': 200, 'fields': 'items(name)'},
            timeout=(2, 8))
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get('items', []), list):
            raise ValueError('Invalid snapshot listing')
        slots = set()
        for row in payload.get('items', [])[:200]:
            name = row.get('name') if isinstance(row, dict) else None
            if not isinstance(name, str) or not re.fullmatch(r'snapshots/\d{4}-\d{2}-\d{2}T\d{2}:00:00Z\.json', name):
                continue
            try:
                slot = datetime.strptime(name, 'snapshots/%Y-%m-%dT%H:00:00Z.json').replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if oldest <= slot <= before:
                slots.add(slot)
        return sorted(slots, reverse=True)

    def _create_named(self, name, payload):
        response = self._client().post(f'https://storage.googleapis.com/upload/storage/v1/b/{self.bucket}/o',
                                       params={'uploadType': 'media', 'name': name, 'ifGenerationMatch': '0'},
                                       data=payload, headers={'Content-Type': 'application/json'}, timeout=(2, 8))
        if response.status_code == 412:
            return False
        response.raise_for_status()
        return True

    def create(self, slot, bundle):
        payload = validate_bundle(bundle)
        return self._create_named(self._name(slot), payload)

    def create_attempt(self, slot, receipt):
        payload = validate_attempt_receipt(receipt, slot)
        return self._create_named(self._attempt_name(slot), payload)

    def read_attempt(self, slot):
        name = quote(self._attempt_name(slot), safe='')
        response = self._client().get(f'https://storage.googleapis.com/storage/v1/b/{self.bucket}/o/{name}',
                                      params={'alt': 'media'}, timeout=(2, 8), stream=True)
        if response.status_code == 404:
            response.close()
            return None
        response.raise_for_status()
        body = bytearray()
        try:
            for chunk in response.iter_content(8192):
                body.extend(chunk)
                if len(body) > MAX_ATTEMPT_BYTES:
                    raise ValueError('Attempt receipt exceeds 8 KiB')
            receipt = json.loads(body)
            validate_attempt_receipt(receipt, slot)
            return receipt
        finally:
            response.close()


class SnapshotNewsService:
    def __init__(self, *, live, store, clock):
        self.live, self.store, self.clock = live, store, clock
        self._lock = threading.RLock()
        self._checked = None
        self._bundle = None
        self._attempt = None

    def get(self, company: dict, force: bool = False) -> dict:
        now = _now(self.clock)
        if force:
            manual = self.live.get(company, force=True)
            manual['collection'] = {'status': 'manual', 'attempted_at': now.isoformat(),
                                    'news_success_at': None, 'fx_success_at': None,
                                    'published': False, 'mode': 'manual'}
            return manual
        with self._lock:
            if self._checked is None or (now - self._checked).total_seconds() >= 900:
                self._checked = now
                found_direct = False
                for hours in range(8):
                    slot = _slot(now) - timedelta(hours=hours)
                    try:
                        candidate = self.store.read(slot)
                        if candidate is None:
                            continue
                        validate_bundle(candidate)
                        if _stamp(candidate['slot']) != slot:
                            continue
                        if self._bundle is None or _stamp(candidate['slot']) >= _stamp(self._bundle['slot']):
                            self._bundle = candidate
                        found_direct = True
                        break
                    except (OSError, ValueError, KeyError, TypeError, GoogleAuthError, requests.RequestException):
                        continue
                if not found_direct and callable(getattr(self.store, 'recent_slots', None)):
                    try:
                        slots = self.store.recent_slots(_slot(now))
                    except (OSError, ValueError, KeyError, TypeError, GoogleAuthError, requests.RequestException):
                        slots = []
                    oldest = _slot(now) - timedelta(days=7)
                    upper = _slot(now) - timedelta(hours=8)
                    eligible = sorted({slot for slot in slots if isinstance(slot, datetime)
                                       and slot.tzinfo is not None and oldest <= slot <= upper}, reverse=True)
                    for slot in eligible[:8]:
                        try:
                            candidate = self.store.read(slot)
                            if candidate is None:
                                continue
                            validate_bundle(candidate)
                            if _stamp(candidate['slot']) != slot:
                                continue
                            if self._bundle is None or slot >= _stamp(self._bundle['slot']):
                                self._bundle = candidate
                            break
                        except (OSError, ValueError, KeyError, TypeError, GoogleAuthError, requests.RequestException):
                            continue
                if callable(getattr(self.store, 'read_attempt', None)):
                    attempts = []
                    for hours in (0, 1):
                        slot = _slot(now) - timedelta(hours=hours)
                        try:
                            receipt = self.store.read_attempt(slot)
                            if receipt is not None:
                                validate_attempt_receipt(receipt, slot)
                                if _stamp(receipt['completed_at']) <= now:
                                    attempts.append(receipt)
                        except (OSError, ValueError, KeyError, TypeError, GoogleAuthError, requests.RequestException):
                            continue
                    self._attempt = max(attempts, key=lambda x: _stamp(x['attempted_at'])) if attempts else None
            bundle = self._bundle
            attempt = self._attempt
        if bundle and company.get('company_id') in bundle['views']:
            view = json.loads(json.dumps(bundle['views'][company['company_id']]))
            company_collection = view.get('collection') or bundle['collection']
            collection = {**company_collection, 'slot': bundle['slot'],
                          'completed_at': bundle['completed_at'], 'mode': 'scheduled'}
            failed_new_attempt = False
            if attempt and _stamp(attempt['attempted_at']) > _stamp(company_collection['attempted_at']):
                collection['attempted_at'] = attempt['attempted_at']
                collection['status'] = attempt['status']
                failed_new_attempt = not attempt['news_success']
            age = now - _stamp(company_collection['news_success_at']) if company_collection.get('news_success_at') else timedelta.max
            stale_source = False
            for source in view['sources']:
                if source.get('state') in ('fresh', 'cached') and source.get('last_checked_at'):
                    if failed_new_attempt or now - _stamp(source['last_checked_at']) > timedelta(hours=2):
                        source['state'] = 'stale'
                        stale_source = True
            if age > timedelta(hours=2) or stale_source or failed_new_attempt or view['cache_state'] in ('stale', 'unavailable'):
                view['cache_state'] = 'stale' if view['items'] or view['fx'] or stale_source else 'unavailable'
            view['collection'] = collection
            return view
        fallback = self.live.get(company, force=force)
        fallback['collection'] = {'status': attempt['status'] if attempt else 'live_fallback',
                                  'attempted_at': attempt['attempted_at'] if attempt else None,
                                  'news_success_at': None, 'fx_success_at': None,
                                  'mode': 'live_fallback'}
        return fallback
