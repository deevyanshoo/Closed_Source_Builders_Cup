"""Hazard and weather EXPOSURE from Open-Meteo and GDACS, normalised into route-evidence records.

Offline by construction: the fetch functions have no default transport (pass ``fetch``, a callable
``url -> (http_status, body_bytes)``), return ``offline`` without calling it when ``OFFLINE=1``,
and never call it for a location the registry does not know. Tests use hand-written payloads.

Each provider answer for one location becomes an *assessment* with an explicit ``state``:
  exposure_found      a versioned threshold in config/hazard_sources_v1.json is met
  no_exposure_found   every rule was decided and none is met. This is not a statement of safety
  insufficient_data   a rule could not be decided (gaps, or the forecast does not cover the window)
  unsupported_location, offline, provider_failure, rate_limited, malformed_payload, not_fetched
  retrieved_after_as_of   rejected by ``hazard_exposure``: not available at the cutoff
``hazard_exposure`` deduplicates repeated fetches (provider, location, period, payload hash; the
earliest retrieval is kept), marks stale answers unusable but visible, and turns each met threshold
into a route-evidence record in exactly the shape ``normalize_route_evidence`` accepts: status
``exposure``, source kind ``weather_service`` or ``hazard_feed``. Measured values, units and
thresholds travel in ``details`` keyed by evidence ID. Nothing here confirms a closure, damage or
delay, and nothing changes an arrival date.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode

from ..settings import PROJECT_ROOT, get_settings
from .evidence import evidence_for_locations, normalize_route_evidence
from .locations import load_registry, resolve

THRESHOLDS_FILE = PROJECT_ROOT / "config" / "hazard_sources_v1.json"
EXPOSURE_NOTE = ("Exposure only, not confirmed: a forecast or hazard event near a facility does not prove a closure, "
                 "damage or delay, and never changes an arrival date. No exposure found is not a statement of safety.")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$")
_HOUR = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")
_GDACS_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")
_ALERT_ORDER = {"Green": 0, "Orange": 1, "Red": 2}
_WEATHER_EVENTS = {"TC", "FL", "DR", "WF"}
USABLE_STATES = {"exposure_found", "no_exposure_found"}
Fetch = Callable[[str], tuple[int, bytes]]


class _Malformed(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def load_thresholds(path: Path | None = None) -> dict:
    content = json.loads(Path(path or THRESHOLDS_FILE).read_text(encoding="utf-8"))
    if not isinstance(content, dict) or not isinstance(content.get("version"), str) or \
            not isinstance(content.get("open_meteo"), dict) or not isinstance(content.get("gdacs"), dict):
        raise ValueError("invalid hazard threshold config")
    for rule in content["open_meteo"].get("rules", []):
        Decimal(rule["threshold"])  # thresholds stay exact decimals
    return content


def _instant(text: str) -> datetime:
    if not isinstance(text, str) or not _TIMESTAMP.fullmatch(text):
        raise ValueError("instants need seconds and a time zone")
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _assessment(provider_id: str, location: dict, state: str, reason: str | None, *, retrieved_at: datetime | None,
                config: dict, synthetic: bool, request: str | None, payload: bytes | None = None) -> dict:
    return {"provider_id": provider_id, "location": location, "state": state, "reason": reason,
            "retrieved_at": None if retrieved_at is None else retrieved_at.isoformat(), "period": None,
            "values": [], "thresholds_version": config["version"],
            "payload_sha256": None if payload is None else _sha(payload), "valid_until": None, "evidence_ids": [],
            "records": [],
            "synthetic": synthetic, "request": request}


def _http_state(status: int) -> tuple[str, str] | None:
    if status == 200:
        return None
    if status == 429:
        return "rate_limited", "http_429"
    return "provider_failure", f"http_{status}"


def _loads(raw: bytes) -> object:
    try:
        return json.loads(raw, parse_float=Decimal, parse_int=Decimal)
    except (TypeError, ValueError, UnicodeDecodeError):
        raise _Malformed("invalid_json") from None


def _number(value: object) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, Decimal) or not value.is_finite():
        raise _Malformed("invalid_value")
    return value


# Open-Meteo


def open_meteo_url(location: dict, config: dict) -> str:
    cfg = config["open_meteo"]
    query = {**cfg["query"], "latitude": location["lat"], "longitude": location["lon"]}
    return f"{cfg['endpoint']}?{urlencode(sorted(query.items()))}"


def _rule(rule: dict, hours: list[datetime], series: list[Decimal | None]) -> dict:
    threshold = Decimal(rule["threshold"])
    row = {"rule_id": rule["rule_id"], "variable": rule["variable"], "aggregate": rule["aggregate"], "value": None,
           "unit": rule["unit"], "threshold": rule["threshold"], "met": None, "at": None, "reason": None,
           "basis": rule["basis"]}
    if rule["aggregate"] == "max_hourly":
        candidates = [(v, t) for v, t in zip(series, hours) if v is not None]
        gaps = len(candidates) != len(series)
    elif rule["aggregate"] == "max_rolling_sum_24h":
        if len(series) < 24:
            return {**row, "reason": "window_shorter_than_24_hours"}
        windows = [series[i:i + 24] for i in range(len(series) - 23)]
        candidates = [(sum(w, Decimal(0)), hours[i]) for i, w in enumerate(windows) if None not in w]
        gaps = len(candidates) != len(windows)
    else:
        return {**row, "reason": "unsupported_aggregate"}
    if candidates:
        value, at = max(candidates, key=lambda c: (c[0], -c[1].timestamp()))
        row.update(value=str(value), at=at.isoformat())
        if value >= threshold:
            return {**row, "met": True}
    if gaps:
        return {**row, "reason": "missing_values"}
    return {**row, "met": False}


def assess_open_meteo(code: str, *, status: int, body: bytes, retrieved_at: str, as_of: str, registry: dict,
                      config: dict, synthetic: bool = False) -> dict:
    location = resolve(registry, code)
    retrieved, cutoff = _instant(retrieved_at), _instant(as_of)
    cfg = config["open_meteo"]
    if location["status"] != "ok":
        return _assessment(cfg["provider_id"], location, "unsupported_location", location["reason"],
                           retrieved_at=None, config=config, synthetic=synthetic, request=None)
    request = open_meteo_url(location, config)
    base = dict(retrieved_at=retrieved, config=config, synthetic=synthetic, request=request)
    if retrieved > cutoff:
        return _assessment(cfg["provider_id"], location, "retrieved_after_as_of", None, payload=body, **base)
    failed = _http_state(status)
    if failed:
        return _assessment(cfg["provider_id"], location, *failed, **base)
    out = _assessment(cfg["provider_id"], location, "malformed_payload", None, payload=body, **base)
    try:
        payload = _loads(body)
        if not isinstance(payload, dict) or not isinstance(payload.get("hourly"), dict) or \
                not isinstance(payload.get("hourly_units"), dict):
            raise _Malformed("missing_hourly")
        if payload.get("utc_offset_seconds") != 0:
            raise _Malformed("not_utc")
        offset = Decimal(cfg["max_grid_offset_degrees"])
        lat, lon = _number(payload.get("latitude")), _number(payload.get("longitude"))
        if lat is None or lon is None or abs(lat - Decimal(location["lat"])) > offset or \
                abs(lon - Decimal(location["lon"])) > offset:
            raise _Malformed("location_mismatch")
        hourly, units = payload["hourly"], payload["hourly_units"]
        times = hourly.get("time")
        if not isinstance(times, list) or not times or not all(isinstance(t, str) and _HOUR.fullmatch(t) for t in times):
            raise _Malformed("invalid_times")
        stamps = [datetime.fromisoformat(t).replace(tzinfo=timezone.utc) for t in times]
        if any(b - a != timedelta(hours=1) for a, b in zip(stamps, stamps[1:])):
            raise _Malformed("irregular_times")
        columns = {}
        for rule in cfg["rules"]:
            column = hourly.get(rule["variable"])
            if not isinstance(column, list) or len(column) != len(times):
                raise _Malformed("missing_variable")
            if units.get(rule["variable"]) != rule["unit"]:
                raise _Malformed("unexpected_unit")
            columns[rule["variable"]] = [_number(v) for v in column]
    except _Malformed as exc:
        return {**out, "reason": exc.reason}
    end = cutoff + timedelta(hours=cfg["window_hours"])
    index = [i for i, t in enumerate(stamps) if cutoff <= t < end]
    if not index:
        return {**out, "state": "insufficient_data", "reason": "forecast_does_not_cover_window"}
    hours = [stamps[i] for i in index]
    values = [_rule(rule, hours, [columns[rule["variable"]][i] for i in index]) for rule in cfg["rules"]]
    met = [v for v in values if v["met"]]
    complete_window = (len(hours) == cfg["window_hours"] and hours[0] == cutoff
                       and hours[-1] + timedelta(hours=1) == end)
    undecided_rule = any(v["met"] is None for v in values)
    state = "exposure_found" if met else ("insufficient_data" if not complete_window or undecided_rule
                                          else "no_exposure_found")
    reason = (("forecast_does_not_cover_window" if not complete_window else "rule_undecided")
              if state == "insufficient_data" else None)
    out.update(state=state, reason=reason, values=values,
               period={"start": hours[0].isoformat(), "end": (hours[-1] + timedelta(hours=1)).isoformat()},
               valid_until=(retrieved + timedelta(hours=cfg["valid_hours"])).isoformat())
    if met:
        evidence_id = f"haz-open-meteo-{code}-{out['payload_sha256'][:12]}"
        facts = "; ".join(f"{v['rule_id']} {v['value']} {v['unit']} >= {v['threshold']} {v['unit']}" for v in met)
        out["evidence_ids"] = [evidence_id]
        out["records"] = [{
            "synthetic": synthetic, "evidence_id": evidence_id, "kind": "weather_hazard",
            "summary": f"Open-Meteo forecast at {location['name']}: {facts} ({config['version']}). "
                       "Exposure only, not a confirmed closure or delay.",
            "facility": {"kind": location["kind"], "code": code, "name": location["name"]},
            "source": {"provider_id": cfg["provider_id"], "kind": "weather_service", "reference": request},
            "retrieved_at": retrieved.isoformat(), "observed_period": out["period"], "status": "exposure",
            "valid_until": out["valid_until"], "content_sha256": out["payload_sha256"]}]
    return out


def _offline_or_unsupported(provider_id: str, location: dict, config: dict, synthetic: bool) -> dict | None:
    if location["status"] != "ok":
        return _assessment(provider_id, location, "unsupported_location", location["reason"], retrieved_at=None,
                           config=config, synthetic=synthetic, request=None)
    if get_settings().offline:
        return _assessment(provider_id, location, "offline", "offline_mode", retrieved_at=None, config=config,
                           synthetic=synthetic, request=None)
    return None


def _call(fetch: Fetch, url: str) -> tuple[int, bytes] | str:
    try:
        return fetch(url)
    except TimeoutError:
        return "timeout"
    except Exception:  # any transport failure is an explicit provider failure, never "no exposure"
        return "transport_error"


def fetch_open_meteo(code: str, *, fetch: Fetch, retrieved_at: str, as_of: str, registry: dict | None = None,
                     config: dict | None = None, synthetic: bool = False) -> dict:
    registry, config = registry or load_registry(), config or load_thresholds()
    location = resolve(registry, code)
    stop = _offline_or_unsupported(config["open_meteo"]["provider_id"], location, config, synthetic)
    if stop:
        return stop
    url = open_meteo_url(location, config)
    answer = _call(fetch, url)
    if isinstance(answer, str):
        return _assessment(config["open_meteo"]["provider_id"], location, "provider_failure", answer,
                           retrieved_at=_instant(retrieved_at), config=config, synthetic=synthetic, request=url)
    return assess_open_meteo(code, status=answer[0], body=answer[1], retrieved_at=retrieved_at, as_of=as_of,
                             registry=registry, config=config, synthetic=synthetic)


# GDACS


def _distance_km(lat1: Decimal, lon1: Decimal, lat2: Decimal, lon2: Decimal) -> Decimal:
    p1, p2 = math.radians(float(lat1)), math.radians(float(lat2))
    dp, dl = p2 - p1, math.radians(float(lon2) - float(lon1))
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return Decimal(round(2 * 6371.0088 * math.asin(math.sqrt(h))))


def _gdacs_time(text: object) -> datetime:
    if not isinstance(text, str) or not _GDACS_TIME.fullmatch(text):
        raise _Malformed("invalid_event_time")
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)  # GDACS dates are UTC without an offset


def _gdacs_events(raw: bytes) -> list[dict]:
    payload = _loads(raw)
    if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection" or \
            not isinstance(payload.get("features"), list):
        raise _Malformed("not_a_feature_collection")
    events = []
    for feature in payload["features"]:
        props = feature.get("properties") if isinstance(feature, dict) else None
        geometry = feature.get("geometry") if isinstance(feature, dict) else None
        if not isinstance(props, dict) or not isinstance(geometry, dict) or geometry.get("type") != "Point":
            raise _Malformed("invalid_feature")
        coords = geometry.get("coordinates")
        if not isinstance(coords, list) or len(coords) < 2:
            raise _Malformed("invalid_feature")
        lon, lat = _number(coords[0]), _number(coords[1])
        if lon is None or lat is None or abs(lat) > 90 or abs(lon) > 180:
            raise _Malformed("invalid_feature")
        if props.get("alertlevel") not in _ALERT_ORDER or not isinstance(props.get("eventtype"), str):
            raise _Malformed("invalid_feature")
        ids = [props.get("eventid"), props.get("episodeid")]
        if not all(isinstance(i, Decimal) and i == i.to_integral_value() for i in ids):
            raise _Malformed("invalid_feature")
        start, end = _gdacs_time(props.get("fromdate")), _gdacs_time(props.get("todate"))
        if end < start:
            raise _Malformed("invalid_event_time")
        severity = props.get("severitydata") if isinstance(props.get("severitydata"), dict) else {}
        name = props.get("name") if isinstance(props.get("name"), str) else None
        events.append({"event_id": str(int(ids[0])), "episode_id": str(int(ids[1])), "event_type": props["eventtype"],
                       "alert_level": props["alertlevel"], "lat": lat, "lon": lon, "start": start, "end": end,
                       "name": None if name is None else name[:200],
                       "severity": {"value": None if not isinstance(severity.get("severity"), Decimal)
                                    else str(severity["severity"]),
                                    "unit": severity.get("severityunit") if isinstance(severity.get("severityunit"), str)
                                    else None}})
    return events


def assess_gdacs(codes: list[str], *, status: int, body: bytes, retrieved_at: str, as_of: str, registry: dict,
                 config: dict, synthetic: bool = False) -> list[dict]:
    cfg = config["gdacs"]
    retrieved, cutoff = _instant(retrieved_at), _instant(as_of)
    radius, levels = Decimal(cfg["radius_km"]), set(cfg["alert_levels"])
    threshold = f"{' or '.join(cfg['alert_levels'])} within {cfg['radius_km']} km"
    failed, events, malformed = _http_state(status), None, None
    if not failed:
        try:
            events = _gdacs_events(body)
        except _Malformed as exc:
            malformed = exc.reason
    out = []
    for code in sorted(set(codes)):
        location = resolve(registry, code)
        if location["status"] != "ok":
            out.append(_assessment(cfg["provider_id"], location, "unsupported_location", location["reason"],
                                   retrieved_at=None, config=config, synthetic=synthetic, request=None))
            continue
        base = dict(retrieved_at=retrieved, config=config, synthetic=synthetic, request=cfg["endpoint"])
        if retrieved > cutoff:
            out.append(_assessment(cfg["provider_id"], location, "retrieved_after_as_of", None, payload=body, **base))
            continue
        if failed:
            out.append(_assessment(cfg["provider_id"], location, *failed, **base))
            continue
        item = _assessment(cfg["provider_id"], location, "malformed_payload", malformed, payload=body, **base)
        if malformed:
            out.append(item)
            continue
        values, beyond = [], 0
        for event in sorted(events, key=lambda e: (e["event_id"], e["episode_id"])):
            distance = _distance_km(Decimal(location["lat"]), Decimal(location["lon"]), event["lat"], event["lon"])
            if distance > radius:
                beyond += 1
                continue
            reason = None
            if event["alert_level"] not in levels:
                reason = "alert_level_below_threshold"
            elif event["end"] < cutoff:
                reason = "ended_before_as_of"
            values.append({"rule_id": "gdacs_alert_within_radius", "event_id": event["event_id"],
                           "episode_id": event["episode_id"], "event_type": event["event_type"],
                           "alert_level": event["alert_level"], "name": event["name"], "severity": event["severity"],
                           "distance_km": str(distance), "unit": "km", "threshold": threshold, "met": reason is None,
                           "reason": reason, "period": {"start": event["start"].isoformat(),
                                                        "end": event["end"].isoformat()}})
        met = [v for v in values if v["met"]]
        item.update(state="exposure_found" if met else "no_exposure_found", reason=None, values=values,
                    events_beyond_radius=beyond,
                    valid_until=(retrieved + timedelta(hours=cfg["valid_hours"])).isoformat())
        for v in met:
            evidence_id = f"haz-gdacs-{code}-{v['event_id']}-{v['episode_id']}"
            item["evidence_ids"].append(evidence_id)
            item["records"].append({
                "synthetic": synthetic, "evidence_id": evidence_id,
                "kind": "weather_hazard" if v["event_type"] in _WEATHER_EVENTS else "route_hazard",
                "summary": f"GDACS {v['alert_level']} {v['event_type']} alert {v['distance_km']} km from "
                           f"{location['name']} ({threshold}, {config['version']}). Exposure only, not a confirmed "
                           "closure or delay.",
                "facility": {"kind": location["kind"], "code": code, "name": location["name"]},
                "source": {"provider_id": cfg["provider_id"], "kind": "hazard_feed",
                           "reference": f"GDACS {v['event_type']} {v['event_id']} episode {v['episode_id']}"},
                "retrieved_at": retrieved.isoformat(), "observed_period": v["period"], "status": "exposure",
                "valid_until": item["valid_until"], "content_sha256": item["payload_sha256"]})
        out.append(item)
    return out


def fetch_gdacs(codes: list[str], *, fetch: Fetch, retrieved_at: str, as_of: str, registry: dict | None = None,
                config: dict | None = None, synthetic: bool = False) -> list[dict]:
    registry, config = registry or load_registry(), config or load_thresholds()
    cfg = config["gdacs"]
    stops = {code: _offline_or_unsupported(cfg["provider_id"], resolve(registry, code), config, synthetic)
             for code in sorted(set(codes))}
    live = [code for code, stop in stops.items() if stop is None]
    out = [stop for stop in stops.values() if stop is not None]
    if live:
        answer = _call(fetch, cfg["endpoint"])
        if isinstance(answer, str):
            out += [_assessment(cfg["provider_id"], resolve(registry, code), "provider_failure", answer,
                                retrieved_at=_instant(retrieved_at), config=config, synthetic=synthetic,
                                request=cfg["endpoint"]) for code in live]
        else:
            out += assess_gdacs(live, status=answer[0], body=answer[1], retrieved_at=retrieved_at, as_of=as_of,
                                registry=registry, config=config, synthetic=synthetic)
    return sorted(out, key=lambda a: a["location"]["code"])


# Normalisation into route evidence


def _key(a: dict) -> tuple:
    return (a["provider_id"], str(a["location"]["code"]), a["retrieved_at"] or "", a["state"])


def hazard_exposure(assessments: list[dict], *, as_of: str) -> dict:
    cutoff = _instant(as_of)
    config_version = {a["thresholds_version"] for a in assessments}
    items = [copy.deepcopy(a) for a in sorted(assessments, key=_key)]
    rejected = [a for a in items if a["state"] == "retrieved_after_as_of"]
    kept, groups = [], {}
    for a in items:
        if a["state"] == "retrieved_after_as_of":
            continue
        if a["payload_sha256"] is None:
            kept.append(a)
            continue
        identity = (a["provider_id"], a["location"]["code"], json.dumps(a["period"], sort_keys=True),
                    a["payload_sha256"], a["state"])
        groups.setdefault(identity, []).append(a)
    duplicates = []
    for copies in groups.values():
        copies.sort(key=lambda a: a["retrieved_at"])
        kept.append(copies[0])
        if len(copies) > 1:
            duplicates.append({"provider_id": copies[0]["provider_id"], "location": copies[0]["location"]["code"],
                               "kept_retrieved_at": copies[0]["retrieved_at"],
                               "dropped_retrieved_at": [c["retrieved_at"] for c in copies[1:]]})
    records, details = [], {}
    for a in kept:
        valid = None if a["valid_until"] is None else _instant(a["valid_until"])
        a["freshness"] = None if valid is None else ("stale" if valid < cutoff else "current")
        a["usable"] = a["state"] in USABLE_STATES and a["freshness"] == "current"
        for r in a["records"]:
            records.append(r)
            details[r["evidence_id"]] = {"provider_id": a["provider_id"], "location": a["location"]["code"],
                                         "values": a["values"], "thresholds_version": a["thresholds_version"],
                                         "payload_sha256": a["payload_sha256"]}
    for a in [*kept, *rejected]:
        a.pop("records", None)
    kept.sort(key=_key)
    records.sort(key=lambda r: r["evidence_id"])
    duplicates.sort(key=lambda d: (d["provider_id"], d["location"]))
    return {"as_of": cutoff.isoformat(), "thresholds_version": sorted(config_version)[0] if config_version else None,
            "assessments": kept, "rejected": rejected, "duplicates": duplicates, "records": records,
            "route_evidence": normalize_route_evidence(records, as_of=as_of), "details": details,
            "note": EXPOSURE_NOTE}


def hazards_for_locations(result: dict, location_keys: list[str]) -> dict:
    wanted = set(location_keys)
    rows = [a for a in result["assessments"] if a["location"]["code"] in wanted]
    evidence = evidence_for_locations(result["route_evidence"], sorted(wanted))
    return {"status": "ok" if rows else "empty", "reasons": [] if rows else ["no_assessments"], "as_of": result["as_of"],
            "thresholds_version": result["thresholds_version"], "location_keys": sorted(wanted),
            "assessments": rows, "evidence": evidence,
            "details": {k: v for k, v in result["details"].items() if v["location"] in wanted},
            "note": EXPOSURE_NOTE}


def recorded_assessments(manifest: Path, *, as_of: str, location_codes: list[str], registry: dict | None = None,
                         config: dict | None = None) -> list[dict]:
    """Assess recorded (here: hand-written synthetic) responses for every requested location."""
    data = json.loads(Path(manifest).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("synthetic") is not True:
        raise ValueError("recorded responses must be labelled synthetic")
    registry, config = registry or load_registry(), config or load_thresholds()
    folder, synthetic = Path(manifest).parent, True
    codes = sorted(set(location_codes))

    def raw(row: dict) -> bytes:
        return b"" if row.get("file") is None else (folder / row["file"]).read_bytes()

    out = []
    for code in codes:
        rows = [r for r in data["responses"] if r["provider"] == "open-meteo" and r["location"] == code]
        location = resolve(registry, code)
        if rows:
            out += [assess_open_meteo(code, status=r["http_status"], body=raw(r), retrieved_at=r["retrieved_at"],
                                      as_of=as_of, registry=registry, config=config, synthetic=synthetic)
                    for r in rows]
        elif location["status"] != "ok":
            out.append(_assessment("open-meteo", location, "unsupported_location", location["reason"],
                                   retrieved_at=None, config=config, synthetic=synthetic, request=None))
        else:
            out.append(_assessment("open-meteo", location, "not_fetched", "no_recorded_response", retrieved_at=None,
                                   config=config, synthetic=synthetic, request=None))
    gdacs_rows = [r for r in data["responses"] if r["provider"] == "gdacs"]
    for r in gdacs_rows:
        out += assess_gdacs(codes, status=r["http_status"], body=raw(r), retrieved_at=r["retrieved_at"], as_of=as_of,
                            registry=registry, config=config, synthetic=synthetic)
    if not gdacs_rows:
        out += [_assessment("gdacs", resolve(registry, c), "not_fetched", "no_recorded_response", retrieved_at=None,
                            config=config, synthetic=synthetic, request=None) for c in codes]
    return out
