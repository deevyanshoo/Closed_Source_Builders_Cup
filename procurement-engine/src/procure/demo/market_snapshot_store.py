"""Small JSON-only, generation-bound GCS archive for public market evidence.

Authentication uses the Cloud Run metadata identity. Company state never enters
this bucket. Every archive write is create-only; only control/latest.json can be
replaced using a generation compare-and-swap.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


MAX_JSON = 2_000_000
TRUSTED_AS_OF_UTC = '2026-10-17T11:11:33Z'
TRUSTED_MODEL_MANIFEST = '3e084dda2fc0d0feb4314e4c87ac853f16635910b3a25ea750cba7e361e20d89'
TRUSTED_SOURCE_MANIFEST = '5ff7e0202759b6f509bd292549a7ad7085b20ed877ffe17036cd4764ff7d71f4'
TRUSTED_FEATURE_SPEC = '6ca19debb1ba626194e1f75a200ad18f5710c727bcb9fcc986f6b757dae3eefb'
TRUSTED_CELLS_SHA256 = 'c60b6c26f06c8a87d816639f88ac62c7bec4488a325ab41f3af0baff5a426324'
TRUSTED_PACKAGE_SOURCE_SHA256 = '1eaabe61440c19aba6a2112a8c7e686476bb85f3fed08bca89d6b3bf5f73d569'
TRUSTED_RUNTIME = {'python': '3.11.15', 'numpy': '2.4.6', 'scipy': '1.17.1',
                   'scikit_learn': '1.9.1', 'joblib': '1.6.0'}
IMAGE_DIGEST = re.compile(r'sha256:[0-9a-f]{64}\Z')


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json(raw: bytes) -> dict:
    if len(raw) > MAX_JSON:
        raise ValueError('Oversized market object')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('Market object must be JSON object')
    return value


def verify_reference_snapshot(snapshot: dict, *, expected_image_digest: str | None = None) -> None:
    """Pin the public replay to its original clock, cells, models and worker."""
    if (snapshot.get('publication_mode') != 'REFERENCE_REPLAY' or
            snapshot.get('as_of_utc') != TRUSTED_AS_OF_UTC or
            snapshot.get('completed_at_utc') != TRUSTED_AS_OF_UTC or
            snapshot.get('status') != 'COMPLETE' or
            snapshot.get('forecast_count') != 18 or
            snapshot.get('model_manifest_sha256') != TRUSTED_MODEL_MANIFEST or
            snapshot.get('source_manifest_sha256') != TRUSTED_SOURCE_MANIFEST or
            snapshot.get('feature_spec_sha256') != TRUSTED_FEATURE_SPEC or
            snapshot.get('source_code_sha256') != TRUSTED_PACKAGE_SOURCE_SHA256 or
            snapshot.get('runtime') != TRUSTED_RUNTIME):
        raise ValueError('Untrusted reference replay identity')
    image = snapshot.get('job_image_digest')
    if not isinstance(image, str) or IMAGE_DIGEST.fullmatch(image) is None:
        raise ValueError('Invalid job image digest')
    if expected_image_digest is not None and image != expected_image_digest:
        raise ValueError('Job image digest differs from approved deployment')
    cells = snapshot.get('forecasts')
    if not isinstance(cells, list) or len(cells) != 18:
        raise ValueError('Incomplete reference cells')
    identities = [(row.get('forecast_kind'), row.get('fx_pair'),
                   row.get('horizon_observed_rows')) for row in cells if isinstance(row, dict)]
    if len(identities) != 18 or len(set(identities)) != 18:
        raise ValueError('Duplicate or malformed reference cell identity')
    if any(row.get('run_id') != snapshot.get('run_id') or
           row.get('target_market_session_date') is not None or
           row.get('current_outcomes_observed') is not False or
           row.get('evaluation') is not None for row in cells):
        raise ValueError('Reference outcomes or run identity changed')
    roles = [row.get('model_artifact_sha256') for row in cells]
    roles.extend(row['magnitude_context'].get('model_artifact_sha256')
                 for row in cells if row.get('magnitude_context') is not None)
    if len(roles) != 27 or len(set(roles)) != 27 or any(
            not isinstance(role, str) or re.fullmatch(r'[0-9a-f]{64}', role) is None
            for role in roles):
        raise ValueError('Selected model roles incomplete')
    normalized = [{key: value for key, value in row.items() if key != 'run_id'} for row in cells]
    if digest(canonical(normalized)) != TRUSTED_CELLS_SHA256:
        raise ValueError('Reference cell or selected model role changed')


def verify_envelope(pointer_raw: bytes, receipt_raw: bytes, snapshot_raw: bytes,
                    *, expected_image_digest: str | None = None) -> dict:
    pointer, receipt, snapshot = map(_json, (pointer_raw, receipt_raw, snapshot_raw))
    if any(row.get('contract_version') != 'prediction.v1' for row in (pointer, receipt, snapshot)):
        raise ValueError('Prediction contract mismatch')
    run = pointer.get('run_id')
    if not isinstance(run, str) or not run or receipt.get('run_id') != run or snapshot.get('run_id') != run:
        raise ValueError('Prediction run mismatch')
    if digest(receipt_raw) != pointer['receipt']['sha256'] or digest(snapshot_raw) != pointer['snapshot']['sha256']:
        raise ValueError('Prediction content hash mismatch')
    if (receipt.get('snapshot_sha256') != pointer['snapshot']['sha256'] or
            str(receipt.get('snapshot_generation')) != str(pointer['snapshot']['generation']) or
            receipt.get('snapshot_key') != pointer['snapshot']['key']):
        raise ValueError('Receipt does not bind snapshot generation')
    if (pointer.get('as_of_utc') != snapshot.get('as_of_utc') or
            receipt.get('as_of_utc') != snapshot.get('as_of_utc')):
        raise ValueError('Source as-of mismatch')
    for key in ('model_manifest_sha256', 'source_manifest_sha256', 'feature_spec_sha256',
                'job_image_digest', 'source_code_sha256'):
        if receipt.get(key) != snapshot.get(key):
            raise ValueError(f'{key} mismatch')
    if pointer.get('source_code_sha256') != snapshot.get('source_code_sha256') or \
            pointer.get('job_image_digest') != snapshot.get('job_image_digest'):
        raise ValueError('Pointer source identity mismatch')
    verify_reference_snapshot(snapshot, expected_image_digest=expected_image_digest)
    return snapshot


class GcsMarketStore:
    def __init__(self, bucket: str):
        if not bucket or not all(char.isalnum() or char in '.-_' for char in bucket):
            raise ValueError('Invalid bucket')
        self.bucket = bucket

    def _token(self) -> str:
        request = Request('http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token',
                          headers={'Metadata-Flavor': 'Google'})
        with urlopen(request, timeout=3) as response:
            return _json(response.read(4096))['access_token']

    def _call(self, url: str, *, method='GET', data=None) -> bytes:
        headers = {'Authorization': f'Bearer {self._token()}'}
        if data is not None:
            headers['Content-Type'] = 'application/json'
        request = Request(url, data=data, headers=headers, method=method)
        with urlopen(request, timeout=5) as response:
            raw = response.read(MAX_JSON + 1)
        if len(raw) > MAX_JSON:
            raise ValueError('Oversized market response')
        return raw

    def _object_url(self, key: str, **params) -> str:
        if not key or key.startswith('/') or '..' in key.split('/'):
            raise ValueError('Invalid object key')
        url = f'https://storage.googleapis.com/storage/v1/b/{quote(self.bucket)}/o/{quote(key, safe="")}'
        return url + ('?' + urlencode(params) if params else '')

    def metadata(self, key: str) -> dict:
        return _json(self._call(self._object_url(key)))

    def get(self, key: str, generation: str | None = None) -> bytes:
        params = {'alt': 'media'}
        if generation is not None:
            params['generation'] = generation
        return self._call(self._object_url(key, **params))

    def create(self, key: str, raw: bytes, *, generation_match: str = '0') -> str:
        if len(raw) > MAX_JSON:
            raise ValueError('Oversized market write')
        url = (f'https://storage.googleapis.com/upload/storage/v1/b/{quote(self.bucket)}/o?'
               + urlencode({'uploadType': 'media', 'name': key, 'ifGenerationMatch': generation_match}))
        return str(_json(self._call(url, method='POST', data=raw))['generation'])

    def create_immutable(self, key: str, raw: bytes) -> str:
        try:
            return self.create(key, raw)
        except HTTPError as exc:
            if exc.code != 412:
                raise
            metadata = self.metadata(key)
            generation = str(metadata['generation'])
            if self.get(key, generation) != raw:
                raise ValueError('Conflicting immutable market object') from exc
            return generation

    def read_latest(self) -> tuple[dict, dict, str]:
        pointer_meta = self.metadata('control/latest.json')
        generation = str(pointer_meta['generation'])
        pointer_raw = self.get('control/latest.json', generation)
        pointer = _json(pointer_raw)
        receipt = pointer['receipt']
        snapshot = pointer['snapshot']
        if receipt.get('bucket') != self.bucket or snapshot.get('bucket') != self.bucket:
            raise ValueError('Market pointer crosses bucket boundary')
        receipt_raw = self.get(receipt['key'], str(receipt['generation']))
        snapshot_raw = self.get(snapshot['key'], str(snapshot['generation']))
        approved_image = (os.environ.get('ZOORQ_PREDICTION_JOB_IMAGE_DIGEST') or
                          os.environ.get('ZOORQ_JOB_IMAGE_DIGEST'))
        if not approved_image or IMAGE_DIGEST.fullmatch(approved_image) is None:
            raise ValueError('Approved exact prediction job image digest is required')
        return verify_envelope(pointer_raw, receipt_raw, snapshot_raw,
                               expected_image_digest=approved_image), pointer, generation

    def publish(self, snapshot: dict) -> dict:
        verify_reference_snapshot(snapshot, expected_image_digest=os.environ.get('ZOORQ_JOB_IMAGE_DIGEST'))
        run = snapshot['run_id']
        if not isinstance(run, str) or not run.startswith('reference-') or '/' in run:
            raise ValueError('Invalid reference run')
        snapshot_raw = canonical(snapshot)
        try:
            previous, pointer, generation = self.read_latest()
        except HTTPError as exc:
            if exc.code != 404:
                raise
            previous, pointer, generation = None, None, '0'
        if previous is not None:
            if previous['run_id'] == run:
                if pointer['snapshot']['sha256'] != digest(snapshot_raw):
                    raise ValueError('Conflicting same-run snapshot')
                return pointer
            if previous['as_of_utc'] >= snapshot['as_of_utc']:
                raise ValueError('Refusing older or conflicting same-session promotion')
        snapshot_key = f'runs/{run}/snapshot.json'
        snapshot_generation = self.create_immutable(snapshot_key, snapshot_raw)
        receipt = {'contract_version': 'prediction.v1', 'run_id': run,
                   'as_of_utc': snapshot['as_of_utc'], 'snapshot_key': snapshot_key,
                   'snapshot_generation': snapshot_generation, 'snapshot_sha256': digest(snapshot_raw),
                   'model_manifest_sha256': snapshot['model_manifest_sha256'],
                   'source_manifest_sha256': snapshot['source_manifest_sha256'],
                   'feature_spec_sha256': snapshot['feature_spec_sha256'],
                   'job_image_digest': snapshot['job_image_digest'],
                   'source_code_sha256': snapshot['source_code_sha256']}
        receipt_raw = canonical(receipt)
        receipt_key = f'runs/{run}/receipt.json'
        receipt_generation = self.create_immutable(receipt_key, receipt_raw)
        next_pointer = {'contract_version': 'prediction.v1', 'run_id': run,
                        'as_of_utc': snapshot['as_of_utc'],
                        'job_image_digest': snapshot['job_image_digest'],
                        'source_code_sha256': snapshot['source_code_sha256'],
                        'snapshot': {'bucket': self.bucket, 'key': snapshot_key, 'generation': snapshot_generation, 'sha256': digest(snapshot_raw)},
                        'receipt': {'bucket': self.bucket, 'key': receipt_key, 'generation': receipt_generation, 'sha256': digest(receipt_raw)}}
        try:
            self.create('control/latest.json', canonical(next_pointer), generation_match=generation)
        except HTTPError as exc:
            if exc.code != 412:
                raise
            latest, latest_pointer, _ = self.read_latest()
            if latest['run_id'] == run and latest['as_of_utc'] == snapshot['as_of_utc']:
                return latest_pointer
            raise ValueError('Pointer changed during publication; review newer run') from exc
        return next_pointer
