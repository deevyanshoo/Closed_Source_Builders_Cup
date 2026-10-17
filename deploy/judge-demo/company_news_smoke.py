#!/usr/bin/env python3
"""Black-box company/news acceptance for the hosted judge demo.

The deployment owner runs this against an intended HTTPS revision. --live
requires an observed official provider response from the current cache window;
--offline verifies
explicit source unavailability. The optional baseline run invokes smoke.py,
which performs actual Gemini fixture extractions and procurement checks.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from urllib.parse import urlsplit

# Keep cookies, redirect restrictions, response bounds, Origin and CSRF behavior
# identical to the independent hosted procurement acceptance script.
from smoke import AcceptanceError, Client, base_url, require

OFFICIAL_SOURCES = {"eia", "ecb", "ecb_fx"}
KNOWN_SOURCES = OFFICIAL_SOURCES | {"gdelt"}
SOURCE_STATES = {"fresh", "cached", "stale", "empty", "unavailable", "offline"}
CACHE_STATES = {"fresh", "cached", "stale", "empty", "unavailable", "offline"}


def check_decimal(value: object, stage: str) -> None:
    require(isinstance(value, str), stage, "quantity_not_string")
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError):
        raise AcceptanceError(stage, "invalid_decimal_quantity") from None
    require(number.is_finite() and number >= 0, stage, "invalid_decimal_quantity")


def nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def check_profile(profile: object) -> dict:
    stage = "profile"
    require(isinstance(profile, dict), stage, "invalid_profile")
    company_id = profile.get("company_id")
    require(nonempty(company_id), stage, "company_id_missing")
    require(profile.get("synthetic") is True, stage, "profile_not_synthetic")
    for field in ("name", "sector", "size", "as_of", "description", "reporting_currency"):
        require(nonempty(profile.get(field)), stage, "missing_" + field)
    locations = profile.get("locations")
    require(isinstance(locations, list) and locations and all(
        isinstance(x, dict) and nonempty(x.get("country")) and nonempty(x.get("city")) for x in locations
    ), stage, "invalid_locations")
    currencies = profile.get("currencies")
    require(isinstance(currencies, list) and currencies and
            all(nonempty(x) for x in currencies) and
            profile["reporting_currency"] in currencies, stage, "invalid_currencies")
    materials = profile.get("materials")
    require(isinstance(materials, list) and materials, stage, "materials_missing")
    material_ids = set()
    for material in materials:
        require(isinstance(material, dict), stage, "invalid_material")
        mid = material.get("material_id")
        require(nonempty(mid) and mid not in material_ids and nonempty(material.get("name"))
                and nonempty(material.get("quantity_unit")), stage, "invalid_material")
        material_ids.add(mid)
        for field in ("stock", "consumption_per_day", "monthly_requirement"):
            check_decimal(material.get(field), stage)
        require(nonempty(material.get("criticality")) and isinstance(material.get("keywords"), list),
                stage, "invalid_material_metadata")
    for field in ("suppliers", "orders", "routes"):
        require(isinstance(profile.get(field), list) and profile[field], stage, "missing_" + field)
    for order in profile["orders"]:
        require(isinstance(order, dict) and order.get("material_id") in material_ids,
                stage, "invalid_order_material")
        unit = next(x["quantity_unit"] for x in materials if x["material_id"] == order["material_id"])
        require(order.get("unit") == unit and nonempty(order.get("need_by")) and
                nonempty(order.get("status")), stage, "invalid_order")
        check_decimal(order.get("quantity"), stage)
    for route in profile["routes"]:
        require(isinstance(route, dict) and nonempty(route.get("route_id")) and
                nonempty(route.get("origin")) and nonempty(route.get("destination")) and
                isinstance(route.get("via"), list) and route.get("direction") in ("import", "export")
                and route.get("currency") in currencies and isinstance(route.get("materials"), list)
                and set(route["materials"]) <= material_ids, stage, "invalid_route")
    for supplier in profile["suppliers"]:
        require(isinstance(supplier, dict) and nonempty(supplier.get("supplier_id")) and
                nonempty(supplier.get("name")) and nonempty(supplier.get("country")) and
                supplier.get("settlement_currency") in currencies and
                isinstance(supplier.get("materials"), list) and supplier["materials"] and
                set(supplier["materials"]) <= material_ids and
                type(supplier.get("lead_time_days")) is int and
                supplier["lead_time_days"] >= 0, stage, "invalid_supplier")
    require(len({s["supplier_id"] for s in profile["suppliers"]}) == len(profile["suppliers"])
            and material_ids <= {mid for s in profile["suppliers"] for mid in s["materials"]},
            stage, "supplier_material_refs_invalid")
    coverage = profile.get("quote_coverage")
    require(isinstance(coverage, dict) and isinstance(coverage.get("materials"), list) and
            isinstance(coverage.get("fixture_ids"), list) and nonempty(coverage.get("note")),
            stage, "invalid_quote_coverage")
    require(isinstance(profile.get("news_keywords"), list), stage, "invalid_news_keywords")
    return profile


def check_catalogue(data: dict, receipt: dict) -> dict[str, dict]:
    profiles = data.get("companies")
    require(isinstance(profiles, list) and len(profiles) == 10, "catalogue", "not_ten_profiles")
    indexed = {p["company_id"]: p for p in (check_profile(x) for x in profiles)}
    require(len(indexed) == 10 and "solara-cables" in indexed, "catalogue", "duplicate_or_default_missing")
    require(len({p["sector"] for p in profiles}) >= 8, "catalogue", "sectors_not_diverse")
    require(len({p["reporting_currency"] for p in profiles}) >= 4, "catalogue", "currencies_not_diverse")
    require(len({m["name"] for p in profiles for m in p["materials"]}) >= 10,
            "catalogue", "materials_not_diverse")
    require(len({len(p["materials"]) for p in profiles}) >= 3,
            "catalogue", "inventory_shapes_not_diverse")
    minimums = {"atlas-petrochem": 12, "gridspring-batteries": 10,
                "stonebridge-materials": 8}
    require(all(len(indexed[cid]["materials"]) >= count for cid, count in minimums.items())
            and all(len(p["materials"]) >= 3 for p in profiles),
            "catalogue", "material_depth_missing")
    default = indexed["solara-cables"]
    require(default["quote_coverage"]["scenarios"][0]["material_id"] == "copper" and
            set(default["quote_coverage"]["scenarios"][0]["fixture_ids"]) ==
            {"late-copper", "express-copper", "eu-copper"} and
            isinstance(default.get("buyer_defaults"), dict) and
            default["buyer_defaults"].get("material") == "copper", "catalogue", "default_coverage_changed")
    require(all(set(p["quote_coverage"]["materials"]) == {m["material_id"] for m in p["materials"]}
                for p in indexed.values()), "catalogue", "incomplete_material_coverage")
    receipt["checks"]["ten_diverse_profiles_and_coverage"] = True
    receipt["profiles"] = [{"company_id": p["company_id"], "sector": p["sector"],
                            "material_count": len(p["materials"]), "route_count": len(p["routes"]),
                            "reporting_currency": p["reporting_currency"]} for p in profiles]
    return indexed


def check_session(session: dict, cid: str, version: int, profiles: dict[str, dict], stage: str) -> str:
    require(isinstance(session, dict) and session.get("synthetic") is True and
            session.get("persistence") == "ephemeral" and
            session.get("company") == profiles[cid] and
            type(session.get("context_version")) is int and session["context_version"] == version,
            stage, "session_context_mismatch")
    token = session.get("csrf_token")
    require(nonempty(token), stage, "csrf_missing")
    return token


def expect_409(client: Client, stage: str, path: str, body: dict, csrf: str) -> None:
    try:
        client.json(stage, path, body=body, csrf=csrf)
    except AcceptanceError as exc:
        require(exc.status == 409, stage, "stale_context_not_rejected")
    else:
        raise AcceptanceError(stage, "stale_context_accepted")


def check_news(view: dict, profile: dict, version: int, *, live: bool, stage: str) -> dict:
    require(view.get("company_id") == profile["company_id"] and
            view.get("context_version") == version, stage, "news_context_mismatch")
    require(nonempty(view.get("checked_at")) and view.get("cache_state") in CACHE_STATES,
            stage, "invalid_news_state")
    sources = view.get("sources")
    require(isinstance(sources, list) and {x.get("source_id") for x in sources if isinstance(x, dict)}
            == KNOWN_SOURCES, stage, "source_inventory_mismatch")
    for source in sources:
        require(nonempty(source.get("name")) and source.get("state") in SOURCE_STATES and
                nonempty(source.get("reason")) and
                (source.get("last_checked_at") is None or nonempty(source.get("last_checked_at"))),
                stage, "invalid_source_health")
    items, fx = view.get("items"), view.get("fx")
    require(isinstance(items, list) and isinstance(fx, list) and len(items) <= 80 and
            isinstance(view.get("limitations"), list) and view["limitations"],
            stage, "invalid_news_payload")
    material_ids = {x["material_id"] for x in profile["materials"]}
    route_ids = {x["route_id"] for x in profile["routes"]}
    item_signatures = {}
    for item in items:
        require(isinstance(item, dict) and item.get("synthetic") is False and
                nonempty(item.get("item_id")) and nonempty(item.get("title")) and
                nonempty(item.get("publisher")) and item.get("provider") in ("EIA", "ECB", "GDELT") and
                nonempty(item.get("retrieved_at")) and nonempty(item.get("first_seen_at")) and
                nonempty(item.get("relevance_reason")) and nonempty(item.get("implication")) and
                nonempty(item.get("reliability_reason")) and
                isinstance(item.get("next_steps"), list), stage, "invalid_observed_item")
        parsed = urlsplit(item.get("url", ""))
        require(parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and
                not parsed.password, stage, "unsafe_source_url")
        mids, rids, currencies = (item.get(k) for k in
            ("matched_materials", "matched_routes", "matched_currencies"))
        require(isinstance(mids, list) and isinstance(rids, list) and isinstance(currencies, list)
                and set(mids) <= material_ids and set(rids) <= route_ids and
                set(currencies) <= set(profile["currencies"]), stage, "foreign_exposure_binding")
        item_signatures[item["item_id"]] = (tuple(sorted(mids)), tuple(sorted(rids)),
                                            tuple(sorted(currencies)))
    for observation in fx:
        require(isinstance(observation, dict) and observation.get("provider") == "ECB" and
                observation.get("base") == "EUR" and
                observation.get("quote") in profile["currencies"] and
                observation.get("informational") is True and
                nonempty(observation.get("observation_date")) and
                nonempty(observation.get("retrieved_at")), stage, "invalid_fx_observation")
        check_decimal(observation.get("rate"), stage)
    if not live:
        require(view["cache_state"] == "offline" and all(x["state"] == "offline" for x in sources)
                and not items and not fx, stage, "offline_mode_not_explicit")
    return {"cache_state": view["cache_state"], "source_states":
            {x["source_id"]: x["state"] for x in sources}, "items": len(items),
            "fx": len(fx), "signatures": item_signatures}


def checked_time(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        require(parsed.tzinfo is not None, "live_provider", "invalid_checked_at")
        return parsed.astimezone(timezone.utc)
    except ValueError:
        raise AcceptanceError("live_provider", "invalid_checked_at") from None


def check_official_observation(view: dict, receipt: dict, *, now: datetime | None = None) -> None:
    """Prove official metadata in a fresh fetch or a still-valid shared cache."""
    stage = "live_provider"
    state = view["cache_state"]
    require(state in ("fresh", "cached"), stage, "no_current_official_observation")
    timestamp = checked_time(view["checked_at"])
    age = ((now or datetime.now(timezone.utc)) - timestamp).total_seconds()
    require(-60 <= age <= 960, stage, "observation_outside_cache_window")
    sources = {s["source_id"]: s for s in view["sources"]}
    expected_source_state = "fresh" if state == "fresh" else "cached"
    healthy = [sid for sid in OFFICIAL_SOURCES if sources[sid]["state"] == expected_source_state
               and sources[sid]["last_checked_at"] == view["checked_at"]]
    observed = [x for x in view["items"] if x["provider"] in ("EIA", "ECB") and
                x["retrieved_at"] == view["checked_at"] and
                ((x["provider"] == "EIA" and "eia" in healthy) or
                 (x["provider"] == "ECB" and "ecb" in healthy))]
    rates = [x for x in view["fx"] if "ecb_fx" in healthy and
             x["retrieved_at"] == view["checked_at"]]
    require(healthy and (observed or rates), stage, "no_current_official_observation")
    receipt["live_evidence"] = {
        "observation_mode": "new_fetch_on_initial_get" if state == "fresh" else "existing_shared_cache",
        "new_provider_fetch_observed": state == "fresh",
        "checked_at": view["checked_at"], "age_seconds_at_check": round(age),
        "official_source_states": {sid: sources[sid]["state"] for sid in sorted(healthy)},
        "official_source_last_checked_at": {sid: sources[sid]["last_checked_at"] for sid in sorted(healthy)},
        "observed_items": len(observed), "fx_observations": len(rates),
        "sample_provider": observed[0]["provider"] if observed else "ECB",
        "sample_url": observed[0]["url"] if observed else None,
        "sample_retrieved_at": (observed[0] if observed else rates[0])["retrieved_at"],
        "sample_observation_date": rates[0]["observation_date"] if rates else None,
    }
    receipt["checks"]["current_actual_official_provider_observation"] = True


def observe_live(client: Client, profile: dict, csrf: str, receipt: dict,
                 *, now: datetime | None = None) -> dict:
    initial = client.json("initial_news", "/api/demo/news")
    check_news(initial, profile, 1, live=True, stage="initial_news")
    check_official_observation(initial, receipt, now=now)
    refreshed = client.json("official_refresh", "/api/demo/news/refresh",
                            body={"expected_context_version": 1,
                                  "idempotency_key": secrets.token_hex(16)}, csrf=csrf)
    check_news(refreshed, profile, 1, live=True, stage="official_refresh")
    require(refreshed["cache_state"] == "cached" and
            refreshed["checked_at"] == initial["checked_at"],
            "official_refresh", "cache_cap_not_preserved")
    sources = {s["source_id"]: s for s in refreshed["sources"]}
    observed_sources = receipt["live_evidence"]["official_source_states"]
    require(all(sources[sid]["state"] == "cached" and
                sources[sid]["last_checked_at"] == initial["checked_at"]
                for sid in observed_sources), "official_refresh", "cached_source_lineage_changed")
    receipt["checks"]["refresh_respects_shared_cache"] = True
    return initial


def self_test_live_gate() -> None:
    """Credential-free contract check for the first GET and capped refresh."""
    stamp = "2026-10-14T10:00:00+00:00"
    now = datetime(2026, 10, 14, 10, 1, tzinfo=timezone.utc)
    profile = {"company_id": "solara-cables", "materials": [], "routes": [],
               "currencies": ["USD"]}

    def view(state: str) -> dict:
        return {"company_id": "solara-cables", "context_version": 1,
                "checked_at": stamp, "cache_state": state,
                "sources": [{"source_id": sid, "name": sid,
                             "state": (state if sid == "ecb_fx" else "unavailable"),
                             "last_checked_at": stamp, "reason": "Provider status observed."}
                            for sid in ("eia", "ecb", "ecb_fx", "gdelt")],
                "items": [], "fx": [{"base": "EUR", "quote": "USD", "rate": "1.16",
                                      "observation_date": "2026-10-14", "provider": "ECB",
                                      "retrieved_at": stamp, "informational": True}],
                "limitations": ["Reference rates are informational."]}

    class FakeClient:
        def __init__(self, first: dict, second: dict):
            self.responses = [first, second]

        def json(self, stage: str, path: str, *, body: dict | None = None,
                 csrf: str | None = None) -> dict:
            index = 2 - len(self.responses)
            if index == 0:
                assert stage == "initial_news" and path == "/api/demo/news" and body is None
            else:
                assert stage == "official_refresh" and path == "/api/demo/news/refresh"
                assert body and body["expected_context_version"] == 1 and body["idempotency_key"]
                assert csrf == "token"
            return self.responses.pop(0)

    for first_state, expected_fetch in (("fresh", True), ("cached", False)):
        receipt = {"checks": {}, "live_evidence": None}
        observe_live(FakeClient(view(first_state), view("cached")), profile,
                     "token", receipt, now=now)
        assert receipt["live_evidence"]["new_provider_fetch_observed"] is expected_fetch
        assert receipt["checks"]["refresh_respects_shared_cache"] is True
    try:
        observe_live(FakeClient(view("stale"), view("cached")), profile,
                     "token", {"checks": {}, "live_evidence": None}, now=now)
    except AcceptanceError as exc:
        assert exc.stage == "live_provider"
    else:
        raise AssertionError("A stale source was accepted as current")


def run(base: str, *, live: bool, receipt: dict) -> None:
    client = Client(base)
    health = client.json("health", "/-/health")
    require(health.get("ready") is True and nonempty(health.get("revision")) and
            health["revision"] != "local", "health", "hosted_revision_not_ready")
    receipt["deployed_revision"] = health["revision"]
    receipt["checks"]["hosted_health"] = True
    profiles = check_catalogue(client.json("catalogue", "/api/demo/companies"), receipt)
    session = client.json("default_session", "/api/demo/session")
    csrf = check_session(session, "solara-cables", 1, profiles, "default_session")
    require(session.get("offers") == [], "default_session", "session_not_fresh")
    receipt["checks"]["default_context_and_fresh_session"] = True

    if live:
        initial = observe_live(client, profiles["solara-cables"], csrf, receipt)
    else:
        initial = client.json("initial_news", "/api/demo/news")
        check_news(initial, profiles["solara-cables"], 1, live=False, stage="initial_news")
        receipt["checks"]["explicit_offline_sources"] = True

    ids = [cid for cid in profiles if cid != "solara-cables"] + ["solara-cables"]
    version = 1
    signatures_by_company = {"solara-cables": check_news(
        initial, profiles["solara-cables"], 1, live=live, stage="initial_news")["signatures"]}
    for cid in ids:
        old_version = version
        session = client.json("select_" + cid, "/api/demo/company",
                              body={"company_id": cid, "expected_context_version": version,
                                    "idempotency_key": secrets.token_hex(16)}, csrf=csrf)
        version += 1
        csrf = check_session(session, cid, version, profiles, "select_" + cid)
        restored = client.json("session_" + cid, "/api/demo/session")
        check_session(restored, cid, version, profiles, "session_" + cid)
        require(restored.get("offers") == [] and restored.get("plan") is None,
                "session_" + cid, "foreign_workspace_state")
        view = client.json("news_" + cid, "/api/demo/news")
        summary = check_news(view, profiles[cid], version, live=live, stage="news_" + cid)
        signatures_by_company[cid] = summary["signatures"]
        receipt["company_views"].append({"company_id": cid, "context_version": version,
                                         "news_items": summary["items"], "fx_observations": summary["fx"],
                                         "cache_state": summary["cache_state"]})
        expect_409(client, "stale_refresh_" + cid, "/api/demo/news/refresh",
                   {"expected_context_version": old_version,
                    "idempotency_key": secrets.token_hex(16)}, csrf)
        require(client.json("post_stale_" + cid, "/api/demo/session")["context_version"] == version,
                "post_stale_" + cid, "stale_mutation_changed_context")
    require(version == 11, "switches", "switch_count_wrong")
    receipt["checks"]["all_ten_company_switches_and_session_versions"] = True
    receipt["checks"]["stale_news_mutation_rejected"] = True
    restored = client.json("default_restored", "/api/demo/session")
    check_session(restored, "solara-cables", version, profiles, "default_restored")
    require(restored["company"]["quote_coverage"]["fixture_ids"] and restored.get("offers") == [],
            "default_restored", "default_workspace_not_restored")
    receipt["checks"]["original_default_profile_restored"] = True

    if live:
        # The same provider metadata is projected against each company's own exposures.
        signatures = [(cid, item_id, sig) for cid, entries in signatures_by_company.items()
                      for item_id, sig in entries.items()]
        relevant = [entry for entry in signatures if any(entry[2][i] for i in range(3))]
        different = any(a[1] == b[1] and a[2] != b[2] for a in signatures for b in signatures)
        require(relevant and different, "news_relevance", "no_distinct_company_binding")
        receipt["checks"]["different_company_relevance"] = True
        receipt["relevance_evidence"] = {"matched_company_count": len({x[0] for x in relevant}),
                                         "distinct_matching_signatures": len({x[2] for x in relevant})}

    other = Client(base)
    other_session = other.json("foreign_session", "/api/demo/session")
    other_csrf = check_session(other_session, "solara-cables", 1, profiles, "foreign_session")
    other_selected = other.json("foreign_switch", "/api/demo/company",
                                body={"company_id": "terrapack", "expected_context_version": 1,
                                      "idempotency_key": secrets.token_hex(16)}, csrf=other_csrf)
    check_session(other_selected, "terrapack", 2, profiles, "foreign_switch")
    check_session(client.json("own_unchanged", "/api/demo/session"), "solara-cables", version,
                  profiles, "own_unchanged")
    other_news = other.json("foreign_news", "/api/demo/news")
    check_news(other_news, profiles["terrapack"], 2, live=live, stage="foreign_news")
    require(other_news["company_id"] != client.json("own_news", "/api/demo/news")["company_id"],
            "foreign_session", "cross_session_company_leak")
    receipt["checks"]["foreign_session_isolated"] = True


def write_receipt(path: Path, receipt: dict) -> None:
    path = path.expanduser().resolve()
    require(path.parent.is_dir() and path != Path(__file__).resolve(), "output", "invalid_output")
    temporary = path.with_name(path.name + "." + secrets.token_hex(6) + ".tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(receipt, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="HTTPS hosted demo base URL")
    parser.add_argument("--output", required=True, type=Path, help="Receipt JSON path")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true", help="Require a fresh real official provider response")
    mode.add_argument("--offline", action="store_true", help="Require explicit offline source states")
    parser.add_argument("--with-baseline-smoke", action="store_true",
                        help="Also run the original quote/recovery/RFQ smoke in a new session")
    args = parser.parse_args()
    receipt = {"success": False, "mode": "live" if args.live else "offline",
               "deployed_revision": None, "checks": {}, "profiles": [], "company_views": [],
               "live_evidence": None, "relevance_evidence": None, "baseline_smoke": None,
               "errors": []}
    try:
        base = base_url(args.url)
        run(base, live=args.live, receipt=receipt)
        if args.with_baseline_smoke:
            baseline_path = args.output.expanduser().resolve().with_name(
                args.output.stem + "-baseline.json")
            completed = subprocess.run([sys.executable, str(Path(__file__).with_name("smoke.py")),
                                        "--url", base, "--output", str(baseline_path)],
                                       check=False, capture_output=True, text=True, timeout=300)
            require(completed.returncode == 0 and baseline_path.is_file(),
                    "baseline_smoke", "baseline_smoke_failed")
            baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
            require(baseline.get("success") is True and
                    baseline.get("deployed_revision") == receipt["deployed_revision"],
                    "baseline_smoke", "baseline_receipt_invalid")
            receipt["baseline_smoke"] = str(baseline_path)
            receipt["checks"]["original_quote_recovery_rfq_smoke"] = True
        receipt["success"] = True
    except AcceptanceError as exc:
        receipt["errors"].append({"stage": exc.stage, "code": exc.code, "status": exc.status})
    except (OSError, ValueError, subprocess.TimeoutExpired):
        receipt["errors"].append({"stage": "script", "code": "script_or_io_error", "status": None})
    try:
        write_receipt(args.output, receipt)
    except (OSError, AcceptanceError):
        print("receipt_write_failed", file=sys.stderr)
        return 2
    print(str(args.output.expanduser().resolve()))
    return 0 if receipt["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
