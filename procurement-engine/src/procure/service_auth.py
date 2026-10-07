"""Google service identity boundary; no credential file or payload-only trust."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit

from google.auth.transport.requests import Request
from google.oauth2 import id_token

ISSUERS = frozenset({'accounts.google.com', 'https://accounts.google.com'})
EMAIL = re.compile(r'[a-z0-9][a-z0-9._-]*@[a-z0-9][a-z0-9.-]*\.gserviceaccount\.com\Z')

@dataclass(frozen=True)
class Caller:
    subject: str
    email: str

class TokenRejected(ValueError):
    """Safe public authentication failure; do not expose claims or tokens."""


def validate_audience(value: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError('An explicit HTTPS service audience is required')
    try:
        url = urlsplit(value)
        port = url.port
    except ValueError as exc:
        raise ValueError('Invalid service audience') from exc
    if (url.scheme != 'https' or not url.hostname or url.username is not None
            or url.password is not None or url.path not in ('', '/')
            or url.query or url.fragment or any(c.isspace() for c in value)
            or (port is not None and not 1 <= port <= 65535)):
        raise ValueError('An explicit HTTPS service origin audience is required')
    return value.rstrip('/')


def load_callers(path: Path) -> tuple[Caller, ...]:
    """Read an operator-owned, restricted caller map, never account credentials."""
    try:
        path = Path(path)
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > 65536:
            raise ValueError
        value = json.loads(path.read_text())
        if not isinstance(value, dict) or set(value) != {'callers'}:
            raise ValueError
        records = value['callers']
        if not isinstance(records, list) or not records:
            raise ValueError
        callers, subjects, emails = [], set(), set()
        for row in records:
            if not isinstance(row, dict) or set(row) != {'subject', 'email'}:
                raise ValueError
            subject, email = row['subject'], row['email']
            if (not isinstance(subject, str) or not subject or subject != subject.strip()
                    or not isinstance(email, str) or not EMAIL.fullmatch(email)
                    or subject in subjects or email in emails):
                raise ValueError
            subjects.add(subject); emails.add(email)
            callers.append(Caller(subject, email))
        return tuple(callers)
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise ValueError('Restricted service caller configuration is invalid') from exc


class GoogleVerifier:
    """Verify Google cert signature, time and audience using the vendor SDK.

    request is an in-process test seam; no runtime environment bypass or cert URL
    override is exposed. Production requests use the SDK's fixed Google cert URL.
    """
    def __init__(self, request=None):
        self.request = request if request is not None else Request()

    def verify(self, token: str, audience: str) -> dict:
        def bounded_request(url, **kwargs):
            kwargs['timeout'] = 3
            return self.request(url, **kwargs)
        return id_token.verify_oauth2_token(token, bounded_request, audience=audience,
                                           clock_skew_in_seconds=0)


def authorize(token: str, audience: str, callers: tuple[Caller, ...], *, verifier=None) -> Caller:
    """Require verified claims plus the exact operator allowlisted caller pair."""
    audience = validate_audience(audience)
    if not isinstance(token, str) or not token or len(token) > 16384:
        raise TokenRejected('Service identity required')
    try:
        claims = (verifier or GoogleVerifier()).verify(token, audience)
        if (not isinstance(claims, dict) or claims.get('iss') not in ISSUERS
                or claims.get('aud') != audience or claims.get('email_verified') is not True
                or not isinstance(claims.get('sub'), str)
                or not isinstance(claims.get('email'), str)):
            raise ValueError
        for caller in callers:
            if claims['sub'] == caller.subject and claims['email'] == caller.email:
                return caller
    except Exception as exc:
        raise TokenRejected('Service identity required') from exc
    raise TokenRejected('Service identity required')
