"""Shared HTTP helper (timeouts, retries with backoff) and raw-cache helpers."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from ..settings import get_settings

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (procurement-engine prototype; research use)"


class OfflineError(RuntimeError):
    """Raised when a live fetch is attempted while OFFLINE=1."""


def http_get(url: str, params: dict[str, Any] | None = None, retries: int = 3,
             backoff: float = 2.0, timeout: float | None = None) -> httpx.Response:
    """GET with timeout and retry. Raises the last error after `retries` attempts."""
    settings = get_settings()
    if settings.offline:
        raise OfflineError("OFFLINE=1, live fetch skipped")
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with httpx.Client(timeout=timeout or settings.http_timeout, follow_redirects=True,
                              headers={"User-Agent": USER_AGENT}) as client:
                resp = client.get(url, params=params)
            if resp.status_code == 429:
                raise httpx.HTTPStatusError("429 rate limited", request=resp.request, response=resp)
            resp.raise_for_status()
            return resp
        except Exception as exc:  # noqa: BLE001 - we retry on anything network related
            last = exc
            log.warning("GET %s failed (attempt %d/%d): %s", url, attempt, retries, exc)
            if attempt < retries:
                time.sleep(backoff * attempt)
    assert last is not None
    raise last


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def cache_write(prefix: str, suffix: str, content: bytes) -> Path:
    path = get_settings().raw_dir / f"{prefix}_{stamp()}{suffix}"
    path.write_bytes(content)
    return path


def cache_latest(prefix: str, suffix: str) -> Path | None:
    files = sorted(get_settings().raw_dir.glob(f"{prefix}_*{suffix}"))
    return files[-1] if files else None


def cache_time(path: Path) -> datetime:
    """Parse the timestamp embedded in a cache file name."""
    ts = path.stem.rsplit("_", 1)[-1]
    return datetime.strptime(ts, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
