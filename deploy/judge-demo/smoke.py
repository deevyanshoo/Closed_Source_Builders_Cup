#!/usr/bin/env python3
"""Bounded HTTPS acceptance check for the hosted synthetic quote demo.

This script performs three allowlisted Gemini extractions when invoked. It is
intentionally separate from local tests and must only be run by the deployment
owner against the intended Cloud Run revision.
"""

from __future__ import annotations

import argparse
import base64
from decimal import Decimal, InvalidOperation
import http.cookiejar
import json
import os
from pathlib import Path
import re
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request


FIXTURES = {
    "late-copper": ("1256c543568466fe44d1f3791c2861e65bd21f833883f98c5a85eae2784398df", "Atacama"),
    "express-copper": ("eb05f6b26257600f4ca1c23a8ba6d542fd21a97775084291257a45f9baad98ba", "Swift"),
    "eu-copper": ("8dc9d68ae21f47b03585325f2d2297a0e2710d3ef3a6bdce212faa5bdaf727a5", "Europa"),
}
TIMEOUT_SECONDS = 45
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
SAFE_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


class AcceptanceError(Exception):
    def __init__(self, stage: str, code: str, status: int | None = None):
        self.stage = stage
        self.code = code if SAFE_CODE.fullmatch(code) else "invalid_error_code"
        self.status = status
        super().__init__(self.code)


def require(condition: bool, stage: str, code: str) -> None:
    if not condition:
        raise AcceptanceError(stage, code)


def origin_of(url: str) -> tuple[str, str, int]:
    try:
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.port is None and parsed.netloc.endswith(":")):
            raise ValueError
        return parsed.scheme, parsed.hostname.lower(), parsed.port or 443
    except ValueError:
        raise AcceptanceError("url", "invalid_url") from None


def base_url(value: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
        origin_of(value)
        if (not value or any(char.isspace() or ord(char) < 32 for char in value)
                or parsed.query or parsed.fragment or "?" in value or "#" in value
                or parsed.path not in ("", "/") or not parsed.netloc):
            raise ValueError
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    except (ValueError, AcceptanceError):
        raise AcceptanceError("url", "invalid_url") from None


class SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, origin: tuple[str, str, int]):
        self.origin = origin

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        try:
            parsed = urllib.parse.urlsplit(newurl)
            if origin_of(newurl) != self.origin or parsed.fragment or parsed.username or parsed.password:
                raise ValueError
        except (ValueError, AcceptanceError):
            raise AcceptanceError("redirect", "cross_origin_redirect") from None
        return super().redirect_request(request, fp, code, msg, headers, newurl)


class Client:
    def __init__(self, base: str):
        self.base = base
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
            SameOriginRedirect(origin_of(base)),
        )

    def json(self, stage: str, path: str, *, body: dict | None = None, csrf: str | None = None,
             origin: str | None = None, expected_status: int = 200) -> dict:
        payload = None if body is None else json.dumps(body, separators=(",", ":"), allow_nan=False).encode("utf-8")
        headers = {"Accept": "application/json"}
        if payload is not None:
            headers.update({"Content-Type": "application/json", "Origin": origin or self.base})
            if csrf is not None:
                headers["X-CSRF-Token"] = csrf
        request = urllib.request.Request(self.base + path, data=payload, headers=headers,
                                         method="POST" if body is not None else "GET")
        try:
            with self.opener.open(request, timeout=TIMEOUT_SECONDS) as response:
                status = response.status
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            status = exc.code
            raw = exc.read(MAX_RESPONSE_BYTES + 1)
        except AcceptanceError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError):
            raise AcceptanceError(stage, "transport_error") from None
        require(len(raw) <= MAX_RESPONSE_BYTES, stage, "response_too_large")
        try:
            data = json.loads(raw)
        except (UnicodeError, ValueError):
            raise AcceptanceError(stage, "invalid_json_response") from None
        require(isinstance(data, dict), stage, "invalid_response")
        if status != expected_status:
            error = data.get("error")
            code = error.get("code") if isinstance(error, dict) else None
            raise AcceptanceError(stage, code if isinstance(code, str) else "unexpected_http_status", status)
        return data


def denied(client: Client, stage: str, path: str, body: dict, csrf: str,
           expected_status: int, expected_code: str, *, origin: str | None = None) -> None:
    try:
        client.json(stage, path, body=body, csrf=csrf, origin=origin)
    except AcceptanceError as exc:
        if exc.status != expected_status or exc.code != expected_code:
            raise AcceptanceError(stage, exc.code, exc.status) from None
    else:
        raise AcceptanceError(stage, "unexpected_success")


def key() -> str:
    return secrets.token_hex(16)


def numeric(value: object, stage: str) -> Decimal:
    require(isinstance(value, str), stage, "money_not_decimal_string")
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise AcceptanceError(stage, "invalid_decimal") from None
    require(number.is_finite(), stage, "invalid_decimal")
    return number


def offer_evidence(offer: dict, fixture: str) -> None:
    stage = "offer_evidence"
    require(offer.get("synthetic") is True, stage, "synthetic_label_missing")
    require(offer.get("extractor") == "gemini", stage, "extractor_not_gemini")
    detail = offer.get("extractor_detail")
    require(isinstance(detail, dict) and detail.get("call") == "real", stage, "provider_call_not_real")
    document = offer.get("document")
    require(isinstance(document, dict) and document.get("document_id") == "sha256:" + FIXTURES[fixture][0],
            stage, "fixture_hash_mismatch")
    fields = offer.get("fields")
    items = offer.get("items")
    require(isinstance(fields, dict) and isinstance(items, list) and items, stage, "fields_missing")
    require(all(isinstance(field, dict) and isinstance(field.get("status"), str)
                and isinstance(field.get("evidence"), list) for field in fields.values()),
            stage, "field_provenance_missing")
    require(any(field.get("evidence") for field in fields.values()), stage, "document_evidence_missing")
    require(all(isinstance(item, dict) and isinstance(item.get("pricing"), dict)
                and isinstance(item["pricing"].get("evidence"), list) for item in items),
            stage, "item_evidence_missing")


def status_summary(offer: dict) -> dict:
    return {"fields": {name: field.get("status") for name, field in offer["fields"].items()},
            "items": [{name: field.get("status") for name, field in item.items()} for item in offer["items"]]}


def blocking_codes(offer: dict) -> list[str]:
    reasons = offer.get("blocking_reasons")
    require(isinstance(reasons, list), "offer_review", "blocking_reasons_missing")
    return [reason.get("code") if isinstance(reason, dict) and isinstance(reason.get("code"), str)
            and SAFE_CODE.fullmatch(reason["code"]) else "invalid_reason" for reason in reasons]


def require_unblocked(offer: dict, stage: str) -> None:
    codes = blocking_codes(offer)
    if codes:
        raise AcceptanceError(stage, codes[0])


def summarize_plan(plan: dict, offers: dict[str, dict]) -> dict:
    alternatives = plan.get("alternatives")
    require(isinstance(alternatives, list), "plan", "alternatives_missing")
    by_id = {offer["offer_id"]: fixture for fixture, offer in offers.items()}
    require(len(alternatives) == 3 and {entry.get("offer_id") for entry in alternatives
                                        if isinstance(entry, dict)} == set(by_id),
            "plan", "alternatives_mismatch")
    summary = []
    for entry in alternatives:
        fixture = by_id[entry["offer_id"]]
        require(isinstance(entry.get("supplier"), str)
                and entry["supplier"].startswith(FIXTURES[fixture][1]),
                "plan", "supplier_mismatch")
        reasons = entry.get("reasons")
        require(isinstance(reasons, list) and all(isinstance(reason, str) and SAFE_CODE.fullmatch(reason)
                                                  for reason in reasons), "plan", "invalid_reasons")
        summary.append({"fixture": fixture, "supplier": entry.get("supplier"),
                        "feasible": entry.get("feasible"), "reasons": reasons,
                        "arrival_date": entry.get("arrival_date"),
                        "unit_price_per_kg": entry.get("unit_price_per_kg"),
                        "total_cost": entry.get("total_cost"), "quantity_kg": entry.get("quantity_kg")})
    selected = plan.get("selected_offer_id")
    require(selected is None or selected in by_id, "plan", "unknown_selection")
    return {"required_purchase_kg": plan.get("required_purchase_kg"),
            "selected_fixture": by_id.get(selected), "total_cost": plan.get("total_cost"),
            "reporting_currency": plan.get("reporting_currency"), "alternatives": summary,
            "synthetic": plan.get("synthetic"),
            "assumptions": {name: plan.get("assumptions", {}).get(name) for name in
                            ("additional_cost_per_kg", "additional_cost_currency",
                             "additional_cost_source", "fx_source", "fx_rates", "comparison")}}


def check_plan(plan: dict, offers: dict[str, dict], *, low_budget: bool) -> dict:
    stage = "low_budget_plan" if low_budget else "main_plan"
    summary = summarize_plan(plan, offers)
    require(summary["synthetic"] is True and summary["reporting_currency"] == "USD", stage, "plan_labels_wrong")
    require(numeric(summary["required_purchase_kg"], stage) == Decimal("8000"), stage, "purchase_wrong")
    require(plan.get("forecast_action") == "ABSTAIN" and plan.get("customer_savings") is None,
            stage, "unsupported_claim")
    require(plan.get("offer_versions") == {offer["offer_id"]: offer["version"] for offer in offers.values()},
            stage, "plan_versions_wrong")
    alternatives = {entry["fixture"]: entry for entry in summary["alternatives"]}
    late, express, europa = (alternatives[name] for name in FIXTURES)
    require(late["feasible"] is False and "arrives_after_need_by" in late["reasons"],
            stage, "late_offer_not_excluded")
    for entry, expected in ((express, "86000"), (europa, "82000")):
        require(numeric(entry["total_cost"], stage) == Decimal(expected), stage, "alternative_cost_wrong")
        require(numeric(entry["quantity_kg"], stage) == Decimal("8000"), stage, "alternative_quantity_wrong")
    if low_budget:
        require(summary["selected_fixture"] is None and summary["total_cost"] is None,
                stage, "low_budget_selected_offer")
        require(express["feasible"] is False and europa["feasible"] is False
                and "over_budget" in express["reasons"] and "over_budget" in europa["reasons"],
                stage, "low_budget_not_enforced")
    else:
        require(express["feasible"] is True and europa["feasible"] is True,
                stage, "feasible_offers_missing")
        require(summary["selected_fixture"] == "eu-copper"
                and numeric(summary["total_cost"], stage) == Decimal("82000"),
                stage, "main_selection_wrong")
    return summary


def run(base: str, receipt: dict) -> None:
    client = Client(base)
    health = client.json("health", "/-/health")
    require(health.get("ready") is True and isinstance(health.get("revision"), str)
            and bool(health["revision"]) and health["revision"] != "local", "health", "not_ready")
    receipt["deployed_revision"] = health["revision"]
    receipt["checks"]["health"] = True

    session = client.json("session", "/api/demo/session")
    csrf = session.get("csrf_token")
    require(isinstance(csrf, str) and bool(csrf) and session.get("synthetic") is True
            and session.get("persistence") == "ephemeral", "session", "invalid_session")
    require(session.get("offers") == [], "session", "session_not_fresh")
    receipt["checks"]["fresh_session"] = True

    offers = {}
    for fixture in FIXTURES:
        result = client.json("extract_" + fixture.replace("-", "_"), "/api/demo/extract",
                             body={"fixture_id": fixture, "mode": "gemini", "idempotency_key": key()}, csrf=csrf)
        offer = result.get("offer")
        require(isinstance(offer, dict) and result.get("replayed") is False,
                "extract", "invalid_extraction")
        offer_evidence(offer, fixture)
        require(isinstance(offer.get("offer_id"), str) and type(offer.get("version")) is int,
                "extract", "invalid_offer_identity")
        require(type(result.get("served_from_cache")) is bool
                and result["served_from_cache"] is offer.get("served_from_cache"),
                "extract", "cache_provenance_mismatch")
        detail = offer["extractor_detail"]
        receipt["extractions"].append({"fixture": fixture, "document_sha256": FIXTURES[fixture][0],
                                       "extractor": offer["extractor"], "call": detail["call"],
                                       "served_from_cache": result.get("served_from_cache") is True,
                                       "model": detail.get("model"),
                                       "latency_ms": detail.get("latency_ms"),
                                       "prompt_tokens": detail.get("prompt_tokens"),
                                       "output_tokens": detail.get("output_tokens"),
                                       "field_statuses": status_summary(offer)})
        require(isinstance(detail.get("model"), str) and bool(detail["model"]),
                "extract", "model_missing")
        require(offer["fields"].get("valid_until", {}).get("value") == "2026-12-31",
                "extract", "expiry_wrong")
        offers[fixture] = offer
    receipt["checks"]["three_real_gemini_results"] = True

    late = offers["late-copper"]
    require(late["fields"].get("settlement_currency", {}).get("status") == "missing",
            "late_review", "settlement_not_missing")
    late_reasons = late.get("blocking_reasons")
    require(isinstance(late_reasons, list), "late_review", "blocking_reasons_missing")
    for reason in late_reasons:
        if not isinstance(reason, dict) or reason.get("code") != "field_missing" or reason.get("field") != "settlement_currency":
            code = reason.get("code") if isinstance(reason, dict) else "invalid_reason"
            raise AcceptanceError("late_review", code if isinstance(code, str) else "invalid_reason")
    require(len(late_reasons) == 1, "late_review", "settlement_blocker_missing")
    changed = client.json("late_correction", "/api/demo/offers/" + late["offer_id"] + "/correct",
                          body={"field": "settlement_currency", "value": "USD",
                                "expected_version": late["version"], "idempotency_key": key()}, csrf=csrf)
    late = changed.get("offer")
    require(isinstance(late, dict) and late.get("version") == offers["late-copper"]["version"] + 1,
            "late_correction", "version_wrong")
    corrected = late.get("fields", {}).get("settlement_currency", {})
    require(corrected.get("status") == "corrected" and corrected.get("value") == "USD"
            and corrected.get("source") == "user"
            and corrected.get("evidence_status") == "user_entered_without_document_evidence",
            "late_correction", "correction_not_recorded")
    offers["late-copper"] = late
    receipt["checks"]["reviewer_currency_correction"] = True

    for fixture, offer in offers.items():
        require_unblocked(offer, "save_" + fixture.replace("-", "_"))
        result = client.json("save_" + fixture.replace("-", "_"),
                             "/api/demo/offers/" + offer["offer_id"] + "/save",
                             body={"expected_version": offer["version"], "idempotency_key": key()}, csrf=csrf)
        saved = result.get("offer")
        require(isinstance(saved, dict) and saved.get("status") == "saved"
                and saved.get("version") == offer["version"] + 1,
                "save", "offer_not_saved")
        offer_evidence(saved, fixture)
        offers[fixture] = saved
    receipt["checks"]["all_offers_saved"] = True

    plan_body = {"offer_ids": [offer["offer_id"] for offer in offers.values()],
                 "expected_versions": {offer["offer_id"]: offer["version"] for offer in offers.values()},
                 "material": "copper", "quantity_kg": "10000", "current_stock_kg": "2000",
                 "decision_date": "2026-10-14", "need_by": "2026-11-05",
                 "reporting_currency": "USD", "budget": "80000", "additional_cost_per_kg": "0.25"}
    low = client.json("low_budget_plan", "/api/demo/plan", body=plan_body, csrf=csrf)
    receipt["plans"]["low_budget"] = check_plan(low, offers, low_budget=True)
    receipt["checks"]["low_budget_null_selection"] = True
    plan_body["budget"] = "100000"
    main = client.json("main_plan", "/api/demo/plan", body=plan_body, csrf=csrf)
    receipt["plans"]["main"] = check_plan(main, offers, low_budget=False)
    receipt["checks"]["main_plan_arithmetic"] = True

    exported = client.json("export", "/api/demo/export")
    exported_offers = exported.get("offers")
    require(exported.get("synthetic") is True and isinstance(exported_offers, list)
            and len(exported_offers) == 3, "export", "offers_missing")
    indexed = {offer.get("offer_id"): offer for offer in exported_offers if isinstance(offer, dict)}
    require(set(indexed) == {offer["offer_id"] for offer in offers.values()}, "export", "offers_mismatch")
    for fixture, offer in offers.items():
        exported_offer = indexed[offer["offer_id"]]
        offer_evidence(exported_offer, fixture)
        require(exported_offer.get("status") == "saved" and exported_offer.get("version") == offer["version"]
                and exported_offer.get("served_from_cache") == offer.get("served_from_cache"),
                "export", "offer_state_mismatch")
    require(indexed[offers["late-copper"]["offer_id"]]["fields"]["settlement_currency"].get("evidence_status")
            == "user_entered_without_document_evidence", "export", "correction_evidence_missing")
    exported_plan = exported.get("plan")
    require(isinstance(exported_plan, dict), "export", "plan_missing")
    require(exported_plan.get("plan_id") == main.get("plan_id"), "export", "plan_identity_mismatch")
    require(check_plan(exported_plan, offers, low_budget=False) == receipt["plans"]["main"],
            "export", "plan_mismatch")
    receipt["checks"]["export_evidence_and_plan"] = True

    other = Client(base)
    other_session = other.json("other_session", "/api/demo/session")
    other_csrf = other_session.get("csrf_token")
    require(isinstance(other_csrf, str) and bool(other_csrf), "other_session", "invalid_session")
    original = offers["eu-copper"]
    denied(other, "cross_session_save", "/api/demo/offers/" + original["offer_id"] + "/save",
           {"expected_version": original["version"], "idempotency_key": key()}, other_csrf,
           404, "offer_not_found")
    receipt["checks"]["cross_session_save_404"] = True

    denied(client, "forged_origin_reset", "/api/demo/reset", {}, csrf,
           403, "invalid_origin", origin="https://forged-origin.invalid")
    receipt["checks"]["forged_origin_reset_403"] = True

    unlisted = base64.b64encode(b"synthetic,unlisted\nnot,a,fixture\n").decode("ascii")
    denied(client, "unlisted_upload", "/api/demo/extract",
           {"filename": "unlisted-synthetic.csv", "document_base64": unlisted,
            "mode": "gemini", "idempotency_key": key()}, csrf, 422, "fixture_not_allowed")
    receipt["checks"]["unlisted_upload_422"] = True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="HTTPS Cloud Run base URL")
    parser.add_argument("--output", required=True, type=Path, help="Task-owned receipt JSON path")
    args = parser.parse_args()
    receipt = {"success": False, "deployed_revision": None,
               "fixture_hashes": {name: value[0] for name, value in FIXTURES.items()},
               "checks": {}, "extractions": [], "plans": {}, "errors": []}
    try:
        base = base_url(args.url)
        run(base, receipt)
        receipt["success"] = True
    except AcceptanceError as exc:
        receipt["errors"].append({"stage": exc.stage, "code": exc.code, "status": exc.status})
    except Exception:
        receipt["errors"].append({"stage": "script", "code": "unexpected_script_error", "status": None})
    try:
        output = args.output.expanduser().resolve()
        require(output != Path(__file__).resolve() and output.parent.is_dir(), "output", "invalid_output")
        temporary = output.with_name(output.name + "." + secrets.token_hex(6) + ".tmp")
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(receipt, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
        os.replace(temporary, output)
    except (OSError, AcceptanceError):
        print("receipt_write_failed", file=sys.stderr)
        return 2
    print(str(output))
    return 0 if receipt["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
