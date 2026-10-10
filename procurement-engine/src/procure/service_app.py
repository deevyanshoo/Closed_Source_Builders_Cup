"""Restricted service entrypoint; legacy API startup remains unchanged.

This wrapper owns startup: validate/restore a cache, then dispatch only numerical
reads and recommendation calculations. It never invokes the legacy API lifespan.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import ipaddress
import os
from pathlib import Path

import anyio
from starlette.responses import JSONResponse
import yaml

from .service_auth import Caller, EMAIL, TokenRejected, authorize, load_callers, validate_audience
from .offers.quote_transport import serve_signed_quote

@dataclass(frozen=True)
class Config:
    mode: str
    audience: str
    callers: tuple[Caller, ...]
    data_dir: Path
    db_path: Path
    bootstrap_dir: Path | None
    bootstrap_sha256: str
    materials: tuple[str, ...]


def prepare_cache(config: Config) -> dict:
    from .service_cache import CacheUnavailable, restore_snapshot, validate_database
    if config.mode == 'private_machine':
        if config.bootstrap_dir is None or not config.bootstrap_sha256:
            raise CacheUnavailable('Pinned bootstrap snapshot is required')
        return restore_snapshot(config.bootstrap_dir, config.data_dir,
                                config.bootstrap_sha256, config.materials)
    validate_database(config.db_path, config.materials)
    return {'db_path': str(config.db_path), 'cache_policy': 'existing_validated'}


class ServiceApp:
    def __init__(self, config: Config, backend=None, verifier=None, quote_verifier=None):
        self.config = config
        self.backend = backend
        self.verifier = verifier
        self.quote_verifier = quote_verifier
        self.ready = False
        self.owns_core = backend is None

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'lifespan':
            while True:
                event = await receive()
                if event['type'] == 'lifespan.startup':
                    try:
                        await anyio.to_thread.run_sync(prepare_cache, self.config)
                        if self.owns_core:
                            # Dedicated service process only; no legacy startup or ingestion.
                            os.environ['PROCURE_DATA_DIR'] = str(self.config.data_dir)
                            os.environ['PROCURE_DB_PATH'] = str(self.config.db_path)
                            from . import db, settings
                            db.close()
                            settings.reset_caches()
                            from .api import app
                            self.backend = app
                        self.ready = True
                    except Exception:
                        await send({'type': 'lifespan.startup.failed',
                                    'message': 'Validated service cache unavailable'})
                        raise RuntimeError('Validated service cache unavailable') from None
                    await send({'type': 'lifespan.startup.complete'})
                elif event['type'] == 'lifespan.shutdown':
                    self.ready = False
                    if self.owns_core:
                        from . import db
                        db.close()
                    await send({'type': 'lifespan.shutdown.complete'})
                    return
            return
        if scope['type'] != 'http':
            if scope['type'] == 'websocket':
                await send({'type': 'websocket.close', 'code': 1008})
            return

        async def error(status, code):
            await JSONResponse({'detail': {'code': code}}, status_code=status)(scope, receive, send)

        method, path = scope['method'], scope['path']
        if method == 'GET' and path == '/-/health':
            await JSONResponse({'status': 'ok', 'provider': 'not_verified'})(scope, receive, send)
            return
        headers = scope.get('headers', [])
        if path == '/offers' or path.startswith('/offers/'):
            if self.config.mode != 'local':
                await error(403, 'service_route_unavailable')
                return
            try:
                local = ipaddress.ip_address(scope.get('client', ('', 0))[0]).is_loopback
            except (ValueError, TypeError):
                local = False
            if not local:
                await error(403, 'local_only')
                return
            await self._quote(scope, receive, send)
            return
        if self.config.mode == 'private_machine':
            auth = [value for name, value in headers if name.lower() == b'authorization']
            if len(auth) != 1 or not auth[0].startswith(b'Bearer '):
                await error(401, 'unauthorized')
                return
            try:
                token = auth[0][7:].decode('ascii')
                await anyio.to_thread.run_sync(lambda: authorize(
                    token, self.config.audience, self.config.callers, verifier=self.verifier))
            except (TokenRejected, UnicodeError):
                await error(401, 'unauthorized')
                return
            if any(name.lower() in {b'origin', b'x-role', b'x-actor', b'x-principal',
                                   b'x-principal-id', b'x-workspace', b'x-workspace-id'}
                   for name, _ in headers):
                await error(403, 'client_identity_forbidden')
                return
        else:
            try:
                local = ipaddress.ip_address(scope.get('client', ('', 0))[0]).is_loopback
            except (ValueError, TypeError):
                local = False
            if not local:
                await error(403, 'local_only')
                return
        allowed = ((method == 'GET' and path in {'/health', '/materials', '/metrics', '/overview'})
                   or (method == 'GET' and any(path.startswith(prefix) and
                       '/' not in path[len(prefix):] and bool(path[len(prefix):])
                       for prefix in ('/prices/', '/forecast/', '/news/', '/backtest/')))
                   or (method == 'POST' and path in {'/recommend',
                       '/production/receipt-recovery/comparisons'}))
        if not allowed:
            await error(403, 'service_route_unavailable')
            return
        if not self.ready or self.backend is None:
            await error(503, 'cache_unavailable')
            return
        await self.backend(scope, receive, send)

    async def _quote(self, scope, receive, send):
        await serve_signed_quote(scope, receive, send, backend=self.backend,
                                 verifier=self.quote_verifier, ready=self.ready)


def create_app(*, mode=None, audience=None, callers=None, backend=None, verifier=None, quote_verifier=None,
               data_dir=None, bootstrap_dir=None, bootstrap_sha256=None, materials=None):
    mode = mode if mode is not None else os.getenv('PROCURE_SERVICE_MODE', 'local')
    if mode not in {'local', 'private_machine'}:
        raise ValueError('Invalid service mode')
    audience = audience if audience is not None else os.getenv('PROCURE_SERVICE_AUDIENCE', '')
    if mode == 'private_machine':
        audience = validate_audience(audience)
        if callers is None:
            path = os.getenv('PROCURE_SERVICE_CALLERS_FILE', '')
            if not path:
                raise ValueError('Restricted service caller configuration is required')
            callers = load_callers(Path(path))
        if (not isinstance(callers, tuple) or not callers
                or any(not isinstance(c, Caller) or not c.subject or not EMAIL.fullmatch(c.email)
                       for c in callers)):
            raise ValueError('Restricted service caller configuration is required')
    else:
        callers = ()
    project = Path(__file__).resolve().parents[2]
    explicit_data = data_dir if data_dir is not None else os.getenv('PROCURE_DATA_DIR')
    if mode == 'private_machine' and not explicit_data:
        raise ValueError('Explicit mutable cache directory is required')
    data = Path(explicit_data or project / 'data').absolute()
    db = Path(os.getenv('PROCURE_DB_PATH', str(data / 'app.duckdb'))).absolute()
    if mode == 'private_machine' and db != data / 'app.duckdb':
        raise ValueError('Private database must be inside mutable cache directory')
    bootstrap = bootstrap_dir if bootstrap_dir is not None else os.getenv('PROCURE_BOOTSTRAP_DIR')
    digest = bootstrap_sha256 if bootstrap_sha256 is not None else os.getenv('PROCURE_BOOTSTRAP_SHA256', '')
    if materials is None:
        config_dir = Path(os.getenv('PROCURE_CONFIG_DIR', project / 'config'))
        config = yaml.safe_load((config_dir / 'materials.yaml').read_text())
        materials = tuple(config['materials'])
    return ServiceApp(Config(mode, audience, callers, data, db,
                            Path(bootstrap).absolute() if bootstrap else None,
                            digest, materials), backend, verifier, quote_verifier)


app = create_app()


def main():
    import uvicorn
    value = os.getenv('PORT', '8018' if app.config.mode == 'local' else '8080')
    if not value.isdecimal() or not 1 <= int(value) <= 65535:
        raise ValueError('PORT must be a valid TCP port')
    uvicorn.run(app, host='127.0.0.1' if app.config.mode == 'local' else '0.0.0.0',
                port=int(value), workers=1, proxy_headers=False)


if __name__ == '__main__':
    main()
