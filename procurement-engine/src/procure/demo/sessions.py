"""Bounded anonymous sessions, optionally durable across service restarts."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import os
import secrets
import threading
from typing import Callable

from .company import DEFAULT_COMPANY_ID
from .session_persistence import (FirestoreSessionPersistence, StorageConflict,
                                  StorageInvalid, StorageTooLarge, StorageUnavailable,
                                  document_id)


class SessionError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 401):
        self.code, self.message, self.status_code = code, message, status_code
        super().__init__(message)


@dataclass
class Session:
    session_id: str
    csrf_token: str
    expires_at: datetime
    offers: dict = field(default_factory=dict)
    idempotency: dict = field(default_factory=dict)
    last_plan: dict | None = None
    plan_request: dict | None = None
    buyer_draft: dict | None = None
    selected_material_id: str | None = None
    simulated_outbox: list = field(default_factory=list)
    drafts: list = field(default_factory=list)
    recovery: dict | None = None
    rfq: dict | None = None
    news: dict | None = None
    risk_report: dict | None = None
    watch_items: list = field(default_factory=list)
    pending_proposal: dict | None = None
    operator_turns: int = 0
    operator_generation: int = 0
    voice_calls: int = 0
    live_voice_sessions: int = 0
    live_voice_starts_by_company: dict = field(default_factory=dict)
    company_id: str = DEFAULT_COMPANY_ID
    context_version: int = 1
    workspaces: dict = field(default_factory=dict)
    chat_history: dict = field(default_factory=dict)
    research_attempts: int = 0
    research_records: dict = field(default_factory=dict)
    active: bool = True
    requests: int = 0
    live_calls: int = 0
    live_failed_calls: int = 0
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    storage_version: str | int | None = field(default=None, repr=False)

    def select_company(self, company_id: str) -> None:
        """Move the active workspace; retained state never crosses company IDs."""
        if company_id == self.company_id:
            return
        self.workspaces[self.company_id] = {
            'offers': self.offers, 'last_plan': self.last_plan,
            'plan_request': self.plan_request, 'recovery': self.recovery,
            'rfq': self.rfq, 'watch_items': self.watch_items,
            'buyer_draft': self.buyer_draft, 'selected_material_id': self.selected_material_id,
            'simulated_outbox': self.simulated_outbox,
            'drafts': self.drafts,
        }
        restored = self.workspaces.pop(company_id, None) or {}
        self.offers = restored.get('offers', {})
        self.last_plan = restored.get('last_plan')
        self.plan_request = restored.get('plan_request')
        self.recovery = restored.get('recovery')
        self.rfq = restored.get('rfq')
        self.watch_items = restored.get('watch_items', [])
        self.buyer_draft = restored.get('buyer_draft')
        self.selected_material_id = restored.get('selected_material_id')
        self.simulated_outbox = restored.get('simulated_outbox', [])
        self.drafts = restored.get('drafts', [])
        self.pending_proposal = None
        # A news wrapper names a context version; a revisit always receives a new one.
        self.news = None
        self.risk_report = None
        self.company_id = company_id
        self.context_version += 1


class SessionStore:
    _STATE_FIELDS = ('offers', 'idempotency', 'last_plan', 'plan_request', 'buyer_draft',
                     'selected_material_id', 'simulated_outbox', 'recovery', 'rfq', 'news',
                     'risk_report', 'watch_items', 'operator_turns', 'operator_generation',
                     'voice_calls', 'live_voice_sessions', 'live_voice_starts_by_company',
                     'company_id', 'context_version', 'workspaces', 'requests', 'live_calls',
                     'live_failed_calls', 'chat_history', 'research_attempts', 'research_records', 'drafts')

    def __init__(self, *, clock: Callable | None = None, ttl_seconds: int | None = None,
                 max_sessions: int = 256, persistence_backend=None):
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        if persistence_backend is None and os.getenv('DEMO_SESSION_BACKEND', '').strip().lower() == 'firestore':
            project = os.getenv('GOOGLE_CLOUD_PROJECT', '').strip()
            persistence_backend = FirestoreSessionPersistence(project)
        self.backend = persistence_backend
        self.persistence = 'firestore' if self.backend is not None else 'ephemeral'
        if ttl_seconds is None:
            ttl_seconds = 7 * 24 * 3600 if self.backend is not None else 3600
        if ttl_seconds < 1 or (self.backend is not None and ttl_seconds > 7 * 24 * 3600):
            raise ValueError('Invalid session TTL')
        self.ttl_seconds, self.max_sessions = ttl_seconds, max_sessions
        self._sessions: dict[str, Session] = {}
        self._lock = threading.RLock()

    def now(self) -> datetime:
        value = self.clock()
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, timezone.utc)
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

    def create(self) -> Session:
        with self._lock:
            self._prune_expired()
            if len(self._sessions) >= self.max_sessions:
                raise SessionError('session_capacity', 'Demo is busy. Please try later.', 503)
            session = Session(secrets.token_urlsafe(32), secrets.token_urlsafe(32),
                              self.now() + timedelta(seconds=self.ttl_seconds))
            self._sessions[session.session_id] = session
            if self.backend is not None:
                self.save(session)
                # Bounded explicit cleanup; expiration checks also run on every restore.
                try:
                    self.backend.cleanup(self.now(), limit=8)
                except (StorageConflict, StorageUnavailable, StorageInvalid):
                    pass
            return session

    def _prune_expired(self) -> None:
        """Caller holds the store lock; expired cache entries never count toward capacity."""
        now = self.now()
        for value in list(self._sessions.values()):
            if not value.active or value.expires_at <= now:
                self.invalidate(value, delete_remote=False)

    def get(self, session_id: str | None, *, refresh: bool = False) -> Session:
        with self._lock:
            session = self._sessions.get(session_id or '')
            if self.backend is not None and session_id and (session is None or refresh):
                try:
                    document_id(session_id)
                except StorageInvalid:
                    raise SessionError('session_expired', 'Session expired. Start a new demo session.') from None
                try:
                    # A cached refresh holds the same lock as save. The remote
                    # read cannot race with this process's conditional write.
                    if session is not None:
                        with session.lock:
                            loaded = self.backend.load(session_id)
                            if loaded is None:
                                self.invalidate(session, delete_remote=False)
                                session = None
                            elif loaded[1] != session.storage_version:
                                replacement = self._restore(session_id, *loaded)
                                self.invalidate(session, delete_remote=False)
                                session = replacement
                                if replacement.expires_at > self.now():
                                    self._sessions[session_id] = replacement
                    else:
                        loaded = self.backend.load(session_id)
                    if loaded is not None:
                        if session is None:
                            replacement = self._restore(session_id, *loaded)
                            if replacement.expires_at > self.now():
                                self._prune_expired()
                                if len(self._sessions) >= self.max_sessions:
                                    raise SessionError('session_capacity', 'Demo is busy. Please try later.', 503)
                                self._sessions[session_id] = replacement
                                session = replacement
                            else:
                                try:
                                    self.backend.delete(session_id, replacement.storage_version)
                                except (StorageConflict, StorageUnavailable):
                                    pass
                        elif session.expires_at <= self.now():
                            self.invalidate(session, delete_remote=False)
                        else:
                            pass
                except (StorageUnavailable, StorageInvalid) as exc:
                    if session is not None:
                        self.invalidate(session, delete_remote=False)
                    raise self._storage_error(exc) from exc
            if session is None:
                raise SessionError('session_expired', 'Session expired. Start a new demo session.')
            with session.lock:
                if not session.active or session.expires_at <= self.now():
                    self.invalidate(session, delete_remote=False)
                    raise SessionError('session_expired', 'Session expired. Start a new demo session.')
            return session

    @staticmethod
    def _storage_error(exc):
        if isinstance(exc, StorageConflict):
            return SessionError('session_conflict', 'Session changed in another request. Refresh the demo.', 409)
        if isinstance(exc, StorageTooLarge):
            return SessionError('session_too_large', 'Demo session exceeded its storage limit.', 413)
        return SessionError('session_storage_unavailable', 'Session storage is unavailable. Start a new demo session.', 503)

    @classmethod
    def _snapshot(cls, session: Session) -> dict:
        if len(session.chat_history) > 16 or any(
                not isinstance(items, list) or len(items) > 100 for items in session.chat_history.values()):
            raise StorageTooLarge('Conversation exceeds history limit')
        if not isinstance(session.research_records, dict) or len(session.research_records) > 64:
            raise StorageTooLarge('Research records exceed limit')
        if type(session.research_attempts) is not int or not 0 <= session.research_attempts <= 6:
            raise StorageInvalid('Invalid research attempt count')
        state = {'schema_version': 1, 'session_id': session.session_id,
                 'csrf_token': session.csrf_token, 'expires_at': session.expires_at.isoformat()}
        state.update({name: getattr(session, name) for name in cls._STATE_FIELDS})
        return state

    @classmethod
    def _restore(cls, session_id: str, state: dict, version):
        if (not isinstance(state, dict) or state.get('schema_version') != 1 or
                state.get('session_id') != session_id or not isinstance(state.get('csrf_token'), str) or
                len(state['csrf_token']) != 43):
            raise StorageInvalid('Invalid session document')
        try:
            expires_at = datetime.fromisoformat(state['expires_at'])
            if expires_at.tzinfo is None:
                raise ValueError()
            expires_at = expires_at.astimezone(timezone.utc)
            values = {name: state.get(name, []) if name == 'drafts' else state[name]
                      for name in cls._STATE_FIELDS}
            for name in ('offers', 'idempotency', 'live_voice_starts_by_company', 'workspaces',
                         'chat_history', 'research_records'):
                if not isinstance(values[name], dict):
                    raise ValueError()
            for name in ('simulated_outbox', 'watch_items', 'drafts'):
                if not isinstance(values[name], list):
                    raise ValueError()
            if len(values['drafts']) > 24:
                raise ValueError()
            if len(values['chat_history']) > 16 or any(
                    not isinstance(items, list) or len(items) > 100 for items in values['chat_history'].values()):
                raise ValueError()
            if type(values['research_attempts']) is not int or not 0 <= values['research_attempts'] <= 6:
                raise ValueError()
            if len(values['research_records']) > 64:
                raise ValueError()
            if type(values['context_version']) is not int or values['context_version'] < 1:
                raise ValueError()
            for name in ('requests', 'operator_turns', 'operator_generation', 'voice_calls',
                         'live_voice_sessions', 'live_calls', 'live_failed_calls'):
                if type(values[name]) is not int or values[name] < 0:
                    raise ValueError()
            restored = Session(session_id, state['csrf_token'], expires_at, **values)
            restored.storage_version = version
            return restored
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise StorageInvalid('Invalid session document') from exc

    def save(self, session: Session) -> None:
        if self.backend is None:
            return
        with session.lock:
            if (not session.active or self._sessions.get(session.session_id) is not session or
                    session.expires_at <= self.now()):
                raise SessionError('session_expired', 'Session expired. Start a new demo session.')
            try:
                session.storage_version = self.backend.save(
                    session.session_id, self._snapshot(session), session.storage_version)
            except (StorageConflict, StorageUnavailable, StorageInvalid, StorageTooLarge) as exc:
                self.invalidate(session, delete_remote=False)
                raise self._storage_error(exc) from exc

    def reserve_research_budget(self, day: str, limit: int) -> bool:
        if self.backend is None:
            return _reserve_ephemeral_budget(day, limit)
        try:
            return self.backend.reserve_research_budget(day, limit)
        except (StorageConflict, StorageUnavailable, StorageInvalid) as exc:
            raise self._storage_error(exc) from exc

    def invalidate(self, session: Session, *, delete_remote: bool = True) -> None:
        with session.lock:
            if self.backend is not None and delete_remote and session.active:
                try:
                    self.backend.delete(session.session_id, session.storage_version)
                except (StorageConflict, StorageUnavailable, StorageInvalid) as exc:
                    self.invalidate(session, delete_remote=False)
                    raise self._storage_error(exc) from exc
            session.active = False
            session.offers.clear()
            session.idempotency.clear()
            session.last_plan = None
            session.plan_request = None
            session.buyer_draft = None
            session.selected_material_id = None
            session.simulated_outbox.clear()
            session.recovery = None
            session.rfq = None
            session.news = None
            session.risk_report = None
            session.watch_items.clear()
            session.pending_proposal = None
            session.operator_turns = 0
            session.operator_generation = 0
            session.voice_calls = 0
            session.workspaces.clear()
            session.chat_history.clear()
            session.research_attempts = 0
            session.research_records.clear()
            self._sessions.pop(session.session_id, None)


_budget_lock = threading.RLock()
_ephemeral_budget: dict[str, int] = {}


def _reserve_ephemeral_budget(day: str, limit: int) -> bool:
    from .session_persistence import _validate_budget
    _validate_budget(day, limit)
    with _budget_lock:
        for old_day in list(_ephemeral_budget):
            if old_day < day:
                del _ephemeral_budget[old_day]
        count = _ephemeral_budget.get(day, 0)
        if count >= limit:
            return False
        _ephemeral_budget[day] = count + 1
        return True
