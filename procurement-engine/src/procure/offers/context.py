"""Local quote hop identity. Runtime has no key until a later activation scope."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

from .lifecycle import OfferError


@dataclass(frozen=True, slots=True)
class OfferContext:
    principal_id: str
    workspace_id: str
    role: str


def require_context(context: OfferContext | None, *, editor: bool = False, system: bool = False,
                    allow_system: bool = False) -> OfferContext:
    if (type(context) is not OfferContext or type(context.principal_id) is not str
            or not context.principal_id.strip() or type(context.workspace_id) is not str
            or not context.workspace_id.strip() or type(context.role) is not str
            or context.role not in ('viewer', 'editor', 'system')):
        raise OfferError('identity_required', 'Valid quote context required')
    if system and context.role != 'system':
        raise OfferError('forbidden', 'System quote context required')
    if editor and context.role != 'editor':
        raise OfferError('forbidden', 'Editor quote context required')
    if not system and not allow_system and context.role == 'system':
        raise OfferError('forbidden', 'System context cannot use user quote operation')
    return context


class HopRejected(ValueError):
    """Fixed-code internal assertion refusal; no input is included in the message."""


def _reject(code='invalid_quote_context'):
    raise HopRejected(code) from None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _reject('duplicate_json_key')
        result[key] = value
    return result


def strict_json(data: bytes):
    try:
        return json.loads(data, object_pairs_hook=_unique_object, parse_constant=lambda _: _reject())
    except HopRejected:
        raise
    except (UnicodeError, ValueError, TypeError, RecursionError):
        _reject('invalid_json')


def _one(headers: list[tuple[bytes, bytes]], name: bytes) -> bytes | None:
    values = [v for k, v in headers if k.lower() == name]
    if len(values) > 1:
        _reject('duplicate_header')
    return values[0] if values else None


FIELDS = frozenset(('v', 'method', 'raw_path', 'raw_query', 'body_sha256', 'issued_at_unix',
                    'nonce', 'principal_id', 'workspace_id', 'role', 'idempotency_key'))


class QuoteHopVerifier:
    """Single-process 15-second nonce verifier for generated-key tests only."""

    def __init__(self, key: bytes, *, clock=None, max_nonces: int = 4096):
        if type(key) is not bytes or len(key) < 32 or type(max_nonces) is not int or max_nonces < 1:
            raise ValueError('invalid quote verifier configuration')
        self._key = key
        self._clock = clock or time.time
        self._nonces: OrderedDict[str, int] = OrderedDict()
        self._lock = threading.Lock()
        self._max_nonces = max_nonces

    def verify(self, scope: dict, body: bytes, *, idempotent: bool) -> OfferContext:
        headers = scope.get('headers', [])
        for forbidden in (b'authorization', b'origin', b'x-actor', b'x-role', b'x-principal',
                          b'x-principal-id', b'x-workspace', b'x-workspace-id'):
            if _one(headers, forbidden) is not None:
                _reject('client_identity_forbidden')
        if any(k.lower().startswith(b'x-quote-') and k.lower() not in
               (b'x-quote-context', b'x-quote-signature') for k, _ in headers):
            _reject('client_identity_forbidden')
        encoded, signature = _one(headers, b'x-quote-context'), _one(headers, b'x-quote-signature')
        if encoded is None or signature is None or len(encoded) > 2732 or len(signature) != 43:
            _reject()
        try:
            assertion = base64.urlsafe_b64decode(encoded + b'=' * (-len(encoded) % 4))
            signed = base64.urlsafe_b64decode(signature + b'=' * (-len(signature) % 4))
        except (ValueError, UnicodeError):
            _reject()
        if (base64.urlsafe_b64encode(assertion).rstrip(b'=') != encoded
                or base64.urlsafe_b64encode(signed).rstrip(b'=') != signature):
            _reject()
        if len(assertion) > 2048 or not hmac.compare_digest(hmac.new(self._key, assertion, hashlib.sha256).digest(), signed):
            _reject()
        value = strict_json(assertion)
        if type(value) is not dict or set(value) != FIELDS or type(value['v']) is not int or value['v'] != 1:
            _reject()
        for name in ('method', 'raw_path', 'raw_query', 'body_sha256', 'nonce', 'principal_id', 'workspace_id', 'role'):
            if type(value[name]) is not str:
                _reject()
        if (value['method'] != scope['method'] or value['raw_path'] != scope['raw_path'].decode('ascii', 'strict')
                or value['raw_query'] != scope.get('query_string', b'').decode('ascii', 'strict')
                or value['body_sha256'] != hashlib.sha256(body).hexdigest()
                or not re.fullmatch('[0-9a-f]{64}', value['body_sha256'])
                or not re.fullmatch('[0-9a-f]{32}', value['nonce'])):
            _reject()
        if (type(value['issued_at_unix']) is not int or abs(int(self._clock()) - value['issued_at_unix']) > 15
                or not value['principal_id'].strip() or not value['workspace_id'].strip()
                or value['role'] not in ('viewer', 'editor')):
            _reject()
        key = _one(headers, b'idempotency-key')
        if idempotent:
            if (key is None or len(key) < 8 or len(key) > 128 or any(c < 33 or c > 126 or c == 44 for c in key)
                    or value['idempotency_key'] != key.decode('ascii')):
                _reject()
        elif key is not None or value['idempotency_key'] is not None:
            _reject()
        with self._lock:
            now = int(self._clock())
            for nonce, issued in list(self._nonces.items()):
                if issued + 15 < now:
                    del self._nonces[nonce]
            if value['nonce'] in self._nonces or len(self._nonces) >= self._max_nonces:
                _reject('quote_replay')
            self._nonces[value['nonce']] = value['issued_at_unix']
        return OfferContext(value['principal_id'], value['workspace_id'], value['role'])
