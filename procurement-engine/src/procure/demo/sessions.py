"""Bounded, process-local sessions. No private-machine authentication is reused."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import secrets
import threading
from typing import Callable

from .company import DEFAULT_COMPANY_ID


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
    recovery: dict | None = None
    rfq: dict | None = None
    news: dict | None = None
    company_id: str = DEFAULT_COMPANY_ID
    context_version: int = 1
    workspaces: dict = field(default_factory=dict)
    active: bool = True
    requests: int = 0
    live_calls: int = 0
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def select_company(self, company_id: str) -> None:
        """Move the active workspace; retained state never crosses company IDs."""
        if company_id == self.company_id:
            return
        self.workspaces[self.company_id] = {
            'offers': self.offers, 'last_plan': self.last_plan,
            'plan_request': self.plan_request, 'recovery': self.recovery,
            'rfq': self.rfq,
        }
        restored = self.workspaces.pop(company_id, None) or {}
        self.offers = restored.get('offers', {})
        self.last_plan = restored.get('last_plan')
        self.plan_request = restored.get('plan_request')
        self.recovery = restored.get('recovery')
        self.rfq = restored.get('rfq')
        # A news wrapper names a context version; a revisit always receives a new one.
        self.news = None
        self.company_id = company_id
        self.context_version += 1


class SessionStore:
    def __init__(self, *, clock: Callable | None = None, ttl_seconds: int = 3600,
                 max_sessions: int = 256):
        self.clock = clock or (lambda: datetime.now(timezone.utc))
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
            for value in list(self._sessions.values()):
                if value.expires_at <= self.now():
                    self.invalidate(value)
            if len(self._sessions) >= self.max_sessions:
                raise SessionError('session_capacity', 'Demo is busy. Please try later.', 503)
            session = Session(secrets.token_urlsafe(32), secrets.token_urlsafe(32),
                              self.now() + timedelta(seconds=self.ttl_seconds))
            self._sessions[session.session_id] = session
            return session

    def get(self, session_id: str | None) -> Session:
        with self._lock:
            session = self._sessions.get(session_id or '')
            if session is None or not session.active or session.expires_at <= self.now():
                if session is not None:
                    self.invalidate(session)
                raise SessionError('session_expired', 'Session expired. Start a new demo session.')
            return session

    def invalidate(self, session: Session) -> None:
        with self._lock, session.lock:
            session.active = False
            session.offers.clear()
            session.idempotency.clear()
            session.last_plan = None
            session.plan_request = None
            session.recovery = None
            session.rfq = None
            session.news = None
            session.workspaces.clear()
            self._sessions.pop(session.session_id, None)
