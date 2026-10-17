"""Bounded, private Firestore REST storage for anonymous demo sessions."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
import re
import threading
import zlib

import google.auth
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import AuthorizedSession
import requests


MAX_UNCOMPRESSED_BYTES = 8 * 1024 * 1024
MAX_ENCODED_BYTES = 700_000  # Firestore document limit is 1 MiB, including field overhead.
_ID = re.compile(r'[A-Za-z0-9_-]{43}\Z')
_DAY = re.compile(r'\d{4}-\d{2}-\d{2}\Z')
_READINESS_PROBE_ID = '0' * 43  # Reserved anonymous ID; never created by the probe.


class StorageConflict(Exception):
    pass


class StorageUnavailable(Exception):
    pass


class StorageInvalid(Exception):
    pass


class StorageTooLarge(Exception):
    pass


def document_id(session_id: str) -> str:
    if not isinstance(session_id, str) or not _ID.fullmatch(session_id):
        raise StorageInvalid('Invalid session ID')
    return hashlib.sha256(session_id.encode('ascii')).hexdigest()


def encode_state(state: dict) -> str:
    try:
        raw = json.dumps(state, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise StorageInvalid('Session state is not valid JSON') from exc
    if len(raw) > MAX_UNCOMPRESSED_BYTES:
        raise StorageTooLarge('Session exceeds storage limit')
    encoded = base64.b64encode(zlib.compress(raw, level=6)).decode('ascii')
    if len(encoded) > MAX_ENCODED_BYTES:
        raise StorageTooLarge('Session exceeds document limit')
    return encoded


def decode_state(encoded: str) -> dict:
    if not isinstance(encoded, str) or len(encoded) > MAX_ENCODED_BYTES:
        raise StorageInvalid('Invalid session payload')
    try:
        compressed = base64.b64decode(encoded, validate=True)
        decoder = zlib.decompressobj()
        raw = decoder.decompress(compressed, MAX_UNCOMPRESSED_BYTES + 1)
        if len(raw) > MAX_UNCOMPRESSED_BYTES or decoder.unconsumed_tail or not decoder.eof:
            raise ValueError('Oversized session payload')
        raw += decoder.flush()
        if len(raw) > MAX_UNCOMPRESSED_BYTES or decoder.unused_data:
            raise ValueError('Oversized session payload')
        state = json.loads(raw)
        if not isinstance(state, dict):
            raise ValueError('Invalid session payload')
        return state
    except (ValueError, UnicodeError, zlib.error, RecursionError) as exc:
        raise StorageInvalid('Invalid session payload') from exc


class MemorySessionPersistence:
    """Deterministic test backend with the same version and payload bounds."""
    def __init__(self):
        self._documents = {}
        self._budgets = {}
        self._lock = threading.RLock()
        self.unavailable = False

    def _available(self):
        if self.unavailable:
            raise StorageUnavailable('Storage unavailable')

    def load(self, session_id: str):
        key = document_id(session_id)
        with self._lock:
            self._available()
            value = self._documents.get(key)
            if value is None:
                return None
            state = decode_state(value[0])
            if len(value) > 2:
                state['requests'] = value[2]
            return state, value[1]

    def probe_readiness(self) -> bool:
        self.load(_READINESS_PROBE_ID)
        return True

    def save(self, session_id: str, state: dict, expected_version):
        key = document_id(session_id)
        payload = encode_state(state)
        with self._lock:
            self._available()
            old = self._documents.get(key)
            if (old is None and expected_version is not None) or (old is not None and old[1] != expected_version):
                raise StorageConflict('Session version changed')
            version = (old[1] + 1) if old else 1
            count = old[2] if old and len(old) > 2 else state.get('requests', 0)
            self._documents[key] = (payload, version, count)
            return version

    def delete(self, session_id: str, expected_version):
        key = document_id(session_id)
        with self._lock:
            self._available()
            old = self._documents.get(key)
            if old is None or old[1] != expected_version:
                raise StorageConflict('Session version changed')
            del self._documents[key]

    def cleanup(self, now: datetime, limit: int = 8):
        with self._lock:
            self._available()
            removed = 0
            for key, document in list(self._documents.items()):
                if removed >= limit:
                    break
                try:
                    expires = datetime.fromisoformat(decode_state(document[0])['expires_at'])
                except (KeyError, ValueError, StorageInvalid):
                    continue
                if expires <= now:
                    del self._documents[key]
                    removed += 1
            return removed

    def reserve_research_budget(self, day: str, limit: int) -> bool:
        _validate_budget(day, limit)
        with self._lock:
            self._available()
            current = self._budgets.get(day, 0)
            if current >= limit:
                return False
            self._budgets[day] = current + 1
            return True

    def reserve_session_request(self, session_id: str, limit: int, initial_count: int,
                                expected_version):
        key = document_id(session_id)
        _validate_request_budget(limit, initial_count)
        with self._lock:
            self._available()
            old = self._documents.get(key)
            if old is None or old[1] != expected_version:
                raise StorageConflict('Session version changed')
            count = old[2] if len(old) > 2 else initial_count
            if count >= limit:
                return None
            self._documents[key] = (old[0], old[1] + 1, count + 1)
            return count + 1, old[1] + 1


def _validate_budget(day: str, limit: int):
    if not isinstance(day, str) or not _DAY.fullmatch(day):
        raise StorageInvalid('Invalid budget day')
    try:
        if datetime.fromisoformat(day).date().isoformat() != day:
            raise ValueError()
    except ValueError as exc:
        raise StorageInvalid('Invalid budget day') from exc
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise StorageInvalid('Invalid budget limit')


def _validate_request_budget(limit: int, initial_count: int):
    if (type(limit) is not int or not 1 <= limit <= 100_000 or
            type(initial_count) is not int or initial_count < 0):
        raise StorageInvalid('Invalid session request budget')


class FirestoreSessionPersistence:
    """Firestore REST with ADC, hashed document IDs and preconditioned writes."""
    def __init__(self, project: str, database: str = '(default)'):
        if not isinstance(project, str) or not re.fullmatch(r'[a-z][a-z0-9-]{4,62}', project):
            raise StorageInvalid('Invalid Google Cloud project')
        if database != '(default)':
            raise StorageInvalid('Only the default Firestore database is supported')
        self.database_name = f'projects/{project}/databases/{database}'
        self.base = f'https://firestore.googleapis.com/v1/{self.database_name}/documents'
        self._client = None
        self._clients = threading.local()

    def _request(self, method, path, **kwargs):
        try:
            client = self._client or getattr(self._clients, 'client', None)
            if client is None:
                credentials, _ = google.auth.default(scopes=['https://www.googleapis.com/auth/datastore'])
                client = AuthorizedSession(credentials)
                self._clients.client = client
            return client.request(method, self.base + path, timeout=(2, 8), **kwargs)
        except (GoogleAuthError, requests.RequestException, OSError) as exc:
            raise StorageUnavailable('Session storage unavailable') from exc

    @staticmethod
    def _document(response):
        try:
            result = response.json()
            if not isinstance(result, dict) or not isinstance(result.get('updateTime'), str):
                raise ValueError()
            return result
        except (ValueError, TypeError) as exc:
            raise StorageInvalid('Invalid Firestore response') from exc

    @staticmethod
    def _is_conflict(response, conflict=(409, 412)):
        if response.status_code in conflict:
            return True
        if response.status_code != 400:
            return False
        try:
            body = response.json()
        except (ValueError, TypeError):
            return False
        return (isinstance(body, dict) and isinstance(body.get('error'), dict) and
                body['error'].get('status') == 'FAILED_PRECONDITION')

    @classmethod
    def _status(cls, response, *, conflict=(409, 412)):
        if cls._is_conflict(response, conflict):
            raise StorageConflict('Session version changed')
        if not 200 <= response.status_code < 300:
            raise StorageUnavailable('Session storage unavailable')

    def load(self, session_id: str):
        key = document_id(session_id)
        response = self._request('GET', '/demo_sessions/' + key)
        if response.status_code == 404:
            return None
        self._status(response)
        document = self._document(response)
        try:
            fields = document['fields']
            state = decode_state(fields['payload']['stringValue'])
            if 'requestCount' in fields:
                count = int(fields['requestCount']['integerValue'])
                if count < 0:
                    raise ValueError()
                state['requests'] = count
            return state, document['updateTime']
        except (KeyError, TypeError, ValueError) as exc:
            raise StorageInvalid('Invalid Firestore session document') from exc

    def probe_readiness(self) -> bool:
        """Check the database through one projected read; document absence is healthy."""
        name = (f'{self.database_name}/documents/demo_sessions/'
                f'{document_id(_READINESS_PROBE_ID)}')
        response = self._request('POST', ':batchGet', json={
            'documents': [name], 'mask': {'fieldPaths': ['expiresAt']}})
        # A missing database returns HTTP 404. An existing empty database returns
        # HTTP 200 with a named `missing` result for this document.
        self._status(response)
        try:
            result = response.json()
        except (ValueError, TypeError) as exc:
            raise StorageInvalid('Invalid Firestore readiness response') from exc
        if isinstance(result, list):
            if len(result) != 1:
                raise StorageInvalid('Invalid Firestore readiness response')
            result = result[0]
        if (not isinstance(result, dict) or
                set(result) - {'missing', 'found', 'readTime'} or
                ('readTime' in result and not isinstance(result['readTime'], str)) or
                ('missing' in result) == ('found' in result)):
            raise StorageInvalid('Invalid Firestore readiness response')
        if 'missing' in result:
            valid = result['missing'] == name
        else:
            found = result['found']
            valid = isinstance(found, dict) and found.get('name') == name
        if not valid:
            raise StorageInvalid('Invalid Firestore readiness response')
        return True

    def save(self, session_id: str, state: dict, expected_version):
        key = document_id(session_id)
        encoded = encode_state(state)
        expires = state.get('expires_at')
        if not isinstance(expires, str):
            raise StorageInvalid('Missing session expiry')
        try:
            if datetime.fromisoformat(expires).tzinfo is None:
                raise ValueError()
        except ValueError as exc:
            raise StorageInvalid('Invalid session expiry') from exc
        body = {'fields': {'payload': {'stringValue': encoded},
                           'expiresAt': {'timestampValue': expires}}}
        if expected_version is None:
            body['fields']['requestCount'] = {'integerValue': str(state.get('requests', 0))}
            response = self._request('POST', '/demo_sessions',
                                     params={'documentId': key}, json=body)
        else:
            response = self._request('PATCH', '/demo_sessions/' + key,
                                     params={'currentDocument.updateTime': expected_version,
                                             'updateMask.fieldPaths': ['payload', 'expiresAt']}, json=body)
        self._status(response, conflict=(404, 409, 412))
        return self._document(response)['updateTime']

    def delete(self, session_id: str, expected_version):
        key = document_id(session_id)
        response = self._request('DELETE', '/demo_sessions/' + key,
                                 params={'currentDocument.updateTime': expected_version})
        self._status(response, conflict=(404, 409, 412))

    def cleanup(self, now: datetime, limit: int = 8):
        if not 1 <= limit <= 32:
            raise StorageInvalid('Invalid cleanup limit')
        query = {'structuredQuery': {
            'from': [{'collectionId': 'demo_sessions'}],
            'select': {'fields': [{'fieldPath': 'expiresAt'}]},
            'where': {'fieldFilter': {'field': {'fieldPath': 'expiresAt'},
                                      'op': 'LESS_THAN_OR_EQUAL',
                                      'value': {'timestampValue': now.astimezone(timezone.utc).isoformat()}}},
            'limit': limit}}
        response = self._request('POST', ':runQuery', json=query)
        self._status(response)
        try:
            rows = response.json()
            if not isinstance(rows, list):
                raise ValueError()
        except (ValueError, TypeError) as exc:
            raise StorageInvalid('Invalid cleanup response') from exc
        removed = 0
        for row in rows[:limit]:
            doc = row.get('document') if isinstance(row, dict) else None
            if not isinstance(doc, dict):
                continue
            name, version = doc.get('name'), doc.get('updateTime')
            resource_prefix = self.base.removeprefix('https://firestore.googleapis.com/v1/').lstrip('/') + '/demo_sessions/'
            if not isinstance(name, str) or not name.startswith(resource_prefix) or not isinstance(version, str):
                continue
            key = name.rsplit('/', 1)[-1]
            if not re.fullmatch(r'[0-9a-f]{64}', key):
                continue
            result = self._request('DELETE', '/demo_sessions/' + key,
                                   params={'currentDocument.updateTime': version})
            if self._is_conflict(result, (404, 409, 412)):
                continue
            self._status(result)
            removed += 1
        return removed

    def reserve_research_budget(self, day: str, limit: int) -> bool:
        _validate_budget(day, limit)
        path = '/demo_research_budget/' + day
        for attempt in range(3):
            response = self._request('GET', path)
            if response.status_code == 404:
                count, version = 0, None
            else:
                self._status(response)
                document = self._document(response)
                try:
                    count = int(document['fields']['count']['integerValue'])
                except (KeyError, TypeError, ValueError) as exc:
                    raise StorageInvalid('Invalid budget document') from exc
                if count < 0:
                    raise StorageInvalid('Invalid budget count')
                version = document['updateTime']
            if count >= limit:
                return False
            body = {'fields': {'count': {'integerValue': str(count + 1)}}}
            if version is None:
                result = self._request('POST', '/demo_research_budget', params={'documentId': day}, json=body)
            else:
                result = self._request('PATCH', path,
                                       params={'currentDocument.updateTime': version}, json=body)
            if self._is_conflict(result, (404, 409, 412)) and attempt < 2:
                continue
            self._status(result, conflict=(404, 409, 412))
            self._document(result)
            return True
        raise StorageConflict('Budget reservation conflicted')

    def reserve_session_request(self, session_id: str, limit: int, initial_count: int,
                                expected_version):
        key = document_id(session_id)
        _validate_request_budget(limit, initial_count)
        if initial_count >= limit:
            return None
        body = {'fields': {'requestCount': {'integerValue': str(initial_count + 1)}}}
        response = self._request('PATCH', '/demo_sessions/' + key,
                                 params={'currentDocument.updateTime': expected_version,
                                         'updateMask.fieldPaths': ['requestCount']}, json=body)
        self._status(response, conflict=(404, 409, 412))
        return initial_count + 1, self._document(response)['updateTime']
