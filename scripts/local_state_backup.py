#!/usr/bin/env python3
"""Private operator-only export and empty-database restore of cs_plan_actions."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import select
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit

BIN = Path('/opt/homebrew/opt/postgresql@17/bin')
SCHEMA = 'cs_plan_actions'
TABLES = ('schema_migrations', 'plans', 'plan_revisions', 'approvals', 'action_receipts', 'audit_events')
MIGRATION = Path(__file__).resolve().parents[1] / 'services/gateway/migrations/001_plans_actions.up.sql'
SHA40 = re.compile(r'[0-9a-f]{40}\Z')
SHA64 = re.compile(r'[0-9a-f]{64}\Z')
SNAPSHOT = re.compile(r'[0-9A-Fa-f-]{4,100}\Z')
PG17_VERSION = re.compile(r'17\.\d+(?:\.\d+)?(?: \(Homebrew\))?\Z')


class BackupError(Exception):
    """A sanitized failure: never include a URL or raw tool output."""


@dataclass(frozen=True)
class Connection:
    host: str
    port: int
    user: str
    database: str
    password: str = field(repr=False)


def private_dir(path: Path) -> None:
    try:
        info = path.lstat()
    except OSError:
        raise BackupError('Private directory is unavailable.') from None
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise BackupError('Private directory must be owned and mode 0700.')


def private_file(path: Path, maximum: int | None = None) -> None:
    private_dir(path.parent)
    try:
        info = path.lstat()
    except OSError:
        raise BackupError('Private file is unavailable.') from None
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise BackupError('Private file must be owned, regular and mode 0600.')
    if maximum is not None and info.st_size > maximum:
        raise BackupError('Private file exceeds its size limit.')


def load_connection(path: str | Path) -> Connection:
    path = Path(path)
    private_file(path, 8192)
    try:
        raw = path.read_text(encoding='utf-8').rstrip('\n')
        if not raw or '\n' in raw or '\r' in raw or any(c.isspace() for c in raw):
            raise ValueError()
        url = urlsplit(raw)
        if url.scheme not in ('postgresql', 'postgres') or url.query or url.fragment or url.hostname not in ('127.0.0.1', 'localhost'):
            raise ValueError()
        if not url.port or not 1 <= url.port <= 65535 or not url.username or url.password is None:
            raise ValueError()
        user, password, database = unquote(url.username), unquote(url.password), unquote(url.path.removeprefix('/'))
        if not user or not password or not database or '/' in database or database in ('postgres', 'template0', 'template1'):
            raise ValueError()
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]{0,62}', user) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]{0,62}', database):
            raise ValueError()
        if url.netloc.count('@') != 1 or any(c in value for value in (user, password, database) for c in ('\x00', '\n', '\r')):
            raise ValueError()
        return Connection('127.0.0.1', url.port, user, database, password)
    except (OSError, UnicodeError, ValueError):
        raise BackupError('Connection file must contain one explicit loopback PostgreSQL URL for a non-system database.') from None


@contextmanager
def pg_environment(connection: Connection):
    with tempfile.TemporaryDirectory(prefix='local-state-pgpass-') as temporary:
        directory = Path(temporary)
        directory.chmod(0o700)
        password_file = directory / 'pgpass'
        def escape(value: str) -> str:
            return value.replace('\\', '\\\\').replace(':', '\\:')
        line = ':'.join((connection.host, str(connection.port), escape(connection.database), escape(connection.user), escape(connection.password))) + '\n'
        descriptor = os.open(password_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())
        yield {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'PGPASSFILE': str(password_file), 'PGCONNECT_TIMEOUT': '5', 'PGTZ': 'UTC'}


def _binary(name: str) -> str:
    if name not in ('psql', 'pg_dump', 'pg_restore'):
        raise BackupError('Unsupported PostgreSQL tool.')
    return str(BIN / name)


def run_tool(name: str, args: list[str], connection: Connection, timeout: int = 15) -> bytes:
    if not 1 <= timeout <= 120:
        raise BackupError('Invalid tool timeout.')
    command = [_binary(name), '--no-password', '--host', connection.host, '--port', str(connection.port), '--username', connection.user, '--dbname', connection.database, *args]
    with pg_environment(connection) as environment:
        try:
            return subprocess.run(command, env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  timeout=timeout, check=True).stdout
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            raise BackupError(f'{name} failed or timed out; inspect private task state before retrying.') from None


def _plain_tool(name: str, args: list[str], timeout: int = 15) -> bytes:
    try:
        return subprocess.run([_binary(name), *args], env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'}, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=timeout, check=True).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise BackupError(f'{name} inspection failed or timed out.') from None


def tool_versions() -> dict[str, str]:
    versions = {}
    for name in ('psql', 'pg_dump', 'pg_restore'):
        output = _plain_tool(name, ['--version']).decode('ascii', errors='replace').strip()
        prefix = f'{name} (PostgreSQL) '
        version = output.removeprefix(prefix)
        if not output.startswith(prefix) or not PG17_VERSION.fullmatch(version):
            raise BackupError('PostgreSQL 17 tool version is required.')
        versions[name] = version
    if len(set(versions.values())) != 1:
        raise BackupError('PostgreSQL tool versions do not match.')
    return versions


def query(connection: Connection, sql: str, timeout: int = 15) -> str:
    output = run_tool('psql', ['-X', '-q', '-A', '-t', '-v', 'ON_ERROR_STOP=1', '-c', sql], connection, timeout)
    return output.decode('utf-8', errors='strict').strip()


def identity(connection: Connection) -> dict[str, str]:
    output = query(connection, "SELECT system_identifier::text || '|' || (SELECT oid::text FROM pg_database WHERE datname=current_database()) || '|' || current_database() FROM pg_control_system()")
    parts = output.split('|')
    if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit() or parts[2] != connection.database:
        raise BackupError('Could not verify actual database identity.')
    return {'system_identifier': parts[0], 'database_oid': parts[1], 'database': parts[2]}


def assert_distinct(source: dict[str, str], target: dict[str, str]) -> None:
    if source['system_identifier'] == target['system_identifier'] and source['database_oid'] == target['database_oid']:
        raise BackupError('Source and destination are the same actual database.')


def verify_empty(connection: Connection) -> None:
    sql = """SELECT
      (SELECT count(*) FROM pg_namespace WHERE nspname NOT IN ('public','information_schema') AND nspname !~ '^pg_')
    + (SELECT count(*) FROM pg_class WHERE oid >= 16384)
    + (SELECT count(*) FROM pg_proc WHERE oid >= 16384)
    + (SELECT count(*) FROM pg_type WHERE oid >= 16384)
    + (SELECT count(*) FROM pg_extension WHERE extname<>'plpgsql')"""
    if query(connection, sql) != '0':
        raise BackupError('Restore target is not an empty local database.')


def _source_schema(connection: Connection) -> None:
    names = query(connection, "SELECT string_agg(tablename, ',' ORDER BY tablename) FROM pg_tables WHERE schemaname='cs_plan_actions'")
    if names != ','.join(sorted(TABLES)):
        raise BackupError('Schema does not have exactly the accepted six tables.')
    versions = query(connection, "SELECT string_agg(version::text, ',' ORDER BY version) FROM cs_plan_actions.schema_migrations")
    if versions != '1':
        raise BackupError('Schema migration version is not 1.')


def _server_version(connection: Connection) -> str:
    version = query(connection, 'SHOW server_version')
    if not PG17_VERSION.fullmatch(version):
        raise BackupError('PostgreSQL 17 server is required.')
    return version


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _migration_sha() -> str:
    return _sha_file(MIGRATION)


def _git_revision() -> str:
    try:
        output = subprocess.run(['/usr/bin/git', 'rev-parse', 'HEAD'], cwd=Path(__file__).resolve().parents[1],
                                env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'}, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, check=True).stdout
        revision = output.decode('ascii').strip()
    except (OSError, UnicodeError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise BackupError('Could not verify the local source revision.') from None
    if not SHA40.fullmatch(revision):
        raise BackupError('Local source revision is invalid.')
    return revision


def _table_digest(connection: Connection, table: str, snapshot: str | None = None) -> dict[str, object]:
    if table not in TABLES or snapshot is not None and not SNAPSHOT.fullmatch(snapshot):
        raise BackupError('Invalid table or snapshot selection.')
    sql = ("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; "
           + (f"SET TRANSACTION SNAPSHOT '{snapshot}'; " if snapshot else '')
           + "SET LOCAL TIME ZONE 'UTC'; "
           + f"COPY (SELECT encode(convert_to(to_jsonb(t)::text, 'UTF8'), 'hex') FROM {SCHEMA}.{table} t ORDER BY to_jsonb(t)::text COLLATE \"C\") TO STDOUT; COMMIT;")
    output = run_tool('psql', ['-X', '-q', '-A', '-t', '-v', 'ON_ERROR_STOP=1', '-c', sql], connection, 120)
    digest = hashlib.sha256()
    count = 0
    for line in output.splitlines():
        try:
            row = bytes.fromhex(line.decode('ascii'))
        except (UnicodeError, ValueError):
            raise BackupError('Canonical row extraction failed.') from None
        digest.update(len(row).to_bytes(8, 'big'))
        digest.update(row)
        count += 1
    return {'count': count, 'sha256': digest.hexdigest()}


def table_digests(connection: Connection, snapshot: str | None = None) -> dict[str, dict[str, object]]:
    return {table: _table_digest(connection, table, snapshot) for table in TABLES}


@contextmanager
def snapshot_holder(connection: Connection):
    command = [_binary('psql'), '--no-password', '--host', connection.host, '--port', str(connection.port), '--username', connection.user,
               '--dbname', connection.database, '-X', '-q', '-A', '-t', '-v', 'ON_ERROR_STOP=1']
    with pg_environment(connection) as environment:
        try:
            process = subprocess.Popen(command, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.DEVNULL, bufsize=0)
        except OSError:
            raise BackupError('Could not start the snapshot holder.') from None
        try:
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write(b'BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\nSELECT pg_export_snapshot();\n')
            process.stdin.flush()
            deadline = time.monotonic() + 15
            line = bytearray()
            while b'\n' not in line:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([process.stdout], [], [], remaining)[0]:
                    raise BackupError('Snapshot export timed out.')
                chunk = os.read(process.stdout.fileno(), 128)
                if not chunk or len(line) + len(chunk) > 256:
                    raise BackupError('Snapshot export failed.')
                line.extend(chunk)
            snapshot = bytes(line).split(b'\n', 1)[0].decode('ascii', errors='replace').strip()
            if not SNAPSHOT.fullmatch(snapshot) or process.poll() is not None:
                raise BackupError('Snapshot export failed.')
            yield snapshot
        finally:
            if process.stdin and not process.stdin.closed:
                try:
                    process.stdin.write(b'ROLLBACK;\n\\q\n')
                    process.stdin.flush()
                except OSError:
                    pass
                process.stdin.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def archive_list(path: Path) -> None:
    output = _plain_tool('pg_restore', ['--list', str(path)], 15)
    entries = [line for line in output.decode('utf-8', errors='replace').splitlines() if re.match(r'^\d+;', line)]
    tables, data_tables = [], []
    schemas = 0
    for line in entries:
        item = re.match(r'^\d+;\s+\d+\s+\d+\s+(.+)$', line)
        if not item:
            raise BackupError('Archive TOC entry is invalid.')
        detail = item.group(1)
        if re.fullmatch(r'SCHEMA - cs_plan_actions \S+', detail):
            schemas += 1
            continue
        kind = re.fullmatch(r'(TABLE DATA|TABLE|INDEX|CONSTRAINT|FK CONSTRAINT|DEFAULT) cs_plan_actions ([a-z_][a-z_0-9]*) (?:\S+ ?)+', detail)
        if not kind:
            raise BackupError('Archive contains unsupported or foreign objects.')
        if kind.group(1) == 'TABLE':
            tables.append(kind.group(2))
        if kind.group(1) == 'TABLE DATA':
            data_tables.append(kind.group(2))
    if schemas != 1 or sorted(tables) != sorted(TABLES) or sorted(data_tables) != sorted(TABLES):
        raise BackupError('Archive does not contain exactly the accepted schema and six tables.')


def write_private(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def publish_bundle(stage: Path, destination: Path) -> None:
    if sys.platform != 'darwin':
        raise BackupError('Atomic no-overwrite publication requires macOS renamex_np.')
    if destination.exists() or destination.is_symlink():
        raise BackupError('Output directory already exists; never overwrite a bundle.')
    library = ctypes.CDLL(None, use_errno=True)
    library.renamex_np.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
    library.renamex_np.restype = ctypes.c_int
    if library.renamex_np(os.fsencode(stage), os.fsencode(destination), 0x00000004) != 0:
        code = ctypes.get_errno()
        if code in (errno.EEXIST, errno.ENOTEMPTY):
            raise BackupError('Output directory already exists; never overwrite a bundle.')
        raise BackupError('Could not atomically publish the private bundle.')


def check_hash(value: str, pattern: re.Pattern[str], label: str) -> None:
    if not pattern.fullmatch(value):
        raise BackupError(f'{label} must be a lowercase hexadecimal digest.')


def validate_manifest(bundle: str | Path, expected_sha: str, expected_revision: str,
                      versions: dict[str, str], migration_sha: str) -> dict[str, object]:
    bundle = Path(bundle)
    private_dir(bundle.parent)
    private_dir(bundle)
    manifest_file, archive = bundle / 'manifest.json', bundle / 'archive.dump'
    private_file(manifest_file, 65536)
    private_file(archive)
    check_hash(expected_sha, SHA64, 'Trusted manifest SHA256')
    check_hash(expected_revision, SHA40, 'Expected source revision')
    if _sha_file(manifest_file) != expected_sha:
        raise BackupError('Manifest does not match the externally trusted SHA256.')
    try:
        data = json.loads(manifest_file.read_bytes())
    except (OSError, ValueError):
        raise BackupError('Manifest is unreadable.') from None
    keys = {'format_version', 'schema', 'schema_version', 'source_revision', 'migration_sha256', 'archive_sha256',
            'source_identity', 'server_version', 'tools', 'tables'}
    if not isinstance(data, dict) or set(data) != keys or type(data['format_version']) is not int or data['format_version'] != 1 or data['schema'] != SCHEMA or type(data['schema_version']) is not int or data['schema_version'] != 1:
        raise BackupError('Manifest format or schema is unsupported.')
    if data['source_revision'] != expected_revision or data['migration_sha256'] != migration_sha or data['tools'] != versions:
        raise BackupError('Manifest revision, migration or PostgreSQL tool identity differs.')
    source = data['source_identity']
    if not isinstance(source, dict) or set(source) != {'system_identifier', 'database_oid', 'database'}:
        raise BackupError('Manifest source identity is invalid.')
    if not isinstance(source['system_identifier'], str) or not source['system_identifier'].isdigit() or not isinstance(source['database_oid'], str) or not source['database_oid'].isdigit() or not isinstance(source['database'], str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]{0,62}', source['database']):
        raise BackupError('Manifest source identity is invalid.')
    if not isinstance(data['server_version'], str) or not PG17_VERSION.fullmatch(data['server_version']):
        raise BackupError('Manifest PostgreSQL server version is unsupported.')
    if not isinstance(data['tables'], dict) or set(data['tables']) != set(TABLES):
        raise BackupError('Manifest table set is invalid.')
    for table in TABLES:
        entry = data['tables'][table]
        if not isinstance(entry, dict) or set(entry) != {'count', 'sha256'} or type(entry['count']) is not int or entry['count'] < 0 or not isinstance(entry['sha256'], str) or not SHA64.fullmatch(entry['sha256']):
            raise BackupError('Manifest table count or digest is invalid.')
    if not isinstance(data['archive_sha256'], str) or not SHA64.fullmatch(data['archive_sha256']) or _sha_file(archive) != data['archive_sha256']:
        raise BackupError('Archive SHA256 does not match the trusted manifest.')
    return data


def restore_archive(archive: str | Path, connection: Connection) -> None:
    run_tool('pg_restore', ['--single-transaction', '--exit-on-error', '--no-owner', '--no-acl', str(archive)], connection, 120)


def export_bundle(source_file: str, output_dir: str, revision: str) -> dict[str, object]:
    check_hash(revision, SHA40, 'Source revision')
    if _git_revision() != revision:
        raise BackupError('Supplied source revision differs from this checkout HEAD.')
    destination = Path(output_dir)
    private_dir(destination.parent)
    if destination.exists() or destination.is_symlink():
        raise BackupError('Output directory already exists; never overwrite a bundle.')
    source = load_connection(source_file)
    versions = tool_versions()
    server = _server_version(source)
    if server != versions['pg_dump']:
        raise BackupError('Source server and PostgreSQL 17 tools must have the same version.')
    source_id = identity(source)
    _source_schema(source)
    stage = Path(tempfile.mkdtemp(prefix='.local-state-stage-', dir=destination.parent))
    try:
        stage.chmod(0o700)
        archive = stage / 'archive.dump'
        write_private(archive, b'')
        with snapshot_holder(source) as snapshot:
            run_tool('pg_dump', ['--format=custom', '--schema=' + SCHEMA, '--no-owner', '--no-acl', '--snapshot=' + snapshot,
                                 '--file=' + str(archive)], source, 120)
            tables = table_digests(source, snapshot)
        private_file(archive)
        archive_list(archive)
        manifest = {'format_version': 1, 'schema': SCHEMA, 'schema_version': 1, 'source_revision': revision,
                    'migration_sha256': _migration_sha(), 'archive_sha256': _sha_file(archive),
                    'source_identity': source_id, 'server_version': server, 'tools': versions, 'tables': tables}
        manifest_bytes = (json.dumps(manifest, sort_keys=True, separators=(',', ':')) + '\n').encode('utf-8')
        write_private(stage / 'manifest.json', manifest_bytes)
        descriptor = os.open(stage, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        publish_bundle(stage, destination)
        return {'status': 'exported', 'manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest(),
                'archive_sha256': manifest['archive_sha256'], 'source_revision': revision, 'tables': tables}
    except Exception:
        if stage.exists():
            shutil.rmtree(stage)
        raise


def restore_bundle(source_file: str, target_file: str, bundle_dir: str, expected_sha: str, expected_revision: str) -> dict[str, object]:
    source, target = load_connection(source_file), load_connection(target_file)
    versions = tool_versions()
    manifest = validate_manifest(bundle_dir, expected_sha, expected_revision, versions, _migration_sha())
    archive = Path(bundle_dir) / 'archive.dump'
    archive_list(archive)
    actual_source, actual_target = identity(source), identity(target)
    if actual_source != manifest['source_identity']:
        raise BackupError('Live source database differs from the trusted export source.')
    assert_distinct(actual_source, actual_target)
    if _server_version(source) != manifest['server_version'] or _server_version(target) != manifest['server_version']:
        raise BackupError('Source or target PostgreSQL server version differs from the export.')
    verify_empty(target)
    restore_archive(archive, target)
    try:
        _source_schema(target)
        actual_tables = table_digests(target)
        if actual_tables != manifest['tables']:
            raise BackupError('Restored table counts or row digests differ from the trusted export.')
    except Exception:
        raise BackupError('Restore target is quarantined; verify before any configuration switch.') from None
    return {'status': 'restored_verified', 'source_revision': expected_revision, 'manifest_sha256': expected_sha,
            'source_identity': actual_source, 'target_identity': actual_target, 'tables': actual_tables}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Private loopback PostgreSQL 17 saved-state export and empty-target restore')
    commands = parser.add_subparsers(dest='command', required=True)
    export = commands.add_parser('export')
    export.add_argument('--source-connection-file', required=True)
    export.add_argument('--output-dir', required=True)
    export.add_argument('--source-revision', required=True)
    restore = commands.add_parser('restore')
    restore.add_argument('--source-connection-file', required=True)
    restore.add_argument('--target-connection-file', required=True)
    restore.add_argument('--bundle-dir', required=True)
    restore.add_argument('--expected-manifest-sha256', required=True)
    restore.add_argument('--expected-source-revision', required=True)
    restore.add_argument('--local-test-restore', action='store_true', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'export':
            result = export_bundle(args.source_connection_file, args.output_dir, args.source_revision)
        else:
            result = restore_bundle(args.source_connection_file, args.target_connection_file, args.bundle_dir,
                                    args.expected_manifest_sha256, args.expected_source_revision)
        print(json.dumps(result, sort_keys=True, separators=(',', ':')))
        return 0
    except BackupError as error:
        print(json.dumps({'status': 'failed', 'error': str(error)}, sort_keys=True), file=sys.stderr)
        return 1
    except Exception:
        print(json.dumps({'status': 'failed', 'error': 'Operator action failed; inspect private task state before retrying.'}, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
