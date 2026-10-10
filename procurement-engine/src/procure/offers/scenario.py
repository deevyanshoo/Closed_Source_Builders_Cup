"""Expiry and payment-timing scenarios for offers (issue #3, offline).

- `validity_at`: a quote is open through its valid_until date. A missing date is `not_stated`, never assumed open.
- `comparison_set`: expired, unstated-validity and rejected offers stay listed but are excluded, each with a reason.
- `cash_timing`: places each payment instalment on the user-entered date of its event, in the quote currency.
- `compare_terms`: the change in amount-weighted payment days between two schedules, and its value at a rate the
  user enters. No rate is assumed and no FX conversion is made.

All amounts are exact decimal text; only the rate-based value is rounded, half-even to 0.01. The web page's local
adapter (apps/web/src/offers-scenario.ts) mirrors these rules and is checked against examples this module exports.
"""
from __future__ import annotations

import datetime as dt
import re
from decimal import ROUND_HALF_EVEN, Decimal, Inexact, localcontext

from procure.offers.normalize import KG_PER_UNIT
from procure.offers.terms import USABLE, parse_payment_terms, plain

EVENTS = ("shipment", "bill_of_lading", "documents", "invoice", "delivery", "arrival")
DAY_COUNT = 365
_RATE = re.compile(r"^(0|[1-9]\d{0,2})(\.\d{1,6})?$")
ASSUMPTIONS = (
    "Synthetic scenario on synthetic quotes. Nothing is ordered, paid or sent.",
    "A quote is open through its valid-until date. A missing date is not stated and is never treated as open.",
    "The order is placed on the decision date, so advance payments fall on it.",
    "Shipment, bill of lading, documents, invoice, delivery and arrival dates are user inputs, not forecasts.",
    "An instalment due before an event is placed on its stated deadline, the latest it may be paid.",
    "Amounts are quantity times unit price in the quote currency, with no FX conversion, tax or freight added.",
    "The value of paying later uses the annual rate you enter, simple interest on actual days over 365, "
    "rounded half-even to 0.01. No rate is assumed.",
)


def _date(text: str | None, name: str) -> dt.date | None:
    if text is None or text == "":
        return None
    if not isinstance(text, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise ValueError(f"{name} must be a date in YYYY-MM-DD")
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"{name} must be a real calendar date") from None


def _evidence(field: dict) -> list[dict]:
    return [{"locator": e["locator"], "quote": e["quote"]} for e in field.get("evidence") or []]


def validity_at(field: dict, on: dt.date) -> dict:
    """State of a valid_until field on a date: valid (with days left), expired, not_stated or unavailable."""
    if field.get("status") == "missing" or (field.get("status") in USABLE and not field.get("value")):
        return {"state": "not_stated", "valid_until": None, "days_left": None, "evidence": []}
    if field.get("status") not in USABLE:
        return {"state": "unavailable", "valid_until": None, "days_left": None, "evidence": []}
    until = _date(field["value"], "valid_until")
    left = (until - on).days
    return {"state": "valid" if left >= 0 else "expired", "valid_until": field["value"],
            "days_left": left if left >= 0 else None, "evidence": _evidence(field)}


def comparison_set(offers: list[dict], on: dt.date) -> dict:
    included, excluded = [], []
    for offer in offers:
        validity = validity_at(offer["fields"]["valid_until"], on)
        reason = ("rejected" if offer.get("status") == "rejected" else
                  {"expired": "expired", "not_stated": "validity_not_stated",
                   "unavailable": "validity_unavailable"}.get(validity["state"]))
        if reason:
            excluded.append({"offer_id": offer["offer_id"], "reason": reason,
                             "valid_until": offer["fields"]["valid_until"].get("value")})
        else:
            included.append(offer["offer_id"])
    return {"date": on.isoformat(), "included": included, "excluded": excluded}


def expiry_scenario(offers: list[dict], *, as_of: dt.date, decision_date: dt.date) -> dict:
    before, after = comparison_set(offers, as_of), comparison_set(offers, decision_date)
    dropped = [e for e in after["excluded"] if e["reason"] == "expired" and e["offer_id"] in before["included"]]
    return {"as_of": before, "decision": after,
            "newly_expired": [{"offer_id": e["offer_id"], "valid_until": e["valid_until"]} for e in dropped]}


def _unavailable(reason: str) -> dict:
    return {"amount": None, "currency": None, "reason": reason}


def offer_amount(offer: dict) -> dict:
    """Total price in the quote currency: sum of quantity x unit price over fixed-price items, exact or unavailable."""
    items = offer.get("normalized", {}).get("items") or []
    if not items:
        return _unavailable("no_items")
    total, currencies = Decimal(0), set()
    for item in items:
        quantity, pricing = item.get("quantity") or {}, item.get("pricing") or {}
        if quantity.get("status") != "ok" or pricing.get("status") != "ok":
            return _unavailable("price_or_quantity_unavailable")
        if pricing.get("type") != "fixed":
            return _unavailable("price_not_fixed")
        q, price = quantity["original"], pricing["original"]
        with localcontext() as context:
            context.prec = 80
            context.traps[Inexact] = True
            try:
                units = Decimal(q["amount"]) * KG_PER_UNIT[q["unit"]] / KG_PER_UNIT[price["per_unit"]]
            except Inexact:
                return _unavailable("unit_conversion_not_exact")
            total += units * Decimal(price["unit_price"]["amount"])
        currencies.add(price["unit_price"]["currency"])
    if len(currencies) != 1:
        return _unavailable("mixed_currencies")
    return {"amount": plain(total), "currency": currencies.pop(), "reason": None}


def cash_timing(terms: dict, *, amount: dict | None, dates: dict, decision_date: dt.date) -> dict:
    """Payment schedule for parsed terms. Incomplete when a percent, event or event date is not known."""
    events = {"order": decision_date, **{e: _date(dates.get(e), e) for e in EVENTS}}
    lines, missing = [], [] if terms["status"] == "parsed" else [f"terms_{terms['status']}"]
    for inst in terms["instalments"]:
        due, reason = None, None
        if inst["anchor"] == "not_stated":
            reason = "anchor_not_stated"
        elif events.get(inst["anchor"]) is None:
            reason = f"date_not_entered:{inst['anchor']}"
        else:
            offset = -(inst["days"] or 0) if inst["timing"] == "on_or_before" else (inst["days"] or 0)
            due = events[inst["anchor"]] + dt.timedelta(days=offset)
        if inst["percent"] is None:
            reason = reason or "percent_not_stated"
        share = None
        if amount and amount.get("amount") is not None and inst["percent"] is not None:
            share = {"amount": plain((Decimal(amount["amount"]) * Decimal(inst["percent"])).scaleb(-2)),
                     "currency": amount["currency"]}
        if reason and reason not in missing:
            missing.append(reason)
        lines.append({**{k: inst[k] for k in ("percent", "percent_basis", "instrument", "anchor", "days", "timing",
                                              "clause")},
                      "due_date": due.isoformat() if due else None,
                      "days_from_decision": (due - decision_date).days if due else None,
                      "amount": share, "reason": reason})
    total = sum((Decimal(line["percent"]) for line in lines if line["percent"] is not None), Decimal(0))
    complete = bool(lines) and not missing
    weighted = (plain(sum(Decimal(line["percent"]) * line["days_from_decision"] for line in lines).scaleb(-2))
                if complete else None)
    return {"decision_date": decision_date.isoformat(), "lines": lines, "complete": complete,
            "weighted_days": weighted, "total_percent": plain(total), "missing": missing}


def _rate(text: str | None) -> Decimal | None:
    if text is None:
        return None
    if not isinstance(text, str) or not _RATE.fullmatch(text) or Decimal(text) > 100:
        raise ValueError("rate_percent must be a decimal from 0 to 100, such as 8 or 7.25")
    return Decimal(text)


def compare_terms(base: dict, alternative: dict, *, amount: dict | None, rate_percent: str | None) -> dict:
    """Change in amount-weighted payment days (alternative minus base) and, with a rate, the value of that delay."""
    rate = _rate(rate_percent)
    out = {"weighted_days_change": None, "value_of_later_payment": None, "rate_percent": rate_percent,
           "day_count": DAY_COUNT, "rounding": "half_even_0.01", "reason": None}
    if not (base["complete"] and alternative["complete"]):
        return {**out, "reason": "schedule_incomplete"}
    change = Decimal(alternative["weighted_days"]) - Decimal(base["weighted_days"])
    out["weighted_days_change"] = plain(change)
    if rate is None:
        return {**out, "reason": "no_rate_entered"}
    if not amount or amount.get("amount") is None:
        return {**out, "reason": "amount_unavailable"}
    with localcontext() as context:
        context.prec = 80
        value = (Decimal(amount["amount"]) * rate * change / (100 * DAY_COUNT)).quantize(Decimal("0.01"),
                                                                                         ROUND_HALF_EVEN)
    return {**out, "value_of_later_payment": {"amount": format(value, "f"), "currency": amount["currency"]}}


def offer_scenario(offer: dict, offers: list[dict], *, as_of: dt.date, decision_date: dt.date, dates: dict,
                   rate_percent: str | None = None, alternative_offer_id: str | None = None) -> dict:
    """Everything the /offers scenario panel shows and exports for one offer."""
    terms = parse_payment_terms(offer["fields"]["payment_terms"])
    amount = offer_amount(offer)
    usable_amount = amount if amount["amount"] is not None else None
    schedule = cash_timing(terms, amount=usable_amount, dates=dates, decision_date=decision_date)
    alternative = comparison = None
    if alternative_offer_id:
        other = next((o for o in offers if o["offer_id"] == alternative_offer_id), None)
        if other is None:
            raise ValueError(f"unknown alternative offer {alternative_offer_id}")
        alt_terms = parse_payment_terms(other["fields"]["payment_terms"])
        alt_schedule = cash_timing(alt_terms, amount=usable_amount, dates=dates, decision_date=decision_date)
        alternative = {"offer_id": alternative_offer_id, "payment_terms": alt_terms, "schedule": alt_schedule}
        comparison = compare_terms(schedule, alt_schedule, amount=usable_amount, rate_percent=rate_percent)
    else:
        _rate(rate_percent)
    return {"synthetic": True, "assumptions": list(ASSUMPTIONS), "as_of": as_of.isoformat(),
            "decision_date": decision_date.isoformat(),
            "event_dates": {e: dates.get(e) or None for e in EVENTS}, "rate_percent": rate_percent,
            "validity": {"as_of": validity_at(offer["fields"]["valid_until"], as_of),
                         "decision": validity_at(offer["fields"]["valid_until"], decision_date)},
            "comparison": expiry_scenario(offers, as_of=as_of, decision_date=decision_date),
            "payment_terms": terms, "amount": amount, "schedule": schedule,
            "alternative": alternative, "terms_comparison": comparison}
