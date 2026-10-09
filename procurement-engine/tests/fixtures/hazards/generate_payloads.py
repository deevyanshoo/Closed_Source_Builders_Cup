"""Write the HAND-WRITTEN, SYNTHETIC hazard provider payloads used by the offline tests.

Shapes follow the public Open-Meteo forecast response (``hourly_units`` and ``hourly`` arrays in
GMT) and the GDACS event-list GeoJSON (``features`` with ``properties.eventtype``, ``alertlevel``,
``fromdate``, ``todate``). Every value is invented; none was fetched from a provider.

Run from procurement-engine/:  .venv/bin/python tests/fixtures/hazards/generate_payloads.py
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).parent
START = datetime(2026, 10, 8, 0, 0)
HOURS = 96


def times() -> list[str]:
    return [(START + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(HOURS)]


def open_meteo(lat: float, lon: float, wind, gusts, rain) -> dict:
    return {"latitude": lat, "longitude": lon, "generationtime_ms": 0.5, "utc_offset_seconds": 0,
            "timezone": "GMT", "timezone_abbreviation": "GMT", "elevation": 5.0,
            "hourly_units": {"time": "iso8601", "wind_speed_10m": "km/h", "wind_gusts_10m": "km/h",
                             "precipitation": "mm"},
            "hourly": {"time": times(), "wind_speed_10m": wind, "wind_gusts_10m": gusts, "precipitation": rain}}


def series(base: float, peaks: dict[int, float] | None = None) -> list[float]:
    values = [base] * HOURS
    for hour, value in (peaks or {}).items():
        values[hour] = value
    return values


def write(name: str, body: object | str) -> None:
    text = body if isinstance(body, str) else json.dumps(body, separators=(",", ":"))
    (HERE / name).write_text(text + "\n", encoding="utf-8")


def main() -> None:
    # INNSA: 3.2 mm every hour on 9 October, so 76.8 mm in 24 hours, above the 64.5 mm rule.
    rain = series(0.0, {h: 3.2 for h in range(24, 48)})
    write("open_meteo_innsa.json", open_meteo(18.875, 72.875, series(18.0, {30: 41.0}), series(30.0, {30: 58.0}), rain))
    # INMAA: everything below every threshold.
    write("open_meteo_inmaa.json", open_meteo(13.125, 80.25, series(20.0, {40: 38.0}), series(30.0, {40: 55.0}),
                                             series(0.1)))
    # AEJEA: precipitation has gaps, wind below threshold: the rain rule cannot be decided.
    gappy = series(0.0)
    gappy[20] = None
    write("open_meteo_aejea.json", open_meteo(25.0, 55.0, series(15.0), series(25.0), gappy))
    # INMUN: answers for a grid point far from the asked location.
    write("open_meteo_inmun.json", open_meteo(10.0, 69.75, series(10.0), series(20.0), series(0.0)))
    # INPRT: gale-force wind, but retrieved long before the cutoff.
    write("open_meteo_inprt.json", open_meteo(20.25, 86.625, series(30.0, {36: 70.0}), series(45.0, {36: 95.0}),
                                             series(0.5)))
    # CLVAP: a body without the hourly block.
    write("open_meteo_clvap_malformed.json", {"latitude": -33.0, "longitude": -71.625, "timezone": "GMT"})
    # BRTUB: the provider's documented error body (HTTP 400).
    write("open_meteo_error.json", {"error": True, "reason": "Synthetic error: parameter 'hourly' is invalid"})
    write("gdacs_events.json", {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [87.5, 19.5]},
         "properties": {"eventtype": "TC", "eventid": 9100001, "episodeid": 4, "name": "SYNTHETIC-CYCLONE-A",
                        "alertlevel": "Orange", "alertscore": 2, "iscurrent": "true",
                        "fromdate": "2026-10-07T00:00:00", "todate": "2026-10-10T00:00:00", "country": "India",
                        "severitydata": {"severity": 120.0, "severitytext": "Synthetic storm, 120 km/h",
                                         "severityunit": "km/h"},
                        "url": {"report": "synthetic://gdacs/report/9100001"}}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [73.1, 19.2]},
         "properties": {"eventtype": "FL", "eventid": 9100002, "episodeid": 1, "name": "SYNTHETIC-FLOOD-B",
                        "alertlevel": "Green", "alertscore": 1, "iscurrent": "true",
                        "fromdate": "2026-10-06T00:00:00", "todate": "2026-10-12T00:00:00", "country": "India",
                        "severitydata": {"severity": 0.0, "severitytext": "Synthetic minor flood",
                                         "severityunit": ""},
                        "url": {"report": "synthetic://gdacs/report/9100002"}}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [140.0, 35.0]},
         "properties": {"eventtype": "EQ", "eventid": 9100003, "episodeid": 1, "name": "SYNTHETIC-QUAKE-C",
                        "alertlevel": "Red", "alertscore": 3, "iscurrent": "true",
                        "fromdate": "2026-10-08T03:00:00", "todate": "2026-10-08T03:00:00", "country": "Japan",
                        "severitydata": {"severity": 7.1, "severitytext": "Synthetic magnitude 7.1",
                                         "severityunit": "M"},
                        "url": {"report": "synthetic://gdacs/report/9100003"}}},
    ]})
    write("gdacs_malformed.json", '{"type": "FeatureCollection", "features": [{"type": "Feature"')


if __name__ == "__main__":
    main()
