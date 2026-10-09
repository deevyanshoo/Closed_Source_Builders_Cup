"""Holiday provider interface and working-day lead time.

A provider answers ``holidays(country, year)`` with a mapping:
  status             ok, unsupported_country, unsupported_year or source_unavailable
  reason             why, when the status is not ok (otherwise None)
  country, year      as asked
  holidays           national public holidays that remove a working day: [{date, name}]
  regional_holidays  holidays for part of the country only; never applied, because the facility's
                     region is not known: [{date, name, counties}]
  other_types        observances that are not public holidays (bank, school, optional); never applied
  weekend            weekday names that are not working days, or None when not ok
  synthetic          True for the bundled synthetic table
  source             provider_id, kind and notice or URL

Nothing is guessed: a country, year or weekend the provider does not state is unsupported, and a
provider failure is ``source_unavailable``, never "no holidays".
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Protocol

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
COUNTRY = re.compile(r"^[A-Z]{2}$")
CONVENTION = ("Counts the days after the start date up to and including the end date, less weekend days and "
              "national public holidays. Regional holidays and other observances are listed, not applied.")


class HolidayProvider(Protocol):
    provider_id: str

    def holidays(self, country: str, year: int) -> dict: ...


def unavailable(status: str, reason: str, country: object, year: object, source: dict, synthetic: bool) -> dict:
    return {"status": status, "reason": reason, "country": country, "year": year, "holidays": [],
            "regional_holidays": [], "other_types": [], "weekend": None, "synthetic": synthetic, "source": source}


def _empty(status: str, reason: str | None, country: object, start: date, end: date, **extra) -> dict:
    return {"status": status, "reason": reason, "country": country, "year": None, "start": start.isoformat(),
            "end": end.isoformat(), "working_days": None, "calendar_days": None, "weekend_days": None,
            "holidays": [], "not_applied": [], "synthetic": None, "source": None, "convention": CONVENTION, **extra}


def working_days(provider: HolidayProvider, country: object, start: date, end: date) -> dict:
    """Working days in (start, end] for one country, from the provider's calendar."""
    if not isinstance(country, str) or not COUNTRY.fullmatch(country):
        return _empty("unsupported_country", "invalid_country_code", country, start, end)
    if end < start:
        return _empty("invalid_range", "end_before_start", country, start, end)
    calendars = []
    for year in range(start.year, end.year + 1):
        result = provider.holidays(country, year)
        if result.get("status") != "ok":
            return {**_empty(result.get("status", "source_unavailable"), result.get("reason"), country, start, end),
                    "year": year, "synthetic": result.get("synthetic"), "source": result.get("source")}
        calendars.append(result)
    weekends = {tuple(c["weekend"]) for c in calendars}
    if len(weekends) != 1:
        return _empty("source_unavailable", "inconsistent_weekend", country, start, end)
    weekend = {WEEKDAYS.index(day) for day in calendars[0]["weekend"]}
    national = {h["date"]: h["name"] for c in calendars for h in c["holidays"]}
    skipped = [{"date": h["date"], "name": h["name"], "reason": "regional_holiday"}
               for c in calendars for h in c["regional_holidays"]]
    skipped += [{"date": h["date"], "name": h["name"], "reason": "not_a_public_holiday"}
                for c in calendars for h in c["other_types"]]
    working, weekend_days, used = 0, 0, []
    day = start
    while day < end:
        day += timedelta(days=1)
        iso = day.isoformat()
        if day.weekday() in weekend:
            weekend_days += 1
        elif iso in national:
            used.append({"date": iso, "name": national[iso]})
        else:
            working += 1
    in_range = [s for s in skipped if start.isoformat() < s["date"] <= end.isoformat()]
    return {"status": "ok", "reason": None, "country": country, "year": None, "start": start.isoformat(),
            "end": end.isoformat(), "working_days": working, "calendar_days": (end - start).days,
            "weekend_days": weekend_days, "holidays": used,
            "not_applied": sorted(in_range, key=lambda s: (s["date"], s["name"])),
            "synthetic": any(c["synthetic"] for c in calendars), "source": calendars[0]["source"],
            "convention": CONVENTION}
