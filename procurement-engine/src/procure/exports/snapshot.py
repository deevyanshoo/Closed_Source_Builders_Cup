"""Committed UN Comtrade snapshot for export opportunities: field extraction, digests and loading.

The hosted app only reads the committed JSON. scripts/build_export_snapshot.py is the only code that calls UN
Comtrade, once, offline. A row that UN Comtrade did not return is absent here and reads as "not reported" later;
it is never stored as zero.
"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path

PRODUCTS_FILE = 'export_products_v1.json'
SNAPSHOT_FILE = 'export_trade_snapshot_v1.json'
FX_FILE = 'export_fx_v1.json'

# UN Comtrade response field -> stored field. Values are kept as returned (numbers or null).
FIELDS = {
    'period': 'period', 'reporterCode': 'reporter_code', 'reporterDesc': 'reporter_name', 'flowCode': 'flow',
    'partnerCode': 'partner_code', 'partnerDesc': 'partner_name', 'cmdCode': 'cmd_code',
    'classificationCode': 'classification_code', 'isOriginalClassification': 'is_original_classification',
    'primaryValue': 'primary_value', 'cifvalue': 'cif_value', 'fobvalue': 'fob_value', 'netWgt': 'net_wgt',
    'isNetWgtEstimated': 'is_net_wgt_estimated', 'qty': 'qty', 'qtyUnitCode': 'qty_unit_code',
    'qtyUnitAbbr': 'qty_unit_abbr', 'isQtyEstimated': 'is_qty_estimated', 'isReported': 'is_reported',
    'isAggregate': 'is_aggregate', 'legacyEstimationFlag': 'legacy_estimation_flag',
}


def extract_row(record: dict) -> dict:
    return {stored: record.get(source) for source, stored in FIELDS.items()}


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def rows_digest(rows: list[dict]) -> str:
    return hashlib.sha256(canonical(rows)).hexdigest()


def sort_rows(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: (str(r['period']), r['flow'], r['reporter_code'], r['partner_code']))


def _config_dir(config_dir: Path | None) -> Path:
    if config_dir is not None:
        return Path(config_dir)
    from procure.settings import get_settings
    return get_settings().config_dir


@lru_cache(maxsize=8)
def _read(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding='utf-8'))


def load(name: str, config_dir: Path | None = None) -> dict:
    return _read(str(_config_dir(config_dir) / name))
