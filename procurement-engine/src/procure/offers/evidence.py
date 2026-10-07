"""Evidence check for extracted offer fields.

An extractor (rules, Gemini or a person) proposes candidates: a value plus the locators and
quoted text it came from. A candidate is verified only when every cited locator exists in the
document, the quote is really inside that segment, any supplied hash matches the segment hash,
the segment is not hidden content or an uncomputed formula, and the value (number, unit,
currency, date, code) can be read from the cited text.

Field status:
  extracted   one verified value
  conflict    two or more different verified values; no value is chosen
  unverified  candidates exist but none passed the check; no value is kept
  missing     no candidate at all; no value is filled in
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from ..documents import Document
from ..settings import materials_config
from . import normalize as nz

CRITICAL_FIELDS = frozenset({"supplier_name", "material", "quantity", "pricing", "settlement_currency",
                             "incoterm", "valid_until"})
HIDDEN_FLAGS = frozenset({"low_visibility", "partly_low_visibility", "visibility_unverified",
                          "hidden_sheet", "hidden_row", "hidden_column"})
KINDS = ("text", "date", "integer", "quantity", "currency", "incoterm", "pricing", "shipments", "material")


def base_name(name: str) -> str:
    """`items[0].pricing` -> `pricing`."""
    return name.rsplit(".", 1)[-1]


@dataclass(frozen=True)
class EvidenceRef:
    document_id: str
    locator: str
    quote: str
    text_sha256: str
    segment_text: str


@dataclass
class Candidate:
    value: Any
    evidence: list[EvidenceRef]
    problems: list[str]
    cited: list[dict] = field(default_factory=list)  # what the extractor claimed, kept for review

    @property
    def verified(self) -> bool:
        return not self.problems

    def to_dict(self) -> dict:
        return {"value": self.value, "verified": self.verified, "problems": self.problems,
                "evidence": [asdict(e) for e in self.evidence], "cited": self.cited}


@dataclass
class FieldResult:
    name: str
    kind: str
    status: str  # extracted | missing | conflict | unverified | corrected
    value: Any
    evidence: list[EvidenceRef]
    candidates: list[Candidate]
    critical: bool
    source: str  # rules | gemini | user
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"name": self.name, "kind": self.kind, "status": self.status, "value": self.value,
                "critical": self.critical, "source": self.source, "reasons": self.reasons,
                "evidence": [asdict(e) for e in self.evidence],
                "candidates": [c.to_dict() for c in self.candidates]}


# ---------- does the cited text show the value? ----------

def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return number if number.is_finite() else None


def _exact_sum_matches(target: Decimal, values: list[Decimal]) -> bool:
    """Compare bounded decimal coefficients as integers, without context rounding."""
    if not values or len(values) > 256:
        return False
    parts = [number.as_tuple() for number in [target, *values]]
    if any(len(part.digits) > 256 or abs(part.exponent) > 256 for part in parts):
        return False
    exponent = min(part.exponent for part in parts)
    def scaled(part):
        coefficient = int("".join(map(str, part.digits)))
        return (-1 if part.sign else 1) * coefficient * 10 ** (part.exponent - exponent)
    return scaled(parts[0]) == sum(scaled(part) for part in parts[1:])


def _amount_problem(amount: Any, texts: list[str]) -> str | None:
    target = _decimal(amount)
    if target is None:
        return "invalid_value"
    style = nz.number_style(texts)
    if any(target in nz.numbers_in(text, decimal_comma=style) for text in texts):
        return None
    # A total explicitly shown as separate numeric cells may be added, but never by
    # interpreting an ambiguous separator in two styles at once.
    cells = [nz.numbers_in(text, decimal_comma=style) for text in texts
             if re.fullmatch(r"[\d.,]+", text.strip())]
    if len(cells) > 1 and all(len(cell) == 1 for cell in cells) and _exact_sum_matches(
            target, [next(iter(cell)) for cell in cells]):
        return None
    if style is None and any(nz.ambiguous_number_in(text) for text in texts):
        return "ambiguous_number"
    return "amount"


_RELATIVE_DATE_ANCHOR = re.compile(
    r"\b\d+\s+(?:(?:calendar|business|working)\s+)?"
    r"(?:days?|weeks?|months?)\s+(?:from|after|following)\b", re.I)
_EXPLICIT_DATE_END = re.compile(r"\b(?:valid\s+(?:until|through|to)|expires?|expiry(?:\s+date)?|end\s+date)\b", re.I)


def _date_problem(value: Any, refs: list[EvidenceRef]) -> str | None:
    if not isinstance(value, str):
        return "date"
    separate_matches = []
    has_relative_anchor = False
    for ref in refs:
        # Inspect the full source segment, never a model's shortened quoted excerpt.
        for clause in re.split(r"[;\n]", ref.segment_text):
            if _RELATIVE_DATE_ANCHOR.search(clause):
                has_relative_anchor = True
                continue
            if value in nz.dates_in(clause):
                separate_matches.append(clause)
    if not separate_matches:
        return "date"
    if has_relative_anchor and not any(_EXPLICIT_DATE_END.search(clause) for clause in separate_matches):
        return "date_anchor"
    return None


def _cell_parts(locator: str) -> tuple[str, int, int] | None:
    csv = re.fullmatch(r"r(\d+):c(\d+)", locator)
    if csv:
        return "csv", int(csv.group(1)), int(csv.group(2))
    xlsx = re.fullmatch(r"(.+)!([A-Z]+)(\d+)", locator)
    if xlsx:
        column = 0
        for letter in xlsx.group(2):
            column = column * 26 + ord(letter) - ord("A") + 1
        return xlsx.group(1), int(xlsx.group(3)), column
    return None


def _cell_locator(sheet: str, row: int, column: int) -> str:
    if sheet == "csv":
        return f"r{row}:c{column}"
    letters = ""
    while column:
        column, remainder = divmod(column - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return f"{sheet}!{letters}{row}"


def _header_reaches(amount_ref: EvidenceRef, header_ref: EvidenceRef, doc: Document,
                    unit_data: bool = False) -> bool:
    amount, header = _cell_parts(amount_ref.locator), _cell_parts(header_ref.locator)
    if not amount or not header or amount[0] != header[0] or amount[2] != header[2]:
        return False
    if not 0 < amount[1] - header[1] <= 7:
        return False
    # The first nonnumeric visible cell above the data row is its header. A
    # second table, hidden cell, or intervening prose cannot be skipped over.
    for row in range(amount[1] - 1, header[1], -1):
        segment = doc.segment(_cell_locator(amount[0], row, amount[2]))
        data_text = segment.text.strip() if segment else ""
        if segment and (HIDDEN_FLAGS.intersection(segment.flags)
                        or "formula_without_cached_value" in segment.flags
                        or (data_text and not re.fullmatch(r"[\d.,]+", data_text)
                            and not (unit_data and re.fullmatch(r"(?:t|MT|tonnes?|kgs?|kilograms?|lbs?|pounds?)",
                                                             data_text, re.I)))):
            return False
    return True


def _related_cells(first: str, second: str, unit_text: str, doc: Document | None = None) -> bool:
    csv_a, csv_b = re.fullmatch(r"r(\d+):c(\d+)", first), re.fullmatch(r"r(\d+):c(\d+)", second)
    if csv_a and csv_b:
        same_row = csv_a.group(1) == csv_b.group(1) and int(csv_b.group(2)) == int(csv_a.group(2)) + 1
        header = csv_a.group(2) == csv_b.group(2) and int(csv_a.group(1)) == int(csv_b.group(1)) + 1
        left_label = (csv_a.group(1) == csv_b.group(1)
                      and int(csv_b.group(2)) == int(csv_a.group(2)) - 1
                      and bool(re.search(r"(?:quantity|qty|capacity|min[_ ]?order|moq)",
                                         unit_text.replace("_", " "), re.I)))
        return (same_row and bool(re.fullmatch(r"[A-Za-z]+", unit_text.strip()))) or left_label or header
    xlsx_a = re.fullmatch(r"(?:'[^']+'|[^!]+)!([A-Z]+)(\d+)", first)
    xlsx_b = re.fullmatch(r"(?:'[^']+'|[^!]+)!([A-Z]+)(\d+)", second)
    if not xlsx_a or not xlsx_b or doc is None:
        return False
    amount_segment, header_segment = doc.segment(first), doc.segment(second)
    if not amount_segment or not header_segment or HIDDEN_FLAGS.intersection(header_segment.flags):
        return False
    return _header_reaches(EvidenceRef(doc.document_id, first, "", "", amount_segment.text),
                           EvidenceRef(doc.document_id, second, "", "", header_segment.text), doc)


def _quantity_unit_problem(amount: Any, unit: Any, refs: list[EvidenceRef],
                           doc: Document | None = None) -> str | None:
    target = _decimal(amount)
    if target is None or not isinstance(unit, str) or unit not in nz.KG_PER_UNIT:
        return "invalid_value"
    style = nz.number_style([ref.segment_text for ref in refs])
    for ref in refs:
        for clause in re.split(r"[;\n]", ref.segment_text):
            if target not in nz.numbers_in(clause, decimal_comma=style):
                continue
            problem = nz.unit_problem(clause)
            if problem:
                return problem
            mentioned_units = nz.units_in(clause)
            if len(mentioned_units) > 1:
                return "ambiguous_unit"
            if unit in mentioned_units:
                return None
    for amount_ref in refs:
        if amount_ref.segment_text.strip() == "" or target not in nz.numbers_in(amount_ref.segment_text, decimal_comma=style):
            continue
        if not re.fullmatch(r"[\d.,]+", amount_ref.segment_text.strip()):
            continue
        for unit_ref in refs:
            if not _related_cells(amount_ref.locator, unit_ref.locator, unit_ref.segment_text, doc):
                continue
            unit_text = unit_ref.segment_text.strip()
            if not re.fullmatch(r"(?:t|MT|tonnes?|kgs?|kilograms?|lbs?|pounds?)", unit_text, re.I):
                label = unit_text.replace("_", " ")
                if not re.search(r"\b(?:quantity|qty|capacity|minimum order|min order|moq)\b", label, re.I):
                    continue
            if nz.unit_problem(unit_ref.segment_text):
                return nz.unit_problem(unit_ref.segment_text)
            if unit in nz.units_in(unit_ref.segment_text):
                return None
    return "unit"


def _row_identity(doc: Document, header: EvidenceRef, rows: list[int], quantity_column: int,
                  unit_column: int) -> tuple[str, str, dict[str, str]] | None:
    """One visible contiguous table with identical non-lot commercial columns."""
    parts = _cell_parts(header.locator)
    if parts is None or len(rows) > 256:
        return None
    sheet, header_row, _ = parts
    columns = [quantity_column]
    for direction in (-1, 1):
        column = quantity_column + direction
        while column > 0 and len(columns) < 64:
            segment = doc.segment(_cell_locator(sheet, header_row, column))
            if segment is None or not segment.text.strip():
                break
            if HIDDEN_FLAGS.intersection(segment.flags) or "formula_without_cached_value" in segment.flags:
                return None
            columns.append(column)
            column += direction
        if column > 0 and len(columns) >= 64 and doc.segment(_cell_locator(sheet, header_row, column)):
            return None
    labels = {}
    for column in sorted(columns):
        segment = doc.segment(_cell_locator(sheet, header_row, column))
        if segment is None or HIDDEN_FLAGS.intersection(segment.flags):
            return None
        label = re.sub(r"\s+", " ", segment.text.replace("_", " ").strip()).casefold()
        if label in labels.values():
            return None
        labels[column] = label
    product_cols = [column for column, label in labels.items() if label in {"product", "description"}]
    grade_cols = [column for column, label in labels.items() if label == "grade"]
    if len(product_cols) != 1 or len(grade_cols) != 1:
        return None
    excluded = {quantity_column, unit_column}
    excluded.update(column for column, label in labels.items()
                    if label in {"lot", "ship month", "ship window", "shipment window", "laycan"})
    identity = None
    for row in rows:
        values = {}
        for column in labels:
            if column in excluded:
                continue
            segment = doc.segment(_cell_locator(sheet, row, column))
            if (segment is None or HIDDEN_FLAGS.intersection(segment.flags)
                    or "formula_without_cached_value" in segment.flags or not segment.text.strip()):
                return None
            values[column] = re.sub(r"\s+", " ", segment.text.strip()).casefold()
        if identity is None:
            identity = values
        elif values != identity:
            return None
    if identity is None:
        return None
    return (identity[product_cols[0]], identity[grade_cols[0]],
            {labels[column]: value for column, value in identity.items()})


def _aggregate_quantity_problem(amount: Any, unit: str, refs: list[EvidenceRef],
                                doc: Document,
                                item_identity: tuple[str | None, str | None, dict | None] | None = None) -> str | None:
    """Check each contributing table cell before accepting a derived sum."""
    numeric = [ref for ref in refs if re.fullmatch(r"[\d.,]+", ref.segment_text.strip())]
    if len(numeric) < 2:
        return None
    if len({ref.locator for ref in numeric}) != len(numeric):
        return "quantity_binding"
    if not isinstance(unit, str) or unit not in nz.KG_PER_UNIT:
        return "invalid_value"
    target = _decimal(amount)
    style = nz.number_style([ref.segment_text for ref in refs])
    if target is None:
        return "invalid_value"
    values = [nz.numbers_in(ref.segment_text, decimal_comma=style) for ref in numeric]
    if any(len(found) != 1 or next(iter(found)) <= 0 for found in values):
        return "amount"
    if not _exact_sum_matches(target, [next(iter(found)) for found in values]):
        return "amount"
    shared_header = None
    shared_unit_column = None
    rows = []
    for amount_ref in numeric:
        cell = _cell_parts(amount_ref.locator)
        if cell is None:
            return "quantity_binding"
        rows.append(cell[1])
        headers = list({ref.locator: ref for ref in refs if _header_reaches(amount_ref, ref, doc)
                        and re.fullmatch(r"(?:qty|quantity)(?:[_ ](?:tonnes?|kg|lbs?))?",
                                         ref.segment_text.strip(), re.I)}.values())
        if len(headers) != 1:
            return "quantity_binding"
        header = headers[0]
        if shared_header is None:
            shared_header = header.locator
        elif shared_header != header.locator:
            return "quantity_binding"
        if shared_unit_column is None:
            shared_unit_column = cell[2] + 1
        header_units = nz.units_in(header.segment_text)
        if header_units:
            if header_units != {unit}:
                return "unit"
            continue
        unit_refs = [ref for ref in refs if
                     (unit_cell := _cell_parts(ref.locator)) is not None
                     and unit_cell[0] == cell[0] and unit_cell[1] == cell[1]
                     and unit_cell[2] == cell[2] + 1 and nz.units_in(ref.segment_text) == {unit}]
        if len(unit_refs) != 1:
            return "unit"
        unit_header = doc.segment(_cell_locator(cell[0], _cell_parts(header.locator)[1], cell[2] + 1))
        if (unit_header is None or HIDDEN_FLAGS.intersection(unit_header.flags)
                or "formula_without_cached_value" in unit_header.flags
                or not _header_reaches(unit_refs[0],
                                       EvidenceRef(doc.document_id, unit_header.locator, "", "", unit_header.text),
                                       doc, unit_data=True)
                or not re.fullmatch(r"(?:qty|quantity)[_ ]unit", unit_header.text.strip(), re.I)):
            return "quantity_binding"
    identity = _row_identity(doc, header, rows, cell[2], shared_unit_column)
    if identity is None:
        return "quantity_binding"
    if item_identity is not None:
        description, grade, pricing = item_identity
        if (not isinstance(description, str) or not isinstance(grade, str)
                or re.sub(r"\s+", " ", description.strip()).casefold() != identity[0]
                or re.sub(r"\s+", " ", grade.strip()).casefold() != identity[1]):
            return "quantity_binding"
        if (not isinstance(pricing, dict) or pricing.get("type") != "fixed"
                or not isinstance(pricing.get("unit_price"), dict)):
            return "quantity_binding"
        source = identity[2]
        price_amount = _decimal(pricing["unit_price"].get("amount"))
        source_amount = nz.parse_decimal(source.get("price", ""))
        if (price_amount is None or source_amount != price_amount
                or not isinstance(pricing["unit_price"].get("currency"), str)
                or source.get("price currency", source.get("currency")) != pricing["unit_price"]["currency"].casefold()
                or nz.units_in(source.get("price unit", "")) != {pricing.get("per_unit")}):
            return "quantity_binding"
    return None


def _money_supported(money: dict, texts: list[str]) -> list[str]:
    out = []
    if not isinstance(money, dict) or set(money) != {"amount", "currency"}:
        return ["invalid_value"]
    amount_problem = _amount_problem(money.get("amount"), texts)
    if amount_problem:
        out.append(amount_problem)
    if not isinstance(money.get("currency"), str) or not any(
            money["currency"] in nz.currencies_in(text) for text in texts):
        out.append("currency")
    return out


def _price_binding_problem(amount: Any, currency: Any, unit: Any, refs: list[EvidenceRef],
                           doc: Document) -> str | None:
    """Require the price components in one clause or an adjacent price row/header."""
    target = _decimal(amount)
    if target is None or not isinstance(currency, str) or not isinstance(unit, str):
        return "invalid_value"
    style = nz.number_style([ref.segment_text for ref in refs])
    for ref in refs:
        for clause in re.split(r"[;\n]", ref.segment_text):
            for expression in re.finditer(
                    r"(?<![A-Za-z0-9])(?:[-+−]\s*)?(?:[A-Z]{3}|US\$|€|£|₹)(?![A-Za-z])\s*"
                    r"(?:[-+−]\s*)?(?P<amount>\d[\d.,]*)\s*(?:/\s*(?:per\s*)?|per\s*)"
                    r"(?:metric\s+)?(?:tonnes?|tons?|kilograms?|kgs?|pounds?|lbs?|t)\b",
                    clause, re.I):
                written = expression.group(0)
                if (target in nz.numbers_in(written, decimal_comma=style)
                        and currency in nz.currencies_in(written)
                        and unit in nz.units_in(written)
                        and nz.unit_problem(written) is None):
                    return None
    for amount_ref in refs:
        if not re.fullmatch(r"[\d.,]+", amount_ref.segment_text.strip()) or target not in nz.numbers_in(
                amount_ref.segment_text, decimal_comma=style):
            continue
        # A price header directly above its amount can carry both currency and denominator.
        for header_ref in refs:
            if (_related_cells(amount_ref.locator, header_ref.locator, header_ref.segment_text, doc)
                    and re.search(r"\b(?:price|premium)\b", header_ref.segment_text, re.I)
                    and currency in nz.currencies_in(header_ref.segment_text)
                    and unit in nz.units_in(header_ref.segment_text)):
                return None
        # A structured price row binds amount, currency and unit in successive columns.
        amount_cell = re.fullmatch(r"r(\d+):c(\d+)", amount_ref.locator)
        if amount_cell:
            row, column = map(int, amount_cell.groups())
            header = None
            for previous_row in range(row - 1, max(0, row - 8), -1):
                candidate = doc.segment(f"r{previous_row}:c{column}")
                if candidate is None or re.fullmatch(r"[\d.,]+", candidate.text.strip()):
                    continue
                header = candidate
                break
            currency_ref = next((ref for ref in refs if ref.locator == f"r{row}:c{column + 1}"), None)
            unit_ref = next((ref for ref in refs if ref.locator == f"r{row}:c{column + 2}"), None)
            if (header and not HIDDEN_FLAGS.intersection(header.flags)
                    and re.search(r"(?<![A-Za-z])(?:price|premium)(?![A-Za-z])", header.text, re.I)
                    and currency_ref and unit_ref and currency_ref.segment_text.strip() == currency
                    and unit in nz.units_in(unit_ref.segment_text)
                    and nz.unit_problem(unit_ref.segment_text) is None):
                return None
    return "price_binding"


def _currency_supported(value: Any, refs: list[EvidenceRef], doc: Document) -> bool:
    """Bind a settlement currency to an explicit payment/invoice/settlement statement.

    A unit-price currency, generic Currency heading or FX pair has no settlement role.
    Pricing uses its separate amount/currency/unit binding and is unaffected here.
    """
    if (not isinstance(value, str) or value not in nz.ISO_4217_CODES
            or value in nz._NON_SETTLEMENT_CODES):
        return False
    code = re.escape(value)
    # The code must be the stated currency, rather than one leg of a currency pair
    # or a prefix of a longer token. Inspect the complete located segment.
    currency_token = rf"{code}(?![A-Za-z0-9]|\s*[/,]\s*[A-Z]{{3}}\b)"
    role = (rf"\b(?:invoice|settlement|payment)[_ -]+currency\b\s*(?:[:=\-–—]|\bis\b)?\s*{currency_token}"
            rf"|\bcurrency\s+of\s+settlement\b\s*(?:[:=\-–—]|\bis\b)?\s*{currency_token}"
            rf"|\b(?:payable|settled|payment)\s+in\s+{currency_token}"
            rf"|\bsettlement\b\s*[:=\-–—]\s*{currency_token}")
    label_role = re.compile(r"(?:invoice|settlement|payment)[_ -]+currency|currency[_ ]+of[_ ]+settlement", re.I)
    for ref in refs:
        text = ref.segment_text.strip()
        if re.search(role, text, re.I):
            return True
        if text != value:
            continue
        cell = re.fullmatch(r"r(\d+):c(\d+)", ref.locator)
        if not cell:
            continue  # an isolated code needs an explicit role label
        row, column = map(int, cell.groups())
        locators = [f"r{row}:c{column - 1}"]
        for previous_row in range(row - 1, max(0, row - 8), -1):
            above = doc.segment(f"r{previous_row}:c{column}")
            if above is None:
                continue
            if above.text.strip() == value:
                continue  # repeated table data below a shared header
            locators.append(above.locator)
            break
        for locator in locators:
            label = doc.segment(locator)
            if (label and not HIDDEN_FLAGS.intersection(label.flags)
                    and "formula_without_cached_value" not in label.flags
                    and label_role.fullmatch(label.text.strip())):
                return True
    return False


def _shipments_problems(value: Any, refs: list[EvidenceRef]) -> list[str]:
    if not isinstance(value, list) or not value or any(
            not isinstance(lot, dict) or set(lot) != {"quantity", "window"} or
            not isinstance(lot["quantity"], dict) or set(lot["quantity"]) != {"amount", "unit"}
            for lot in value):
        return ["invalid_value"]
    windows = [(window, ref, clause) for ref in refs for clause in re.split(r"[;\n]", ref.segment_text)
               for window in nz.windows_ordered_in(clause)]
    proposed = [lot.get("window") for lot in value]
    if len(windows) != len(value) or proposed != [window for window, _, _ in windows]:
        return ["lot_window"]
    text = " ".join(ref.segment_text for ref in refs)
    count = re.search(r"\b(one|two|three|four|five|\d+)\s+(?:monthly\s+)?lots?\b", text, re.I)
    if count:
        word = count.group(1).lower()
        expected_count = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}.get(word)
        if (expected_count if expected_count is not None else int(word)) != len(value):
            return ["lot_window"]
    for lot, (_, window_ref, clause) in zip(value, windows):
        quantity = lot.get("quantity")
        if not isinstance(quantity, dict):
            return ["invalid_value"]
        if (amount := _decimal(quantity.get("amount"))) is None or amount < 0:
            return ["lot_quantity"]
        if len(nz.windows_ordered_in(clause)) > 1:
            # One shared quantity can describe equal lots. Distinct quantities on the
            # same clause need explicit pairings, which this reader cannot establish.
            quantity_tokens = re.findall(
                r"\b\d[\d.,]*\s*(?:metric\s+tonnes?|metric\s+tons?|tonnes?|kilograms?|kgs?|pounds?|lbs?|MTs?|t)\b",
                clause, re.I)
            if len(quantity_tokens) != 1:
                return ["lot_quantity"]
            if re.search(r"\btotal\b", clause, re.I) or not (
                    re.search(r"\blots?\s+of\s+\d", clause, re.I) or
                    re.search(r"\beach\b", clause, re.I)):
                return ["lot_quantity"]
        row = re.match(r"r(\d+):c", window_ref.locator)
        if clause != window_ref.segment_text:
            related = [EvidenceRef(window_ref.document_id, window_ref.locator, window_ref.quote,
                                   window_ref.text_sha256, clause)]
        elif row and not re.search(r"\b\d[\d.,]*\s*(?:metric\s+)?(?:tonnes?|tons?|kg|lb|t)\b",
                                   window_ref.segment_text, re.I):
            row_number, column = map(int, re.fullmatch(r"r(\d+):c(\d+)", window_ref.locator).groups())
            cells = {int(match.group(1)): ref for ref in refs
                     if (match := re.fullmatch(rf"r{row_number}:c(\d+)", ref.locator))}
            preceding = cells.get(column - 1)
            if preceding and nz.units_in(preceding.segment_text) and nz.numbers_in(preceding.segment_text):
                related = [preceding, window_ref]
            elif (preceding and cells.get(column - 2)
                  and nz.units_in(preceding.segment_text)
                  and nz.numbers_in(cells[column - 2].segment_text)):
                related = [cells[column - 2], preceding, window_ref]
            else:
                return ["lot_quantity"]
        else:
            related = [window_ref]
        texts = [ref.segment_text for ref in related]
        if _amount_problem(quantity.get("amount"), texts) or _quantity_unit_problem(
                quantity.get("amount"), quantity.get("unit"), related):
            return ["lot_quantity"]
    return []


def value_problems(kind: str, value: Any, refs: list[EvidenceRef], doc: Document,
                   item_identity: tuple[str | None, str | None, dict | None] | None = None) -> list[str]:
    """Which parts of the value cannot be read from the cited text (empty list means supported)."""
    texts = [ref.segment_text for ref in refs]
    joined = " ".join(texts)
    if value is None:
        return ["empty_value"]
    if kind == "text":
        return [] if isinstance(value, str) and value.strip() and nz.squash(value) in nz.squash(joined) else ["text"]
    if kind == "date":
        problem = _date_problem(value, refs)
        return [] if problem is None else [problem]
    if kind == "integer":
        number = _decimal(value)
        if number is None or number != number.to_integral_value():
            return ["invalid_value"]
        problem = _amount_problem(value, texts)
        return [] if problem is None else [problem]
    if kind == "currency":
        return [] if _currency_supported(value, refs, doc) else ["currency"]
    if kind == "quantity":
        if not isinstance(value, dict) or not {"amount", "unit"} <= set(value) or (
                set(value) - {"amount", "unit", "period"}):
            return ["invalid_value"]
        if (amount := _decimal(value.get("amount"))) is None or amount < 0:
            return ["invalid_value"]
        out = []
        aggregate = sum(bool(re.fullmatch(r"[\d.,]+", ref.segment_text.strip())) for ref in refs) >= 2
        aggregate_problem = _aggregate_quantity_problem(value.get("amount"), value.get("unit"), refs, doc,
                                                         item_identity)
        if aggregate_problem:
            out.append(aggregate_problem)
        amount_problem = _amount_problem(value.get("amount"), texts)
        if amount_problem:
            out.append(amount_problem)
        unit_problem = None if aggregate and aggregate_problem is None else _quantity_unit_problem(
            value.get("amount"), value.get("unit"), refs, doc)
        if unit_problem:
            out.append(unit_problem)
        period = value.get("period")
        if period is not None and (not isinstance(period, str) or period.casefold() not in joined.casefold()):
            out.append("period")
        return out
    if kind == "incoterm":
        if not isinstance(value, dict) or not isinstance(value.get("code"), str):
            return ["invalid_value"]
        out = [] if value.get("code") in nz.incoterms_in(joined) else ["code"]
        if value.get("place") is not None and not isinstance(value["place"], str):
            return ["invalid_value"]
        place = (value.get("place") or "").split(",")[0].strip()
        return out + ([] if not place or nz.squash(place) in nz.squash(joined) else ["place"])
    if kind == "pricing":
        if not isinstance(value, dict):
            return ["invalid_value"]
        if value.get("type") == "fixed":
            if set(value) != {"type", "unit_price", "per_unit"}:
                return ["invalid_value"]
            money = value.get("unit_price")
            out = _money_supported(money, texts)
            if isinstance(money, dict) and (amount_value := _decimal(money.get("amount"))) is not None and amount_value < 0:
                out.append("invalid_value")
        elif value.get("type") == "index_linked":
            if set(value) - {"type", "index", "averaging_period", "premium", "per_unit",
                              "fx_basis", "fx_terms", "fx_conversion", "quotational_period"}:
                return ["invalid_value"]
            money = value.get("premium")
            out = _money_supported(money, texts)
            index, period = value.get("index"), value.get("averaging_period")
            if not isinstance(index, str) or not index.strip() or nz.squash(index) not in nz.squash(joined):
                out.append("index")
            if not isinstance(period, str) or not period.strip() or nz.squash(period) not in nz.squash(joined):
                out.append("averaging_period")
            for key in ("fx_basis", "fx_terms", "fx_conversion", "quotational_period"):
                if key in value and (not isinstance(value[key], str) or not value[key].strip() or
                                     nz.squash(value[key]) not in nz.squash(joined)):
                    out.append(key)
        else:
            return ["pricing_type"]
        amount = money.get("amount") if isinstance(money, dict) else None
        binding_problem = _price_binding_problem(
            amount, money.get("currency") if isinstance(money, dict) else None,
            value.get("per_unit"), refs, doc)
        return out + ([binding_problem] if binding_problem else [])
    if kind == "shipments":
        return _shipments_problems(value, refs)
    if kind == "material":
        if not isinstance(value, str):
            return ["invalid_value"]
        cfg = materials_config()["materials"].get(value)
        if cfg is None:
            return ["material_not_in_catalog"]
        words = [cfg["display_name"].split()[0], *cfg.get("keywords", []), value.replace("_", " ")]
        return [] if any(nz.squash(w) in nz.squash(joined) for w in words) else ["material"]
    return ["unknown_kind"]


# ---------- the check ----------

def check_candidate(doc: Document, kind: str, proposal: Any,
                    item_identity: tuple[str | None, str | None, dict | None] | None = None) -> Candidate:
    problems, refs = [], []
    if not isinstance(proposal, dict):
        return Candidate(value=None, evidence=[], problems=["invalid_proposal"], cited=[])
    cited = proposal.get("evidence") or []
    if not cited:
        problems.append("no_evidence")
    if not isinstance(cited, list):
        return Candidate(value=proposal.get("value"), evidence=[], problems=["invalid_evidence"], cited=[])
    safe_cited = []
    for item in cited:
        if not isinstance(item, dict) or not isinstance(item.get("locator"), str) or not item.get("locator") or (
                item.get("quote") is not None and not isinstance(item.get("quote"), str)) or (
                item.get("text_sha256") is not None and not isinstance(item.get("text_sha256"), str)):
            problems.append("invalid_evidence")
            continue
        safe_cited.append({key: item[key] for key in ("locator", "quote", "text_sha256") if key in item})
        locator, quote = item.get("locator", ""), item.get("quote") or ""
        seg = doc.segment(locator)
        if seg is None:
            problems.append("locator_not_found")
            continue
        if item.get("text_sha256") and item["text_sha256"] != seg.text_sha256:
            problems.append("hash_mismatch")
        if quote and nz.squash(quote) not in nz.squash(seg.text):
            problems.append("quote_not_at_locator")
        if HIDDEN_FLAGS & set(seg.flags):
            problems.append("hidden_content")
        if "formula_without_cached_value" in seg.flags:
            problems.append("uncomputed_formula")
        refs.append(EvidenceRef(doc.document_id, seg.locator, quote or seg.text, seg.text_sha256, seg.text))
    if refs and not problems:
        order = {segment.locator: index for index, segment in enumerate(doc.segments)}
        refs.sort(key=lambda ref: order[ref.locator])
        missing_parts = value_problems(kind, proposal.get("value"), refs, doc, item_identity)
        if missing_parts:
            problems.append("value_not_in_evidence")
            problems.extend(f"unsupported_{p}" for p in missing_parts)
            problems.extend(p for p in missing_parts if p in {"ambiguous_number", "ambiguous_unit", "invalid_value"})
    return Candidate(value=proposal.get("value"), evidence=refs, problems=sorted(set(problems)),
                     cited=safe_cited)


def canonical(value: Any) -> str:
    """Comparable form: amounts compared as numbers, so 9412.5 and 9412.50 are the same value."""
    def exact_amount(v):
        number = _decimal(v)
        if number is None:
            raise InvalidOperation
        part = number.as_tuple()
        if len(part.digits) > 256 or abs(part.exponent) > 256:
            raise InvalidOperation
        digits = list(part.digits)
        if not any(digits):
            return "0"
        exponent = part.exponent
        while digits[-1] == 0:
            digits.pop()
            exponent += 1
        coefficient = "".join(map(str, digits))
        return f"{'-' if part.sign else ''}{coefficient}e{exponent}"

    def norm(v):
        if isinstance(v, dict):
            return {k: (exact_amount(x) if k == "amount" and x is not None else norm(x))
                    for k, x in sorted(v.items())}
        if isinstance(v, list):
            return [norm(x) for x in v]
        return v
    return json.dumps(norm(value), sort_keys=True)


def resolve_field(doc: Document, name: str, kind: str, proposals: list[dict], source: str = "rules",
                  *, item_identity: tuple[str | None, str | None, dict | None] | None = None) -> FieldResult:
    if not isinstance(proposals, list):
        proposals = [proposals]
    candidates = [check_candidate(doc, kind, p, item_identity) for p in proposals]
    critical = base_name(name) in CRITICAL_FIELDS
    verified, distinct = [], set()
    for candidate in candidates:
        if not candidate.verified:
            continue
        try:
            comparable = canonical(candidate.value)
        except (InvalidOperation, TypeError, ValueError, OverflowError):
            candidate.problems.extend(["value_not_in_evidence", "invalid_value"])
            continue
        verified.append(candidate)
        distinct.add(comparable)
    if not candidates:
        return FieldResult(name, kind, "missing", None, [], [], critical, source, ["not stated in the document"])
    if not verified:
        reasons = sorted({p for c in candidates for p in c.problems})
        return FieldResult(name, kind, "unverified", None, [], candidates, critical, source, reasons)
    if len(distinct) > 1:
        return FieldResult(name, kind, "conflict", None, [], candidates, critical, source,
                           [f"{len(distinct)} different values are stated"])
    evidence = []
    for c in verified:
        evidence.extend(e for e in c.evidence if e not in evidence)
    return FieldResult(name, kind, "extracted", verified[0].value, evidence, candidates, critical, source)
