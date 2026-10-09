"""Nager.Date adapter for ``GET /api/v3/PublicHolidays/{year}/{countryCode}``.

Written against the public response shape (a list of objects with ``date``, ``localName``,
``name``, ``countryCode``, ``fixed``, ``global``, ``counties``, ``launchYear`` and ``types``) and
tested ONLY with mocked responses. It has no default transport: the caller must pass ``fetch``,
a callable ``url -> (http_status, body_bytes)``. No live request has been made or approved.

Rules
- India (IN) and the UAE (AE) are not requested: docs/API_Integration_Guide.md records that the
  live catalog omitted them, so they stay unsupported until another source is approved.
- Nager does not state weekends. The caller passes them per country; a country without one is
  unsupported rather than assumed to have a Saturday and Sunday weekend.
- Only ``global`` holidays whose ``types`` include ``Public`` remove a working day. Regional
  holidays and other observances are reported but never applied.
- An empty list or HTTP 204 is ``unsupported_year`` (not covered), never "a year without holidays".
- 404 is ``unsupported_country``; 400 is ``unsupported_year``; any other failure, malformed body
  or transport error is ``source_unavailable``.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import date

from .provider import COUNTRY, WEEKDAYS, unavailable

NOT_IN_CATALOG = frozenset({"IN", "AE"})
DEFAULT_BASE_URL = "https://date.nager.at"
_YEAR_BOUNDS = (1900, 2199)  # request sanity bound only; not a statement of Nager's coverage


class NagerDateProvider:
    provider_id = "nager.date"

    def __init__(self, *, fetch: Callable[[str], tuple[int, bytes]], weekends: Mapping[str, list[str]],
                 base_url: str = DEFAULT_BASE_URL):
        self._fetch = fetch
        self._weekends = {k: list(v) for k, v in weekends.items()}
        self._base_url = base_url.rstrip("/")

    def holidays(self, country: str, year: int) -> dict:
        url = f"{self._base_url}/api/v3/PublicHolidays/{year}/{country}"
        source = {"provider_id": self.provider_id, "kind": "public_holiday_api", "url": url}

        def fail(status: str, reason: str) -> dict:
            return unavailable(status, reason, country, year, source, False)

        if not isinstance(country, str) or not COUNTRY.fullmatch(country):
            return fail("unsupported_country", "invalid_country_code")
        if type(year) is not int or not _YEAR_BOUNDS[0] <= year <= _YEAR_BOUNDS[1]:
            return fail("unsupported_year", "year_out_of_request_bounds")
        if country in NOT_IN_CATALOG:
            return fail("unsupported_country", "not_in_nager_catalog")
        weekend = self._weekends.get(country)
        if not weekend or not all(day in WEEKDAYS for day in weekend):
            return fail("unsupported_country", "weekend_not_configured")
        try:
            status, body = self._fetch(url)
        except Exception:  # any transport failure is an unavailable source, never "no holidays"
            return fail("source_unavailable", "transport_error")
        if status == 404:
            return fail("unsupported_country", "unknown_country_code")
        if status == 400:
            return fail("unsupported_year", "rejected_by_provider")
        if status == 204:
            return fail("unsupported_year", "no_holidays_returned")
        if status != 200:
            return fail("source_unavailable", f"http_{status}")
        try:
            rows = json.loads(body)
        except (TypeError, ValueError):
            return fail("source_unavailable", "invalid_response")
        if not isinstance(rows, list):
            return fail("source_unavailable", "invalid_response")
        if not rows:
            return fail("unsupported_year", "no_holidays_returned")
        national, regional, other = [], [], []
        for row in rows:
            if not isinstance(row, dict) or row.get("countryCode") != country or \
                    not isinstance(row.get("name"), str) or not row["name"].strip() \
                    or not isinstance(row.get("global"), bool) or not isinstance(row.get("types"), list):
                return fail("source_unavailable", "invalid_response")
            try:
                day = date.fromisoformat(row.get("date"))
            except (TypeError, ValueError):
                return fail("source_unavailable", "invalid_response")
            if day.year != year:
                return fail("source_unavailable", "invalid_response")
            item = {"date": day.isoformat(), "name": row["name"]}
            if "Public" not in row["types"]:
                other.append({**item, "types": [str(t) for t in row["types"]]})
            elif not row["global"]:
                counties = row.get("counties")
                regional.append({**item, "counties": list(counties) if isinstance(counties, list) else []})
            else:
                national.append(item)
        order = lambda r: (r["date"], r["name"])  # noqa: E731
        return {"status": "ok", "reason": None, "country": country, "year": year,
                "holidays": sorted(national, key=order), "regional_holidays": sorted(regional, key=order),
                "other_types": sorted(other, key=order), "weekend": list(weekend), "synthetic": False,
                "source": source}
