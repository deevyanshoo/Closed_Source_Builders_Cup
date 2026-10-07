"""The synthetic quote manifest must be a trustworthy answer key.

These checks do not reuse the generator's logic. They read each document and confirm that
every expected value, including its unit and currency, can be seen in the evidence the
manifest cites, and that fields marked missing are really absent from the document.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pytest

from procure.documents import read_document

FIXTURES = Path(__file__).parent / "fixtures" / "offers"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
QUOTES = {q["id"]: q for q in MANIFEST["quotes"]}

COUNTRY_NAMES = {"CL": "Chile", "AE": "United Arab Emirates", "AU": "Australia", "DE": "Germany", "CN": "China",
                 "MY": "Malaysia", "PE": "Peru", "ZA": "South Africa", "BR": "Brazil", "ZM": "Zambia",
                 "CA": "Canada"}
UNIT_WORDS = {"tonne": ("tonne", "MT", "(t)", "/t)", "t/", " t ", " t,", "metric ton"), "kg": ("kg",), "lb": ("lb",)}
DATE_FORMATS = [(r"\d{4}-\d{2}-\d{2}", "%Y-%m-%d"), (r"\d{2}\.\d{2}\.\d{4}", "%d.%m.%Y"),
                (r"\d{2}/\d{2}/\d{4}", "%d/%m/%Y"), (r"\d{1,2} [A-Z][a-z]+ \d{4}", "%d %B %Y"),
                (r"\d{1,2} [A-Z][a-z]{2} \d{4}", "%d %b %Y"), (r"[A-Z][a-z]{2} \d{1,2}, \d{4}", "%b %d, %Y")]


def numbers(text: str) -> set[Decimal]:
    """Numbers in text, read both as 9,412.50 style and as 2.598,00 style."""
    found = set()
    for token in re.findall(r"\d[\d.,]*", text):
        token = token.rstrip(".,")
        for spelling in (token.replace(",", ""), token.replace(".", "").replace(",", ".")):
            try:
                found.add(Decimal(spelling))
            except InvalidOperation:
                pass
    return found


def dates(text: str) -> set[str]:
    found = set()
    for pattern, fmt in DATE_FORMATS:
        for token in re.findall(pattern, text):
            try:
                found.add(dt.datetime.strptime(token, fmt).date().isoformat())
            except ValueError:
                pass
    return found


def has_unit(texts: list[str], unit: str) -> bool:
    if unit == "tonne" and any(t.strip() == "t" for t in texts):  # a unit column holding just "t"
        return True
    return any(word in f" {t} " for t in texts for word in UNIT_WORDS[unit])


def amount_supported(amount: str, texts: list[str]) -> bool:
    """The amount is written in the evidence, or is the sum of separately cited numeric cells."""
    target = Decimal(amount)
    if any(target in numbers(t) for t in texts):
        return True
    cells = [Decimal(t.replace(",", "")) for t in texts if re.fullmatch(r"[\d,]+(\.\d+)?", t.strip())]
    return bool(cells) and sum(cells) == target


def iter_fields(expected: dict):
    for name, field in expected.items():
        if name == "items":
            for item in field:
                for key in ("description", "grade", "quantity", "pricing"):
                    yield key, item[key]
        else:
            yield name, field


def candidates(field: dict):
    if field["status"] == "present":
        yield field["value"], [e["text"] for e in field["evidence"]]
    elif field["status"] == "conflict":
        for c in field["candidates"]:
            yield c["value"], [e["text"] for e in c["evidence"]]


def problems_for(value, name: str, texts: list[str]) -> list[str]:
    joined = " | ".join(texts)
    if name == "origin_country":
        return [] if COUNTRY_NAMES[value] in joined else ["country name not in evidence"]
    if name == "settlement_currency":
        return [] if value in joined or (value == "USD" and "US$" in joined) else ["currency not in evidence"]
    if name == "valid_until":
        return [] if value in dates(joined) else ["date not in evidence"]
    if name == "lead_time_days":
        out = [] if Decimal(value) in numbers(joined) else ["days not in evidence"]
        return out + ([] if "day" in joined.lower() else ["unit 'days' not in evidence"])
    if name in ("min_order", "capacity", "quantity"):
        out = [] if amount_supported(value["amount"], texts) else ["amount not in evidence"]
        return out + ([] if has_unit(texts, value["unit"]) else [f"unit {value['unit']} not in evidence"])
    if name == "incoterm":
        place = value["place"].split(",")[0]
        return [] if value["code"] in joined and place in joined else ["incoterm/place not in evidence"]
    if name == "pricing":
        money = value["unit_price"] if value["type"] == "fixed" else value["premium"]
        out = [] if Decimal(money["amount"]) in numbers(joined) else ["price not in evidence"]
        if money["currency"] not in joined and not (money["currency"] == "USD" and "US$" in joined):
            out.append("price currency not in evidence")
        return out + ([] if has_unit(texts, value["per_unit"]) else [f"price unit {value['per_unit']} not in evidence"])
    if name == "shipments":
        out = []
        for lot in value:
            if not amount_supported(lot["quantity"]["amount"], texts):
                out.append(f"lot quantity {lot['quantity']['amount']} not in evidence")
            if not has_unit(texts, lot["quantity"]["unit"]):
                out.append("lot unit not in evidence")
        return out
    if isinstance(value, str):
        return [] if value in joined else ["text not in evidence"]
    return []


@pytest.mark.parametrize("quote_id", sorted(QUOTES))
def test_expected_values_are_visible_in_cited_evidence(quote_id):
    errors = []
    for name, field in iter_fields(QUOTES[quote_id]["expected"]):
        for value, texts in candidates(field):
            errors += [f"{name}: {p} ({texts})" for p in problems_for(value, name, texts)]
    assert not errors, "\n".join(errors)


@pytest.mark.parametrize("quote_id", sorted(QUOTES))
def test_fields_marked_missing_are_absent_from_the_document(quote_id):
    expected = QUOTES[quote_id]["expected"]
    text = " ".join(s.text for s in read_document(FIXTURES / QUOTES[quote_id]["file"]).segments).lower()
    words = {"valid_until": ("valid",), "lead_time_days": ("lead time",), "capacity": ("capacity",),
             "shipments": ("lots", "laycan", "shipments", "ship_month", "ship by")}
    for name, keywords in words.items():
        if expected[name]["status"] == "missing":
            hits = [k for k in keywords if k in text]
            assert not hits, f"{name} is marked missing but the document mentions {hits}"


def test_expired_quote_is_expired_on_the_manifest_date():
    as_of = MANIFEST["as_of"]
    for q in MANIFEST["quotes"]:
        valid = q["expected"]["valid_until"]
        is_expired = valid["status"] == "present" and valid["value"] < as_of
        assert is_expired == ("expired" in q["cases"]), q["id"]
