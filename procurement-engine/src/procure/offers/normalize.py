"""Reading numbers, dates, units, currencies and Incoterms out of quote text, with Decimal.

Nothing here guesses. A value that can be read two ways (for example 01/02/2026) is reported
as ambiguous, a unit like "ton" that can mean different masses is ambiguous, and an unknown
unit is unsupported. Callers turn those into missing, unverified or unavailable states.

Number style: "9,412.50" (point decimal) and "2.598,00" (comma decimal) are both read. A
lone comma followed by exactly three digits ("50,000") is a thousands separator unless the
caller says the document uses decimal commas. A lone point is a decimal point unless the
caller says the document uses decimal commas.
"""
from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal, InvalidOperation

# ---------- numbers ----------

_NUMBER = re.compile(r"(?<![\w.,])\d[\d.,]*")


def _decimal_separator(token: str, decimal_comma: bool) -> str | None:
    """Which character is the decimal separator in this token, or None for a whole number."""
    if "," in token and "." in token:
        return "," if token.rfind(",") > token.rfind(".") else "."
    sep = "," if "," in token else "." if "." in token else None
    if sep is None or token.count(sep) > 1:
        return None  # no separator, or a repeated one: thousands groups
    three_digit_tail = len(token.rsplit(sep, 1)[1]) == 3
    if sep == ",":
        return "," if decimal_comma or not three_digit_tail else None
    return None if decimal_comma and three_digit_tail else "."


def _parse(token: str, decimal_comma: bool) -> Decimal | None:
    token = token.rstrip(".,")
    if not token:
        return None
    decimal_sep = _decimal_separator(token, decimal_comma)
    integer, fraction = token.rsplit(decimal_sep, 1) if decimal_sep else (token, "")
    if len(set(re.findall(r"[.,]", integer))) > 1:
        return None  # mixed thousands separators
    groups = re.split(r"[.,]", integer)
    if len(groups) > 1 and (not 1 <= len(groups[0]) <= 3 or any(len(g) != 3 for g in groups[1:])):
        return None  # separators in the wrong places, for example a date like 25.10.2026
    digits = "".join(groups)
    if not digits.isdigit() or (fraction and not fraction.isdigit()):
        return None
    try:
        return Decimal(f"{digits}.{fraction}" if fraction else digits)
    except InvalidOperation:
        return None


def parse_decimal(text: str, *, decimal_comma: bool = False) -> Decimal | None:
    """The single number in `text`, or None if there is none or more than one."""
    found = [n for n in (_parse(t, decimal_comma) for t in _NUMBER.findall(text)) if n is not None]
    return found[0] if len(found) == 1 else None


def numbers_in(text: str) -> set[Decimal]:
    """Every number in the text, read in both styles (used to check a value is supported)."""
    out = set()
    for token in _NUMBER.findall(text):
        for style in (False, True):
            value = _parse(token, style)
            if value is not None:
                out.add(value)
    return out


def uses_decimal_comma(texts) -> bool:
    """True when the document writes numbers like 2.598,00 or 9,15 (comma as decimal separator)."""
    for text in texts:
        for token in _NUMBER.findall(text):
            token = token.rstrip(".,")
            if "," in token and "." in token and token.rfind(",") > token.rfind("."):
                return True
    return False


# ---------- dates ----------

_MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
     "November", "December"], start=1)}
_MONTHS.update({name[:3]: i for name, i in list(_MONTHS.items())})
_MONTH_WORD = r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"


def _date(y, m, d) -> str | None:
    try:
        return dt.date(int(y), int(m), int(d)).isoformat()
    except ValueError:
        return None


def dates_in(text: str) -> set[str]:
    """ISO dates written in the text. Day/month order that cannot be told apart is skipped."""
    found = set()
    for y, m, d in re.findall(r"\b(\d{4})-(\d{2})-(\d{2})\b", text):
        found.add(_date(y, m, d))
    for d, m, y in re.findall(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b", text):
        found.add(_date(y, m, d))  # dotted dates are day.month.year
    for a, b, y in re.findall(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", text):
        a, b = int(a), int(b)
        if a > 12 >= b:
            found.add(_date(y, b, a))
        elif b > 12 >= a:
            found.add(_date(y, a, b))
        # both 12 or less: ambiguous, not read
    for d, mon, y in re.findall(rf"\b(\d{{1,2}})\s+{_MONTH_WORD}\s*,?\s+(\d{{4}})\b", text, re.I):
        found.add(_date(y, _MONTHS[mon.lower()[:3]], d))
    for mon, d, y in re.findall(rf"\b{_MONTH_WORD}\s+(\d{{1,2}}),?\s+(\d{{4}})\b", text, re.I):
        found.add(_date(y, _MONTHS[mon.lower()[:3]], d))
    found.discard(None)
    return found


def months_in(text: str) -> set[str]:
    """Calendar months (YYYY-MM) written as 'November 2026', 'Nov 2026' or '2026-11'."""
    found = {f"{y}-{m}" for y, m in re.findall(r"\b(\d{4})-(\d{2})\b(?!-\d)", text)}
    for mon, y in re.findall(rf"\b{_MONTH_WORD}\s+(\d{{4}})\b", text, re.I):
        found.add(f"{y}-{_MONTHS[mon.lower()[:3]]:02d}")
    return found


# ---------- units ----------

KG_PER_UNIT = {"tonne": Decimal("1000"), "kg": Decimal("1"), "lb": Decimal("0.45359237")}  # exact definitions
_UNIT_PATTERNS = {
    "tonne": [r"metric\s+tonnes?", r"metric\s+tons?", r"tonnes?", r"(?<![A-Za-z0-9])MTs?(?![A-Za-z0-9])",
              r"(?<![A-Za-z0-9])t(?![A-Za-z0-9])"],
    "kg": [r"(?<![A-Za-z])kgs?(?![A-Za-z])", r"kilograms?"],
    "lb": [r"(?<![A-Za-z])lbs?(?![A-Za-z])", r"(?<![A-Za-z])pounds?(?![A-Za-z])"],
}
_AMBIGUOUS_UNIT = re.compile(r"(?<!metric )(?<![A-Za-z])(short |long )?tons?(?![A-Za-z])", re.I)
_UNSUPPORTED_UNIT = re.compile(r"(?<![A-Za-z])(dmt|wmt|cwt|oz|mtu)(?![A-Za-z])", re.I)


def units_in(text: str) -> set[str]:
    found = set()
    for unit, patterns in _UNIT_PATTERNS.items():
        for pattern in patterns:
            flags = 0 if pattern.startswith("(?<![A-Za-z0-9])") else re.I
            if re.search(pattern, text, flags):
                found.add(unit)
                break
    return found


def unit_problem(text: str) -> str | None:
    """'ambiguous_unit' for plain 'ton(s)', 'unsupported_unit' for dmt and similar, else None."""
    if _UNSUPPORTED_UNIT.search(text):
        return "unsupported_unit"
    if _AMBIGUOUS_UNIT.search(text) and not re.search(r"metric\s+tons?", text, re.I):
        return "ambiguous_unit"
    return None


# ---------- currencies and Incoterms ----------

_SYMBOLS = {"US$": "USD", "€": "EUR", "£": "GBP", "₹": "INR", "RMB": "CNY"}  # unambiguous symbols only


def currencies_in(text: str) -> set[str]:
    found = set(re.findall(r"(?<![A-Za-z])([A-Z]{3})(?![A-Za-z])", text))
    found.update(code for symbol, code in _SYMBOLS.items() if symbol in text)
    return found


INCOTERMS_2020 = ("EXW", "FCA", "FAS", "FOB", "CFR", "CIF", "CPT", "CIP", "DAP", "DPU", "DDP")


def incoterms_in(text: str) -> set[str]:
    found = {code for code in INCOTERMS_2020 if re.search(rf"(?<![A-Za-z]){code}(?![A-Za-z])", text)}
    if re.search(r"(?<![A-Za-z])(C&F|CNF)(?![A-Za-z])", text):
        found.add("CFR")
    return found


def squash(text: str) -> str:
    """Case-folded text with single spaces, for tolerant text comparison."""
    return " ".join(str(text).split()).casefold()
