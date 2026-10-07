"""Offer lifecycle rules: statuses, allowed moves, the save gate and correction validation.

Statuses
  extracted     every critical field verified, ready to save
  needs_review  a critical field is missing, conflicting or unverified
  corrected     a reviewer changed at least one field
  saved         reviewed and accepted as a comparable offer
  used_in_plan  included in a planning input
  rfq_drafted   a local RFQ draft was prepared from it (nothing is sent)
  rejected      a reviewer rejected it (terminal)
  expired       its validity date has passed (terminal)

Save is blocked while the quote is expired or any critical field is not extracted or corrected.
Pure functions only; persistence and audit live in store.py.
"""
from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from ..settings import materials_config
from . import countries
from . import normalize as nz
from .evidence import CRITICAL_FIELDS, base_name

TRANSITIONS: dict[str, tuple[set[str], str]] = {
    "correct": ({"extracted", "needs_review", "corrected"}, "corrected"),
    "save": ({"extracted", "needs_review", "corrected"}, "saved"),
    "reject": ({"extracted", "needs_review", "corrected", "saved"}, "rejected"),
    "use_in_plan": ({"saved", "used_in_plan"}, "used_in_plan"),
    "draft_rfq": ({"saved", "used_in_plan"}, "rfq_drafted"),
    "expire": ({"extracted", "needs_review", "corrected", "saved", "used_in_plan"}, "expired"),
}
TERMINAL = {"rejected", "expired", "rfq_drafted"}
USABLE = {"extracted", "corrected"}
OFFER_FIELD_KINDS = {
    "supplier_name": "text", "origin_country": "text", "incoterm": "incoterm", "settlement_currency": "currency",
    "payment_terms": "text", "valid_until": "date", "lead_time_days": "integer", "min_order": "quantity",
    "capacity": "quantity", "shipments": "shipments",
}
ITEM_FIELD_KINDS = {"material": "material", "description": "text", "grade": "text", "quantity": "quantity",
                    "pricing": "pricing"}
_ITEM_FIELD = re.compile(r"^items\[(\d+)\]\.(\w+)$")
_AMOUNT = re.compile(r"^(0|[1-9]\d*)(\.\d+)?$")
_PERIODS = {"month", "quarter", "year", "week"}


class OfferError(ValueError):
    """A rejected offer request. `code` is stable; `status` is the HTTP status to use."""

    STATUS = {"not_found": 404, "invalid_request": 400, "unknown_field": 422, "invalid_value": 422,
              "evidence_not_verified": 422, "document_unreadable": 422, "save_blocked": 409,
              "invalid_transition": 409, "stale_version": 409, "idempotency_conflict": 409,
              "planning_unavailable": 422}

    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details
        self.status = self.STATUS.get(code, 400)

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, **self.details}


# ---------- fields ----------

def field_kind(name: str) -> str:
    if name in OFFER_FIELD_KINDS:
        return OFFER_FIELD_KINDS[name]
    m = _ITEM_FIELD.match(name)
    if m and m.group(2) in ITEM_FIELD_KINDS:
        return ITEM_FIELD_KINDS[m.group(2)]
    raise OfferError("unknown_field", f"{name} is not an offer field")


def get_field(payload: dict, name: str) -> dict:
    field_kind(name)
    m = _ITEM_FIELD.match(name)
    if not m:
        return payload["fields"][name]
    index = int(m.group(1))
    if index >= len(payload["items"]):
        raise OfferError("unknown_field", f"{name}: the offer has {len(payload['items'])} item(s)")
    return payload["items"][index][m.group(2)]


def all_fields(payload: dict):
    yield from payload["fields"].items()
    for i, item in enumerate(payload["items"]):
        for key, f in item.items():
            yield f"items[{i}].{key}", f


def usable(field: dict) -> Any:
    return field["value"] if field["status"] in USABLE else None


# ---------- correction values ----------

def _amount_ok(value: Any) -> bool:
    return isinstance(value, str) and bool(_AMOUNT.match(value)) and Decimal(value) > 0


def _money_ok(money: Any) -> bool:
    return (isinstance(money, dict) and set(money) == {"amount", "currency"} and _amount_ok(money["amount"])
            and isinstance(money["currency"], str) and re.fullmatch(r"[A-Z]{3}", money["currency"]) is not None)


def _quantity_problems(q: Any, allow_period: bool) -> list[str]:
    keys = {"amount", "unit", "period"} if allow_period else {"amount", "unit"}
    if not isinstance(q, dict) or not {"amount", "unit"} <= set(q) or set(q) - keys:
        return ["quantity must be {amount, unit" + (", period" if allow_period else "") + "}"]
    out = [] if _amount_ok(q["amount"]) else ["amount must be a positive decimal string"]
    out += [] if q["unit"] in nz.KG_PER_UNIT else [f"unit must be one of {sorted(nz.KG_PER_UNIT)}"]
    if q.get("period") is not None and q["period"] not in _PERIODS:
        out.append(f"period must be one of {sorted(_PERIODS)}")
    return out


def value_problems(kind: str, value: Any, name: str = "") -> list[str]:
    """Shape problems for a reviewer-entered value; an empty list means it is well formed."""
    if kind == "text":
        return [] if isinstance(value, str) and 0 < len(value.strip()) <= 500 else ["text must be 1 to 500 characters"]
    if kind == "date":
        try:
            dt.date.fromisoformat(value)
            return [] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) else ["date must be YYYY-MM-DD"]
        except (TypeError, ValueError):
            return ["date must be YYYY-MM-DD"]
    if kind == "integer":
        ok = isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 3650
        return [] if ok else ["must be a whole number of days from 0 to 3650"]
    if kind == "currency":
        return [] if isinstance(value, str) and re.fullmatch(r"[A-Z]{3}", value) else ["currency must be an ISO code"]
    if kind == "quantity":
        return _quantity_problems(value, allow_period=base_name(name) == "capacity")
    if kind == "incoterm":
        ok = (isinstance(value, dict) and set(value) <= {"code", "place"} and value.get("code") in nz.INCOTERMS_2020
              and (value.get("place") is None or isinstance(value["place"], str)))
        return [] if ok else [f"incoterm must be {{code, place}} with code in {list(nz.INCOTERMS_2020)}"]
    if kind == "pricing":
        if not isinstance(value, dict) or value.get("per_unit") not in nz.KG_PER_UNIT:
            return [f"pricing needs per_unit in {sorted(nz.KG_PER_UNIT)}"]
        if value.get("type") == "fixed":
            ok = set(value) == {"type", "unit_price", "per_unit"} and _money_ok(value["unit_price"])
            return [] if ok else ["fixed pricing is {type, unit_price{amount, currency}, per_unit}"]
        if value.get("type") == "index_linked":
            ok = (set(value) <= {"type", "index", "averaging_period", "premium", "per_unit"}
                  and isinstance(value.get("index"), str) and value["index"].strip()
                  and _money_ok(value.get("premium")))
            return [] if ok else ["index-linked pricing needs index and premium{amount, currency}"]
        return ["pricing type must be fixed or index_linked"]
    if kind == "shipments":
        if not isinstance(value, list) or not value:
            return ["shipments must be a non-empty list of lots"]
        out = []
        for lot in value:
            if not isinstance(lot, dict) or set(lot) != {"quantity", "window"} or not isinstance(lot["window"], str):
                out.append("each lot is {quantity, window}")
                continue
            out += _quantity_problems(lot["quantity"], allow_period=False)
        return out
    if kind == "material":
        return [] if isinstance(value, str) and value in materials_config()["materials"] else [
            "material must be a catalogue key"]
    return [f"unknown kind {kind}"]


# ---------- gate and status ----------

def blocking_reasons(payload: dict, today: dt.date) -> list[dict]:
    reasons = []
    if not payload["items"]:
        reasons.append({"code": "no_items", "field": "items", "message": "no offered item was found"})
    for name, f in all_fields(payload):
        if base_name(name) in CRITICAL_FIELDS and f["status"] not in USABLE:
            reasons.append({"code": f"field_{f['status']}", "field": name, "message": f"{name} is {f['status']}"})
    valid_until = usable(payload["fields"]["valid_until"])
    if valid_until and nz.expiry_status(valid_until, today) == "expired":
        reasons.insert(0, {"code": "expired", "field": "valid_until",
                           "message": f"the quote expired on {valid_until}"})
    return reasons


def initial_status(payload: dict, today: dt.date) -> str:
    reasons = blocking_reasons(payload, today)
    if any(r["code"] == "expired" for r in reasons):
        return "expired"
    return "needs_review" if reasons else "extracted"


def allowed_actions(status: str, reasons: list[dict]) -> list[str]:
    out = [a for a, (sources, _) in TRANSITIONS.items() if status in sources and a != "expire"]
    if "save" in out and reasons:
        out.remove("save")
    return out


def compute_normalized(payload: dict, today: dt.date, rate_lookup=None) -> dict:
    fields = payload["fields"]
    reporting = payload.get("reporting_currency", "USD")

    def quantity(f):
        v = usable(f)
        return nz.normalize_quantity(v) if v else None

    out = {"as_of": today.isoformat(), "reporting_currency": reporting,
           "origin_country_code": countries.code_for(usable(fields["origin_country"])),
           "expiry": nz.expiry_status(usable(fields["valid_until"]), today),
           "min_order": quantity(fields["min_order"]), "capacity": quantity(fields["capacity"]), "items": []}
    for item in payload["items"]:
        price = usable(item["pricing"])
        out["items"].append({
            "quantity": quantity(item["quantity"]),
            "pricing": nz.normalize_pricing(price, reporting_currency=reporting, as_of=today,
                                            rate_lookup=rate_lookup) if price else None,
        })
    return out


def decimal_or_none(text: Any) -> Decimal | None:
    try:
        return Decimal(str(text)) if text is not None else None
    except InvalidOperation:
        return None
