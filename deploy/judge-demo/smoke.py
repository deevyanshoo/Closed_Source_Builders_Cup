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
import hashlib
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

    def text(self, stage: str, path: str, *, expected_status: int = 200) -> str:
        request = urllib.request.Request(self.base + path, headers={"Accept": "text/plain"})
        try:
            with self.opener.open(request, timeout=TIMEOUT_SECONDS) as response:
                status = response.status
                content_type = response.headers.get_content_type()
                disposition = response.headers.get("Content-Disposition", "")
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            status = exc.code
            content_type = exc.headers.get_content_type()
            disposition = ""
            raw = exc.read(MAX_RESPONSE_BYTES + 1)
        except AcceptanceError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError):
            raise AcceptanceError(stage, "transport_error") from None
        require(len(raw) <= MAX_RESPONSE_BYTES, stage, "response_too_large")
        if status != expected_status:
            try:
                error = json.loads(raw).get("error", {})
                code = error.get("code") if isinstance(error, dict) else None
            except (UnicodeError, ValueError, AttributeError):
                code = None
            raise AcceptanceError(stage, code if isinstance(code, str) else "unexpected_http_status", status)
        require(content_type == "text/plain" and "synthetic-rfq-draft.txt" in disposition,
                stage, "download_headers_wrong")
        try:
            return raw.decode("utf-8")
        except UnicodeError:
            raise AcceptanceError(stage, "download_not_utf8") from None


def denied(client: Client, stage: str, path: str, body: dict, csrf: str,
           expected_status: int, expected_code: str, *, origin: str | None = None) -> None:
    try:
        client.json(stage, path, body=body, csrf=csrf, origin=origin)
    except AcceptanceError as exc:
        if exc.status != expected_status or exc.code != expected_code:
            raise AcceptanceError(stage, exc.code, exc.status) from None
    else:
        raise AcceptanceError(stage, "unexpected_success")


def denied_status(client: Client, stage: str, path: str, body: dict, csrf: str,
                  expected_status: int) -> None:
    try:
        client.json(stage, path, body=body, csrf=csrf)
    except AcceptanceError as exc:
        if exc.status != expected_status:
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


def selected_fixture(plan: dict, offers: dict[str, dict]) -> str | None:
    return next((fixture for fixture, offer in offers.items()
                 if offer["offer_id"] == plan.get("selected_offer_id")), None)


def check_recovery(view: dict, baseline: dict, offers: dict[str, dict],
                   *, selected: str | None, total: str | None, delta: str | None,
                   outcome: str, delayed: dict[str, int]) -> dict:
    stage = "recovery_" + outcome
    require(view.get("synthetic") is True and isinstance(view.get("comparison_id"), str)
            and bool(view["comparison_id"]) and view.get("replayed") is False,
            stage, "invalid_comparison")
    base = view.get("baseline")
    scenario = view.get("scenario")
    require(isinstance(base, dict) and isinstance(scenario, dict)
            and base == baseline and base.get("plan_id") == baseline.get("plan_id"),
            stage, "baseline_changed")
    require(view.get("outcome") == outcome and view.get("selection_changed") is (selected != "eu-copper"),
            stage, "outcome_wrong")
    require(scenario.get("synthetic") is True and scenario.get("reporting_currency") == "USD"
            and scenario.get("offer_versions") == baseline.get("offer_versions")
            and scenario.get("forecast_action") == "ABSTAIN"
            and scenario.get("customer_savings") is None,
            stage, "scenario_provenance_wrong")
    require(selected_fixture(scenario, offers) == selected, stage, "scenario_selection_wrong")
    require((scenario.get("total_cost") is None if total is None else
             numeric(scenario.get("total_cost"), stage) == Decimal(total)), stage, "scenario_cost_wrong")
    require((view.get("cost_delta") is None if delta is None else
             numeric(view.get("cost_delta"), stage) == Decimal(delta)), stage, "cost_delta_wrong")
    original = view.get("baseline_request")
    scenario_request = view.get("scenario_request")
    require(isinstance(original, dict) and isinstance(scenario_request, dict)
            and original.get("delay_days_by_offer", {}) == {}
            and scenario_request.get("delay_days_by_offer", {}) == delayed
            and {k: v for k, v in scenario_request.items() if k != "delay_days_by_offer"}
            == {k: v for k, v in original.items() if k != "delay_days_by_offer"},
            stage, "request_pair_wrong")
    alternatives = {entry["offer_id"]: entry for entry in scenario.get("alternatives", [])
                    if isinstance(entry, dict) and isinstance(entry.get("offer_id"), str)}
    require(set(alternatives) == {offer["offer_id"] for offer in offers.values()},
            stage, "alternatives_wrong")
    eu = alternatives[offers["eu-copper"]["offer_id"]]
    swift = alternatives[offers["express-copper"]["offer_id"]]
    require(numeric(eu.get("total_cost"), stage) == Decimal("82000")
            and numeric(swift.get("total_cost"), stage) == Decimal("86000"),
            stage, "quoted_cost_changed")
    require(eu.get("arrival_date") == ("2026-11-13" if delayed.get(offers["eu-copper"]["offer_id"]) == 20 else "2026-10-24")
            and swift.get("arrival_date") == ("2026-11-07" if delayed.get(offers["express-copper"]["offer_id"]) == 10 else "2026-10-28"),
            stage, "arrival_wrong")
    return {"selected_fixture": selected, "total_cost": scenario.get("total_cost"),
            "cost_delta": view.get("cost_delta"), "outcome": outcome}


def check_rfq(rfq: dict, plan: dict, offers: dict[str, dict], client: Client,
              *, fixture: str) -> dict:
    stage = "rfq_" + fixture.replace("-", "_")
    require(isinstance(rfq, dict) and isinstance(rfq.get("draft_id"), str)
            and bool(rfq["draft_id"]) and rfq.get("plan_id") == plan.get("plan_id")
            and rfq.get("offer_versions") == plan.get("offer_versions")
            and rfq.get("synthetic") is True and rfq.get("status") == "draft_unsent",
            stage, "draft_identity_wrong")
    require(isinstance(rfq.get("supplier"), str)
            and rfq["supplier"].startswith(FIXTURES[fixture][1])
            and rfq.get("material") == "copper"
            and numeric(rfq.get("quantity_kg"), stage) == Decimal("8000")
            and rfq.get("need_by") == "2026-11-05"
            and rfq.get("reporting_currency") == "USD"
            and numeric(rfq.get("total_cost"), stage) == numeric(plan.get("total_cost"), stage)
            and isinstance(rfq.get("created_at"), str) and bool(rfq["created_at"]),
            stage, "draft_facts_wrong")
    body = rfq.get("text")
    require(isinstance(body, str) and bool(body), stage, "draft_text_missing")
    downloaded = client.text(stage + "_download", "/api/demo/rfq/" +
                             urllib.parse.quote(rfq["draft_id"], safe="") + "/download")
    require(downloaded == body, stage, "download_mismatch")
    lower = body.lower()
    require(all(term in lower for term in ("synthetic", "unsent", "request for quotation"))
            and ("no order" in lower or "not an order" in lower or "no purchase order" in lower)
            and "8000" in body and "2026-11-05" in body
            and FIXTURES[fixture][1].lower() in lower
            and ("SYN-" + ("eu-copper.csv" if fixture == "eu-copper" else "express-copper.pdf") in body
                 or "sha256:" + FIXTURES[fixture][0] in body),
            stage, "draft_text_facts_wrong")
    if fixture == "express-copper":
        require("10.50" in body and "USD" in body and "86000" in body
                and "20" in body, stage, "swift_quote_or_delay_missing")
    if fixture == "eu-copper":
        require("8.00" in body and "EUR" in body, stage, "eu_quote_missing")
    return {"fixture": fixture, "total_cost": rfq.get("total_cost"),
            "text_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest()}


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
    settlement = late["fields"].get("settlement_currency", {})
    require(settlement.get("status") in {"missing", "unverified"}
            and settlement.get("value") is None,
            "late_review", "unstated_settlement_was_accepted")
    late_reasons = late.get("blocking_reasons")
    require(isinstance(late_reasons, list), "late_review", "blocking_reasons_missing")
    for reason in late_reasons:
        if not isinstance(reason, dict) or reason.get("code") != "field_" + settlement["status"] or reason.get("field") != "settlement_currency":
            code = reason.get("code") if isinstance(reason, dict) else "invalid_reason"
            raise AcceptanceError("late_review", code if isinstance(code, str) else "invalid_reason")
    require(len(late_reasons) == 1, "late_review", "settlement_blocker_missing")
    denied(client, "unreviewed_save_blocked", "/api/demo/offers/" + late["offer_id"] + "/save",
           {"expected_version": late["version"], "idempotency_key": key()}, csrf, 409, "save_blocked")
    receipt["checks"]["unreviewed_save_blocked"] = True
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

    versions = main["offer_versions"]
    baseline_rfq_body = {"plan_id": main["plan_id"], "expected_versions": versions,
                         "reviewed": True, "idempotency_key": key()}
    denied(client, "rfq_review_required", "/api/demo/rfq",
           {**baseline_rfq_body, "reviewed": False, "idempotency_key": key()},
           csrf, 422, "review_required")
    baseline_rfq = client.json("baseline_rfq", "/api/demo/rfq",
                               body=baseline_rfq_body, csrf=csrf)
    receipt["rfqs"]["baseline"] = check_rfq(baseline_rfq, main, offers, client,
                                              fixture="eu-copper")
    require(client.json("baseline_rfq_replay", "/api/demo/rfq", body=baseline_rfq_body,
                        csrf=csrf) == baseline_rfq,
            "baseline_rfq_replay", "exact_replay_changed_draft")
    receipt["checks"]["reviewed_baseline_rfq_download"] = True

    eu_id = offers["eu-copper"]["offer_id"]
    swift_id = offers["express-copper"]["offer_id"]
    delay_body = {"baseline_plan_id": main["plan_id"], "expected_versions": versions,
                  "delay_days_by_offer": {eu_id: 20}, "idempotency_key": key()}
    for bad in (-1, 1.5, True, 3651):
        denied_status(client, "invalid_delay", "/api/demo/recovery",
                      {**delay_body, "delay_days_by_offer": {eu_id: bad},
                       "idempotency_key": key()}, csrf, 422)
    denied_status(client, "unknown_delay_offer", "/api/demo/recovery",
                  {**delay_body, "delay_days_by_offer": {"foreign-offer": 1},
                   "idempotency_key": key()}, csrf, 422)
    require(client.json("post_invalid_delay_session", "/api/demo/session").get("rfq")
            == baseline_rfq, "post_invalid_delay_session", "valid_draft_overwritten")
    receipt["checks"]["malformed_delay_rejected"] = True

    delayed = client.json("eu_delay", "/api/demo/recovery", body=delay_body, csrf=csrf)
    receipt["recoveries"]["eu_delay"] = check_recovery(
        delayed, main, offers, selected="express-copper", total="86000", delta="4000",
        outcome="changed", delayed={eu_id: 20})
    require(delayed.get("baseline_request") == plan_body,
            "eu_delay", "baseline_request_changed")
    require(client.json("eu_delay_replay", "/api/demo/recovery", body=delay_body,
                        csrf=csrf).get("replayed") is True,
            "eu_delay_replay", "exact_replay_missing")
    delayed_session = client.json("delayed_session", "/api/demo/session")
    require(delayed_session.get("recovery") == delayed
            and delayed_session.get("plan") == delayed["scenario"],
            "delayed_session", "recovery_restore_wrong")
    try:
        client.text("old_rfq_download", "/api/demo/rfq/" +
                    urllib.parse.quote(baseline_rfq["draft_id"], safe="") + "/download")
    except AcceptanceError as exc:
        require(exc.status == 404 and exc.code == "draft_not_found",
                "old_rfq_download", "old_draft_not_invalidated")
    else:
        raise AcceptanceError("old_rfq_download", "unexpected_success")
    denied(client, "old_plan_rfq", "/api/demo/rfq",
           {**baseline_rfq_body, "idempotency_key": key()}, csrf, 409, "stale_plan")
    denied_status(client, "old_rfq_replay", "/api/demo/rfq", baseline_rfq_body, csrf, 409)
    receipt["checks"]["delay_changed_selection_and_stale_draft"] = True

    delayed_plan = delayed["scenario"]
    delayed_rfq_body = {"plan_id": delayed_plan["plan_id"], "expected_versions": versions,
                        "reviewed": True, "idempotency_key": key()}
    delayed_rfq = client.json("delayed_rfq", "/api/demo/rfq",
                              body=delayed_rfq_body, csrf=csrf)
    receipt["rfqs"]["eu_delay"] = check_rfq(delayed_rfq, delayed_plan, offers, client,
                                              fixture="express-copper")
    require(client.json("delayed_rfq_session", "/api/demo/session").get("rfq") == delayed_rfq,
            "delayed_rfq_session", "draft_restore_wrong")
    require(client.json("delayed_rfq_export", "/api/demo/export").get("rfq") == delayed_rfq,
            "delayed_rfq_export", "draft_export_wrong")
    denied(other, "cross_session_rfq", "/api/demo/rfq",
           {**delayed_rfq_body, "idempotency_key": key()}, other_csrf, 409, "stale_plan")
    denied_status(other, "cross_session_recovery", "/api/demo/recovery",
                  {**delay_body, "idempotency_key": key()}, other_csrf, 409)
    denied(client, "rfq_bad_csrf", "/api/demo/rfq",
           {**delayed_rfq_body, "idempotency_key": key()}, "invalid", 403, "invalid_csrf")
    denied(client, "recovery_bad_csrf", "/api/demo/recovery",
           {**delay_body, "idempotency_key": key()}, "invalid", 403, "invalid_csrf")
    try:
        other.text("cross_session_download", "/api/demo/rfq/" +
                   urllib.parse.quote(delayed_rfq["draft_id"], safe="") + "/download")
    except AcceptanceError as exc:
        require(exc.status == 404 and exc.code == "draft_not_found",
                "cross_session_download", "foreign_draft_visible")
    else:
        raise AcceptanceError("cross_session_download", "unexpected_success")
    receipt["checks"]["delayed_rfq_and_cross_session_protection"] = True

    both_body = {**delay_body, "delay_days_by_offer": {eu_id: 20, swift_id: 10},
                 "idempotency_key": key()}
    both = client.json("both_delayed", "/api/demo/recovery", body=both_body, csrf=csrf)
    receipt["recoveries"]["both_delayed"] = check_recovery(
        both, main, offers, selected=None, total=None, delta=None,
        outcome="infeasible", delayed={eu_id: 20, swift_id: 10})
    denied(client, "infeasible_rfq", "/api/demo/rfq",
           {"plan_id": both["scenario"]["plan_id"], "expected_versions": versions,
            "reviewed": True, "idempotency_key": key()}, csrf, 409, "draft_unavailable")
    require(client.json("infeasible_session", "/api/demo/session").get("recovery") == both,
            "infeasible_session", "failed_rfq_overwrote_comparison")
    denied_status(client, "stale_delay_replay", "/api/demo/recovery", delay_body, csrf, 409)
    receipt["checks"]["infeasible_blocks_rfq_and_stale_replay"] = True

    remove_body = {**delay_body, "delay_days_by_offer": {}, "idempotency_key": key()}
    removed = client.json("delay_removed", "/api/demo/recovery", body=remove_body, csrf=csrf)
    receipt["recoveries"]["removed"] = check_recovery(
        removed, main, offers, selected="eu-copper", total="82000", delta="0",
        outcome="unchanged", delayed={})
    current_rfq = client.json("current_rfq", "/api/demo/rfq",
                              body={"plan_id": removed["scenario"]["plan_id"],
                                    "expected_versions": versions, "reviewed": True,
                                    "idempotency_key": key()}, csrf=csrf)
    receipt["rfqs"]["removed"] = check_rfq(current_rfq, removed["scenario"], offers,
                                             client, fixture="eu-copper")
    current_export = client.json("current_export", "/api/demo/export")
    require(current_export.get("plan") == removed["scenario"]
            and current_export.get("baseline_plan") == main
            and current_export.get("recovery") == removed
            and current_export.get("rfq") == current_rfq
            and current_export.get("plan_request") == plan_body,
            "current_export", "paired_state_missing")
    restored = client.json("current_session", "/api/demo/session")
    require(restored.get("plan") == removed["scenario"]
            and restored.get("recovery") == removed
            and restored.get("rfq") == current_rfq,
            "current_session", "paired_state_restore_wrong")
    for fixture, offer in offers.items():
        persisted = next((entry for entry in current_export.get("offers", [])
                          if isinstance(entry, dict) and entry.get("offer_id") == offer["offer_id"]), None)
        require(persisted == offer, "current_export", "quote_evidence_changed")
    receipt["checks"]["removal_export_restore_and_evidence"] = True

    fresh = client.json("fresh_plan", "/api/demo/plan", body=plan_body, csrf=csrf)
    require(check_plan(fresh, offers, low_budget=False) == receipt["plans"]["main"],
            "fresh_plan", "fresh_plan_wrong")
    fresh_session = client.json("fresh_plan_session", "/api/demo/session")
    require(fresh_session.get("plan") == fresh and fresh_session.get("recovery") is None
            and fresh_session.get("rfq") is None,
            "fresh_plan_session", "old_derived_state_retained")
    denied_status(client, "old_comparison_replay", "/api/demo/recovery", remove_body, csrf, 409)
    receipt["checks"]["new_plan_invalidates_derived_state"] = True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="HTTPS Cloud Run base URL")
    parser.add_argument("--output", required=True, type=Path, help="Task-owned receipt JSON path")
    args = parser.parse_args()
    receipt = {"success": False, "deployed_revision": None,
               "fixture_hashes": {name: value[0] for name, value in FIXTURES.items()},
               "checks": {}, "extractions": [], "plans": {}, "recoveries": {},
               "rfqs": {}, "errors": []}
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
