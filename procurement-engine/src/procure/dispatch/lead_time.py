"""Working-day lead time for one shipment timeline.

``shipment_lead_time(timeline, provider)`` reads a ``shipment_timeline`` result:
  departure_to_arrival        arrived: last usable departure before the last usable arrival
  cutoff_to_expected_arrival  not arrived: from the cutoff (``as_of``) to the open ETA
  not_available / no_dates    neither pair of dates exists
The country is the first two letters of the UN/LOCODE at the lane destination, else at the
facility of the event that gives the end date. Anything else is ``unsupported_country``. Dates are
UTC calendar dates.
"""
from __future__ import annotations

import re
from datetime import date

from ..calendars import CONVENTION, HolidayProvider, working_days

_LOCODE = re.compile(r"^[A-Z]{2}[A-Z0-9]{3}$")
NOTE = ("Dates are UTC calendar dates, so a local date can differ by one day. Plant shifts and port cutoffs are not "
        "included.")


def _day(instant: str) -> date:
    return date.fromisoformat(instant[:10])


def _location(event: dict) -> tuple[str | None, str | None]:
    if event.get("lane"):
        code = event["lane"]["destination"]
        label = f"lane_destination:{code}"
    elif event.get("facility"):
        code = event["facility"]["code"]
        label = f"facility:{code}"
    else:
        return None, None
    return (code[:2] if _LOCODE.fullmatch(code) else None), label


def _not_available(reason: str) -> dict:
    return {"status": "not_available", "reason": reason, "basis": None, "country": None, "location": None,
            "start": None, "end": None, "working_days": None, "calendar_days": None, "weekend_days": None,
            "holidays": [], "not_applied": [], "synthetic": None, "source": None, "convention": CONVENTION,
            "note": NOTE}


def shipment_lead_time(timeline: dict, provider: HolidayProvider) -> dict:
    events = [e for e in timeline.get("events") or [] if e["operating_evidence"]]
    current = timeline.get("current") or {}
    arrivals = [e for e in events if e["event_type"] in ("arrived", "delivered")]
    if arrivals:
        end_event = max(arrivals, key=lambda e: (e["occurred_at"], e["event_id"]))
        departures = [e for e in events if e["event_type"] == "departed" and e["occurred_at"] <= end_event["occurred_at"]]
        if not departures:
            return _not_available("no_dates")
        start = _day(max(departures, key=lambda e: (e["occurred_at"], e["event_id"]))["occurred_at"])
        end, basis = _day(end_event["occurred_at"]), "departure_to_arrival"
    elif current.get("open_eta") and timeline.get("as_of"):
        end_event = next(e for e in events if e["event_id"] == current["open_eta"]["event_id"])
        start, end, basis = _day(timeline["as_of"]), _day(current["open_eta"]["eta"]), "cutoff_to_expected_arrival"
    else:
        return _not_available("no_dates")
    country, location = _location(end_event)
    result = working_days(provider, country, start, end)
    if country is None:
        result = {**result, "reason": "no_country_code"}
    return {**result, "basis": basis, "location": location, "note": NOTE}
