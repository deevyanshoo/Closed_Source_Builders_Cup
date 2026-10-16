"""Bounded anonymous sessions, optionally durable across service restarts."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import os
import secrets
import threading
from typing import Callable

from procure.carbon.assessment import validate_assessment

from .company import DEFAULT_COMPANY_ID
from .session_persistence import (FirestoreSessionPersistence, StorageConflict,
                                  StorageInvalid, StorageTooLarge, StorageUnavailable,
                                  document_id, encode_state)

MAX_EXACT_REPLAY_BYTES = 2 * 1024 * 1024
MAX_EXACT_REPLAY_RESULTS = 128
MAX_REPLAY_KEYS = 2400  # Keep aligned with the public action limit in app.py.
RETIRED_REPLAY_RESULT = {'_retry_result_expired': True}


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
    carbon_assessment: dict | None = None
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
    judge_receipts: dict = field(default_factory=dict)
    judge_attempts: int = 0
    research_attempts: int = 0
    research_records: dict = field(default_factory=dict)
    active: bool = True
    requests: int = 0
    live_calls: int = 0
    live_failed_calls: int = 0
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    storage_version: str | int | None = field(default=None, repr=False)
    storage_fingerprint: str | None = field(default=None, repr=False)

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
            'carbon_assessment': self.carbon_assessment,
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
        self.carbon_assessment = restored.get('carbon_assessment')
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
                     'carbon_assessment',
                     'voice_calls', 'live_voice_sessions', 'live_voice_starts_by_company',
                     'company_id', 'context_version', 'workspaces', 'live_calls',
                     'live_failed_calls', 'chat_history', 'research_attempts', 'research_records', 'drafts',
                     'pending_proposal', 'judge_receipts', 'judge_attempts')

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
            expired = self._prune_expired()
            if len(self._sessions) >= self.max_sessions:
                raise SessionError('session_capacity', 'Demo is busy. Please try later.', 503)
            session = Session(secrets.token_urlsafe(32), secrets.token_urlsafe(32),
                              self.now() + timedelta(seconds=self.ttl_seconds))
            self._sessions[session.session_id] = session
        for stale in expired:
            self.invalidate(stale, delete_remote=False)
        if self.backend is not None:
            self.save(session)
            # Bounded explicit cleanup; expiration checks also run on every restore.
            try:
                self.backend.cleanup(self.now(), limit=8)
            except (StorageConflict, StorageUnavailable, StorageInvalid):
                pass
        return session

    def _prune_expired(self) -> list[Session]:
        """Caller holds the store lock; expired cache entries never count toward capacity."""
        now = self.now()
        expired = []
        for value in list(self._sessions.values()):
            if not value.active or value.expires_at <= now:
                self._sessions.pop(value.session_id, None)
                expired.append(value)
        return expired

    def get(self, session_id: str | None, *, refresh: bool = False) -> Session:
        with self._lock:
            session = self._sessions.get(session_id or '')
        if self.backend is not None and session_id and (session is None or refresh):
            try:
                document_id(session_id)
            except StorageInvalid:
                raise SessionError('session_expired', 'Session expired. Start a new demo session.') from None
            try:
                if session is not None:
                    # Only this session is serialized with a remote read/write.
                    with session.lock:
                        loaded = self.backend.load(session_id)
                        if loaded is None:
                            self.invalidate(session, delete_remote=False)
                            session = None
                        elif loaded[1] != session.storage_version:
                            replacement = self._restore(session_id, *loaded, now=self.now())
                            self.invalidate(session, delete_remote=False)
                            session = None
                            if replacement.expires_at > self.now():
                                with self._lock:
                                    self._sessions[session_id] = replacement
                                session = replacement
                else:
                    loaded = self.backend.load(session_id)
                    if loaded is not None:
                        replacement = self._restore(session_id, *loaded, now=self.now())
                        if replacement.expires_at > self.now():
                            with self._lock:
                                expired = self._prune_expired()
                                existing = self._sessions.get(session_id)
                                if existing is None and len(self._sessions) >= self.max_sessions:
                                    raise SessionError('session_capacity', 'Demo is busy. Please try later.', 503)
                                session = self._sessions.setdefault(session_id, replacement)
                            for stale in expired:
                                self.invalidate(stale, delete_remote=False)
                        else:
                            try:
                                self.backend.delete(session_id, replacement.storage_version)
                            except (StorageConflict, StorageUnavailable):
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
        if not cls._valid_replay_journal(session.idempotency):
            raise StorageInvalid('Invalid replay journal')
        if len(session.chat_history) > 16 or any(
                not isinstance(items, list) or len(items) > 100 for items in session.chat_history.values()):
            raise StorageTooLarge('Conversation exceeds history limit')
        if not isinstance(session.research_records, dict) or len(session.research_records) > 64:
            raise StorageTooLarge('Research records exceed limit')
        if type(session.research_attempts) is not int or not 0 <= session.research_attempts <= 6:
            raise StorageInvalid('Invalid research attempt count')
        if session.pending_proposal is not None and not cls._valid_proposal(session.pending_proposal, session):
            raise StorageInvalid('Invalid pending proposal')
        cls._validate_carbon_state(session)
        cls._validate_judge_state(session)
        state = {'schema_version': 1, 'session_id': session.session_id,
                 'csrf_token': session.csrf_token, 'expires_at': session.expires_at.isoformat(),
                 'requests': 0}
        state.update({name: getattr(session, name) for name in cls._STATE_FIELDS})
        return state

    @staticmethod
    def _validate_judge_state(session: Session) -> None:
        from .company import list_companies
        from .judge_ai_receipts import summarize_gemini_receipts
        allowed = {'provider','status','model_id','model_version','provider_call_id','input_source_id',
                   'called_at','source_title','source_url','source_as_of','output','evidence_refs','model_reviewed_flags'}
        ids = {row['company_id'] for row in list_companies()}
        if (not isinstance(session.judge_receipts,dict) or not session.judge_receipts.keys() <= ids
                or type(session.judge_attempts) is not int or not 0 <= session.judge_attempts <= 3):
            raise StorageInvalid('Invalid judge receipt partition')
        for records in session.judge_receipts.values():
            if not isinstance(records,list) or len(records)>3:
                raise StorageInvalid('Invalid judge receipt bounds')
            for record in records:
                if (not isinstance(record,dict) or set(record) != allowed
                        or len(json.dumps(record,ensure_ascii=False))>7000
                        or not isinstance(record.get('model_reviewed_flags'),list)
                        or any(flag not in {'regional_import_exposure','public_policy_change','review_source'}
                               for flag in record['model_reviewed_flags'])
                        or summarize_gemini_receipts([record])['interpreted_articles'] != 1):
                    raise StorageInvalid('Invalid sanitized judge receipt')

    @staticmethod
    def _validate_carbon_state(session: Session) -> None:
        def check(record, company_id):
            if record is None:
                return
            try:
                validate_assessment(record)
            except (TypeError, ValueError, OverflowError, RecursionError) as exc:
                raise StorageInvalid('Invalid carbon assessment') from exc
            if record['company_id'] != company_id or record['context_version'] > session.context_version:
                raise StorageInvalid('Carbon assessment belongs to another context')

        check(session.carbon_assessment, session.company_id)
        for company_id, workspace in session.workspaces.items():
            if not isinstance(company_id, str) or not isinstance(workspace, dict):
                raise StorageInvalid('Invalid company workspace')
            check(workspace.get('carbon_assessment'), company_id)

    @staticmethod
    def _valid_replay_journal(journal: dict) -> bool:
        if not isinstance(journal, dict) or len(journal) > MAX_REPLAY_KEYS:
            return False
        for key, entry in journal.items():
            if (not isinstance(key, str) or not 1 <= len(key) <= 100 or
                    not isinstance(entry, dict) or not isinstance(entry.get('digest'), str) or
                    'result' not in entry or type(entry.get('pending')) is not bool):
                return False
            if entry.get('result_retired') and entry['result'] != RETIRED_REPLAY_RESULT:
                return False
        return True

    @classmethod
    def _retire_replay_results(cls, session: Session) -> None:
        """Bound exact response copies; a retired key remains a durable no-op."""
        if not cls._valid_replay_journal(session.idempotency):
            raise StorageInvalid('Invalid replay journal')
        count = used_bytes = 0
        for entry in reversed(list(session.idempotency.values())):
            result = entry['result']
            if result is None or entry.get('result_retired'):
                # In-flight and failed claims keep their retry semantics. Older
                # completed records may still have pending=True, so result wins.
                continue
            try:
                size = len(json.dumps(result, ensure_ascii=False, separators=(',', ':'),
                                      allow_nan=False).encode('utf-8'))
            except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
                raise StorageInvalid('Invalid replay result') from exc
            if count >= MAX_EXACT_REPLAY_RESULTS or used_bytes + size > MAX_EXACT_REPLAY_BYTES:
                entry['result'] = RETIRED_REPLAY_RESULT.copy()
                entry['result_retired'] = True
            else:
                count += 1
                used_bytes += size

    @classmethod
    def _valid_proposal(cls, proposal: dict, session: Session) -> bool:
        if not isinstance(proposal, dict):
            return False
        required = ('proposal_id', 'kind', 'summary', 'parameters', 'plan_id',
                    'offer_versions', 'context_version', 'expires_at', '_marker',
                    '_company_id', '_session_id')
        if any(name not in proposal for name in required):
            return False
        if (not isinstance(proposal['proposal_id'], str) or not 1 <= len(proposal['proposal_id']) <= 128 or
                proposal['kind'] not in ('research', 'watch', 'recovery', 'rfq', 'simulate_email', 'review_quotes') or
                not isinstance(proposal['summary'], str) or len(proposal['summary']) > 2000 or
                not isinstance(proposal['parameters'], dict) or
                not isinstance(proposal['offer_versions'], dict) or
                not isinstance(proposal['_marker'], str) or not 1 <= len(proposal['_marker']) <= 256 or
                proposal['_session_id'] != session.session_id or
                proposal['_company_id'] != session.company_id or
                type(proposal['context_version']) is not int or
                proposal['context_version'] != session.context_version):
            return False
        try:
            expiry = datetime.fromisoformat(proposal['expires_at'])
            if expiry.tzinfo is None:
                return False
        except (TypeError, ValueError):
            return False
        if proposal['kind'] == 'research':
            from .research import DESTINATION, SHARED_DATA, MAX_QUERY, _unsafe
            params = proposal['parameters']
            if (set(params) != {'query', 'destination', 'shared_data'} or
                    not isinstance(params['query'], str) or
                    not 1 <= len(params['query']) <= MAX_QUERY or
                    any(ord(char) < 32 for char in params['query']) or
                    _unsafe(params['query'], None) or
                    params['destination'] != DESTINATION or params['shared_data'] != SHARED_DATA):
                return False
        return True

    @classmethod
    def _restore(cls, session_id: str, state: dict, version, *, now: datetime | None = None):
        if (not isinstance(state, dict) or state.get('schema_version') != 1 or
                state.get('session_id') != session_id or not isinstance(state.get('csrf_token'), str) or
                len(state['csrf_token']) != 43):
            raise StorageInvalid('Invalid session document')
        try:
            expires_at = datetime.fromisoformat(state['expires_at'])
            if expires_at.tzinfo is None:
                raise ValueError()
            expires_at = expires_at.astimezone(timezone.utc)
            values = {name: state.get(name, {}) if name == 'judge_receipts' else
                      state.get(name, 0) if name == 'judge_attempts' else state.get(name, []) if name == 'drafts' else
                      state.get(name) if name == 'carbon_assessment' else
                      state.get(name) if name == 'pending_proposal' else state[name]
                      for name in cls._STATE_FIELDS}
            for name in ('offers', 'idempotency', 'live_voice_starts_by_company', 'workspaces',
                         'chat_history', 'research_records', 'judge_receipts'):
                if not isinstance(values[name], dict):
                    raise ValueError()
            if not cls._valid_replay_journal(values['idempotency']):
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
            if type(values['judge_attempts']) is not int or not 0 <= values['judge_attempts'] <= 3:
                raise ValueError()
            if len(values['judge_receipts']) > 10 or any(not isinstance(r,list) or len(r)>3 for r in values['judge_receipts'].values()):
                raise ValueError()
            if len(values['research_records']) > 64:
                raise ValueError()
            if type(values['context_version']) is not int or values['context_version'] < 1:
                raise ValueError()
            for name in ('operator_turns', 'operator_generation', 'voice_calls',
                         'live_voice_sessions', 'live_calls', 'live_failed_calls'):
                if type(values[name]) is not int or values[name] < 0:
                    raise ValueError()
            restored = Session(session_id, state['csrf_token'], expires_at, **values)
            cls._validate_carbon_state(restored)
            requests = state.get('requests', 0)
            if type(requests) is not int or requests < 0:
                raise ValueError()
            restored.requests = requests
            if (restored.pending_proposal is not None and
                    (not cls._valid_proposal(restored.pending_proposal, restored) or
                     (now or datetime.now(timezone.utc)) >= datetime.fromisoformat(
                         restored.pending_proposal['expires_at']).astimezone(timezone.utc))):
                restored.pending_proposal = None
            restored.storage_version = version
            restored.storage_fingerprint = encode_state(cls._snapshot(restored))
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
                self._retire_replay_results(session)
                state = self._snapshot(session)
                fingerprint = encode_state(state)
                if session.storage_version is not None and fingerprint == session.storage_fingerprint:
                    return
                session.storage_version = self.backend.save(
                    session.session_id, state, session.storage_version)
                session.storage_fingerprint = fingerprint
            except (StorageConflict, StorageUnavailable, StorageInvalid, StorageTooLarge) as exc:
                self.invalidate(session, delete_remote=False)
                raise self._storage_error(exc) from exc

    def reserve_request(self, session: Session, limit: int) -> int:
        with session.lock:
            if not session.active or session.expires_at <= self.now():
                raise SessionError('session_expired', 'Session expired. Start a new demo session.')
            if self.backend is None:
                count = session.requests + 1
            else:
                try:
                    admitted = self.backend.reserve_session_request(
                        session.session_id, limit, session.requests, session.storage_version)
                except (StorageConflict, StorageUnavailable, StorageInvalid) as exc:
                    raise self._storage_error(exc) from exc
                if admitted is None:
                    raise SessionError('request_limit', 'Session request limit reached. Start a new session later.', 429)
                count, session.storage_version = admitted
            if count is None or count > limit:
                raise SessionError('request_limit', 'Session request limit reached. Start a new session later.', 429)
            session.requests = count
            return count

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
            session.carbon_assessment = None
            session.watch_items.clear()
            session.pending_proposal = None
            session.operator_turns = 0
            session.operator_generation = 0
            session.voice_calls = 0
            session.workspaces.clear()
            session.chat_history.clear()
            session.research_attempts = 0
            session.research_records.clear()
            with self._lock:
                if self._sessions.get(session.session_id) is session:
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
