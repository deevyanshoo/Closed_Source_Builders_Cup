"""Small, immutable-fixture quote boundary for the hosted demonstration.

Document evidence and offer field validation are delegated to the existing procurement
engine. Mutable state, ownership and idempotency belong to the session host.
"""
from __future__ import annotations

import copy
import hashlib
from datetime import date
from decimal import Decimal
from pathlib import Path

from procure.documents import DocumentReadError, read_document
from procure.offers import lifecycle as lc
from procure.offers.extraction import extract_offer
from procure.offers.fx_rates import FxRate

from .errors import DemoError


_FIXTURE_DIR = Path(__file__).resolve().parents[3] / "demo_fixtures"
_FIXTURES = (
    {"id": "late-copper", "name": "Atacama copper · lower price, late arrival",
     "filename": "SYN-late-copper.pdf", "description": "Synthetic PDF quote; settlement currency needs reviewer confirmation.",
     "sha256": "1256c543568466fe44d1f3791c2861e65bd21f833883f98c5a85eae2784398df"},
    {"id": "express-copper", "name": "Swift copper · on-time USD",
     "filename": "SYN-express-copper.pdf", "description": "Synthetic PDF quote with a 14-day lead time.",
     "sha256": "eb05f6b26257600f4ca1c23a8ba6d542fd21a97775084291257a45f9baad98ba"},
    {"id": "eu-copper", "name": "Europa copper · on-time EUR",
     "filename": "SYN-eu-copper.csv", "description": "Synthetic CSV quote; dated synthetic EUR/USD conversion.",
     "sha256": "8dc9d68ae21f47b03585325f2d2297a0e2710d3ef3a6bdce212faa5bdaf727a5"},
)
_BY_ID = {entry["id"]: entry for entry in _FIXTURES}


def list_fixtures() -> list[dict]:
    return [{key: entry[key] for key in ("id", "name", "filename", "description")}
            | {"download_url": f"/api/demo/fixtures/{entry['id']}"} for entry in _FIXTURES]


def fixture_bytes(fixture_id: str) -> tuple[str, bytes]:
    entry = _BY_ID.get(fixture_id)
    if entry is None:
        raise DemoError("fixture_not_found", "Synthetic fixture not found", 404)
    try:
        data = (_FIXTURE_DIR / entry["filename"]).read_bytes()
    except OSError:
        raise DemoError("fixture_unavailable", "Synthetic fixture is unavailable", 503) from None
    if hashlib.sha256(data).hexdigest() != entry["sha256"]:
        raise DemoError("fixture_unavailable", "Synthetic fixture failed its integrity check", 503)
    return entry["filename"], data


def match_fixture(filename: str, data: bytes) -> str:
    if not isinstance(filename, str) or not isinstance(data, bytes):
        raise DemoError("fixture_not_allowed", "Only the listed synthetic fixtures are accepted")
    digest = hashlib.sha256(data).hexdigest()
    for entry in _FIXTURES:
        if filename == entry["filename"] and digest == entry["sha256"]:
            return entry["id"]
    raise DemoError("fixture_not_allowed", "Only the listed synthetic fixtures are accepted")


def synthetic_rate(base: str, quote: str, on: date) -> FxRate:
    """One explicitly dated scenario rate, never a market-data lookup."""
    rate = Decimal("1.25") if (base, quote, on.isoformat()) == ("EUR", "USD", "2026-10-14") else None
    return FxRate(base, quote, rate, on.isoformat() if rate else None,
                  "synthetic_scenario" if rate else None,
                  "2026-10-14T00:00:00+00:00" if rate else None,
                  "cache" if rate else "unavailable",
                  "Fixed synthetic demonstration assumption" if rate else "No dated synthetic rate for this pair")


def extract_fixture(fixture_id: str, *, mode: str, as_of: date) -> dict:
    if mode not in {"rules", "gemini"} or not isinstance(as_of, date):
        raise DemoError("invalid_request", "Choose rules or Gemini and a valid date", 400)
    filename, data = fixture_bytes(fixture_id)
    try:
        doc = read_document(data, filename=filename)
        extraction = extract_offer(doc, mode=mode, as_of=as_of, reporting_currency="USD",
                                   rate_lookup=synthetic_rate, synthetic=True)
    except DocumentReadError:
        raise DemoError("fixture_unavailable", "Synthetic fixture cannot be read", 503) from None
    except Exception:
        if mode == "gemini":
            raise DemoError("gemini_unavailable", "Gemini extraction is unavailable", 503) from None
        raise
    if mode == "gemini" and not (extraction.extractor == "gemini"
                                  and extraction.extractor_detail.get("call") == "real"):
        raise DemoError("gemini_unavailable", "Gemini extraction is unavailable; select rules explicitly", 503)
    return extraction.to_dict()


def _view(payload: dict, *, today: date) -> dict:
    reasons = lc.blocking_reasons(payload, today)
    settlement = lc.usable(payload["fields"]["settlement_currency"])
    if settlement:
        for index, item in enumerate(payload["items"]):
            price = lc.usable(item["pricing"])
            money = price.get("unit_price") if isinstance(price, dict) and price.get("type") == "fixed" else None
            if isinstance(money, dict) and money.get("currency") != settlement:
                reasons.append({"code": "currency_conflict", "field": f"items[{index}].pricing",
                                "message": "Settlement and fixed price currencies disagree"})
    status = payload["status"]
    if status in lc.TRANSITIONS["expire"][0] and any(r["code"] == "expired" for r in reasons):
        status = "expired"
    payload["status"] = status
    payload["as_of"] = today.isoformat()
    payload["normalized"]["as_of"] = today.isoformat()
    payload["normalized"]["expiry"] = lc.nz.expiry_status(lc.usable(payload["fields"]["valid_until"]), today)
    payload["blocking_reasons"] = reasons
    payload["allowed_actions"] = lc.allowed_actions(status, reasons)
    return payload


def new_offer(payload: dict, *, offer_id: str, today: date) -> dict:
    if not isinstance(payload, dict) or not isinstance(offer_id, str) or not offer_id:
        raise DemoError("invalid_request", "A valid extraction and offer ID are required", 400)
    view = copy.deepcopy(payload)
    if view.get("extractor_detail", {}).get("requested") == "gemini" and not (
            view.get("extractor") == "gemini" and view["extractor_detail"].get("call") == "real"):
        raise DemoError("gemini_unavailable", "Gemini extraction was not confirmed as a real call", 503)
    view.update({"offer_id": offer_id, "version": 1, "synthetic": True,
                 "reporting_currency": "USD", "status": lc.initial_status(view, today)})
    return _view(view, today=today)


def _offer_error(exc: lc.OfferError) -> DemoError:
    return DemoError(exc.code, exc.message, exc.status)


def correct_offer(view: dict, *, field: str, value, today: date) -> dict:
    updated = copy.deepcopy(view)
    if updated.get("status") not in lc.TRANSITIONS["correct"][0]:
        raise DemoError("invalid_transition", "This offer cannot be corrected", 409)
    try:
        kind = lc.field_kind(field)
        current = lc.get_field(updated, field)
        if field == "capacity" and isinstance(value, dict) and value.get("period") is not None and not isinstance(value["period"], str):
            raise DemoError("invalid_value", "capacity period must be a supported text value")
        problems = lc.value_problems(kind, value, field)
    except lc.OfferError as exc:
        raise _offer_error(exc) from None
    if problems:
        raise DemoError("invalid_value", f"{field}: " + "; ".join(problems))
    previous = {"status": current["status"], "value": copy.deepcopy(current["value"])}
    if "original" in current:
        original = copy.deepcopy(current["original"])
    elif current["status"] == "corrected":
        # Older corrected offers have no recoverable document-backed snapshot.
        original = {"value": None, "status": "unavailable", "source": "unknown", "evidence": []}
    else:
        original = {key: copy.deepcopy(current.get(key)) for key in ("value", "status", "source", "evidence")}
    current.update({"status": "corrected", "value": value, "source": "user", "evidence": [],
                    "evidence_status": "user_entered_without_document_evidence", "previous": previous,
                    "original": original,
                    "corrected_by": "session_reviewer", "corrected_at": today.isoformat(), "reasons": []})
    updated["normalized"] = lc.compute_normalized(updated, today, rate_lookup=synthetic_rate)
    updated["version"] += 1
    updated["status"] = "corrected"
    return _view(updated, today=today)


def save_offer(view: dict, *, today: date) -> dict:
    updated = copy.deepcopy(view)
    updated = _view(updated, today=today)
    if updated["status"] not in lc.TRANSITIONS["save"][0]:
        raise DemoError("invalid_transition", "This offer cannot be saved", 409)
    if updated["blocking_reasons"]:
        raise DemoError("save_blocked", "Resolve the offer's blocking reasons before saving", 409)
    updated["status"] = "saved"
    updated["version"] += 1
    return _view(updated, today=today)
