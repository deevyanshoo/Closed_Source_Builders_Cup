#!/usr/bin/env python3
"""Synthetic loopback receipt-recovery check. Never provision or refresh providers."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import secrets
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def prepare(directory: Path):
    import duckdb
    from procure.db import SCHEMA
    from procure.settings import material_keys
    from procure.service_cache import validate_database
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    cache = directory / 'cache'
    cache.mkdir(mode=0o700)
    database = cache / 'app.duckdb'
    with duckdb.connect(str(database)) as connection:
        for statement in SCHEMA:
            connection.execute(statement)
        for material in material_keys():
            connection.execute('INSERT INTO prices_monthly VALUES (?,?,?,?,?,?,?,?)',
                [material, '2026-01-01', 1., 'kg', 'synthetic startup fixture', True, '2026-10-07T00:00:00Z', ''])
            connection.execute('INSERT INTO model_selection VALUES (?,?,?,?,?,?,?,?,?)',
                [material, 'naive', 'naive', 1., 1., 0., False, 1., '2026-10-07T00:00:00Z'])
            connection.execute('INSERT INTO model_metrics VALUES (?,?,?,?,?,?,?,?)',
                [material, 'naive', 1, 1., .5, .5, 1, '2026-10-07T00:00:00Z'])
        connection.execute('INSERT INTO fx_monthly VALUES (?,?,?,?,?,?)',
            ['2026-01-01', 1., 'synthetic startup fixture', True, '2026-10-07T00:00:00Z', ''])
    validate_database(database, tuple(material_keys()))
    identities = [dict(token=secrets.token_urlsafe(32), principal_id=f'local-recovery-viewer-{i}',
                       workspace_id=f'local-recovery-{i}', role='viewer') for i in range(2)]
    for name, value in [('identities.json', json.dumps({'identities': identities})),
                        ('token', identities[0]['token']), ('token-secondary', identities[1]['token'])]:
        target = directory / name
        target.write_text(value)
        target.chmod(0o600)
    print('Prepared validated synthetic cache and private local viewer identities; no provider evidence.')


def check(gateway: str, identities_file: Path):
    from procure.production.recovery import compare_receipt_recovery
    url = urlsplit(gateway)
    if url.scheme != 'http' or url.hostname not in {'127.0.0.1', '::1'} or url.username or url.password or url.path not in {'', '/'} or url.query or url.fragment:
        raise ValueError('Gateway must be a bare loopback HTTP origin')
    if identities_file.stat().st_mode & 0o077:
        raise ValueError('Local identities file must be private')
    identity = json.loads(identities_file.read_text())['identities'][0]
    receipts = []
    for name in ('recovery', 'recovery_noop', 'recovery_precision', 'recovery_unsupported'):
        request = json.loads((ROOT / 'contracts/examples' / f'{name}_request.json').read_text())
        for key in ('snapshot', 'scenario', 'recovery'):
            request[key]['workspace_id'] = identity['workspace_id']
        actual = compare_receipt_recovery(request['snapshot'], request['scenario'], request['recovery'])
        expected = ({'contract_version': '0.2.0', 'comparison': actual} if actual['status'] == 'compared'
                    else {'contract_version': '0.2.0', **actual})
        call = Request(gateway.rstrip('/') + '/v2/production/receipt-recovery/comparisons',
                       data=json.dumps(request).encode(), headers={'Content-Type': 'application/json',
                       'Authorization': 'Bearer ' + identity['token']}, method='POST')
        try:
            response = urlopen(call, timeout=10)
        except HTTPError as error:
            response = error
        with response:
            payload = response.read(2 * 1024 * 1024 + 1)
            assert len(payload) <= 2 * 1024 * 1024
            assert response.status == (200 if actual['status'] == 'compared' else 422)
            assert json.loads(payload) == expected, 'Connected result differs from public numerical tool'
        digest = hashlib.sha256(json.dumps(expected, sort_keys=True).encode()).hexdigest()
        receipts.append({'example': name, 'status': response.status, 'public_tool_equal': True, 'result_sha256': digest})
    return {'evidence': 'actual local Go to restricted Python to public numerical tool',
            'provider': 'not_exercised', 'database_persistence': 'not_used', 'checks': receipts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-runtime', type=Path)
    parser.add_argument('--gateway', default='http://127.0.0.1:8820')
    parser.add_argument('--identities-file', type=Path)
    parser.add_argument('--receipt', type=Path)
    args = parser.parse_args()
    if args.prepare_runtime:
        prepare(args.prepare_runtime)
        return
    if not args.identities_file:
        parser.error('--identities-file is required for connected checks')
    receipt = check(args.gateway, args.identities_file)
    if args.receipt:
        args.receipt.write_text(json.dumps(receipt, indent=2) + '\n')
    print('Four actual connected comparisons equal the direct public numerical tool, including exact decimal strings.')


if __name__ == '__main__':
    main()
