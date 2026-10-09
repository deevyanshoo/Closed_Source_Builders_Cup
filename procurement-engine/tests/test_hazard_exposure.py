"""Hazard and weather exposure from Open-Meteo and GDACS, offline.

Every payload is hand-written in the provider's public response shape (tests/fixtures/hazards).
No test makes a network request: the adapters have no default transport, and the fetch tests pass
a recording stub.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from procure.route_evidence import evidence_for_locations, normalize_route_evidence
from procure.route_evidence.hazards import (
    EXPOSURE_NOTE, assess_gdacs, assess_open_meteo, fetch_gdacs, fetch_open_meteo, hazard_exposure,
    hazards_for_locations, load_thresholds, open_meteo_url, recorded_assessments,
)
from procure.route_evidence.locations import load_registry, resolve
from procure.settings import reset_caches

HERE = Path(__file__).parent / "fixtures" / "hazards"
AS_OF = "2026-10-08T12:00:00Z"
REGISTRY = load_registry()
CONFIG = load_thresholds()


def body(name: str) -> bytes:
    return (HERE / name).read_bytes()


def om(code, name, *, status=200, retrieved="2026-10-08T09:00:00Z", as_of=AS_OF, raw=None):
    return assess_open_meteo(code, status=status, body=body(name) if raw is None else raw, retrieved_at=retrieved,
                             as_of=as_of, registry=REGISTRY, config=CONFIG, synthetic=True)


def gd(codes, *, status=200, name="gdacs_events.json", retrieved="2026-10-08T08:00:00Z"):
    return assess_gdacs(codes, status=status, body=body(name), retrieved_at=retrieved, as_of=AS_OF,
                        registry=REGISTRY, config=CONFIG, synthetic=True)


class Recorder:
    def __init__(self, status=200, raw=b"{}", error=None):
        self.status, self.raw, self.error, self.urls = status, raw, error, []

    def __call__(self, url):
        self.urls.append(url)
        if self.error:
            raise self.error
        return self.status, self.raw


# Location registry


def test_registry_is_versioned_and_says_coordinates_are_unverified():
    assert REGISTRY["version"] == "locations-v1"
    found = resolve(REGISTRY, "INNSA")
    assert found == {"status": "ok", "reason": None, "code": "INNSA", "kind": "port", "name": "Nhava Sheva (JNPT)",
                     "lat": "18.95", "lon": "72.95", "source": "hand-entered approximate port location",
                     "verified": False, "registry_version": "locations-v1"}


@pytest.mark.parametrize("code,reason", [("SYN-PLANT-CHAKAN", "not_in_registry"), ("XXZZZ", "not_in_registry"),
                                         ("innsa", "not_in_registry"), (None, "invalid_code")])
def test_unknown_codes_stay_unsupported(code, reason):
    r = resolve(REGISTRY, code)
    assert (r["status"], r["reason"], r["lat"], r["lon"]) == ("unsupported", reason, None, None)


def test_bad_registry_file_is_refused(tmp_path):
    bad = json.loads((Path(__file__).parents[1] / "config" / "locations_v1.json").read_text())
    bad["locations"][0]["lat"] = "95.0"
    path = tmp_path / "locations.json"
    path.write_text(json.dumps(bad))
    with pytest.raises(ValueError):
        load_registry(path)


# Open-Meteo


def test_heavy_rain_is_exposure_with_values_units_and_threshold():
    a = om("INNSA", "open_meteo_innsa.json")
    assert (a["state"], a["provider_id"], a["thresholds_version"]) == ("exposure_found", "open-meteo",
                                                                         "hazard-thresholds-v1")
    rain = next(v for v in a["values"] if v["rule_id"] == "rain_heavy_24h")
    assert (rain["value"], rain["unit"], rain["threshold"], rain["met"]) == ("76.8", "mm", "64.5", True)
    assert rain["at"] == "2026-10-09T00:00:00+00:00"
    wind = next(v for v in a["values"] if v["rule_id"] == "wind_gale")
    assert (wind["value"], wind["met"]) == ("41.0", False)
    assert a["period"] == {"start": "2026-10-08T12:00:00+00:00", "end": "2026-10-11T12:00:00+00:00"}
    assert a["payload_sha256"] == hashlib.sha256(body("open_meteo_innsa.json")).hexdigest()
    assert a["location"]["registry_version"] == "locations-v1"


def test_exposure_becomes_a_valid_route_evidence_record_that_is_never_confirmed():
    out = hazard_exposure([om("INNSA", "open_meteo_innsa.json")], as_of=AS_OF)
    assert out["route_evidence"]["rejected"] == []
    [record] = out["route_evidence"]["evidence"]
    assert (record["status"], record["operating_status"], record["kind"]) == ("exposure", None, "weather_hazard")
    assert record["source"] == {"provider_id": "open-meteo", "kind": "weather_service",
                                "reference": open_meteo_url(resolve(REGISTRY, "INNSA"), CONFIG)}
    assert record["facility"] == {"kind": "port", "code": "INNSA", "name": "Nhava Sheva (JNPT)"}
    assert record["valid_until"] == "2026-10-08T15:00:00+00:00"
    assert record["content_sha256"] == hashlib.sha256(body("open_meteo_innsa.json")).hexdigest()
    assert "76.8 mm" in record["summary"] and "64.5 mm" in record["summary"]
    assert "not a confirmed" in record["summary"]
    assert out["details"][record["evidence_id"]]["values"][2]["rule_id"] == "rain_heavy_24h"
    found = evidence_for_locations(out["route_evidence"], ["INNSA"])
    assert [e["evidence_id"] for e in found["exposure"]] == [record["evidence_id"]] and found["confirmed"] == []


def test_output_never_carries_arrival_dates_or_confirmation():
    text = json.dumps(hazard_exposure(recorded_assessments(HERE / "recorded.json", as_of=AS_OF,
                                                           location_codes=["INNSA", "INPRT"]), as_of=AS_OF))
    for word in ('"eta"', '"arrival_at"', '"confirmed"', '"operating_status": "'):
        assert word not in text.replace('"confirmed": []', "")
    assert EXPOSURE_NOTE.startswith("Exposure only")


def test_below_threshold_is_no_exposure_found_not_safe():
    a = om("INMAA", "open_meteo_inmaa.json")
    assert a["state"] == "no_exposure_found"
    assert all(v["met"] is False for v in a["values"])
    assert "safe" not in json.dumps(a).lower()
    out = hazard_exposure([a], as_of=AS_OF)
    assert out["route_evidence"]["evidence"] == []
    assert out["assessments"][0]["usable"] is True


def test_below_threshold_short_forecast_does_not_clear_full_window():
    payload = json.loads(body("open_meteo_inmaa.json"))
    payload["hourly"] = {key: values[12:36] for key, values in payload["hourly"].items()}
    assessment = om("INMAA", "", raw=json.dumps(payload).encode())
    assert (assessment["state"], assessment["reason"]) == ("insufficient_data", "forecast_does_not_cover_window")
    assert assessment["period"] == {"start": "2026-10-08T12:00:00+00:00",
                                    "end": "2026-10-09T12:00:00+00:00"}
    assert hazard_exposure([assessment], as_of=AS_OF)["assessments"][0]["usable"] is False


def test_short_forecast_still_reports_measured_exposure():
    payload = json.loads(body("open_meteo_innsa.json"))
    payload["hourly"] = {key: values[12:36] for key, values in payload["hourly"].items()}
    payload["hourly"]["wind_speed_10m"][0] = 62
    assessment = om("INNSA", "", raw=json.dumps(payload).encode())
    assert assessment["state"] == "exposure_found"


def test_hourly_forecast_cannot_clear_partial_start_hour():
    assessment = om("INMAA", "open_meteo_inmaa.json", as_of="2026-10-08T12:30:00Z")
    assert (assessment["state"], assessment["reason"]) == ("insufficient_data", "forecast_does_not_cover_window")


def test_gaps_make_a_rule_undecided():
    a = om("AEJEA", "open_meteo_aejea.json")
    assert a["state"] == "insufficient_data"
    rain = next(v for v in a["values"] if v["rule_id"] == "rain_heavy_24h")
    assert rain["met"] is None and rain["reason"] == "missing_values"


@pytest.mark.parametrize("code,name,status,state,reason", [
    ("INMUN", "open_meteo_inmun.json", 200, "malformed_payload", "location_mismatch"),
    ("CLVAP", "open_meteo_clvap_malformed.json", 200, "malformed_payload", "missing_hourly"),
    ("BRTUB", "open_meteo_error.json", 400, "provider_failure", "http_400"),
    ("CNSHA", "open_meteo_error.json", 429, "rate_limited", "http_429"),
])
def test_failures_are_explicit(code, name, status, state, reason):
    a = om(code, name, status=status)
    assert (a["state"], a["reason"], a["values"], a["evidence_ids"]) == (state, reason, [], [])


def test_non_json_and_wrong_units_are_malformed():
    assert om("INNSA", "", raw=b"<html>busy</html>")["reason"] == "invalid_json"
    payload = json.loads(body("open_meteo_innsa.json"))
    payload["hourly_units"]["precipitation"] = "inch"
    assert om("INNSA", "", raw=json.dumps(payload).encode())["reason"] == "unexpected_unit"


def test_retrieved_after_as_of_is_rejected():
    a = om("INNSA", "open_meteo_innsa.json", retrieved="2026-10-08T13:00:00Z")
    out = hazard_exposure([a], as_of=AS_OF)
    assert out["assessments"] == []
    assert [(r["location"]["code"], r["state"]) for r in out["rejected"]] == [("INNSA", "retrieved_after_as_of")]
    assert out["route_evidence"]["evidence"] == []


def test_stale_exposure_stays_visible_but_unusable():
    out = hazard_exposure([om("INPRT", "open_meteo_inprt.json", retrieved="2026-10-07T00:00:00Z")], as_of=AS_OF)
    [a] = out["assessments"]
    assert (a["state"], a["freshness"], a["usable"]) == ("exposure_found", "stale", False)
    found = evidence_for_locations(out["route_evidence"], ["INPRT"])
    assert found["exposure"] == [] and len(found["expired"]) == 1


def test_repeated_fetches_are_deduplicated_keeping_the_earliest():
    first = om("INNSA", "open_meteo_innsa.json", retrieved="2026-10-08T09:00:00Z")
    again = om("INNSA", "open_meteo_innsa.json", retrieved="2026-10-08T10:00:00Z")
    out = hazard_exposure([again, first], as_of=AS_OF)
    assert [a["retrieved_at"] for a in out["assessments"]] == ["2026-10-08T09:00:00+00:00"]
    assert out["duplicates"] == [{"provider_id": "open-meteo", "location": "INNSA",
                                  "kept_retrieved_at": "2026-10-08T09:00:00+00:00",
                                  "dropped_retrieved_at": ["2026-10-08T10:00:00+00:00"]}]
    assert len(out["route_evidence"]["evidence"]) == 1


def test_result_is_deterministic_and_inputs_are_not_mutated():
    items = [om("INNSA", "open_meteo_innsa.json"), om("INMAA", "open_meteo_inmaa.json"), *gd(["INPRT", "INNSA"])]
    before = copy.deepcopy(items)
    assert json.dumps(hazard_exposure(items, as_of=AS_OF), sort_keys=True) == \
        json.dumps(hazard_exposure(list(reversed(items)), as_of=AS_OF), sort_keys=True)
    assert items == before


# Fetch layer: no default transport, OFFLINE and unsupported locations never call out


def test_fetch_needs_an_explicit_transport():
    with pytest.raises(TypeError):
        fetch_open_meteo("INNSA", retrieved_at=AS_OF, as_of=AS_OF)  # type: ignore[call-arg]


def test_fetch_builds_the_documented_request_and_assesses_it(monkeypatch):
    rec = Recorder(200, body("open_meteo_innsa.json"))
    monkeypatch.setenv("OFFLINE", "0")  # the stub transport is local; conftest sets OFFLINE=1 for the suite
    reset_caches()
    try:
        a = fetch_open_meteo("INNSA", fetch=rec, retrieved_at="2026-10-08T09:00:00Z", as_of=AS_OF, synthetic=True)
    finally:
        reset_caches()
    assert rec.urls == ["https://api.open-meteo.com/v1/forecast?forecast_days=4&hourly=wind_speed_10m%2Cwind_gusts_10m"
                        "%2Cprecipitation&latitude=18.95&longitude=72.95&precipitation_unit=mm&timezone=GMT"
                        "&wind_speed_unit=kmh"]
    assert a["state"] == "exposure_found"


def test_offline_and_unsupported_never_call_the_transport(monkeypatch):
    rec = Recorder()
    assert fetch_open_meteo("SYN-PLANT-CHAKAN", fetch=rec, retrieved_at=AS_OF, as_of=AS_OF)["state"] == \
        "unsupported_location"
    assert fetch_gdacs(["SYN-PLANT-CHAKAN"], fetch=rec, retrieved_at=AS_OF, as_of=AS_OF)[0]["state"] == \
        "unsupported_location"
    monkeypatch.setenv("OFFLINE", "1")
    reset_caches()
    try:
        assert fetch_open_meteo("INNSA", fetch=rec, retrieved_at=AS_OF, as_of=AS_OF)["state"] == "offline"
        assert [a["state"] for a in fetch_gdacs(["INNSA"], fetch=rec, retrieved_at=AS_OF, as_of=AS_OF)] == ["offline"]
    finally:
        reset_caches()
    assert rec.urls == []


@pytest.mark.parametrize("error,reason", [(TimeoutError("slow"), "timeout"), (OSError("down"), "transport_error")])
def test_transport_errors_are_provider_failures(monkeypatch, error, reason):
    monkeypatch.setenv("OFFLINE", "0")
    reset_caches()
    try:
        a = fetch_open_meteo("INNSA", fetch=Recorder(error=error), retrieved_at=AS_OF, as_of=AS_OF)
    finally:
        reset_caches()
    assert (a["state"], a["reason"]) == ("provider_failure", reason)


# GDACS


def test_orange_cyclone_within_radius_is_exposure():
    [a] = gd(["INPRT"])
    assert (a["state"], a["provider_id"]) == ("exposure_found", "gdacs")
    [event] = [v for v in a["values"] if v["met"]]
    assert (event["event_id"], event["alert_level"], event["event_type"]) == ("9100001", "Orange", "TC")
    assert int(event["distance_km"]) < 300 and event["threshold"] == "Orange or Red within 300 km"
    out = hazard_exposure([a], as_of=AS_OF)
    [record] = out["route_evidence"]["evidence"]
    assert (record["source"]["kind"], record["kind"], record["status"]) == ("hazard_feed", "weather_hazard", "exposure")
    assert record["observed_period"] == {"start": "2026-10-07T00:00:00+00:00", "end": "2026-10-10T00:00:00+00:00"}


def test_green_alert_nearby_and_red_alert_far_away_are_not_exposure():
    inns, = gd(["INNSA"])
    assert inns["state"] == "no_exposure_found"
    flood = next(v for v in inns["values"] if v["event_id"] == "9100002")
    assert (flood["met"], flood["reason"]) == (False, "alert_level_below_threshold")
    assert all(v["event_id"] != "9100003" for v in inns["values"])


def test_malformed_gdacs_payload_fails_every_location():
    assert [(a["state"], a["reason"]) for a in gd(["INPRT", "INNSA"], name="gdacs_malformed.json")] == [
        ("malformed_payload", "invalid_json"), ("malformed_payload", "invalid_json")]
    assert [a["state"] for a in gd(["INPRT"], status=503)] == ["provider_failure"]


# Recorded responses and dispatch display


def test_recorded_responses_cover_every_requested_location():
    items = recorded_assessments(HERE / "recorded.json", as_of=AS_OF,
                                 location_codes=["INNSA", "SYN-PLANT-CHAKAN", "INMAA", "INMUN"])
    pairs = sorted((a["provider_id"], a["location"]["code"], a["state"]) for a in items)
    assert ("open-meteo", "SYN-PLANT-CHAKAN", "unsupported_location") in pairs
    assert ("gdacs", "SYN-PLANT-CHAKAN", "unsupported_location") in pairs
    assert ("open-meteo", "INNSA", "exposure_found") in pairs
    assert ("open-meteo", "INMUN", "malformed_payload") in pairs
    assert all(a["synthetic"] is True for a in items)


def test_hazards_for_a_shipment_split_exposure_from_failures():
    out = hazard_exposure(recorded_assessments(HERE / "recorded.json", as_of=AS_OF, location_codes=["BRTUB", "INPRT"]),
                          as_of=AS_OF)
    view = hazards_for_locations(out, ["BRTUB", "INPRT"])
    assert view["status"] == "ok" and view["note"] == EXPOSURE_NOTE
    states = {(a["provider_id"], a["location"]["code"]): (a["state"], a["usable"]) for a in view["assessments"]}
    assert states[("open-meteo", "BRTUB")] == ("provider_failure", False)
    assert states[("gdacs", "INPRT")] == ("exposure_found", True)
    assert states[("open-meteo", "INPRT")] == ("exposure_found", False)
    assert [e["source"]["provider_id"] for e in view["evidence"]["exposure"]] == ["gdacs"]
    assert view["evidence"]["confirmed"] == []
    assert hazards_for_locations(out, ["XXZZZ"])["status"] == "empty"


def test_hazard_records_pass_the_existing_route_evidence_normaliser_unchanged():
    out = hazard_exposure(recorded_assessments(HERE / "recorded.json", as_of=AS_OF,
                                               location_codes=["INNSA", "INPRT", "INMAA"]), as_of=AS_OF)
    again = normalize_route_evidence(out["records"], as_of=AS_OF)
    assert again == out["route_evidence"] and again["rejected"] == []
