"""Country name to ISO 3166-1 alpha-2 code, from the full table in config/iso3166_alpha2.csv.

Every assigned code is listed, so there is no country cap. A name the table does not know
returns None and stays as written; it is never guessed.
"""
from __future__ import annotations

import csv
from functools import lru_cache

from ..settings import get_settings
from .normalize import squash


@lru_cache(maxsize=1)
def _table() -> dict[str, str]:
    path = get_settings().config_dir / "iso3166_alpha2.csv"
    names: dict[str, str] = {}
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            for name in [row["name"], *filter(None, (row["aliases"] or "").split("|"))]:
                names[squash(name)] = row["code"]
            names[squash(row["code"])] = row["code"]
    return names


def code_for(name: str | None) -> str | None:
    if not name:
        return None
    return _table().get(squash(name.strip().rstrip(".")))
