"""Explicit, offers-only ASGI host. Construction never starts a listener."""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import os
import stat
from pathlib import Path

from starlette.responses import JSONResponse

from .. import db
from .context import QuoteHopVerifier
from .quote_transport import serve_signed_quote

_CONCRETE_PATH_TYPE = type(Path())


class LocalQuoteUnavailable(RuntimeError):
    """Fixed refusal for an unsealed local quote key or app lifecycle."""


def _key_material(path: Path) -> tuple[bytes, tuple]:
    if not path.is_absolute() or ".." in path.parts:
        raise LocalQuoteUnavailable("quote unavailable")
    for ancestor in reversed(path.parents):
        try:
            if not stat.S_ISDIR(ancestor.lstat().st_mode):
                raise LocalQuoteUnavailable("quote unavailable")
        except OSError:
            raise LocalQuoteUnavailable("quote unavailable") from None
    flags = os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    if not getattr(os, "O_NOFOLLOW", 0):
        raise LocalQuoteUnavailable("quote unavailable")
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise LocalQuoteUnavailable("quote unavailable")
        fd = os.open(path, flags)
        try:
            opened = os.fstat(fd)
            if (not stat.S_ISREG(opened.st_mode) or stat.S_IMODE(opened.st_mode) != 0o600
                    or opened.st_uid != os.getuid() or opened.st_nlink != 1
                    or not 32 <= opened.st_size <= 4096):
                raise LocalQuoteUnavailable("quote unavailable")
            raw = os.read(fd, 4097)
            after = os.fstat(fd)
        finally:
            os.close(fd)
        path_after = path.lstat()
    except (OSError, ValueError):
        raise LocalQuoteUnavailable("quote unavailable") from None
    seal = lambda item: (item.st_dev, item.st_ino, item.st_uid, stat.S_IMODE(item.st_mode), item.st_nlink, item.st_size)
    if (seal(before) != seal(opened) or seal(opened) != seal(after)
            or seal(after) != seal(path_after) or len(raw) != opened.st_size):
        raise LocalQuoteUnavailable("quote unavailable")
    return raw, seal(after) + (hashlib.sha256(raw).digest(),)


class OffersOnlyApp:
    def __init__(self, *, db_path: Path, key_file: Path, data_dir: Path, config_dir: Path,
                 backend, clock=None):
        self.db_path, self.key_file = db_path, key_file
        self.data_dir, self.config_dir = data_dir, config_dir
        self.backend = backend
        key, self.key_seal = _key_material(key_file)
        self.verifier = QuoteHopVerifier(key, clock=clock)
        self.handle: db.OfferConnection | None = None
        self.ready = False
        self.stopping = False
        self.active = 0
        self.changed = asyncio.Condition()

    def _sealed(self) -> bool:
        try:
            _, current = _key_material(self.key_file)
            return current == self.key_seal
        except LocalQuoteUnavailable:
            return False

    async def _lifespan(self, receive, send):
        from . import store
        while True:
            event = await receive()
            if event["type"] == "lifespan.startup":
                try:
                    if not self._sealed():
                        raise LocalQuoteUnavailable("quote unavailable")
                    self.handle = db.open_offers_connection(self.db_path, data_dir=self.data_dir)
                    with db.bind_offers_connection(self.handle):
                        store.ensure_schema()
                    self.ready = True
                except Exception:
                    if self.handle is not None:
                        db.close_offers_connection(self.handle)
                        self.handle = None
                    await send({"type": "lifespan.startup.failed", "message": "offers unavailable"})
                    return
                await send({"type": "lifespan.startup.complete"})
            elif event["type"] == "lifespan.shutdown":
                timed_out = False
                async with self.changed:
                    self.stopping = True
                    self.ready = False
                    try:
                        await asyncio.wait_for(self.changed.wait_for(lambda: self.active == 0), 2)
                    except TimeoutError:
                        timed_out = True
                if timed_out:
                    await send({"type": "lifespan.shutdown.failed", "message": "offers shutdown pending"})
                    return  # The active worker retains its live handle until process exit.
                if self.handle is not None:
                    db.close_offers_connection(self.handle)
                    self.handle = None
                await send({"type": "lifespan.shutdown.complete"})
                return

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await self._lifespan(receive, send)
            return
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return

        async def refuse(status, code):
            await JSONResponse({"detail": {"code": code}}, status_code=status)(scope, receive, send)

        try:
            local = ipaddress.ip_address(scope.get("client", ("", 0))[0]).is_loopback
        except (ValueError, TypeError):
            local = False
        if not local:
            await refuse(403, "local_only")
            return
        if scope["method"] == "GET" and scope["path"] == "/-/health":
            await JSONResponse({"status": "ok" if self.ready and not self.stopping else "unavailable",
                                "provider": "not_verified"}, status_code=200 if self.ready else 503)(scope, receive, send)
            return
        if scope["path"] != "/offers" and not scope["path"].startswith("/offers/"):
            await refuse(403, "service_route_unavailable")
            return
        async with self.changed:
            unavailable = not self.ready or self.stopping or self.handle is None or not self._sealed()
            if not unavailable:
                self.active += 1
                handle = self.handle
        if unavailable:
            await refuse(503, "quote_unavailable")
            return
        try:
            with db.bind_offers_connection(handle):
                await serve_signed_quote(scope, receive, send, backend=self.backend,
                                         verifier=self.verifier, ready=True)
        finally:
            async with self.changed:
                self.active -= 1
                self.changed.notify_all()


def create_offers_only_app(*, db_path: Path, key_file: Path, data_dir: Path, config_dir: Path,
                           backend=None, clock=None):
    if not db._OFFERS_ONLY_STRICT:
        raise db.OffersBindingUnavailable("offers binding unavailable")
    if (any(type(value) is not _CONCRETE_PATH_TYPE
            or not value.is_absolute() or ".." in value.parts
            for value in (db_path, key_file, data_dir, config_dir))
            or db_path != data_dir / "offers-only.duckdb" or key_file.parent != config_dir):
        raise ValueError("explicit local offers paths required")
    if backend is None:
        from fastapi import FastAPI
        from .http import router
        backend = FastAPI()
        backend.include_router(router)
    return OffersOnlyApp(db_path=db_path, key_file=key_file, data_dir=data_dir,
                         config_dir=config_dir, backend=backend, clock=clock)
