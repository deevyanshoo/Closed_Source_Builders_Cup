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
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext

# ---------- numbers ----------

_NUMBER = re.compile(r"(?<![\w.,])[+-]?\d[\d.,]*")


def _signed_text(text: str) -> str:
    """Move a sign before a currency marker onto its following amount."""
    text = text.replace("−", "-")  # the mathematical minus used in PDFs and spreadsheets
    return re.sub(
        r"(?<![\w])([+-])\s*([A-Z]{3}|US\$|€|£|₹)\s*(\d[\d.,]*)",
        lambda match: f"{match.group(2)} {match.group(1)}{match.group(3)}", text)


def _tokens(text: str) -> list[str]:
    return _NUMBER.findall(_signed_text(text))


def _thousands_groups(groups: list[str]) -> bool:
    return 1 <= len(groups[0]) <= 3 and all(len(g) == 3 for g in groups[1:])


def _lakh_groups(groups: list[str]) -> bool:
    """Indian grouping: 1,23,45,678 (groups of two, then a final group of three)."""
    return (len(groups) > 2 and 1 <= len(groups[0]) <= 2 and all(len(g) == 2 for g in groups[1:-1])
            and len(groups[-1]) == 3)


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
    sign = "-" if token.startswith("-") else ""
    token = token.lstrip("+-")
    if not token:
        return None
    decimal_sep = _decimal_separator(token, decimal_comma)
    integer, fraction = token.rsplit(decimal_sep, 1) if decimal_sep else (token, "")
    if len(set(re.findall(r"[.,]", integer))) > 1:
        return None  # mixed thousands separators
    groups = re.split(r"[.,]", integer)
    if len(groups) > 1 and not (_thousands_groups(groups) or _lakh_groups(groups)):
        return None  # separators in the wrong places, for example a date like 25.10.2026
    digits = "".join(groups)
    if not digits.isdigit() or (fraction and not fraction.isdigit()):
        return None
    try:
        return Decimal(sign + (f"{digits}.{fraction}" if fraction else digits))
    except InvalidOperation:
        return None


def parse_decimal(text: str, *, decimal_comma: bool = False) -> Decimal | None:
    """The single number in `text`, or None if there is none or more than one."""
    found = [n for n in (_parse(t, decimal_comma) for t in _tokens(text)) if n is not None]
    return found[0] if len(found) == 1 else None


def number_style(texts: list[str]) -> bool | None:
    """Decimal-comma convention when cited numbers establish it; None means unresolved."""
    styles = set()
    for text in texts:
        for token in _tokens(text):
            token = token.rstrip(".,").lstrip("+-")
            if "," in token and "." in token:
                styles.add(token.rfind(",") > token.rfind("."))
            elif "," in token and token.count(",") == 1:
                whole, fraction = token.split(",")
                if len(whole) <= 3 and 1 <= len(fraction) <= 2:
                    styles.add(True)
            elif "." in token and token.count(".") == 1:
                whole, fraction = token.split(".")
                if len(whole) <= 3 and 1 <= len(fraction) <= 2:
                    styles.add(False)
    return next(iter(styles)) if len(styles) == 1 else None


def numbers_in(text: str, *, decimal_comma: bool | None = None) -> set[Decimal]:
    """Numbers with one defensible interpretation; lone three-digit separators need context."""
    out = set()
    for token in _tokens(text):
        point, comma = _parse(token, False), _parse(token, True)
        if point == comma and point is not None:
            out.add(point)
        elif decimal_comma is not None:
            chosen = comma if decimal_comma else point
            if chosen is not None:
                out.add(chosen)
    return out


def ambiguous_number_in(text: str) -> bool:
    return any(_parse(token, False) != _parse(token, True) for token in _tokens(text))


def uses_decimal_comma(texts) -> bool:
    """True when the document writes numbers like 2.598,00 or 9,15 (comma as decimal separator)."""
    for text in texts:
        for token in _tokens(text):
            token = token.rstrip(".,").lstrip("+-")
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


def windows_ordered_in(text: str) -> list[str]:
    """Explicit calendar windows in source order, without inferring a missing year."""
    pattern = re.compile(rf"\b(?P<iso>\d{{4}}-\d{{2}})(?!-\d)|\b(?P<month>{_MONTH_WORD})\s+(?P<year>\d{{4}})\b", re.I)
    found = []
    for match in pattern.finditer(text):
        if match.group("iso"):
            value = match.group("iso")
        else:
            value = f"{match.group('year')}-{_MONTHS[match.group('month').lower()[:3]]:02d}"
        try:
            dt.date(int(value[:4]), int(value[5:]), 1)
        except ValueError:
            continue
        found.append(value)
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

# SIX ISO 4217 List One, published 2026-09-17. This validates a code's identity, not
# product/provider coverage; an unknown code needs review until this snapshot is refreshed.
# https://www.six-group.com/dam/download/financial-information/data-center/iso-currrency/lists/list-one.xml
ISO_4217_CODES = frozenset("""
AED AFN ALL AMD AOA ARS AUD AWG AZN BAM BBD BDT BHD BIF BMD BND BOB BOV BRL BSD BTN BWP BYN BZD
CAD CDF CHE CHF CHW CLF CLP CNY COP COU CRC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP
GBP GEL GHS GIP GMD GNF GTQ GYD HKD HNL HTG HUF IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR
KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK
MXN MXV MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RWF SAR
SBD SCR SDG SEK SGD SHP SLE SOS SRD SSP STN SVC SYP SZL THB TJS TMT TND TOP TRY TTD TWD TZS UAH
UGX USD USN UYI UYU UYW UZS VED VES VND VUV WST XAD XAF XAG XAU XBA XBB XBC XBD XCD XCG XDR
XOF XPD XPF XPT XSU XTS XUA XXX YER ZAR ZMW ZWG
""".split())
_NON_SETTLEMENT_CODES = frozenset({"XXX", "XTS"})  # no currency; reserved for testing


def _currency_context(text: str, code: str) -> bool:
    if text.strip() == code:  # a dedicated table cell
        return True
    escaped = re.escape(code)
    if re.search(rf"(?<![A-Za-z]){escaped}(?![A-Za-z])\s*[-+−]?\d[\d.,]*\s*(?:/\s*(?:per\s*)?|per\s*)"
                 rf"(?:metric\s+)?(?:t|tonne|kg|lb)\b", text, re.I):
        return True
    if re.search(rf"\b(?:price|premium|currency|settlement\s+currency|invoice\s+currency|quote\s+currency)\b"
                 rf"[^\n;]{{0,48}}\b{escaped}\b", text, re.I):
        return True
    return False


def currencies_in(text: str) -> set[str]:
    found = {code for code in re.findall(r"(?<![A-Za-z])([A-Z]{3})(?![A-Za-z])", text)
             if code in ISO_4217_CODES and code not in _NON_SETTLEMENT_CODES
             and _currency_context(text, code)}
    found.update(code for symbol, code in _SYMBOLS.items() if symbol in text and
                 (text.strip() == symbol or bool(re.search(rf"{re.escape(symbol)}\s*\d", text))))
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


# ---------- normalised values ----------

DERIVED_PLACES = Decimal("1e-10")  # derived per-kg values are rounded half-even to 10 decimal places
MAX_SIGNIFICANT_DIGITS = 30
MAX_FRACTIONAL_PLACES = 10
ARITHMETIC_PRECISION = 80  # quantity * unit, then price / unit * bounded FX, including derived places


def _bounded_decimal(value, *, allow_negative: bool) -> Decimal | None:
    """Accept finite offer inputs within a documented 30-digit, 10-place envelope."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return None
    text = str(value)
    if len(text) > 64:
        return None
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or (number < 0 and not allow_negative):
        return None
    sign, digits, exponent = number.as_tuple()
    if (len(digits) > MAX_SIGNIFICANT_DIGITS or exponent < -MAX_FRACTIONAL_PLACES
            or number.adjusted() > MAX_SIGNIFICANT_DIGITS - 1):
        return None
    return number


def _exact(value: Decimal) -> str:
    """Exact source-derived quantity; never apply the reporting-price rounding rule."""
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def fmt(value: Decimal) -> str:
    """Plain decimal string without exponent; derived values longer than 10 places are rounded."""
    with localcontext() as context:
        context.prec = ARITHMETIC_PRECISION
        if value.as_tuple().exponent < -10:
            value = value.quantize(DERIVED_PLACES, rounding=ROUND_HALF_EVEN)
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _money(amount: Decimal, currency: str) -> dict:
    return {"amount": fmt(amount), "currency": currency}


def normalize_quantity(value: dict) -> dict:
    """A quantity in kg, converted exactly. Unknown units are not converted."""
    out = {"original": value, "kg": None, "status": "ok"}
    if not isinstance(value, dict) or "amount" not in value or "unit" not in value or (
            set(value) - {"amount", "unit", "period"}):
        return {**out, "status": "invalid_amount"}
    amount = _bounded_decimal(value["amount"], allow_negative=False)
    if amount is None:
        return {**out, "status": "invalid_amount"}
    unit = value.get("unit")
    if not isinstance(unit, str) or unit not in KG_PER_UNIT:
        return {**out, "status": "unsupported_unit"}
    with localcontext() as context:
        context.prec = ARITHMETIC_PRECISION
        return {**out, "kg": _exact(amount * KG_PER_UNIT[unit])}


def _usable_rate(rate, base: str, quote: str, as_of: dt.date) -> Decimal | None:
    if (getattr(rate, "base", None) != base or getattr(rate, "quote", None) != quote
            or getattr(rate, "status", None) not in {"live", "cache"}
            or not callable(getattr(rate, "to_dict", None))):
        return None
    number = _bounded_decimal(getattr(rate, "rate", None), allow_negative=False)
    date_text = getattr(rate, "rate_date", None)
    if number is None or number <= 0 or not isinstance(date_text, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_text):
        return None
    try:
        rate_date = dt.date.fromisoformat(date_text)
    except ValueError:
        return None
    return number if rate_date <= as_of else None


def _reporting(amount_per_kg: Decimal, currency: str, reporting_currency: str, as_of: dt.date, rate_lookup) -> dict:
    if currency == reporting_currency:
        return {"currency": reporting_currency, "status": "not_needed", "fx": None,
                "per_kg": _money(amount_per_kg, currency)}
    try:
        rate = rate_lookup(currency, reporting_currency, as_of)
    except Exception:  # an unavailable lookup cannot authorize conversion
        return {"currency": reporting_currency, "status": "unavailable", "fx": None, "per_kg": None}
    usable = _usable_rate(rate, currency, reporting_currency, as_of)
    if usable is None:
        return {"currency": reporting_currency, "status": "unavailable", "fx": None, "per_kg": None}
    with localcontext() as context:
        context.prec = ARITHMETIC_PRECISION
        converted = amount_per_kg * usable
    return {"currency": reporting_currency, "status": rate.status, "fx": rate.to_dict(),
            "per_kg": _money(converted, reporting_currency)}


def normalize_pricing(value: dict, *, reporting_currency: str, as_of: dt.date, rate_lookup=None) -> dict:
    """Per-kg price in its own currency, plus the reporting-currency figure with the rate used.

    Index-linked prices keep their index and averaging period; only the premium is converted.
    No price is invented for an index that has not fixed yet.
    """
    if not isinstance(value, dict):
        return {"type": None, "original": value, "status": "invalid_amount"}
    kind = value.get("type")
    money_key = "unit_price" if kind == "fixed" else "premium" if kind == "index_linked" else None
    if money_key is None:
        return {"type": kind, "original": value,
                "status": "invalid_amount" if kind is None else "unsupported_pricing_type"}
    if (kind == "fixed" and set(value) != {"type", "unit_price", "per_unit"}) or (
            kind == "index_linked" and (not {"type", "index", "averaging_period", "premium", "per_unit"} <= set(value)
                                        or set(value) - {"type", "index", "averaging_period", "premium", "per_unit",
                                                         "fx_basis", "fx_terms", "fx_conversion", "quotational_period"})):
        return {"type": kind, "original": value, "status": "invalid_amount"}
    money = value.get(money_key)
    if not isinstance(money, dict) or set(money) != {"amount", "currency"}:
        return {"type": kind, "original": value, "status": "invalid_amount"}
    amount = _bounded_decimal(money["amount"], allow_negative=kind == "index_linked")
    if amount is None or (kind == "index_linked" and (
            not isinstance(value.get("index"), str) or not value["index"].strip() or
            not isinstance(value.get("averaging_period"), str) or not value["averaging_period"].strip())):
        return {"type": kind, "original": value, "status": "invalid_amount"}
    if (not isinstance(money["currency"], str) or money["currency"] not in ISO_4217_CODES
            or money["currency"] in _NON_SETTLEMENT_CODES or not isinstance(reporting_currency, str)
            or reporting_currency not in ISO_4217_CODES or reporting_currency in _NON_SETTLEMENT_CODES):
        return {"type": kind, "original": value, "status": "unsupported_currency"}
    if rate_lookup is None:
        from .fx_rates import get_rate as rate_lookup
    per_unit = value.get("per_unit")
    if not isinstance(per_unit, str) or per_unit not in KG_PER_UNIT:
        return {"type": value.get("type"), "original": value, "status": "unsupported_unit"}
    kg = KG_PER_UNIT[per_unit]
    if value.get("type") == "fixed":
        with localcontext() as context:
            context.prec = ARITHMETIC_PRECISION
            per_kg = amount / kg
        return {"type": "fixed", "original": value, "status": "ok", "per_kg": _money(per_kg, money["currency"]),
                "reporting": _reporting(per_kg, money["currency"], reporting_currency, as_of, rate_lookup)}
    if value.get("type") == "index_linked":
        with localcontext() as context:
            context.prec = ARITHMETIC_PRECISION
            per_kg = amount / kg
        reporting = _reporting(per_kg, money["currency"], reporting_currency, as_of, rate_lookup)
        reporting["premium_per_kg"] = reporting.pop("per_kg")
        return {"type": "index_linked", "original": value, "status": "ok", "index": value.get("index"),
                "averaging_period": value.get("averaging_period"),
                "premium_per_kg": _money(per_kg, money["currency"]), "reporting": reporting}
    return {"type": value.get("type"), "original": value, "status": "unsupported_pricing_type"}


def expiry_status(valid_until: str | None, as_of: dt.date) -> str:
    """Inclusive expiry; missing is unknown, malformed is invalid."""
    if valid_until is None or valid_until == "":
        return "unknown"
    if not isinstance(valid_until, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", valid_until):
        return "invalid"
    try:
        expiry = dt.date.fromisoformat(valid_until)
    except ValueError:
        return "invalid"
    return "expired" if expiry < as_of else "valid"
