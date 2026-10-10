"""Payment terms: turn the extracted payment_terms text into instalments, or say why not.

Each instalment states its share of the price (`percent`), the payment instrument, the event it is tied to
(`anchor`), the days after that event and whether it is due on or by that date. Anything the text does not state stays
"not_stated"; a clause that cannot be read is kept verbatim under `unrecognised` and gets no share of the price.
The only inference is that a single term with no percentage covers the whole price (`percent_basis: single_term`).
The result keeps the field's evidence locators, so every instalment points back to its page line or cell.
"""
from __future__ import annotations

import re
from decimal import Decimal

USABLE = ("extracted", "corrected")
_LABEL = re.compile(r"^\s*payment(?:\s+terms)?\s*:\s*", re.I)
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_DAYS = re.compile(r"\b(\d{1,4})\s*days?\b", re.I)
_INSTRUMENTS = (
    ("cash_against_documents", re.compile(r"\bcash\s+against\s+documents\b|\bCAD\b", re.I)),
    ("letter_of_credit", re.compile(r"\bL/?C\b|\bletter\s+of\s+credit\b", re.I)),
    ("telegraphic_transfer", re.compile(r"\bT/?T\b|\btelegraphic\s+transfer\b|\bwire\s+transfer\b", re.I)),
    ("open_account", re.compile(r"^\s*net\b|\bopen\s+account\b", re.I)),
)
_EVENTS = {"bill of lading": "bill_of_lading", "bl": "bill_of_lading", "b/l": "bill_of_lading", "invoice": "invoice",
           "delivery": "delivery", "arrival": "arrival", "shipment": "shipment", "documents": "documents"}
_BEFORE = re.compile(r"\b(?:before|prior\s+to)\s+(shipment|loading)\b", re.I)
_ADVANCE = re.compile(r"\b(?:in\s+)?advance\b", re.I)
_SIGHT = re.compile(r"\bat\s+sight\b", re.I)
_AFTER = re.compile(r"\b(?:against|from|after|on)\s+(?:the\s+)?(?:copy\s+of\s+(?:the\s+)?)?"
                    r"(bill\s+of\s+lading|b/?l|invoice|delivery|arrival|shipment|documents)\b", re.I)


def plain(value: Decimal) -> str:
    """Exact decimal text without exponent or trailing fractional zeros."""
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _clause(text: str) -> dict | None:
    percent = _PERCENT.search(text)
    days = _DAYS.search(text)
    instrument = next((name for name, pattern in _INSTRUMENTS if pattern.search(text)), "not_stated")
    anchor, timing = "not_stated", "on"
    if m := _BEFORE.search(text):
        anchor, timing = "shipment", "on_or_before"
    elif _ADVANCE.search(text):
        anchor = "order"
    elif _SIGHT.search(text) or instrument == "cash_against_documents":
        anchor = "documents"
    elif m := _AFTER.search(text):
        anchor = _EVENTS[re.sub(r"\s+", " ", m.group(1).lower())]
    if not (percent or days or anchor != "not_stated" or instrument != "not_stated"):
        return None
    return {"percent": percent.group(1) if percent else None, "percent_basis": "stated" if percent else "not_stated",
            "instrument": instrument, "anchor": anchor,
            "days": int(days.group(1)) if days else (0 if anchor != "not_stated" else None),
            "timing": timing, "clause": text}


def parse_payment_terms(field: dict) -> dict:
    """Normalise one payment_terms field. Status: parsed, partial, inconsistent, unrecognised, not_stated, unavailable."""
    out = {"status": "not_stated", "text": None, "instalments": [], "unrecognised": [],
           "evidence": [{"locator": e["locator"], "quote": e["quote"]} for e in field.get("evidence") or []],
           "reasons": [], "source": field.get("source")}
    if field.get("status") == "missing" or (field.get("status") in USABLE and not field.get("value")):
        return {**out, "evidence": [], "reasons": ["payment_terms_missing"]}
    if field.get("status") not in USABLE or not isinstance(field.get("value"), str):
        return {**out, "status": "unavailable", "evidence": [], "reasons": [f"field_{field.get('status')}"]}
    text = field["value"]
    instalments, unrecognised = [], []
    for part in (p.strip() for p in re.split(r"[;,]", _LABEL.sub("", text))):
        if not part:
            continue
        parsed = _clause(part)
        if parsed is None:
            unrecognised.append(part)
        elif (instalments and parsed["percent"] and instalments[-1]["percent"] is None
              and parsed["anchor"] == "not_stated" and parsed["instrument"] == "not_stated" and parsed["days"] is None):
            instalments[-1].update(percent=parsed["percent"], percent_basis="stated",
                                   clause=f"{instalments[-1]['clause']}, {part}")
        else:
            instalments.append(parsed)
    out.update(text=text, instalments=instalments, unrecognised=unrecognised)
    if not instalments:
        return {**out, "status": "unrecognised", "reasons": ["no_recognised_terms"]}
    if len(instalments) == 1 and instalments[0]["percent"] is None and not unrecognised:
        instalments[0].update(percent="100", percent_basis="single_term")
    reasons = ["unrecognised_clause"] if unrecognised else []
    if any(i["percent"] is None for i in instalments):
        reasons.append("percentage_not_stated")
    total = sum((Decimal(i["percent"]) for i in instalments if i["percent"] is not None), Decimal(0))
    if total > 100:
        return {**out, "status": "inconsistent", "reasons": [*reasons, "percentages_exceed_100"]}
    if total < 100:
        reasons.append(f"percentages_sum_to_{plain(total)}")
    return {**out, "status": "partial" if reasons else "parsed", "reasons": reasons}
