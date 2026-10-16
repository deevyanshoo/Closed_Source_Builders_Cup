"""Read the committed monthly history snapshot (config/prediction_history_v1.json)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

SNAPSHOT_FILE = 'prediction_history_v1.json'


def series_values_sha256(observations: list) -> str:
    """Hash of the exact [month, value-text] pairs, so any edit to a stored value is detectable."""
    canonical = json.dumps(observations, separators=(',', ':'), ensure_ascii=True)
    return hashlib.sha256(canonical.encode('ascii')).hexdigest()


def load_history(path: Path | None = None, *, with_digest: bool = False) -> dict:
    if path is None:
        from procure.settings import get_settings
        path = get_settings().config_dir / SNAPSHOT_FILE
    raw = Path(path).read_bytes()
    data = json.loads(raw)
    for series_id, series in data['series'].items():
        if series['status'] == 'available' and series['values_sha256'] != series_values_sha256(series['observations']):
            raise ValueError(f'History values changed for {series_id}')
    if with_digest:
        data['_sha256'] = hashlib.sha256(raw).hexdigest()
    return data
