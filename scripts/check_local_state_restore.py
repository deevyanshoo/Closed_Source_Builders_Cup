#!/usr/bin/env python3
"""Rehearse saved-state recovery using ONLY new task-owned local resources.

Build .local-tools/state-recovery-gateway from this checkout first. Requires the
existing local Python runtime and PostgreSQL17 tools; no provider credentials.
Raw records, logs, archives and credentials remain inside a new private run-dir.
The optional receipt contains only sanitized counts/hashes/check labels.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
PG = Path('/opt/homebrew/opt/postgresql@17/bin')
PYTHON = ROOT.parent / 'Closed_Source_Builders_Cup/procurement-engine/.venv/bin/python'
TABLES = ('schema_migrations', 'plans', 'plan_revisions', 'approvals', 'action_receipts', 'audit_events')
EXACT = '42123456.123456789012345678'
# Hand-constructed gateway precision fixture, not a forecast/model result.
LEGACY = ('{"id":"local-precision-fixture","material":"copper","action":"SPLIT BUY",'
 '"summary":"Synthetic precision fixture","current_inr_per_kg":800,"latest_data_month":"Oct 2026",'
 '"schedule":[{"month":0,"calendar_month":"Oct 2026","kg":30000,"percent":60,"expected_inr_per_kg":846.12},'
 '{"month":2,"calendar_month":"Dec 2026","kg":20000,"percent":40,"expected_inr_per_kg":900}],'
 '"comparison":[{"name":"Recommended","schedule":"Now and later","expected_cost_inr":' + EXACT + ','
 '"p90_cost_inr":45000000,"stockout_penalty_inr":0}],"warnings":["Synthetic precision fixture"],'
 '"provenance":{"prices":{"used":"synthetic","status":"ok","fetched_at":"2026-10-01T00:00:00Z"},'
 '"fx":{"used":"synthetic","status":"ok"}}}').encode()

class RehearsalError(Exception):
    pass

def require(condition, message):
    if not condition:
        raise RehearsalError(message)

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def private_write(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as file:
        file.write(data)

def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]

def base_env():
    # Do not inherit provider/libpq/gateway identity or service routing settings.
    return {k: v for k, v in os.environ.items()
            if k in {'PATH', 'HOME', 'USER', 'TMPDIR', 'LANG', 'LC_ALL'}}

class Fixture(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != '/recommend':
            self.send_error(404)
            return
        self.rfile.read(int(self.headers.get('Content-Length', '0')))
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(LEGACY)))
        self.end_headers()
        self.wfile.write(LEGACY)
    def log_message(self, *args):
        pass

class Rehearsal:
    def __init__(self, run_dir, port):
        self.run_dir = run_dir
        self.port = port
        self.processes = []
        self.logs = []
        self.cluster_started = False
        self.cluster_attempted = False
        self.owns_run = False
        self.fixture = None
        self.gateway = None
        self.checks = []
        self.env = base_env()
        self.password = secrets.token_urlsafe(32)
        self.gateway_port = free_port()
        self.python_port = free_port()
        self.identities = [
            {'token': secrets.token_urlsafe(32), 'principal_id': 'recovery-editor', 'workspace_id': 'local-workspace', 'role': 'editor'},
            {'token': secrets.token_urlsafe(32), 'principal_id': 'recovery-viewer', 'workspace_id': 'local-workspace', 'role': 'viewer'},
            {'token': secrets.token_urlsafe(32), 'principal_id': 'recovery-other', 'workspace_id': 'other-workspace', 'role': 'editor'}]

    def command(self, argv, env=None, input=None, timeout=120, label='local command'):
        try:
            result = subprocess.run([str(x) for x in argv], env=env or self.env, cwd=ROOT,
                                    input=input, text=True, capture_output=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            raise RehearsalError(label + ' failed or timed out') from None
        require(result.returncode == 0, label + ' failed; private task artifacts retained')
        return result.stdout

    def pg_env(self, database):
        return dict(self.env, PGHOST='127.0.0.1', PGPORT=str(self.port), PGUSER='recovery_operator',
                    PGDATABASE=database, PGPASSFILE=str(self.run_dir / 'pgpass'), PGCONNECT_TIMEOUT='5', PGTZ='UTC')

    def sql(self, database, query):
        return self.command([PG / 'psql', '-X', '-w', '-At', '-v', 'ON_ERROR_STOP=1'],
                            env=self.pg_env(database), input=query, timeout=15, label='task SQL')

    def rows(self, database):
        out = {}
        for table in TABLES:
            query = "SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text), '[]'::jsonb)::text FROM cs_plan_actions." + table + ' t;'
            out[table] = json.loads(self.sql(database, query))
        return out

    def start_process(self, argv, env, name):
        log = open(self.run_dir / (name + '.log'), 'x')
        os.chmod(log.name, 0o600)
        self.logs.append(log)
        process = subprocess.Popen([str(x) for x in argv], cwd=ROOT, env=env, stdout=log, stderr=log)
        self.processes.append(process)
        return process

    def stop(self, process):
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    def wait_http(self, base, route, process, timeout=120):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            require(process.poll() is None, 'task service exited; inspect private task log')
            try:
                with urlopen(base + route, timeout=2) as response:
                    if response.status == 200:
                        return json.load(response)
            except (URLError, TimeoutError, OSError):
                pass
            time.sleep(.2)
        raise RehearsalError('task service readiness timed out')

    def start_gateway(self, database, upstream):
        self.stop(self.gateway)
        env = dict(self.env, GATEWAY_ADDR='127.0.0.1:' + str(self.gateway_port), GATEWAY_MODE='local',
                   DATABASE_URL_FILE=str(self.run_dir / (database + '.url')),
                   DEV_IDENTITIES_FILE=str(self.run_dir / 'identities.json'), DEV_ALLOWED_ORIGIN='http://127.0.0.1:5173',
                   PYTHON_SERVICE_URL=upstream, CONTRACT_EXAMPLES_DIR=str(ROOT / 'contracts/examples'))
        self.gateway = self.start_process([ROOT / '.local-tools/state-recovery-gateway'], env, 'gateway-' + secrets.token_hex(4))
        self.url = 'http://127.0.0.1:' + str(self.gateway_port)
        self.wait_http(self.url, '/ready', self.gateway, 30)

    def http(self, method, path, body=None, role=0, status=200):
        headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.identities[role]['token']}
        request = Request(self.url + path, method=method, headers=headers,
                          data=None if body is None else json.dumps(body).encode())
        try:
            response = urlopen(request, timeout=30)
        except HTTPError as error:
            response = error
        with response:
            require(response.status == status, 'unexpected HTTP status for ' + method + ' ' + path.split('/')[1])
            return json.load(response)

    def backup(self, arguments, success=True, expected_error=None):
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/local_state_backup.py'), *map(str, arguments)],
                                cwd=ROOT, env=self.env, text=True, capture_output=True, timeout=150)
        private_write(self.run_dir / ('backup-' + secrets.token_hex(4) + '.log'), result.stdout + result.stderr)
        require((result.returncode == 0) == success, 'backup tool outcome differed from expected gate; inspect private backup log')
        require(self.password not in result.stdout + result.stderr, 'backup credential redaction failed')
        require('postgresql://' not in result.stdout + result.stderr, 'backup URL redaction failed')
        require(all(identity['token'] not in result.stdout + result.stderr for identity in self.identities), 'identity redaction failed')
        if success:
            try:
                payload = json.loads(result.stdout)
                require(payload.get('status') == ('exported' if arguments[0] == 'export' else 'restored_verified'), 'backup tool did not verify requested operation')
                return payload
            except (ValueError, TypeError):
                raise RehearsalError('backup tool did not produce a verified JSON result') from None
        try:
            failure = json.loads(result.stderr)
        except ValueError:
            raise RehearsalError('backup rejection was not structured') from None
        require(failure.get('status') == 'failed' and (expected_error is None or expected_error in failure.get('error', '')), 'backup rejection reason differed from expected gate')
        return {'rejected': True}

    def setup(self):
        require(not self.run_dir.exists(), 'run-dir must be new; never reuse an existing cluster')
        require(self.run_dir.parent == ROOT / '.local-tools' and self.run_dir.name.startswith('state-recovery-'),
                'run-dir must be a task-owned .local-tools/state-recovery-* directory')
        require(not self.run_dir.parent.is_symlink(), 'unsafe local-tools directory')
        with socket.socket() as sock:
            try:
                sock.bind(('127.0.0.1', self.port))
            except OSError:
                raise RehearsalError('requested task database port is occupied') from None
        require(self.port != 55416 and 1024 < self.port < 65536, 'invalid task port')
        self.run_dir.mkdir(mode=0o700)
        self.owns_run = True
        private_write(self.run_dir / 'password', self.password + '\n')
        private_write(self.run_dir / 'pgpass', '127.0.0.1:' + str(self.port) + ':*:recovery_operator:' + self.password + '\n')
        private_write(self.run_dir / 'identities.json', json.dumps({'identities': self.identities}))
        self.command([PG / 'initdb', '-D', self.run_dir / 'cluster', '-U', 'recovery_operator',
                      '--auth=scram-sha-256', '--pwfile', self.run_dir / 'password'], label='new task initdb')
        self.cluster_attempted = True
        self.command([PG / 'pg_ctl', '-D', self.run_dir / 'cluster', '-l', self.run_dir / 'postgres.log',
                      '-o', '-h 127.0.0.1 -p ' + str(self.port) + ' -c unix_socket_directories=', '-w', 'start'], label='new task cluster start')
        self.cluster_started = True
        os.chmod(self.run_dir / 'postgres.log', 0o600)
        for database in ('recovery_source', 'recovery_destination', 'recovery_negative', 'recovery_custom_schema', 'recovery_system_object'):
            self.sql('postgres', 'CREATE DATABASE ' + database + ';')
            private_write(self.run_dir / (database + '.url'), 'postgresql://recovery_operator:' + quote(self.password, safe='') + '@127.0.0.1:' + str(self.port) + '/' + database + '\n')
        self.sql('recovery_custom_schema', 'CREATE SCHEMA pgfoo;')
        self.sql('recovery_system_object', 'CREATE TABLE information_schema.recovery_marker (id integer); INSERT INTO information_schema.recovery_marker VALUES (11);')
        private_write(self.run_dir / 'alias.url', 'postgresql://recovery_operator:' + quote(self.password, safe='') + '@localhost:' + str(self.port) + '/recovery_source\n')
        migration = ROOT / 'services/gateway/migrations/001_plans_actions.up.sql'
        self.sql('recovery_source', migration.read_text())
        self.sql('recovery_negative', 'CREATE TABLE retained_marker (id integer PRIMARY KEY); INSERT INTO retained_marker VALUES (7);')

    def seed(self):
        python_env = dict(self.env, OFFLINE='1', GEMINI_API_KEY='', PROCURE_SEED='42',
                          PROCURE_DATA_DIR=str(self.run_dir / 'python-data'), PROCURE_DB_PATH=str(self.run_dir / 'python-data/app.duckdb'),
                          PROCURE_CONFIG_DIR=str(ROOT / 'procurement-engine/config'), PYTHONPATH=str(ROOT / 'procurement-engine/src'),
                          PYTHONDONTWRITEBYTECODE='1', OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
        python = self.start_process([PYTHON, '-m', 'uvicorn', 'procure.api:app', '--host', '127.0.0.1', '--port', str(self.python_port)], python_env, 'python-offline')
        upstream = 'http://127.0.0.1:' + str(self.python_port)
        health = self.wait_http(upstream, '/health', python)
        require(health['offline'] is True and health['gemini_enabled'] is False, 'Python must be offline with model provider disabled')
        request = json.loads((ROOT / 'contracts/examples/request.json').read_text())
        create = {'contract_version': '0.2.0', 'planning_request': request}
        self.start_gateway('recovery_source', upstream)
        offline = self.http('POST', '/v2/workspaces/local-workspace/plans', create, status=201)['plan']
        require(offline['snapshot']['status'] == 'ready', 'offline Python connected save must be ready')
        self.offline_path = '/v2/plans/' + offline['saved_plan_id']
        self.offline_http = self.http('GET', self.offline_path)
        self.checks.append('actual_OFFLINE_Python_to_gateway_saved_plan')
        self.stop(python)
        self.fixture = ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
        threading.Thread(target=self.fixture.serve_forever, daemon=True).start()
        self.fixture_url = 'http://127.0.0.1:' + str(self.fixture.server_port)
        self.start_gateway('recovery_source', self.fixture_url)
        saved = self.http('POST', '/v2/workspaces/local-workspace/plans', create, status=201)['plan']
        require(saved['snapshot']['plan']['comparisons'][0]['expected_cost']['amount'] == EXACT, 'exact precision fixture changed before persistence')
        self.path = '/v2/plans/' + saved['saved_plan_id']
        arguments = {key: request[key] for key in ('material', 'quantity', 'quote_currency', 'settlement_currency', 'reporting_currency')}
        arguments.update(buyer_id=request['buyer']['id'], destination_country=request['buyer']['destination_country'])
        confirm = {'contract_version': '0.2.0', 'expected_revision': 1,
                   'expected_plan_version': saved['snapshot']['plan']['plan_version'], 'action_kind': 'prepare_rfq', 'arguments': arguments}
        approval = self.http('POST', self.path + '/approvals', confirm, status=201)['approval']
        self.execute = dict(confirm, workspace_id='local-workspace', saved_plan_id=saved['saved_plan_id'],
                            approval_id=approval['approval_id'], idempotency_key='recovery-executed-key')
        self.receipt = self.http('POST', '/v2/actions/prepare_rfq', self.execute)
        require(self.receipt['receipt']['supplier_communication'] is False, 'receipt must be draft only')
        unused = self.http('POST', self.path + '/approvals', confirm, status=201)['approval']
        self.stale = dict(self.execute, approval_id=unused['approval_id'], idempotency_key='recovery-stale-key')
        revised = self.http('PUT', self.path, dict(create, expected_revision=1))['plan']
        current_confirm = dict(confirm, expected_revision=2, expected_plan_version=revised['snapshot']['plan']['plan_version'])
        current_approval = self.http('POST', self.path + '/approvals', current_confirm, status=201)['approval']
        self.current_execute = dict(current_confirm, workspace_id='local-workspace', saved_plan_id=saved['saved_plan_id'],
                                    approval_id=current_approval['approval_id'], idempotency_key='recovery-current-executed-key')
        self.http('POST', '/v2/actions/prepare_rfq', self.current_execute)
        self.before_http = self.http_state()
        self.before_rows = self.rows('recovery_source')
        require({key: len(value) for key, value in self.before_rows.items()} ==
                dict(schema_migrations=1, plans=2, plan_revisions=3, approvals=3, action_receipts=2, audit_events=8), 'unexpected seeded row counts')
        require(sorted(row['status'] for row in self.before_rows['approvals']) == ['confirmed', 'executed', 'executed'], 'approval statuses not seeded')
        self.checks.append('real_gateway_save_revision_confirmed_executed_receipt_audit_precision_fixture')
        self.stop(self.gateway)  # quiesce task writers before export

    def http_state(self):
        return {suffix: self.http('GET', self.path + suffix) for suffix in ('', '/approvals', '/receipts', '/audit')}

    def recover(self, revision):
        self.negative_catalog_before = self.sql('recovery_negative', "SELECT jsonb_agg(jsonb_build_array(n.nspname,c.relname,c.relkind) ORDER BY n.nspname,c.relname)::text FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public';")
        bundle = self.run_dir / 'bundle'
        self.backup(['export', '--source-connection-file', self.run_dir / 'recovery_source.url',
                     '--output-dir', bundle, '--source-revision', revision])
        manifest = json.loads((bundle / 'manifest.json').read_text())
        manifest_hash = hashlib.sha256((bundle / 'manifest.json').read_bytes()).hexdigest()
        restore = ['restore', '--source-connection-file', self.run_dir / 'recovery_source.url',
                   '--target-connection-file', self.run_dir / 'recovery_destination.url', '--bundle-dir', bundle,
                   '--expected-manifest-sha256', manifest_hash, '--expected-source-revision', revision, '--local-test-restore']
        alias = list(restore)
        alias[alias.index('--target-connection-file') + 1] = self.run_dir / 'alias.url'
        self.backup(alias, False, 'same actual database')
        require(self.rows('recovery_source') == self.before_rows, 'alias rejection changed source')
        populated = list(restore)
        populated[populated.index('--target-connection-file') + 1] = self.run_dir / 'recovery_negative.url'
        self.backup(populated, False, 'not an empty local database')
        require(self.sql('recovery_negative', 'SELECT id FROM retained_marker;').strip() == '7', 'populated rejection changed marker')
        require(self.sql('recovery_negative', "SELECT jsonb_agg(jsonb_build_array(n.nspname,c.relname,c.relkind) ORDER BY n.nspname,c.relname)::text FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public';") == self.negative_catalog_before, 'populated rejection changed object inventory')
        require(self.sql('recovery_negative', "SELECT count(*) FROM pg_namespace WHERE nspname='cs_plan_actions';").strip() == '0', 'populated rejection added schema')
        custom = list(restore)
        custom[custom.index('--target-connection-file') + 1] = self.run_dir / 'recovery_custom_schema.url'
        require(self.sql('recovery_custom_schema', "SELECT count(*) FROM pg_namespace WHERE nspname NOT IN ('public','information_schema') AND nspname NOT LIKE 'pg_%';").strip() == '0', 'old wildcard regression setup differed')
        self.backup(custom, False, 'not an empty local database')
        require(self.sql('recovery_custom_schema', "SELECT count(*) FROM pg_namespace WHERE nspname='pgfoo';").strip() == '1', 'custom-schema rejection changed target')
        require(self.sql('recovery_custom_schema', "SELECT count(*) FROM pg_namespace WHERE nspname='cs_plan_actions';").strip() == '0', 'custom-schema rejection added restore schema')
        system_object = list(restore)
        system_object[system_object.index('--target-connection-file') + 1] = self.run_dir / 'recovery_system_object.url'
        self.backup(system_object, False, 'not an empty local database')
        require(self.sql('recovery_system_object', 'SELECT id FROM information_schema.recovery_marker;').strip() == '11', 'system-schema object rejection changed marker')
        require(self.sql('recovery_system_object', "SELECT count(*) FROM pg_namespace WHERE nspname='cs_plan_actions';").strip() == '0', 'system-schema object rejection added restore schema')
        archive = bundle / 'archive.dump'
        original = archive.read_bytes()
        with archive.open('wb') as file:
            file.write(original + b'corrupt')
        self.backup(restore, False, 'Archive SHA256')
        require(self.sql('recovery_destination', "SELECT count(*) FROM pg_namespace WHERE nspname='cs_plan_actions';").strip() == '0', 'corruption rejection wrote target')
        with archive.open('wb') as file:
            file.write(original)
        self.backup(restore)
        after = self.rows('recovery_destination')
        require(after == self.before_rows, 'full restored rows differ')
        require(self.rows('recovery_source') == self.before_rows, 'restore changed source')
        self.checks.extend(['same_database_alias_rejected_unchanged', 'populated_target_rejected_marker_unchanged',
                            'custom_pgfoo_schema_real_catalog_regression_rejected_unchanged', 'non_system_object_in_information_schema_rejected_unchanged', 'corrupt_archive_rejected_empty_target', 'all_six_tables_exact_rows_restored'])
        self.command([PG / 'pg_ctl', '-D', self.run_dir / 'cluster', '-w', 'stop', '-m', 'fast'], label='task cluster stop')
        self.cluster_started = False
        self.command([PG / 'pg_ctl', '-D', self.run_dir / 'cluster', '-l', self.run_dir / 'postgres.log',
                      '-o', '-h 127.0.0.1 -p ' + str(self.port) + ' -c unix_socket_directories=', '-w', 'start'], label='task cluster restart')
        self.cluster_started = True
        self.start_gateway('recovery_destination', self.fixture_url)
        require(self.http('GET', self.offline_path) == self.offline_http, 'offline Python saved snapshot changed after restore')
        require(self.http_state() == self.before_http, 'HTTP snapshots/revisions/approvals/receipts/audit changed after restart')
        with ThreadPoolExecutor(max_workers=8) as pool:
            receipts = list(pool.map(lambda _: self.http('POST', '/v2/actions/prepare_rfq', self.execute), range(8)))
        require(all(item == self.receipt for item in receipts), 'concurrent replay changed original receipt')
        changed = copy.deepcopy(self.execute)
        changed['arguments']['quantity']['amount'] = '50001'
        require(self.http('POST', '/v2/actions/prepare_rfq', changed, status=409)['error']['code'] == 'conflict', 'changed-content conflict semantics')
        changed_key = dict(self.current_execute, idempotency_key='new-key-for-consumed-approval')
        changed_key_error = self.http('POST', '/v2/actions/prepare_rfq', changed_key, status=409)['error']
        require(changed_key_error['code'] == 'conflict' and changed_key_error['message'] == 'Approval is stale or already consumed', 'changed-key consumed approval semantics')
        stale_error = self.http('POST', '/v2/actions/prepare_rfq', self.stale, status=409)['error']
        require(stale_error['code'] == 'conflict' and stale_error['message'] == 'Plan revision is stale', 'stale error semantics')
        for role in (1, 2):
            require(self.http('POST', '/v2/actions/prepare_rfq', self.execute, role=role, status=403)['error']['code'] == 'forbidden', 'permission error semantics')
        require(self.http('GET', self.path, role=2, status=404)['error']['code'] == 'not_found', 'foreign plan error semantics')
        require(self.http_state() == self.before_http, 'replays/rejections changed HTTP history')
        require(self.rows('recovery_destination') == self.before_rows, 'replays/rejections added database effects')
        require(self.rows('recovery_source') == self.before_rows, 'post-switch source changed')
        self.checks.extend(['task_database_and_gateway_restart_http_equal', 'eight_concurrent_replays_original_receipt_no_new_effect',
                            'changed_content_key_stale_foreign_viewer_rejected', 'original_source_retained_unchanged'])
        return {'status': 'verified_local_only', 'source_revision': revision,
                'postgres_version': self.command([PG / 'pg_dump', '--version']).strip(),
                'manifest_sha256': manifest_hash, 'archive_sha256': hashlib.sha256(original).hexdigest(),
                'source_files_sha256': {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in (ROOT / 'scripts/local_state_backup.py', ROOT / 'scripts/check_local_state_restore.py')},
                'gateway_binary_sha256': hashlib.sha256((ROOT / '.local-tools/state-recovery-gateway').read_bytes()).hexdigest(),
                'migration_sha256': hashlib.sha256((ROOT / 'services/gateway/migrations/001_plans_actions.up.sql').read_bytes()).hexdigest(),
                'table_counts': {key: len(value) for key, value in self.before_rows.items()},
                'table_row_sha256': {key: digest(value) for key, value in self.before_rows.items()},
                'checks': self.checks, 'ports': {'postgres': self.port, 'gateway': self.gateway_port, 'python': self.python_port},
                'upstream_evidence': ['actual current Python OFFLINE synthetic/sample data, provider disabled',
                                      'hand-constructed legacy precision HTTP fixture; not numerical/model proof'],
                'limits': ['local PostgreSQL17 only; no Cloud SQL/PostgreSQL18 proof', 'quiesced task writers; no online switch proof',
                           'checksum integrity requires trusted artifact/hash; no authenticity proof', 'no provider, SQL activation, supplier communication']}

    def cleanup(self):
        if not self.owns_run:
            return
        errors = []
        for process in self.processes:
            try:
                self.stop(process)
            except (OSError, subprocess.TimeoutExpired):
                errors.append('task child process cleanup failed')
        if self.fixture:
            self.fixture.shutdown()
            self.fixture.server_close()
        if self.cluster_started or (self.cluster_attempted and (self.run_dir / 'cluster/postmaster.pid').exists()):
            try:
                self.command([PG / 'pg_ctl', '-D', self.run_dir / 'cluster', '-w', 'stop', '-m', 'fast'], label='task cluster cleanup')
                self.cluster_started = False
            except RehearsalError:
                errors.append('task cluster cleanup failed; cluster retained')
        for log in self.logs:
            log.close()
        for name in ('password', 'pgpass', 'identities.json', 'alias.url', 'recovery_source.url', 'recovery_destination.url', 'recovery_negative.url', 'recovery_custom_schema.url', 'recovery_system_object.url'):
            (self.run_dir / name).unlink(missing_ok=True)
        require(not errors, '; '.join(errors))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--postgres-port', type=int, default=55428)
    parser.add_argument('--receipt', type=Path)
    args = parser.parse_args()
    rehearsal = Rehearsal(args.run_dir.absolute(), args.postgres_port)
    started = time.monotonic()
    try:
        revision = rehearsal.command(['git', 'rev-parse', 'HEAD']).strip()
        rehearsal.setup()
        rehearsal.seed()
        receipt = rehearsal.recover(revision)
    except (Exception, KeyboardInterrupt) as error:
        # Never echo tool output or application records. Raw diagnostics stay private.
        message = str(error) if isinstance(error, RehearsalError) else 'local rehearsal failed; inspect private task artifacts'
        receipt = {'status': 'failed', 'error': message, 'error_type': type(error).__name__, 'checks': rehearsal.checks}
    try:
        rehearsal.cleanup()
    except Exception:
        receipt = {'status': 'failed', 'error': 'task cleanup failed; inspect private task processes'}
    receipt['elapsed_seconds'] = round(time.monotonic() - started, 2)
    if args.receipt:
        private_write(args.receipt, json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt['status'] == 'verified_local_only' else 1

if __name__ == '__main__':
    sys.exit(main())
