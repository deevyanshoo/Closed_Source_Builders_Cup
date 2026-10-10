"""Versioned location registry: facility and port codes to coordinates for hazard providers.

``load_registry(path)`` validates ``config/locations_v2.json``; ``resolve(registry, code)`` returns
the entry or ``unsupported`` with a reason. A code that is not listed (including synthetic plant
codes) is never given guessed coordinates. Coordinates stay decimal strings as written. Every
entry carries provenance: a verified entry cites ``source_url`` and ``checked_on``; an unverified
entry states ``unverified_reason``. An entry with neither is refused.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ..settings import PROJECT_ROOT

REGISTRY_FILE = PROJECT_ROOT / "config" / "locations_v2.json"
_CODE = re.compile(r"^[A-Z]{2}[A-Z0-9]{3}$")
_COORD = re.compile(r"^-?\d{1,3}(?:\.\d{1,6})?$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FIELDS = ("code", "kind", "name", "lat", "lon", "source", "verified", "source_url", "checked_on", "unverified_reason")


def _coordinate(text: object, bound: int) -> bool:
    if not isinstance(text, str) or not _COORD.fullmatch(text):
        return False
    try:
        return -bound <= Decimal(text) <= bound
    except InvalidOperation:
        return False


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _provenance(row: dict) -> bool:
    if not isinstance(row.get("checked_on"), str) or not _DATE.fullmatch(row["checked_on"]):
        return False
    if row["verified"]:
        return _text(row.get("source_url")) and row["source_url"].startswith("https://") \
            and row.get("unverified_reason") is None
    return _text(row.get("unverified_reason"))


def load_registry(path: Path | None = None) -> dict:
    content = json.loads(Path(path or REGISTRY_FILE).read_text(encoding="utf-8"))
    if not isinstance(content, dict) or not isinstance(content.get("version"), str) or \
            not isinstance(content.get("locations"), list):
        raise ValueError("invalid location registry")
    entries = {}
    for row in content["locations"]:
        if not isinstance(row, dict) or not isinstance(row.get("code"), str) or not _CODE.fullmatch(row["code"]) \
                or row["code"] in entries or not _coordinate(row.get("lat"), 90) \
                or not _coordinate(row.get("lon"), 180) or type(row.get("verified")) is not bool \
                or not all(isinstance(row.get(k), str) and row[k].strip() for k in ("kind", "name", "source")):
            raise ValueError("invalid location registry entry")
        if not _provenance(row):
            raise ValueError(f"location {row['code']} needs a cited source or an unverified reason")
        entries[row["code"]] = {k: row.get(k) for k in FIELDS}
    return {"version": content["version"], "notice": content.get("notice"), "entries": entries}


def resolve(registry: dict, code: object) -> dict:
    empty = {**{k: None for k in FIELDS}, "code": code, "registry_version": registry["version"]}
    if not isinstance(code, str) or not code:
        return {"status": "unsupported", "reason": "invalid_code", **empty}
    entry = registry["entries"].get(code)
    if entry is None:
        return {"status": "unsupported", "reason": "not_in_registry", **empty}
    return {"status": "ok", "reason": None, **entry, "registry_version": registry["version"]}
