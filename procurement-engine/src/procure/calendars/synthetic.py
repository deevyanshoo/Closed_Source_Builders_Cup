"""The bundled SYNTHETIC holiday table (tests/fixtures/calendars/synthetic_holidays.json).

Every date and name in it is invented, and its weekend is a fixture assumption. It exists so the
dispatch workspace can show working-day lead times offline; it is not a source of real holidays.
A missing, unreadable, unlabelled or malformed table is ``source_unavailable``.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from ..settings import PROJECT_ROOT
from .provider import COUNTRY, WEEKDAYS, unavailable

TABLE = PROJECT_ROOT / "tests" / "fixtures" / "calendars" / "synthetic_holidays.json"


class _Invalid(ValueError):
    pass


def _load(path: Path) -> dict:
    try:
        content = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise _Invalid from exc
    if not isinstance(content, dict) or content.get("synthetic") is not True:
        raise _Invalid
    years, countries = content.get("years"), content.get("countries")
    if not isinstance(years, list) or not all(type(y) is int for y in years) or not isinstance(countries, dict):
        raise _Invalid
    for code, entry in countries.items():
        if not COUNTRY.fullmatch(str(code)) or not isinstance(entry, dict):
            raise _Invalid
        weekend, holidays = entry.get("weekend"), entry.get("holidays")
        if not isinstance(weekend, list) or not all(day in WEEKDAYS for day in weekend) or not isinstance(holidays, list):
            raise _Invalid
        for row in holidays:
            if not isinstance(row, dict) or not isinstance(row.get("name"), str) or not row["name"].strip():
                raise _Invalid
            try:
                date.fromisoformat(row.get("date"))
            except (TypeError, ValueError) as exc:
                raise _Invalid from exc
    return content


class SyntheticHolidayProvider:
    provider_id = "synthetic-holiday-table"

    def __init__(self, path: Path | None = None):
        self.path = path or TABLE

    def holidays(self, country: str, year: int) -> dict:
        source = {"provider_id": self.provider_id, "kind": "synthetic_fixture", "notice": None}
        try:
            table = _load(self.path)
        except _Invalid:
            return unavailable("source_unavailable", "table_unavailable", country, year, source, True)
        source["notice"] = table.get("notice")
        entry = table["countries"].get(country) if isinstance(country, str) else None
        if entry is None:
            return unavailable("unsupported_country", "not_in_table", country, year, source, True)
        if year not in table["years"]:
            return unavailable("unsupported_year", "not_in_table", country, year, source, True)
        rows = sorted((r for r in entry["holidays"] if r["date"].startswith(f"{year:04d}-")),
                      key=lambda r: (r["date"], r["name"]))
        return {"status": "ok", "reason": None, "country": country, "year": year,
                "holidays": [{"date": r["date"], "name": r["name"]} for r in rows],
                "regional_holidays": [], "other_types": [], "weekend": list(entry["weekend"]),
                "synthetic": True, "source": source}
