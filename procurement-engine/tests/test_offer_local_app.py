"""Synthetic offers-only boundary tests. Run each node in its own guarded process.

Product imports deliberately live in test bodies. The fixed outer runner suppresses
legacy conftest, pins inputs, and provides an offline credential-free environment.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
import time


def _paths(tmp_path: Path):
    data = tmp_path / "data"
    config = tmp_path / "config"
    data.mkdir(parents=True)
    config.mkdir(parents=True)
    data.chmod(0o700)
    config.chmod(0o700)
    (config / "materials.yaml").write_text("materials:\n  synthetic: {}\n")
    key_file = config / "quote-hop.key"
    key = os.urandom(32)
    key_file.write_bytes(key)
    key_file.chmod(0o600)
    return data, config, data / "offers-only.duckdb", key_file, key


def _signed(key: bytes, method: str, path: str, *, body: bytes = b"", role: str = "viewer",
            principal: str = "reviewer", workspace: str = "workspace-a", operation_key: str | None = None,
            query: bytes = b"") -> list[tuple[bytes, bytes]]:
    assertion = {
        "v": 1, "method": method, "raw_path": path, "raw_query": query.decode("ascii"),
        "body_sha256": hashlib.sha256(body).hexdigest(), "issued_at_unix": int(time.time()),
        "nonce": os.urandom(16).hex(), "principal_id": principal, "workspace_id": workspace,
        "role": role, "idempotency_key": operation_key,
    }
    wire = json.dumps(assertion, separators=(",", ":")).encode("utf-8")
    headers = [
        (b"x-quote-context", base64.urlsafe_b64encode(wire).rstrip(b"=")),
        (b"x-quote-signature", base64.urlsafe_b64encode(hmac.new(key, wire, hashlib.sha256).digest()).rstrip(b"=")),
    ]
    if operation_key is not None:
        headers.append((b"idempotency-key", operation_key.encode("ascii")))
    if body:
        headers.extend(((b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())))
    return headers


async def _request(app, method: str, path: str, *, headers=(), body: bytes = b"", query: bytes = b"",
                   client: str = "127.0.0.1"):
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "scheme": "http", "method": method, "path": path, "raw_path": path.encode(),
        "query_string": query, "root_path": "", "headers": list(headers),
        "client": (client, 19000), "server": ("127.0.0.1", 8018),
    }
    delivered = False
    messages = []

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.sleep(0)
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)
    starts = [m for m in messages if m["type"] == "http.response.start"]
    assert len(starts) == 1
    raw = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    response_headers = {k.lower(): v for k, v in starts[0].get("headers", [])}
    return starts[0]["status"], json.loads(raw) if raw else None, response_headers


class _Lifespan:
    def __init__(self, app):
        self.app = app
        self.incoming = asyncio.Queue()
        self.outgoing = asyncio.Queue()
        self.task = None

    async def start(self):
        self.task = asyncio.create_task(self.app({"type": "lifespan", "asgi": {"version": "3.0"}},
                                                 self.incoming.get, self.outgoing.put))
        await self.incoming.put({"type": "lifespan.startup"})
        event = await asyncio.wait_for(self.outgoing.get(), 5)
        assert event["type"] == "lifespan.startup.complete", event

    async def stop(self):
        await self.incoming.put({"type": "lifespan.shutdown"})
        event = await asyncio.wait_for(self.outgoing.get(), 10)
        assert event["type"] == "lifespan.shutdown.complete", event
        await asyncio.wait_for(self.task, 5)


def test_shared_quote_transport_matches_service_app(tmp_path, monkeypatch):
    """Moving the quote path must preserve signed responses and refusal semantics."""
    data, config, _, _, key = _paths(tmp_path)
    monkeypatch.setenv("PROCURE_CONFIG_DIR", str(config))
    monkeypatch.setenv("PROCURE_DATA_DIR", str(data))
    monkeypatch.setenv("PROCURE_DB_PATH", str(data / "unused.duckdb"))
    from procure import service_app
    from procure.offers.context import QuoteHopVerifier
    from procure.offers.quote_transport import serve_signed_quote

    seen = []

    async def fake_backend(scope, receive, send):
        seen.append(scope["offer_context"].workspace_id)
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": b'{"offers":[]}', "more_body": False})

    normal_verifier = QuoteHopVerifier(key)
    helper_verifier = QuoteHopVerifier(key)
    normal = service_app.ServiceApp(
        service_app.Config("local", "", (), data, data / "unused.duckdb", None, "", ("synthetic",)),
        backend=fake_backend, quote_verifier=normal_verifier)
    normal.ready = True  # No lifespan, cache, API or numerical startup.

    async def direct(scope, receive, send):
        await serve_signed_quote(scope, receive, send, backend=fake_backend,
                                 verifier=helper_verifier, ready=True)

    async def compare():
        cases = [
            ("GET", "/offers", [], b"", b"", 403),
            ("GET", "/offers", _signed(key, "GET", "/offers"), b"", b"", 200),
            ("GET", "/offers", [], b"", b"as_of=" + b"1" * 5000, 422),
            ("POST", "/offers/o/save", _signed(key, "POST", "/offers/o/save",
               body=b'{"expected_version":1}', role="viewer", operation_key="save-key-0001"),
             b'{"expected_version":1}', b"", 403),
        ]
        for method, path, headers, body, query, expected in cases:
            got_normal = await _request(normal._quote, method, path, headers=headers, body=body, query=query)
            got_helper = await _request(direct, method, path, headers=headers, body=body, query=query)
            assert got_normal[:2] == got_helper[:2]
            assert got_normal[0] == expected
            if expected == 200:
                assert got_normal[1] == {"workspace_id": "workspace-a", "data": {"offers": []}}
                assert got_normal[2][b"cache-control"] == b"no-store"
                assert got_helper[2][b"cache-control"] == b"no-store"
                assert (await _request(normal._quote, method, path, headers=headers))[0] == 403
                assert (await _request(direct, method, path, headers=headers))[0] == 403

    asyncio.run(compare())
    assert seen == ["workspace-a", "workspace-a"]


def test_offers_only_fresh_import_denies_normal_service(tmp_path, monkeypatch):
    """Building the dedicated app cannot import the module-global normal service."""
    import importlib.abc
    import socket
    from procure import settings

    data, config, db_path, key_file, _ = _paths(tmp_path)
    denied = ("procure.service_app", "procure.api", "procure.service_cache",
              "google.genai", "google.generativeai")
    assert all(name not in sys.modules for name in denied)

    class DenyNormal(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if any(fullname == name or fullname.startswith(name + ".") for name in denied):
                raise AssertionError("normal service/provider import attempted")
            return None

    original_read_text = Path.read_text

    def controlled_read_text(path, *args, **kwargs):
        if path.name == "materials.yaml" and path.parent != config:
            raise AssertionError("default materials read attempted")
        return original_read_text(path, *args, **kwargs)

    def no_settings():
        raise AssertionError("settings accessor used during local app construction")

    def no_listener(*args, **kwargs):
        raise AssertionError("listener created during app construction")

    monkeypatch.setattr(Path, "read_text", controlled_read_text)
    monkeypatch.setattr(settings, "get_settings", no_settings)
    monkeypatch.setattr(socket.socket, "bind", no_listener)
    finder = DenyNormal()
    sys.meta_path.insert(0, finder)
    try:
        from serve_offers_local import build_offers_only_app
        app = build_offers_only_app([
            "--quote-key-file", str(key_file), "--offers-db-path", str(db_path),
            "--data-dir", str(data), "--config-dir", str(config),
            "--host", "127.0.0.1", "--port", "8018",
        ])
        assert callable(app)
        assert all(name not in sys.modules for name in denied)
        assert not db_path.exists()  # Startup owns the first DB open.
    finally:
        sys.meta_path.remove(finder)


def test_strict_store_binding_never_falls_back(tmp_path, monkeypatch):
    """A lost connection binding never reopens the default numerical database."""
    import threading
    import pytest
    from procure import db

    data, _, path, _, _ = _paths(tmp_path)
    observed = []

    def forbidden_settings():
        observed.append("settings")
        raise AssertionError("strict path entered legacy settings")

    monkeypatch.setattr(db, "get_settings", forbidden_settings)
    db.enable_offers_only_strict()
    db.enable_offers_only_strict()  # No call may disable the process-wide mode.
    with pytest.raises(db.OffersBindingUnavailable):
        db._connect()
    assert observed == [] and db._CONN is None
    handle = db.open_offers_connection(path, data_dir=data)
    try:
        with db.bind_offers_connection(handle):
            assert int(db.query_df("SELECT 1 AS marker").iloc[0]["marker"]) == 1
            outcome = []

            def unbound_thread():
                try:
                    db._connect()
                except db.OffersBindingUnavailable:
                    outcome.append("closed")

            thread = threading.Thread(target=unbound_thread)
            thread.start()
            thread.join(timeout=2)
            assert not thread.is_alive() and outcome == ["closed"]
        with pytest.raises(db.OffersBindingUnavailable):
            db._connect()
        with pytest.raises(RuntimeError, match="synthetic failure"):
            with db.bind_offers_connection(handle):
                raise RuntimeError("synthetic failure")
        with pytest.raises(db.OffersBindingUnavailable):
            db._connect()
    finally:
        db.close_offers_connection(handle)
    with pytest.raises(db.OffersBindingUnavailable):
        db._connect()
    assert observed == [] and db._CONN is None
    assert not (data / "app.duckdb").exists()


def test_ordinary_legacy_db_path_remains_available(tmp_path, monkeypatch):
    """Strict offers mode in another process must not change normal DB behavior."""
    from procure import db, settings

    data = tmp_path / "ordinary"
    data.mkdir()
    path = data / "app.duckdb"
    monkeypatch.setenv("PROCURE_DATA_DIR", str(data))
    monkeypatch.setenv("PROCURE_DB_PATH", str(path))
    settings.reset_caches()
    try:
        db._connect()
        tables = set(db.query_df("SELECT table_name FROM information_schema.tables WHERE table_schema='main'")["table_name"])
        assert "prices_monthly" in tables and "model_selection" in tables
        assert path.exists()
    finally:
        db.close()
        settings.reset_caches()


def test_two_apps_keep_owned_connections_and_workspace_rows(tmp_path):
    """Two local apps cannot share handles, receipts or null-workspace rows."""
    import anyio
    from procure import db
    db.enable_offers_only_strict()
    from procure.offers.local_app import create_offers_only_app
    from procure.offers import store
    from procure.offers.lifecycle import OfferError

    left = _paths(tmp_path / "left")
    right = _paths(tmp_path / "right")
    seen = []

    def backend_for(label):
        async def backend(scope, receive, send):
            ctx = scope["offer_context"]

            def sync_offer_route():
                # AnyIO's worker must retain this app's ContextVar connection.
                if scope["path"] == "/offers/left-owned":
                    try:
                        store.get_offer("left-owned", context=ctx)
                    except OfferError as exc:
                        return exc.status, {"error": {"code": exc.code}}, None, None
                    return 200, {"unexpected": "foreign offer became visible"}, None, None
                db.insert_row("offer_requests", {
                    "idempotency_key": label + "-synthetic-key", "action": "synthetic-test",
                    "request_hash": label, "response_json": "{}", "created_at": "2026-10-10",
                    "workspace_id": ctx.workspace_id,
                })
                # A historical unscoped row remains invisible; the app must not
                # assign it to whichever workspace happens to query next.
                db.insert_row("offers", {
                    "id": label + "-legacy-null", "status": "saved", "version": 1,
                    "payload_json": "{}", "created_at": "2026-01-01", "updated_at": "2026-01-01",
                })
                owned = int(db.query_df("SELECT COUNT(*) AS n FROM offer_requests WHERE workspace_id = ?",
                                        [ctx.workspace_id]).iloc[0]["n"])
                visible = store.list_offers(context=ctx)
                tables = set(db.query_df("SELECT table_name FROM information_schema.tables WHERE table_schema='main'")["table_name"])
                if label == "left":
                    db.insert_row("offers", {
                        "id": "left-owned", "status": "saved", "version": 1,
                        "payload_json": "{}", "created_at": "2026-10-10", "updated_at": "2026-10-10",
                        "workspace_id": ctx.workspace_id,
                    })
                return 200, {"offers": visible, "receipts": owned}, visible, tables

            status, value, visible, tables = await anyio.to_thread.run_sync(sync_offer_route)
            if visible is not None:
                seen.append((label, ctx.workspace_id, value["receipts"], visible, tables))
            payload = json.dumps(value).encode()
            await send({"type": "http.response.start", "status": status,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": payload, "more_body": False})
        return backend

    async def scenario():
        a = create_offers_only_app(db_path=left[2], key_file=left[3], data_dir=left[0],
                                   config_dir=left[1], backend=backend_for("left"))
        b = create_offers_only_app(db_path=right[2], key_file=right[3], data_dir=right[0],
                                   config_dir=right[1], backend=backend_for("right"))
        la, lb = _Lifespan(a), _Lifespan(b)
        await la.start()
        await lb.start()
        try:
            for app, fixture, workspace in ((a, left, "workspace-a"), (b, right, "workspace-b")):
                status, value, _ = await _request(app, "GET", "/offers",
                                                  headers=_signed(fixture[4], "GET", "/offers", workspace=workspace))
                assert status == 200
                assert value == {"workspace_id": workspace, "data": {"offers": [], "receipts": 1}}
            foreign = await _request(b, "GET", "/offers/left-owned",
                                     headers=_signed(right[4], "GET", "/offers/left-owned", workspace="workspace-b"))
            assert foreign[0] == 404 and foreign[1]["data"]["error"]["code"] == "not_found"
        finally:
            await lb.stop()
            await la.stop()

    asyncio.run(scenario())
    assert [(label, workspace, count) for label, workspace, count, _, _ in seen] == [
        ("left", "workspace-a", 1), ("right", "workspace-b", 1)]
    assert all(visible == [] and tables == set(store.TABLES) for _, _, _, visible, tables in seen)
    assert not (tmp_path / "left" / "data" / "app.duckdb").exists()
    assert not (tmp_path / "right" / "data" / "app.duckdb").exists()


def test_shutdown_drains_bound_request_before_closing(tmp_path):
    """A synchronous offer worker keeps its owned connection until it returns."""
    import anyio
    import threading
    from procure import db
    db.enable_offers_only_strict()
    from procure.offers.local_app import create_offers_only_app

    data, config, path, key_file, key = _paths(tmp_path)

    async def scenario():
        entered = threading.Event()
        release = threading.Event()

        async def backend(scope, receive, send):
            def blocked_sync_route():
                entered.set()
                assert release.wait(15), "test worker release missing"
                return int(db.query_df("SELECT COUNT(*) AS n FROM offer_documents").iloc[0]["n"])

            count = await anyio.to_thread.run_sync(blocked_sync_route)
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": json.dumps({"count": count}).encode(),
                        "more_body": False})

        app = create_offers_only_app(db_path=path, key_file=key_file, data_dir=data, config_dir=config,
                                     backend=backend)
        life = _Lifespan(app)
        await life.start()
        request = asyncio.create_task(_request(app, "GET", "/offers",
                                               headers=_signed(key, "GET", "/offers")))
        assert await anyio.to_thread.run_sync(entered.wait, 2)
        closing = asyncio.create_task(life.stop())
        try:
            try:
                await asyncio.wait_for(asyncio.shield(closing), 0.05)
                assert False, "shutdown closed an active request"
            except asyncio.TimeoutError:
                pass
            release.set()
            response = await asyncio.wait_for(request, 2)
            assert response[0] == 200 and response[1]["data"] == {"count": 0}
            await asyncio.wait_for(closing, 3)
        finally:
            release.set()

    asyncio.run(scenario())
    assert not path.with_name("app.duckdb").exists()


def test_shutdown_timeout_keeps_active_connection_until_worker_returns(tmp_path):
    """A bounded failed shutdown must not close a handle under a blocked route."""
    import anyio
    import threading
    from procure import db
    db.enable_offers_only_strict()
    from procure.offers.local_app import create_offers_only_app

    data, config, path, key_file, key = _paths(tmp_path)

    async def scenario():
        entered = threading.Event()
        release = threading.Event()

        async def backend(scope, receive, send):
            def blocked_sync_route():
                entered.set()
                assert release.wait(15), "test worker release missing"
                # The owned handle must still be usable after shutdown's timeout.
                return int(db.query_df("SELECT COUNT(*) AS n FROM offers").iloc[0]["n"])

            rows = await anyio.to_thread.run_sync(blocked_sync_route)
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": json.dumps({"rows": rows}).encode(),
                        "more_body": False})

        app = create_offers_only_app(db_path=path, key_file=key_file, data_dir=data, config_dir=config,
                                     backend=backend)
        life = _Lifespan(app)
        await life.start()
        request = asyncio.create_task(_request(app, "GET", "/offers",
                                               headers=_signed(key, "GET", "/offers")))
        assert await anyio.to_thread.run_sync(entered.wait, 2)
        await life.incoming.put({"type": "lifespan.shutdown"})
        try:
            event = await asyncio.wait_for(life.outgoing.get(), 10)
            assert event["type"] == "lifespan.shutdown.failed", event
            release.set()
            response = await asyncio.wait_for(request, 2)
            assert response[0] == 200 and response[1]["data"] == {"rows": 0}
        finally:
            release.set()
            try:
                await asyncio.wait_for(life.task, 2)
            except RuntimeError:
                pass  # ASGI lifespan reports startup/shutdown failure by raising.

    asyncio.run(scenario())


def test_local_key_drift_and_route_gates_fail_closed(tmp_path):
    """A changed sealed key, direct browser identity and numerical route never reach offers."""
    from procure import db
    db.enable_offers_only_strict()
    from procure.offers.local_app import create_offers_only_app

    data, config, path, key_file, key = _paths(tmp_path)
    calls = []

    async def backend(scope, receive, send):
        calls.append(scope["path"])
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": b'{"offers":[]}', "more_body": False})

    async def scenario():
        app = create_offers_only_app(db_path=path, key_file=key_file, data_dir=data, config_dir=config,
                                     backend=backend)
        life = _Lifespan(app)
        await life.start()
        try:
            good = await _request(app, "GET", "/offers", headers=_signed(key, "GET", "/offers"))
            assert good[0] == 200 and good[1]["workspace_id"] == "workspace-a"
            browser = await _request(app, "GET", "/offers", headers=[(b"authorization", b"Bearer synthetic")])
            assert browser[0] == 403
            remote = await _request(app, "GET", "/offers", headers=_signed(key, "GET", "/offers"),
                                    client="192.0.2.1")
            assert remote[0] == 403
            numerical = await _request(app, "GET", "/forecast/copper")
            assert numerical[0] in (403, 404)
            health = await _request(app, "GET", "/-/health")
            assert health[0] == 200 and health[1]["provider"] == "not_verified"
            key_file.chmod(0o644)
            drift = await _request(app, "GET", "/offers", headers=_signed(key, "GET", "/offers"))
            assert drift[0] == 503 and drift[1]["detail"]["code"] == "quote_unavailable"
            key_file.chmod(0o600)
            key_file.write_bytes(os.urandom(32))
            replaced = await _request(app, "GET", "/offers", headers=_signed(key, "GET", "/offers"))
            assert replaced[0] == 503 and replaced[1]["detail"]["code"] == "quote_unavailable"
        finally:
            key_file.chmod(0o600)
            await life.stop()

    asyncio.run(scenario())
    assert calls == ["/offers"]


def test_local_upload_requires_rules_and_explicit_synthetic(tmp_path):
    """An editor's signed upload cannot select Gemini or omit the synthetic label."""
    from procure import db
    db.enable_offers_only_strict()
    from procure.offers.local_app import create_offers_only_app

    data, config, path, key_file, key = _paths(tmp_path)
    accepted = []

    async def fake_backend(scope, receive, send):
        accepted.append(scope["offer_context"].workspace_id)
        await send({"type": "http.response.start", "status": 201,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": b'{"extraction_id":"synthetic-only"}',
                    "more_body": False})

    async def scenario():
        app = create_offers_only_app(db_path=path, key_file=key_file, data_dir=data, config_dir=config,
                                     backend=fake_backend)
        life = _Lifespan(app)
        await life.start()
        try:
            base = {"filename": "synthetic.txt", "content_base64": base64.b64encode(b"Synthetic supplier quote").decode(),
                    "mode": "rules", "synthetic": True}
            for changed in ({**base, "mode": "gemini"}, {k: v for k, v in base.items() if k != "synthetic"},
                            {**base, "synthetic": False}):
                body = json.dumps(changed).encode()
                response = await _request(app, "POST", "/offers/extractions", body=body,
                                          headers=_signed(key, "POST", "/offers/extractions", body=body,
                                                          role="editor"))
                assert response[0] == 422
            body = json.dumps(base).encode()
            viewer = await _request(app, "POST", "/offers/extractions", body=body,
                                    headers=_signed(key, "POST", "/offers/extractions", body=body,
                                                    role="viewer"))
            assert viewer[0] == 403
            valid = await _request(app, "POST", "/offers/extractions", body=body,
                                   headers=_signed(key, "POST", "/offers/extractions", body=body,
                                                   role="editor"))
            assert valid[0] == 201 and valid[1]["data"]["extraction_id"] == "synthetic-only"
        finally:
            await life.stop()

    asyncio.run(scenario())
    assert accepted == ["workspace-a"]


def test_shutdown_never_waits_for_blocked_refusal_or_failure_send(tmp_path, monkeypatch):
    """A stalled ASGI sender must not retain the shutdown condition or active worker."""
    data, config, path, key_file, key = _paths(tmp_path)
    monkeypatch.setenv("PROCURE_DATA_DIR", str(data))
    monkeypatch.setenv("PROCURE_DB_PATH", str(path))
    monkeypatch.setenv("PROCURE_CONFIG_DIR", str(config))
    monkeypatch.setenv("OFFLINE", "1")
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    from procure import db
    db.enable_offers_only_strict()
    from procure.offers.local_app import create_offers_only_app

    async def reached(awaitable, name, seconds=5):
        try:
            return await asyncio.wait_for(awaitable, seconds)
        except TimeoutError:
            raise AssertionError(name) from None

    async def settle(*tasks):
        owned = tuple(task for task in tasks if task is not None)
        pending = {task for task in owned if not task.done()}
        if pending:
            _, pending = await asyncio.wait(pending, timeout=4)
        needed_cancel = bool(pending)
        if pending:
            for task in pending:
                task.cancel()
        await asyncio.gather(*owned, return_exceptions=True)
        return needed_cancel

    async def locked_refusal_scenario():
        release_refusal_send = asyncio.Event()
        refusal_send_started = asyncio.Event()
        lifecycle_in = asyncio.Queue()
        lifecycle_out = asyncio.Queue()
        refusal_messages = []

        async def backend(scope, receive, send):
            raise AssertionError("refused request reached backend")

        app = create_offers_only_app(db_path=path, key_file=key_file, data_dir=data,
                                     config_dir=config, backend=backend)
        lifecycle = asyncio.create_task(app(
            {"type": "lifespan", "asgi": {"version": "3.0"}},
            lifecycle_in.get, lifecycle_out.put))
        refusal = None
        needed_cancel = False
        try:
            await lifecycle_in.put({"type": "lifespan.startup"})
            startup = await reached(lifecycle_out.get(), "loopback refusal startup did not complete")
            assert startup["type"] == "lifespan.startup.complete", startup
            key_file.chmod(0o644)  # A loopback offer request must now refuse inside the condition.
            scope = {
                "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                "scheme": "http", "method": "GET", "path": "/offers", "raw_path": b"/offers",
                "query_string": b"", "root_path": "", "headers": [],
                "client": ("127.0.0.1", 19000), "server": ("127.0.0.1", 8018),
            }

            async def refusal_receive():
                return {"type": "http.disconnect"}

            async def refusal_send(message):
                refusal_messages.append(message)
                if message["type"] == "http.response.start":
                    refusal_send_started.set()
                    await release_refusal_send.wait()

            refusal = asyncio.create_task(app(scope, refusal_receive, refusal_send))
            await reached(refusal_send_started.wait(), "loopback locked refusal sender was not reached", 3)
            assert not refusal.done(), "loopback refusal sender did not remain blocked"
            await lifecycle_in.put({"type": "lifespan.shutdown"})
            done, _ = await asyncio.wait({lifecycle}, timeout=0.5)
            assert lifecycle in done, "blocked loopback refusal held shutdown condition"
            assert not lifecycle.cancelled(), "shutdown lifecycle was cancelled"
            assert lifecycle.result() is None
            assert app.handle is None, "shutdown left the owned connection open"
            assert not refusal.done(), "loopback refusal sender released before its gate"
        finally:
            release_refusal_send.set()
            needed_cancel = await settle(refusal, lifecycle)
            if app.handle is not None and not app.handle.closed:
                db.close_offers_connection(app.handle)
            key_file.chmod(0o600)

        assert not needed_cancel, "loopback refusal workers required cancellation"
        assert refusal is not None and not refusal.cancelled()
        assert refusal.exception() is None
        assert not lifecycle.cancelled() and lifecycle.exception() is None
        starts = [m for m in refusal_messages if m["type"] == "http.response.start"]
        assert len(starts) == 1 and starts[0]["status"] == 503

    async def blocked_failure_send_scenario():
        backend_started = asyncio.Event()
        release_backend = asyncio.Event()
        failed_send_started = asyncio.Event()
        release_failed_send = asyncio.Event()
        lifecycle_in = asyncio.Queue()
        lifecycle_out = asyncio.Queue()

        async def backend(scope, receive, send):
            backend_started.set()
            await release_backend.wait()
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": b'{"offers":[]}', "more_body": False})

        app = create_offers_only_app(db_path=path, key_file=key_file, data_dir=data,
                                     config_dir=config, backend=backend)

        async def lifecycle_send(message):
            await lifecycle_out.put(message)
            if message["type"] == "lifespan.shutdown.failed":
                failed_send_started.set()
                await release_failed_send.wait()

        lifecycle = asyncio.create_task(app(
            {"type": "lifespan", "asgi": {"version": "3.0"}}, lifecycle_in.get, lifecycle_send))
        active_request = None
        needed_cancel = False
        try:
            await lifecycle_in.put({"type": "lifespan.startup"})
            startup = await reached(lifecycle_out.get(), "blocked failure-send startup did not complete")
            assert startup["type"] == "lifespan.startup.complete", startup
            active_request = asyncio.create_task(_request(
                app, "GET", "/offers", headers=_signed(key, "GET", "/offers")))
            await reached(backend_started.wait(), "active backend was not admitted", 3)
            await lifecycle_in.put({"type": "lifespan.shutdown"})
            await reached(failed_send_started.wait(), "shutdown failure sender was not reached", 4)
            assert app.handle is not None and not app.handle.closed, (
                "shutdown closed the owned connection under its active request"
            )
            release_backend.set()
            done, _ = await asyncio.wait({active_request}, timeout=0.5)
            assert active_request in done, (
                "active ASGI request could not finish while lifespan.shutdown.failed send was blocked"
            )
            assert not active_request.cancelled(), "active ASGI request was cancelled"
            assert active_request.result()[0] == 200
            assert not lifecycle.done(), "failure-send gate did not hold lifespan task"
        finally:
            release_backend.set()
            release_failed_send.set()
            needed_cancel = await settle(active_request, lifecycle)
            if app.handle is not None and not app.handle.closed:
                db.close_offers_connection(app.handle)

        assert not needed_cancel, "blocked failure-send workers required cancellation"
        assert active_request is not None and not active_request.cancelled()
        assert active_request.exception() is None
        assert not lifecycle.cancelled() and lifecycle.exception() is None
        failed = await reached(lifecycle_out.get(), "shutdown failed result was not delivered")
        assert failed["type"] == "lifespan.shutdown.failed", failed

    async def scenario():
        await locked_refusal_scenario()
        await blocked_failure_send_scenario()

    asyncio.run(scenario())


def test_default_router_real_synthetic_upload_lifecycle_and_tenant_isolation(tmp_path, monkeypatch):
    """Exercise real offers routes over one generated synthetic document and two workspaces."""
    data, config, path, key_file, key = _paths(tmp_path)
    (config / "materials.yaml").write_text(
        "materials:\n  synthetic_copper:\n    display_name: Synthetic Copper\n    keywords:\n      - copper\n",
        encoding="utf-8")
    monkeypatch.setenv("PROCURE_DATA_DIR", str(data))
    monkeypatch.setenv("PROCURE_DB_PATH", str(path))
    monkeypatch.setenv("PROCURE_CONFIG_DIR", str(config))
    monkeypatch.setenv("OFFLINE", "1")
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    from procure import db
    db.enable_offers_only_strict()
    from procure.settings import get_settings
    from procure.offers import store
    from procure.offers.local_app import create_offers_only_app

    settings = get_settings()
    assert settings.data_dir == data and settings.db_path == path and settings.config_dir == config
    assert settings.offline is True

    fixed_epoch = 1_900_000_000
    csv_bytes = (
        b"Supplier,Synthetic Supplier Ltd.\n"
        b"Settlement Currency,USD\n"
        b"Incoterm,FOB Synthetic Port\n"
        b"Valid until,2099-12-31\n"
        b"Product,Synthetic Copper\n"
        b"Quantity,1000 kg\n"
        b"Price,USD 8.50/kg\n"
    )

    def sign(method, request_path, *, body=b"", query=b"", role="viewer", workspace="workspace-a",
             operation_key=None):
        sign.counter += 1
        assertion = {
            "v": 1, "method": method, "raw_path": request_path, "raw_query": query.decode("ascii"),
            "body_sha256": hashlib.sha256(body).hexdigest(), "issued_at_unix": fixed_epoch,
            "nonce": f"{sign.counter:032x}", "principal_id": "synthetic-reviewer",
            "workspace_id": workspace, "role": role, "idempotency_key": operation_key,
        }
        wire = json.dumps(assertion, separators=(",", ":")).encode("utf-8")
        headers = [
            (b"x-quote-context", base64.urlsafe_b64encode(wire).rstrip(b"=")),
            (b"x-quote-signature", base64.urlsafe_b64encode(
                hmac.new(key, wire, hashlib.sha256).digest()).rstrip(b"=")),
        ]
        if operation_key is not None:
            headers.append((b"idempotency-key", operation_key.encode("ascii")))
        if body:
            headers.extend(((b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode("ascii"))))
        return headers
    sign.counter = 0

    async def scenario():
        app = create_offers_only_app(db_path=path, key_file=key_file, data_dir=data, config_dir=config,
                                     clock=lambda: fixed_epoch)
        life = _Lifespan(app)
        await life.start()

        async def request(method, request_path, *, body=b"", query=b"", role="viewer",
                          workspace="workspace-a", operation_key=None):
            headers = sign(method, request_path, body=body, query=query, role=role,
                           workspace=workspace, operation_key=operation_key)
            return await _request(app, method, request_path, headers=headers, body=body, query=query)

        try:
            upload = json.dumps({
                "filename": "synthetic-offer.csv",
                "content_base64": base64.b64encode(csv_bytes).decode("ascii"),
                "mode": "rules", "synthetic": True, "reporting_currency": "USD",
            }, separators=(",", ":")).encode("utf-8")
            status, uploaded, headers = await request("POST", "/offers/extractions", body=upload, role="editor")
            assert status == 201 and headers.get(b"cache-control") == b"no-store"
            assert set(uploaded) == {"workspace_id", "data"} and uploaded["workspace_id"] == "workspace-a"
            initial = uploaded["data"]["offer"]
            assert uploaded["data"]["deduplicated"] is False
            assert uploaded["data"]["extraction_id"] == initial["extraction_id"]
            assert initial["synthetic"] is True and initial["document"]["filename"] == "synthetic-offer.csv"
            offer_id = initial["offer_id"]
            extraction_id = uploaded["data"]["extraction_id"]

            status, extracted, headers = await request("GET", f"/offers/extractions/{extraction_id}")
            assert status == 200 and headers.get(b"cache-control") == b"no-store"
            extraction = extracted["data"]
            assert extraction["extraction_id"] == extraction_id
            document_id = "sha256:" + hashlib.sha256(csv_bytes).hexdigest()
            assert extraction["extraction"]["document"]["document_id"] == document_id
            assert extraction["segments"]
            assert any(segment["locator"] == "r1:c2" and segment["text"] == "Synthetic Supplier Ltd."
                       for segment in extraction["segments"])
            for segment in extraction["segments"]:
                assert segment["text_sha256"] == hashlib.sha256(segment["text"].encode("utf-8")).hexdigest()

            correction_key = "synthetic-correction-0001"
            correction_body = json.dumps({
                "field": "supplier_name", "value": "Reviewed Synthetic Supplier Ltd.",
                "expected_version": initial["version"], "reason": "synthetic reviewer correction",
            }, separators=(",", ":")).encode("utf-8")
            status, corrected, headers = await request(
                "POST", f"/offers/{offer_id}/corrections", body=correction_body, role="editor",
                operation_key=correction_key)
            assert status == 200 and headers.get(b"cache-control") == b"no-store"
            corrected = corrected["data"]
            assert corrected["version"] == initial["version"] + 1
            assert corrected["fields"]["supplier_name"]["value"] == "Reviewed Synthetic Supplier Ltd."
            assert corrected["fields"]["supplier_name"]["evidence_status"] == "user_entered_without_document_evidence"

            save_key = "synthetic-save-0001"
            save_body = json.dumps({"expected_version": corrected["version"]}, separators=(",", ":")).encode("utf-8")
            status, saved, headers = await request(
                "POST", f"/offers/{offer_id}/save", body=save_body, role="editor", operation_key=save_key)
            assert status == 200 and headers.get(b"cache-control") == b"no-store"
            saved = saved["data"]
            assert saved["status"] == "saved" and saved["version"] == corrected["version"] + 1

            view_query = b"include_events=true&as_of=2090-01-01"
            status, detail, _ = await request("GET", f"/offers/{offer_id}", query=view_query)
            assert status == 200
            detail = detail["data"]
            assert detail["status"] == "saved" and detail["version"] == saved["version"]
            assert detail["as_of"] == "2090-01-01" and detail["normalized"]["as_of"] == "2090-01-01"
            assert {event["action"] for event in detail["events"]} >= {"created", "correct", "save"}
            assert detail["fields"]["supplier_name"]["value"] == "Reviewed Synthetic Supplier Ltd."
            assert detail["fields"]["supplier_name"]["evidence_status"] == "user_entered_without_document_evidence"
            assert detail["normalized"]["items"][0]["quantity"]["status"] == "ok"
            assert detail["normalized"]["items"][0]["pricing"]["status"] == "ok"

            list_query = b"status=saved&as_of=2090-01-01"
            status, listed, _ = await request("GET", "/offers", query=list_query)
            assert status == 200 and [row["offer_id"] for row in listed["data"]["offers"]] == [offer_id]
            status, sourcing, _ = await request(
                "GET", "/offers/sourcing-candidates", query=b"as_of=2090-01-01")
            assert status == 200
            candidates = sourcing["data"]["candidates"]
            assert len(candidates) == 1 and sourcing["data"]["excluded"] == []
            candidate = candidates[0]
            assert candidate["offer_id"] == offer_id and candidate["offer_version"] == saved["version"]
            assert candidate["item_index"] == 0 and candidate["document_id"] == document_id
            assert candidate["material"] == "synthetic_copper"
            assert candidate["quantity"]["kg"] == "1000"

            with db.bind_offers_connection(app.handle):
                db.execute("INSERT INTO offers (id, status, version, synthetic, workspace_id) VALUES (?, ?, ?, ?, NULL)",
                           ["synthetic-null-workspace-legacy", "saved", 1, True])
                actual_tables = set(db.query_df("SHOW TABLES")["name"].tolist())
            assert actual_tables == set(store.TABLES)

            status, owner_list, _ = await request("GET", "/offers", query=list_query)
            assert status == 200 and [row["offer_id"] for row in owner_list["data"]["offers"]] == [offer_id]

            for request_path in (f"/offers/{offer_id}", f"/offers/extractions/{extraction_id}"):
                status, foreign, _ = await request("GET", request_path, workspace="workspace-b")
                assert status == 404 and foreign["data"]["error"]["code"] == "not_found"
            for request_path in ("/offers", "/offers/sourcing-candidates"):
                query = b"status=saved&as_of=2090-01-01" if request_path == "/offers" else b"as_of=2090-01-01"
                status, foreign, _ = await request("GET", request_path, query=query, workspace="workspace-b")
                assert status == 200
                if request_path == "/offers":
                    assert foreign["data"]["offers"] == []
                else:
                    assert foreign["data"]["candidates"] == [] and foreign["data"]["excluded"] == []

            for workspace in ("workspace-a", "workspace-b"):
                status, legacy, _ = await request("GET", "/offers/synthetic-null-workspace-legacy",
                                                  workspace=workspace)
                assert status == 404 and legacy["data"]["error"]["code"] == "not_found"
        finally:
            await life.stop()

    asyncio.run(scenario())
