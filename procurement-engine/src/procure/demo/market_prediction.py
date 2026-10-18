"""Verified, JSON-only market reference for the hosted Python 3.12 service.

The numerical models live in an isolated batch image. This module never imports
joblib or treats a research replay as a live data feed or transaction price.
"""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
from time import monotonic

from .market_snapshot_store import GcsMarketStore


_FILES = {
    'market_reference_snapshot_v1.json': 'bf069006027d43712eb8f0bb30bcf8a282792ee96c6c46f9492fee55f56b7de7',
    'market_reference_manifest_v1.json': '3e084dda2fc0d0feb4314e4c87ac853f16635910b3a25ea750cba7e361e20d89',
    'market_reference_registry_v1.json': '69d904acbab1bd437f507955daffa244b46451756fa1af009d7ca1bb11835d6a',
    'market_reference_sources_v1.json': '89bc277dfbbf763ba09891a4eac7326b4c902f8496e440784d2c69e3ad709b24',
}
_remote_cache = {'bucket': None, 'image': None, 'until': 0.0,
                 'value': None, 'last_good': None}


def _config_dir() -> Path:
    return Path(os.environ.get('PROCURE_CONFIG_DIR') or Path(__file__).resolve().parents[3] / 'config')


@lru_cache(maxsize=1)
def _reference() -> dict:
    values = {}
    for name, expected in _FILES.items():
        raw = (_config_dir() / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError(f'Unverified market reference: {name}')
        values[name] = json.loads(raw)
    snapshot = values['market_reference_snapshot_v1.json']
    manifest = values['market_reference_manifest_v1.json']
    registry = values['market_reference_registry_v1.json']
    sources = values['market_reference_sources_v1.json']
    if (snapshot.get('contract_version') != 'prediction.v1' or
            snapshot.get('model_manifest_sha256') != _FILES['market_reference_manifest_v1.json'] or
            snapshot.get('forecast_count') != 18 or len(snapshot.get('forecasts', [])) != 18 or
            manifest.get('selected_joblib_count') != 27 or
            registry.get('anchor_date') != '2026-10-16' or
            snapshot.get('status') != 'COMPLETE'):
        raise ValueError('Market reference metadata mismatch')
    artifacts = {path: sha for path, sha in manifest['files_sha256'].items() if path.endswith('.joblib')}
    if len(artifacts) != 27:
        raise ValueError('Model artifact role count mismatch')
    registry_hashes = set()
    for row in (*registry['commodity_cells'], *registry['fx_cells']):
        refs = ([role['artifact'] for role in row['models'].values()]
                if 'models' in row else [row['model']])
        for ref in refs:
            if artifacts.get(ref['relative_path']) != ref['sha256']:
                raise ValueError('Model registry does not match manifest')
            registry_hashes.add(ref['sha256'])
    if len(registry_hashes) != 27:
        raise ValueError('Model registry artifact roles incomplete')
    if any(cell.get('anchor_market_session_date') != '2026-10-16' or
           cell.get('target_market_session_date') is not None or
           cell.get('current_outcomes_observed') is not False or
           cell.get('model_artifact_sha256') not in registry_hashes
           for cell in snapshot['forecasts']):
        raise ValueError('Unresolved reference outcome or model mismatch')
    if not isinstance(sources, dict) or not sources:
        raise ValueError('Original source metadata missing')
    return snapshot


def _display(snapshot: dict, run_hash: str, delivery_state: str) -> dict:
    from .company import list_companies
    if (snapshot.get('as_of_utc') != '2026-10-17T11:11:33Z' or
            snapshot.get('model_manifest_sha256') != _FILES['market_reference_manifest_v1.json'] or
            snapshot.get('feature_spec_sha256') != '6ca19debb1ba626194e1f75a200ad18f5710c727bcb9fcc986f6b757dae3eefb' or
            snapshot.get('source_manifest_sha256') != '5ff7e0202759b6f509bd292549a7ad7085b20ed877ffe17036cd4764ff7d71f4' or
            snapshot.get('forecast_count') != 18 or len(snapshot.get('forecasts', [])) != 18 or
            not isinstance(snapshot.get('job_image_digest'), str) or
            not snapshot['job_image_digest']):
        raise ValueError('Unverified market source, feature or model identity')
    if delivery_state.startswith('VERIFIED_GCS') and not snapshot['job_image_digest'].startswith('sha256:'):
        raise ValueError('Published job image digest missing')
    reference_cells = _reference()['forecasts']
    for observed, expected in zip(snapshot['forecasts'], reference_cells):
        if {key: value for key, value in observed.items() if key != 'run_id'} != {
                key: value for key, value in expected.items() if key != 'run_id'}:
            raise ValueError('Remote reference cells differ from trusted replay')
    return {
        'contract_version': 'prediction.v1', 'status': 'REFERENCE_REPLAY',
        'publication_mode': 'REFERENCE_REPLAY', 'as_of_utc': snapshot['as_of_utc'],
        'delivery_state': delivery_state,
        'anchor_market_session_date': '2026-10-16',
        'run_id': snapshot['run_id'], 'run_hash': run_hash,
        'forecast_count': 18, 'artifact_role_count': 27,
        'coverage': {'market_series': ['HG_ROLLING_FUTURES', 'EURUSD', 'GBPUSD', 'USDJPY'],
                     'observed_session_horizons': [1, 3, 5],
                     'direct_joint_cost_reporting_currencies': [],
                     'unsupported_reporting_currencies': sorted({c['reporting_currency'] for c in list_companies()}),
                     'material_basis': {'copper_cathode': 'HG_FUTURES_CONTEXT_ONLY',
                                        'copper_rod': 'UNSUPPORTED_GRADE',
                                        'copper_foil': 'UNSUPPORTED_GRADE',
                                        'other_materials': 'UNSUPPORTED'},
                     'existing_company_direct_joint_cost_matches': 0},
        'source': {'model_manifest_sha256': _FILES['market_reference_manifest_v1.json'],
                   'registry_sha256': _FILES['market_reference_registry_v1.json'],
                   'original_source_manifest_sha256': _FILES['market_reference_sources_v1.json'],
                   'model_set': '2026-10-16 selected reference; future outcomes unresolved'},
        'limitations': [
            'Research replay as of the stated source session; no approved fresh feed.',
            'HG rolling futures are not a physical cathode, rod or foil supplier offer.',
            'Uncalibrated scores are not validated probabilities or savings.',
            'Observed-session horizons do not establish calendar delivery or payment dates.',
        ],
        'forecasts': [{**deepcopy(cell), 'status': 'TARGET_UNRESOLVED',
                       'applicability': 'CONTEXT_ONLY'} for cell in snapshot['forecasts']],
    }


def market_read_model() -> dict:
    """Use generation-verified private JSON, or disclose bundled reference fallback."""
    bucket = os.environ.get('ZOORQ_PREDICTION_BUCKET', '').strip()
    image = os.environ.get('ZOORQ_PREDICTION_JOB_IMAGE_DIGEST', '').strip()
    if not bucket:
        return _display(_reference(), _FILES['market_reference_snapshot_v1.json'], 'BUNDLED_REFERENCE')
    if (_remote_cache['bucket'], _remote_cache['image']) != (bucket, image):
        _remote_cache.update(bucket=bucket, image=image, until=0.0,
                             value=None, last_good=None)
    if monotonic() < _remote_cache['until']:
        return deepcopy(_remote_cache['value'])
    try:
        snapshot, pointer, _generation = GcsMarketStore(bucket).read_latest()
        result = _display(snapshot, pointer['snapshot']['sha256'], 'VERIFIED_GCS_REFERENCE')
        _remote_cache['last_good'] = deepcopy(result)
    except (OSError, ValueError, KeyError, TypeError, TimeoutError):
        if _remote_cache['bucket'] == bucket and _remote_cache['last_good'] is not None:
            result = deepcopy(_remote_cache['last_good'])
            result['delivery_state'] = 'LAST_VERIFIED_GCS_REMOTE_UNAVAILABLE'
            result['limitations'].append('Private archive refresh failed; last generation-verified dated reference shown.')
        else:
            result = _display(_reference(), _FILES['market_reference_snapshot_v1.json'],
                              'BUNDLED_REFERENCE_REMOTE_UNAVAILABLE')
            result['limitations'].append('Configured private archive could not be verified; bundled dated reference shown.')
    _remote_cache.update(bucket=bucket, image=image, value=deepcopy(result),
                         until=monotonic() + 30)
    return result


def influence_for_material(company: dict, material_id: str, market: dict | None = None) -> dict:
    """Classify material/currency applicability without guessing basis conversion."""
    market = market or market_read_model()
    material = next((row for row in company.get('materials', []) if row['material_id'] == material_id), None)
    if material is None:
        return {'status': 'UNSUPPORTED', 'reason_code': 'MATERIAL_NOT_IN_COMPANY', 'direct_joint_cost_match': False}
    currency = company['reporting_currency']
    is_cathode = material_id == 'copper' and 'cathode' in material['name'].casefold()
    if market['status'] == 'UNAVAILABLE':
        status, reason = 'UNAVAILABLE', 'MARKET_SNAPSHOT_UNAVAILABLE'
    elif is_cathode:
        status, reason = 'CONTEXT_ONLY', 'HG_FUTURES_NOT_PHYSICAL_CATHODE_AND_CURRENCY_UNMATCHED'
    else:
        status, reason = 'UNSUPPORTED', 'MATERIAL_OR_CURRENCY_HAS_NO_DIRECT_MODEL'
    return {'status': status, 'reason_code': reason, 'material_id': material_id,
            'quantity_unit': material['quantity_unit'], 'reporting_currency': currency,
            'direct_joint_cost_match': False, 'market_run_hash': market['run_hash'],
            'as_of_utc': market['as_of_utc'], 'publication_mode': market['publication_mode']}
