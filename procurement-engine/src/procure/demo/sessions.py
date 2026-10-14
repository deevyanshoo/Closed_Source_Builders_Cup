"""Bounded, process-local sessions. No private-machine authentication is reused."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import secrets
import threading
from typing import Callable


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
    active: bool = True
    requests: int = 0
    live_calls: int = 0
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)


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
            self._sessions.pop(session.session_id, None)
