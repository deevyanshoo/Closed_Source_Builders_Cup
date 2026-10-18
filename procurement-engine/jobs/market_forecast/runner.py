"""Pinned selected-model replay and optional immutable reference publication.

Only the exact trusted bundle may be unpickled, after all 59 hashes match.
No network data intake or scheduled fit exists in this reference job.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
TRUSTED_MANIFEST = '3e084dda2fc0d0feb4314e4c87ac853f16635910b3a25ea750cba7e361e20d89'
TRUSTED_FIXTURE = 'bf069006027d43712eb8f0bb30bcf8a282792ee96c6c46f9492fee55f56b7de7'
TRUSTED_AS_OF_UTC = '2026-10-17T11:11:33Z'


def package_source_sha256() -> str:
    """Bind replay code, worker entrypoint, build recipe and runtime lock."""
    paths = ('runner.py', 'Dockerfile', 'requirements.txt',
             'bundle/replay.py', 'bundle/runtime_lock.json')
    identities = {name: hashlib.sha256((HERE / name).read_bytes()).hexdigest()
                  for name in paths}
    return hashlib.sha256(json.dumps(identities, sort_keys=True,
                                   separators=(',', ':')).encode()).hexdigest()


def verified_snapshot() -> dict:
    sys.path.insert(0, str(HERE / 'bundle'))
    import replay
    # replay.main calls check_bundle: manifest SHA, every file hash, exact
    # Python/package versions and cell metadata before its first joblib.load.
    scored = replay.main(TRUSTED_MANIFEST)
    if scored['status'] != 'ALL_27_SELECTED_MODELS_HASH_VERIFIED_AND_REPLAYED':
        raise ValueError('Incomplete model replay')
    raw = (HERE / 'reference_snapshot.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != TRUSTED_FIXTURE:
        raise ValueError('Reference snapshot hash mismatch')
    snapshot = json.loads(raw)
    if snapshot.get('as_of_utc') != TRUSTED_AS_OF_UTC:
        raise ValueError('Trusted source as-of clock changed')
    if snapshot.get('forecast_count') != 18 or len(snapshot.get('forecasts', [])) != 18:
        raise ValueError('Reference snapshot cell count mismatch')
    cells = {(row['forecast_kind'], row['fx_pair'], row['horizon_observed_rows']): row
             for row in snapshot['forecasts']}
    checked = 0
    def close(actual, expected):
        if not math.isclose(float(actual), float(expected), rel_tol=1e-10, abs_tol=1e-10):
            raise ValueError('Published score differs from Linux replay')
    for row in scored['joint']:
        pair, horizon, _ = row['cell'].split('_', 2)
        cell = cells['JOINT_COPPER_FX_LANDED_PROXY', pair, int(horizon[1:])]
        close(row['score_landed_cost_up_uncalibrated'], cell['prob_direction_up_uncalibrated'])
        close(row['predicted_landed_return_shrunk'], cell['magnitude_context']['signed_estimated_landed_return'])
        checked += 2
    for row in scored['fx']:
        pair, horizon, _ = row['cell'].split('_', 2)
        cell = cells['FX_USD_PAYABLE_DISPLAY_ONLY', pair, int(horizon[1:])]
        close(row['score_domestic_usd_cost_up_uncalibrated'], cell['prob_direction_up_uncalibrated'])
        checked += 1
    if checked != 27:
        raise ValueError('Not all 27 selected scores compared')
    snapshot = deepcopy(snapshot)
    snapshot['run_id'] = 'reference-2026-10-16-' + TRUSTED_MANIFEST[:16]
    snapshot['publication_mode'] = 'REFERENCE_REPLAY'
    snapshot['job_image_digest'] = os.environ.get('ZOORQ_JOB_IMAGE_DIGEST', 'VERIFY_ONLY_NO_IMAGE_DIGEST')
    snapshot['source_code_sha256'] = package_source_sha256()
    for cell in snapshot['forecasts']:
        cell['run_id'] = snapshot['run_id']
        if cell.get('target_market_session_date') is not None or cell.get('current_outcomes_observed') is not False:
            raise ValueError('Reference outcome was rewritten')
    return snapshot


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--verify-only', action='store_true')
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    if args.verify_only == args.publish:
        parser.error('Choose exactly one of --verify-only or --publish')
    snapshot = verified_snapshot()
    if args.publish:
        from procure.demo.market_snapshot_store import GcsMarketStore
        bucket = os.environ['ZOORQ_PREDICTION_BUCKET']
        import re
        if re.fullmatch(r'sha256:[0-9a-f]{64}', snapshot['job_image_digest']) is None:
            raise ValueError('Pinned job image digest is required before publication')
        pointer = GcsMarketStore(bucket).publish(snapshot)
        print(json.dumps({'status':'PUBLISHED_REFERENCE_REPLAY', 'run_id':pointer['run_id'],
                          'snapshot_sha256':pointer['snapshot']['sha256'],
                          'receipt_sha256':pointer['receipt']['sha256']}))
    else:
        print(json.dumps({'status':'LINUX_PARITY_VERIFIED', 'scores':27,
                          'cells':18, 'manifest_sha256':TRUSTED_MANIFEST}))


if __name__ == '__main__':
    main()
