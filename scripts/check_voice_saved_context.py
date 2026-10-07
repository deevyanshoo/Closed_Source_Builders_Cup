#!/usr/bin/env python3
"""Opt-in loopback synthetic acceptance harness; no provider or browser automation.

verify: exercise a running task-owned gateway and compare all four results to the
public Python authority. proxy: observe mounted browser requests and verify ready
results independently; a private control file can deliberately delay a ready
response. Credentials/raw payloads stay in the supplied mode-0700 private dir.
"""
import argparse
import base64
import re
import hashlib
import http.client
import http.server
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from urllib.parse import urlparse, parse_qs


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


class Harness:
    def __init__(self, args):
        self.args = args
        self.root = Path(__file__).resolve().parents[1]
        self.private = Path(args.private_dir).resolve()
        if not self.private.is_dir() or self.private.stat().st_mode & 0o077:
            raise ValueError('Use a task-owned mode-0700 private directory')
        endpoint = urlparse(args.gateway)
        if endpoint.scheme != 'http' or endpoint.hostname != '127.0.0.1' or not endpoint.port or endpoint.path:
            raise ValueError('Gateway must be explicit loopback HTTP')
        self.port = endpoint.port
        self.identities = json.loads(Path(args.identities_file).read_text())['identities']
        self.lock = threading.Lock()
        self.sessions, self.turns, self.checked = {}, {}, set()

    def event(self, row):
        with self.lock:
            path = self.private / 'events.jsonl'
            fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'w') as stream:
                stream.write(json.dumps(row, sort_keys=True) + '\n')

    def call(self, method, path, body=None, actor='editor', headers=None):
        if headers is None:
            token = next(v['token'] for v in self.identities if v['principal_id'] == actor)
            headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=15)
        try:
            conn.request(method, path, body=encoded(body) if body is not None else None, headers=headers)
            response = conn.getresponse()
            raw = response.read()
            return response.status, json.loads(raw), response.getheaders()
        finally:
            conn.close()

    def expected(self, snapshot, created, arguments):
        common = {'workspace_id': snapshot['workspace_id'], 'source_snapshot_id': snapshot['snapshot_id'],
                  'expected_plan_version': snapshot['plan_version'], 'synthetic': True, 'scenario_id': created['scenario_id']}
        request = {'snapshot': snapshot, 'scenario': dict(common, arrival_replacements=arguments['arrival_replacements']),
                   'recovery': dict(common, assignments=arguments['assignments'])}
        code = 'import json,sys; from procure.production.recovery import compare_receipt_recovery; f=json.load(sys.stdin); print(json.dumps(compare_receipt_recovery(f["snapshot"],f["scenario"],f["recovery"]),sort_keys=True))'
        result = subprocess.run([self.args.python, '-c', code], input=encoded(request), capture_output=True,
            timeout=20, cwd=self.root, check=True, env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': str(self.root/'procurement-engine/src'),
            'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1', 'LANG': 'C.UTF-8'})
        return json.loads(result.stdout)

    def observe(self, method, path, body, status, data, headers):
        if method == 'POST' and path == '/v2/voice/saved-context-sessions' and status == 201:
            read_status, context, _ = self.call('GET', '/v2/production-contexts/' + body['context_id'], headers=headers)
            assert read_status == 200 and context['context']['revision'] == body['expected_revision']
            with self.lock:
                self.sessions[data['session_id']] = {'snapshot': context['context']['snapshot'], 'created': data}
        if method == 'POST' and path.endswith('/turns') and status == 202:
            with self.lock:
                self.turns[(data['session_id'], data['turn_id'])] = body['call']['arguments']
        if method == 'GET' and path.endswith('/result') and data.get('state') == 'ready':
            key = (data['session_id'], data['turn_id'])
            with self.lock:
                if key in self.checked:
                    return
                self.checked.add(key)
                saved, arguments = self.sessions[key[0]], self.turns[key]
            expected = self.expected(saved['snapshot'], saved['created'], arguments)
            outcome = data['outcome']
            equal = outcome['result'] == expected and outcome['status'] == expected['status']
            receipt = {'exact_public_equal': equal, 'status': outcome['status'], 'snapshot_sha256': digest(saved['snapshot']),
                       'expected_sha256': digest(expected), 'actual_sha256': digest(outcome['result']),
                       'revision': saved['created']['production_context_ref']['revision']}
            self.event(dict(kind='connected', **receipt))
            assert equal, 'Connected result differs from public Python authority'

    def verify(self):
        receipts = []
        for name in ['recovery', 'recovery_noop', 'recovery_precision', 'recovery_unsupported']:
            fixture = json.loads((self.root/'contracts/examples'/f'{name}_request.json').read_text())
            snapshot = fixture['snapshot']
            snapshot['workspace_id'] = 'W'
            status, saved, _ = self.call('POST', '/v2/workspaces/W/production-contexts', {'contract_version': '0.2.0', 'snapshot': snapshot})
            assert status == 201, (name, status, saved)
            context = saved['context']; cid = context['context_id']
            expected_snapshot = dict(snapshot, snapshot_id=context['snapshot_id'], plan_version=context['plan_version'])
            assert context['snapshot'] == expected_snapshot
            assert context['metadata'] == {'synthetic': True, 'source': 'caller_supplied_synthetic', 'syntactic_validation': 'accepted', 'numerical_acceptance': 'not_claimed'}
            assert self.call('GET', '/v2/production-contexts/'+cid, actor='foreign')[0] == 404
            assert self.call('PUT', '/v2/production-contexts/'+cid, {'contract_version': '0.2.0', 'expected_revision': 1, 'snapshot': snapshot}, actor='viewer')[0] == 403
            create = {'contract_version': '0.2.0', 'context_id': cid, 'expected_revision': 1}
            assert self.call('POST', '/v2/voice/saved-context-sessions', dict(create, snapshot=snapshot))[0] == 422
            status, created, _ = self.call('POST', '/v2/voice/saved-context-sessions', create)
            assert status == 201, (status, created)
            args = {'arrival_replacements': fixture['scenario']['arrival_replacements'], 'assignments': fixture['recovery']['assignments']}
            turn_body = {'contract_version': '0.2.0', 'context_version': created['context_version'],
                         'call': {'name': 'compare_receipt_recovery', 'arguments': args}}
            sid = created['session_id']; base = '/v2/voice/sessions/'+sid
            status, turn, _ = self.call('POST', base+'/turns', turn_body)
            assert status == 202, (status, turn)
            poll = base+'/turns/'+turn['turn_id']+'/result'
            deadline = time.monotonic()+20
            while True:
                status, ready, _ = self.call('GET', poll)
                assert status == 200, (status, ready)
                if ready['state'] == 'ready': break
                assert ready['state'] == 'pending' and time.monotonic() < deadline, ready
                time.sleep(.05)
            expected = self.expected(context['snapshot'], created, args)
            assert ready['outcome']['result'] == expected and ready['outcome']['status'] == expected['status']
            if name == 'recovery':
                assert expected['disrupted_baseline']['orders'][0]['demands'][0]['shortfall'] == '4'
                assert expected['recovery']['orders'][0]['late_by_seconds'] == '3600'
            receipts.append({'case': name, 'exact_public_equal': True, 'snapshot_decimal_equal': True,
                             'status': expected['status'], 'result_sha256': digest(expected), 'context_id': cid})
            replacement = {'contract_version': '0.2.0', 'expected_revision': 1, 'snapshot': snapshot}
            assert self.call('PUT', '/v2/production-contexts/'+cid, replacement)[0] == 200
            for method, path, body in [('POST', '/v2/voice/saved-context-sessions', create), ('POST', base+'/turns', turn_body), ('GET', poll, None)]:
                status, error, _ = self.call(method, path, body)
                assert status == 409 and error['error']['code'] == 'production_context_stale', (status,error)
            assert self.call('DELETE', base)[0] == 200
            status, current, _ = self.call('GET', '/v2/production-contexts/'+cid)
            assert status == 200 and current['context']['revision'] == 2
        path = self.private/'api-checks.json'
        path.write_bytes(encoded({'checks': receipts, 'source': 'actual local authenticated Go/PostgreSQL/fixed Python'}))
        path.chmod(0o600)
        print(json.dumps({'four_exact_comparisons': True, 'foreign_role_override_stale_cleanup': True, 'receipt': str(path)}))

    def proxy(self):
        harness = self
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self): self.forward()
            def do_POST(self): self.forward()
            def do_PUT(self): self.forward()
            def do_DELETE(self): self.forward()
            def forward(self):
                size = int(self.headers.get('Content-Length', '0'))
                if size > 1048576:
                    self.send_error(413); return
                raw = self.rfile.read(size)
                if self.path == '/__capture':
                    if self.command == 'GET':
                        output = b'<html><body><h1>Task-local screenshot evidence</h1><form method=post><label>Name<input name=name></label><label>JPEG base64<textarea name=image></textarea></label><button>Save screenshot</button></form></body></html>'
                    elif self.command == 'POST':
                        fields = parse_qs(raw.decode())
                        name = fields.get('name', [''])[0]
                        if not re.fullmatch('[a-z0-9-]{1,80}', name): self.send_error(422); return
                        image = base64.b64decode(fields['image'][0], validate=True)
                        if image[:2] != b'\xff\xd8': self.send_error(422); return
                        artifact = harness.private / (name + '.jpg')
                        fd = os.open(artifact, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
                        with os.fdopen(fd, 'wb') as stream: stream.write(image)
                        output = ('<html><body><h1>Saved ' + name + '.jpg</h1></body></html>').encode()
                    else: self.send_error(405); return
                    self.send_response(200); self.send_header('Content-Type', 'text/html; charset=utf-8'); self.send_header('Content-Length', str(len(output))); self.end_headers(); self.wfile.write(output); return
                body = json.loads(raw) if raw else None
                headers = {k:v for k,v in self.headers.items() if k.lower() not in ('host','connection','content-length')}
                try:
                    status, data, response_headers = harness.call(self.command, self.path, body, headers=headers)
                    harness.observe(self.command, self.path, body, status, data, headers)
                    harness.event({'kind': 'request', 'method': self.command, 'path': self.path, 'status': status, 'state': data.get('state')})
                    try: control = json.loads((harness.private/'control.json').read_text())
                    except (ValueError, OSError): control = {}
                    if self.path.endswith('/result') and data.get('state') == 'ready' and control.get('delay_ready'):
                        harness.event({'kind': 'delayed_ready', 'fixture': 'response timing only; numerical result real'})
                        deadline = time.monotonic()+20
                        while time.monotonic() < deadline:
                            try: delay = json.loads((harness.private/'control.json').read_text()).get('delay_ready')
                            except (ValueError, OSError): delay = False
                            if not delay: break
                            time.sleep(.05)
                    output = encoded(data)
                    self.send_response(status)
                    for k,v in response_headers:
                        if k.lower() not in ('connection','content-length','transfer-encoding'): self.send_header(k,v)
                    self.send_header('Content-Length', str(len(output))); self.end_headers(); self.wfile.write(output)
                except (BrokenPipeError, ConnectionResetError):
                    harness.event({'kind': 'abandoned_response', 'path': self.path})
                except Exception:
                    harness.event({'kind': 'harness_failure', 'path': self.path})
                    self.send_error(502, 'Local acceptance harness failure')
        http.server.ThreadingHTTPServer(('127.0.0.1', self.args.proxy_port), Handler).serve_forever()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['verify','proxy'])
    parser.add_argument('--gateway', required=True)
    parser.add_argument('--identities-file', required=True)
    parser.add_argument('--private-dir', required=True)
    parser.add_argument('--python', required=True)
    parser.add_argument('--proxy-port', type=int, default=8832)
    args = parser.parse_args()
    getattr(Harness(args), args.mode)()
