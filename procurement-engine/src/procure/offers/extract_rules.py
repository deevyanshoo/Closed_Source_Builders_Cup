"""Rule-based offer extractor: the offline fallback when Gemini is not used.

It reads located segments only and proposes candidates with the exact locator and quote each
value came from. The evidence check then decides what counts. Rules are generic: label/value
pairs ("Payment: LC at sight", a label cell beside a value cell), table headers, fixed-width
PDF tables, Incoterm and price patterns. Hidden content and uncomputed formulas are never read
as values. When a rule cannot read something it proposes nothing, so the field stays missing.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from ..documents import Document, Segment
from ..settings import materials_config
from . import normalize as nz
from .evidence import HIDDEN_FLAGS

# ---------- entries: label/value pairs and table rows ----------


@dataclass
class Cell:
    """A piece of text with the segment it lives in. `quote` is the exact substring used."""
    seg: Segment
    quote: str

    def ev(self) -> dict:
        return {"locator": self.seg.locator, "quote": self.quote}


@dataclass
class Entry:
    label: str
    value: Cell
    label_cell: Cell | None = None
    continuation: list[Cell] = field(default_factory=list)


@dataclass
class Table:
    header: list[Cell]
    rows: list[list[Cell | None]]


_LABEL_LINE = re.compile(r"^\s*([A-Za-z][A-Za-z /()&'._-]{0,40}?)\s*:\s*(\S.*)$")
_COLUMNS = re.compile(r"\S+(?: \S+)*")  # runs separated by two or more spaces


def _visible(doc: Document) -> list[Segment]:
    return [s for s in doc.segments
            if not (HIDDEN_FLAGS & set(s.flags)) and "formula_without_cached_value" not in s.flags]


def _is_numberish(text: str, decimal_comma: bool) -> bool:
    return nz.parse_decimal(text, decimal_comma=decimal_comma) is not None and len(re.sub(r"[\d.,\s]", "", text)) <= 3


def _split_label(seg: Segment) -> Entry:
    m = _LABEL_LINE.match(seg.text)
    if m:
        return Entry(m.group(1).strip(), Cell(seg, m.group(2).strip()), Cell(seg, m.group(1).strip()))
    return Entry("", Cell(seg, seg.text.strip()))


def _pdf_entries(lines: list[Segment], decimal_comma: bool) -> tuple[list[Entry], list[Table]]:
    entries, tables = [], []
    i = 0
    while i < len(lines):
        seg = lines[i]
        cols = _COLUMNS.findall(seg.text) if "  " in seg.text else []
        nxt = lines[i + 1] if i + 1 < len(lines) else None
        nxt_cols = _COLUMNS.findall(nxt.text) if nxt is not None and "  " in nxt.text else []
        if len(cols) >= 2 and len(nxt_cols) == len(cols) and not any(_is_numberish(c, decimal_comma) for c in cols):
            header = [Cell(seg, c) for c in cols]
            rows = []
            j = i + 1
            while j < len(lines):
                row_cols = _COLUMNS.findall(lines[j].text) if "  " in lines[j].text else []
                if len(row_cols) != len(cols):
                    break
                rows.append([Cell(lines[j], c) for c in row_cols])
                j += 1
            tables.append(Table(header, rows))
            i = j
            continue
        entry = _split_label(seg)
        if entries and not entry.label and (seg.text[:1].islower() or entries[-1].value.quote.rstrip().endswith(",")):
            entries[-1].continuation.append(Cell(seg, seg.text.strip()))
        else:
            entries.append(entry)
        i += 1
    return entries, tables


def _grid_entries(cells: list[Segment], decimal_comma: bool) -> tuple[list[Entry], list[Table]]:
    rows: dict[tuple, list[Segment]] = defaultdict(list)
    for s in cells:
        rows[(s.sheet or "", s.row)].append(s)
    keys = sorted(rows, key=lambda k: (k[0], k[1]))
    entries, tables = [], []
    i = 0
    while i < len(keys):
        row = sorted(rows[keys[i]], key=lambda s: s.column)
        texts = [s.text for s in row]
        if len(row) >= 3 and not any(_is_numberish(t, decimal_comma) or s.value_type in ("number", "date")
                                     for t, s in zip(texts, row)):
            # header row: following rows in the same sheet that fill at least two of its columns
            cols = [s.column for s in row]
            body, j = [], i + 1
            while j < len(keys) and keys[j][0] == keys[i][0] and keys[j][1] == keys[j - 1][1] + 1:
                nxt = {s.column: s for s in rows[keys[j]]}
                if sum(c in nxt for c in cols) < 2:
                    break
                body.append([Cell(nxt[c], nxt[c].text) if c in nxt else None for c in cols])
                j += 1
            if body:
                tables.append(Table([Cell(s, s.text) for s in row], body))
                i = j
                continue
        if len(row) == 1:
            entry = _split_label(row[0])
            prev = entries[-1] if entries else None
            if (not entry.label and prev and prev.label_cell is not None and prev.value.seg.column == row[0].column
                    and prev.value.seg.row == row[0].row - 1 - len(prev.continuation) and prev.value.seg.sheet == row[0].sheet):
                prev.continuation.append(Cell(row[0], row[0].text))
            else:
                entries.append(entry)
        else:
            label, value = row[0], row[1]
            if nz.squash(value.text) in {"value", "values", "detail", "details", "info"}:
                i += 1
                continue
            if not _is_numberish(label.text, decimal_comma):
                entries.append(Entry(label.text.strip(), Cell(value, value.text), Cell(label, label.text)))
            for extra in row[2:]:
                entries.append(_split_label(extra))
        i += 1
    return entries, tables


# ---------- what a label means ----------

_ROLES = [  # order matters: the first match wins
    ("lot", r"^lot( no| number)?$"),
    ("moq", r"minimum (order|parcel)|min(imum)? order|\bmoq\b"),
    ("capacity", r"capacity"),
    ("lead_time", r"lead time"),
    ("payment", r"^payment"),
    ("validity", r"valid"),
    ("unit", r"(^|\s)unit$|^uom$"),
    ("currency", r"currency"),
    ("total", r"line total|^total$|amount$"),
    ("price", r"price|premium|^rate$"),
    ("place", r"^(port|place|named place|port of loading|destination)$"),
    ("incoterm", r"incoterm|delivery terms|^delivery$|^terms$"),
    ("ship_window", r"ship|laycan|month|delivery date|schedule"),
    ("supplier", r"^(supplier|vendor|seller)( name)?$"),
    ("origin", r"origin"),
    ("quantity", r"quantity|^qty"),
    ("grade", r"grade|specification|standard|alloy|temper|^spec"),
    ("product", r"^(product|material|item|description|commodity)$"),
    ("basis", r"price basis|pricing|basis|formula"),
]


def role(label: str) -> str | None:
    text = nz.squash(label.replace("_", " "))
    if not text:
        return None
    m = re.fullmatch(r"(.+?) unit", text)
    if m and role(m.group(1)) in ("price", "quantity", "moq", "capacity"):
        return f"{role(m.group(1))}_unit"
    if re.search(r"price basis|^pricing$", text):
        return "basis"
    for name, pattern in _ROLES:
        if re.search(pattern, text):
            return name
    return None


# ---------- value readers ----------

_UNIT_WORD = r"(metric\s+tonnes?|metric\s+tons?|tonnes?|MTs?|t|kgs?|kilograms?|lbs?|pounds?)"
_AMOUNT_UNIT = re.compile(rf"(\d[\d.,]*)\s*{_UNIT_WORD}(?![A-Za-z])")
_PER_UNIT = re.compile(rf"(?:\bper\b|/)\s*{_UNIT_WORD}(?![A-Za-z])", re.I)
_CURRENCY_PATTERNS = [re.compile(r"(?<![A-Za-z])([A-Z]{3})\s?(?=\d)"), re.compile(r"(?<![A-Za-z])([A-Z]{3})\s*[¥$€£]?\s*/"),
                      re.compile(r"\(([A-Z]{3})\b"), re.compile(r"\b([A-Z]{3})\s+per\b")]
_PERIOD = re.compile(r"(?:\bper\b|/|\ba\b)\s*(month|quarter|year|week)|\b(monthly|quarterly|annual|yearly|weekly)\b", re.I)
_PERIOD_WORD = {"monthly": "month", "quarterly": "quarter", "annual": "year", "yearly": "year", "weekly": "week"}
_COMPANY = re.compile(r"\b(Ltd|Ltda|GmbH|SpA|S\.?A\.?|Inc|LLC|FZE|Co\.|Pty|Sdn Bhd|SAC|AG|BV|PLC|Corp|Limited)\b")


def _unit_of(text: str) -> str | None:
    found = nz.units_in(text)
    return next(iter(found)) if len(found) == 1 else None


def _per_unit(*texts: str) -> str | None:
    for text in texts:
        m = _PER_UNIT.search(text or "")
        if m:
            return _unit_of(m.group(1))
        if text and "per tonne" in text.replace("_", " ").lower():
            return "tonne"
    for text in texts:  # a header like "Price (USD/t)" or a label like price_per_tonne
        unit = _unit_of((text or "").replace("_", " "))
        if unit:
            return unit
    return None


def _currency_of(*texts: str) -> str | None:
    for text in texts:
        if re.fullmatch(r"[A-Z]{3}", (text or "").strip()) and text.strip() not in nz.INCOTERMS_2020:
            return text.strip()  # a currency column holding just the code
        for symbol, code in (("US$", "USD"), ("€", "EUR"), ("£", "GBP"), ("₹", "INR")):
            if symbol in (text or ""):
                return code
        for pattern in _CURRENCY_PATTERNS:
            for code in pattern.findall(text or ""):
                if code not in nz.INCOTERMS_2020 and code not in {"LME", "ECB", "MOQ", "ETP", "ACR", "SYN", "LOT"}:
                    return code
    return None


def _amount(text: str, decimal_comma: bool):
    value = nz.parse_decimal(text, decimal_comma=decimal_comma)
    if value is None:
        m = _AMOUNT_UNIT.search(text) or re.search(r"(?<![A-Za-z])(?:[A-Z]{3}|US\$)\s?(\d[\d.,]*)", text)
        if m:
            value = nz.parse_decimal(m.group(1), decimal_comma=decimal_comma)
    return value


def _quantity(cell: Cell, decimal_comma: bool, *unit_hints: str) -> dict | None:
    m = _AMOUNT_UNIT.search(cell.quote)
    if m:
        amount, unit = nz.parse_decimal(m.group(1), decimal_comma=decimal_comma), _unit_of(m.group(2))
    else:
        amount = nz.parse_decimal(cell.quote, decimal_comma=decimal_comma)
        unit = next((u for u in (_unit_of((h or "").replace("_", " ")) for h in unit_hints) if u), None)
    if amount is None:
        return None
    return {"amount": str(amount), "unit": unit}


def _text_value(text: str) -> str:
    return text.strip().rstrip(".").strip()


def _split_product(text: str) -> tuple[str, str | None]:
    text = _text_value(text)
    if "," in text:
        head, tail = text.split(",", 1)
        return head.strip(), tail.strip() or None
    m = re.match(r"^(.*?)\s+(\d+(?:\.\d+)?%\s*Fe|[A-Z][A-Za-z]*-?\d[\w-]*|[A-Z]{2,}\d*[\w-]*)$", text)
    if m and m.group(1):
        return m.group(1).strip(), m.group(2).strip()
    return text, None


def material_for(text: str) -> str | None:
    """Catalogue material whose name or keywords appear in the text (None if none or several)."""
    found = []
    for key, cfg in materials_config()["materials"].items():
        words = [cfg["display_name"].split()[0], *cfg.get("keywords", []), key.replace("_", " ")]
        if any(re.search(rf"\b{re.escape(w.lower())}", text.lower()) for w in words):
            found.append(key)
    return found[0] if len(found) == 1 else None


# ---------- the extractor ----------

def proposal(value, *cells: Cell | None) -> dict:
    return {"value": value, "evidence": [c.ev() for c in cells if c is not None]}


def _add(bucket: list[dict], prop: dict) -> None:
    key = (repr(prop["value"]), tuple(e["locator"] for e in prop["evidence"]))
    if all((repr(p["value"]), tuple(e["locator"] for e in p["evidence"])) != key for p in bucket):
        bucket.append(prop)


def extract(doc: Document) -> dict:
    """Proposals: {"fields": {name: [proposal]}, "items": [{name: [proposal]}]}."""
    visible = _visible(doc)
    decimal_comma = nz.uses_decimal_comma(s.text for s in visible)
    if doc.media_type == "application/pdf":
        entries, tables = _pdf_entries(visible, decimal_comma)
    else:
        entries, tables = _grid_entries(visible, decimal_comma)

    fields: dict[str, list[dict]] = defaultdict(list)
    items: list[dict[str, list[dict]]] = []
    currency_cells = [e.value for e in entries
                      if role(e.label) == "currency" and re.fullmatch(r"[A-Z]{3}", e.value.quote.strip())]

    def price_props(value_cell: Cell, label_cell: Cell | None, extra: list[Cell | None]) -> dict | None:
        texts = [value_cell.quote, label_cell.quote if label_cell else ""] + [c.quote for c in extra if c]
        amount = _amount(value_cell.quote, decimal_comma)
        currency = _currency_of(*texts)
        cur_cell = None
        if currency is None and currency_cells:
            cur_cell = currency_cells[0]
            currency = cur_cell.quote.strip()
        if amount is None or currency is None:
            return None
        per_unit = _per_unit(*texts)
        value = {"type": "fixed", "unit_price": {"amount": str(amount), "currency": currency}, "per_unit": per_unit}
        return proposal(value, value_cell, label_cell, *extra, cur_cell)

    # ----- labelled entries -----
    single: dict[str, list[dict]] = defaultdict(list)
    index_basis: Entry | None = None
    premium_cells: list[Cell] = []
    for e in entries:
        r = role(e.label)
        text = e.value.quote
        if "premium" in (e.label + " " + text).lower() and _currency_of(text):
            premium_cells.append(e.value)
            continue
        if r == "basis" or (r is None and re.search(r"\bLME\b|cash settlement|index", text)):
            index_basis = e
            continue
        if r == "supplier":
            _add(fields["supplier_name"], proposal(_text_value(text), e.value))
        elif r == "origin":
            _add(fields["origin_country"], proposal(_text_value(text), e.value))
        elif r == "payment":
            _add(fields["payment_terms"], proposal(_text_value(text), e.value))
        elif r == "validity":
            for d in sorted(nz.dates_in(text)):
                _add(fields["valid_until"], proposal(d, e.value))
        elif r == "lead_time":
            m = re.search(r"(\d+)\s*(?:calendar\s+)?days?\b", text, re.I)
            if m:
                _add(fields["lead_time_days"], proposal(int(m.group(1)), e.value))
            elif re.fullmatch(r"\d+", text.strip()) and "day" in e.label.lower():
                _add(fields["lead_time_days"], proposal(int(text.strip()), e.value, e.label_cell))
        elif r in ("moq", "capacity"):
            q = _quantity(e.value, decimal_comma, e.label)
            if q:
                if r == "capacity":
                    m = _PERIOD.search(text) or _PERIOD.search(e.label.replace("_", " "))
                    q["period"] = (m.group(1) or _PERIOD_WORD[m.group(2).lower()]).lower() if m else None
                _add(fields["min_order" if r == "moq" else "capacity"], proposal(q, e.value, e.label_cell))
        elif r == "currency":
            code = text.strip()
            if re.fullmatch(r"[A-Z]{3}", code):
                _add(fields["settlement_currency"], proposal(code, e.value))
        elif r == "incoterm":
            _incoterm(fields, e.value)
        elif r == "quantity":
            q = _quantity(e.value, decimal_comma, e.label)
            if q:
                _add(single["quantity"], proposal(q, e.value, e.label_cell))
            _lot_sentence(fields, e.value, decimal_comma, q)
            _laycan(fields, e.value, decimal_comma, entries)
        elif r == "product":
            desc, grade = _split_product(text)
            _add(single["description"], proposal(desc, e.value))
            if grade:
                _add(single["grade"], proposal(grade, e.value))
        elif r == "grade":
            _add(single["grade"], proposal(_text_value(text), e.value))
        elif r == "price":
            p = price_props(e.value, e.label_cell, [])
            if p:
                _add(single["pricing"], p)
            _incoterm(fields, e.value)
        elif r == "ship_window" or role(e.label) is None:
            _lot_sentence(fields, e.value, decimal_comma, None)
            for cont in [e.value, *e.continuation]:
                _laycan(fields, cont, decimal_comma, entries, e.label)
        if r == "ship_window" and e.label_cell is not None:
            for cell in [e.value, *e.continuation]:
                _lot_line(fields, cell, decimal_comma)
        if r is None:
            _incoterm(fields, e.value, labelled=False)

    # ----- index-linked price -----
    if index_basis is not None and not premium_cells:
        premium_cells = [c for c in index_basis.continuation if "premium" in c.quote.lower() and _currency_of(c.quote)]
    if premium_cells:
        prem = premium_cells[0]
        amount = _amount(prem.quote, decimal_comma)
        currency = _currency_of(prem.quote)
        if amount is not None and currency:
            index_text, averaging, cells = None, None, [prem]
            if index_basis is not None:
                parts = [index_basis.value.quote] + [c.quote for c in index_basis.continuation if c is not prem]
                joined = " ".join(p.strip() for p in parts).rstrip(",")
                index_text, _, averaging = joined.partition(",")
                cells = [index_basis.value, *[c for c in index_basis.continuation if c is not prem], prem]
            value = {"type": "index_linked", "index": (index_text or "").strip() or None,
                     "averaging_period": (averaging or "").strip().rstrip(",") or None,
                     "premium": {"amount": str(amount), "currency": currency}, "per_unit": _per_unit(prem.quote)}
            single["pricing"] = [proposal(value, *cells)]

    # ----- tables -----
    for table in tables:
        roles = [role(h.quote) for h in table.header]
        if "price" in roles:
            items.extend(_table_items(table, roles, decimal_comma, price_props, fields))
        elif "lot" in roles or "ship_window" in roles:
            _table_lots(table, roles, decimal_comma, fields)

    # ----- free-text price statements (for conflicts across the document) -----
    if not items:
        items = [single] if any(single.values()) else []
    if len(items) == 1 and items[0].get("pricing") and items[0]["pricing"][0]["value"]["type"] == "fixed":
        for seg in visible:
            if "premium" in seg.text.lower():
                continue
            for m in re.finditer(rf"(?:([A-Z]{{3}})|US\$)\s?(\d[\d.,]*)\s*(?:per|/)\s*{_UNIT_WORD}(?![A-Za-z])", seg.text):
                amount = nz.parse_decimal(m.group(2), decimal_comma=decimal_comma)
                if amount is None or (m.group(1) or "USD") in nz.INCOTERMS_2020:
                    continue
                value = {"type": "fixed", "unit_price": {"amount": str(amount), "currency": m.group(1) or "USD"},
                         "per_unit": _unit_of(m.group(3))}
                cell = Cell(seg, m.group(0))
                if all(nz.squash(str(p["value"]["unit_price"]["amount"])) != nz.squash(str(amount)) or
                       p["value"]["unit_price"]["currency"] != value["unit_price"]["currency"]
                       for p in items[0]["pricing"]):
                    _add(items[0]["pricing"], proposal(value, cell))

    # ----- supplier fallback for letter-style PDFs -----
    if not fields["supplier_name"]:
        for seg in visible:
            if "SYNTHETIC TEST DOCUMENT" in seg.text or seg.text.lower().startswith(("to:", "for and on behalf")):
                continue
            if _COMPANY.search(seg.text) and ":" not in seg.text:
                _add(fields["supplier_name"], proposal(_text_value(seg.text), Cell(seg, seg.text)))
                break

    # ----- free-text validity ("valid until ...") and currency from prices -----
    if not fields["valid_until"]:
        for seg in visible:
            if re.search(r"\bvalid", seg.text, re.I):
                for d in sorted(nz.dates_in(seg.text)):
                    _add(fields["valid_until"], proposal(d, Cell(seg, seg.text)))
    for item in items:
        for p in item.get("pricing", []):
            money = p["value"].get("unit_price") or p["value"].get("premium")
            if money and money.get("currency"):
                _add(fields["settlement_currency"], {"value": money["currency"], "evidence": p["evidence"]})

    # ----- material from the description -----
    for item in items:
        for p in item.get("description", []):
            key = material_for(p["value"])
            if key:
                _add(item.setdefault("material", []), {"value": key, "evidence": p["evidence"]})
    for props in fields.get("shipments", []):
        props.pop("_lines", None)
    return {"fields": dict(fields), "items": items}


def _incoterm(fields, cell: Cell, labelled: bool = True, place_cell: Cell | None = None) -> None:
    for code in sorted(nz.incoterms_in(cell.quote)):
        m = re.search(rf"(?<![A-Za-z]){code}(?![A-Za-z])\s*([^()\n]*)", cell.quote)
        place = (m.group(1) if m else "").strip().strip(",.;").strip()
        place = re.sub(r"\s*\(?Incoterms?\s*20\d\d\)?", "", place).strip(" ,")
        if not labelled and not place:
            continue
        if place_cell is not None and not place:
            place = place_cell.quote.strip()
        _add(fields["incoterm"], proposal({"code": code, "place": place or None}, cell, place_cell))


_NUMBER_WORDS = {"one": 1, "single": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}


def _lot_sentence(fields, cell: Cell, decimal_comma: bool, total: dict | None) -> None:
    """'two equal lots of 20 t, November 2026 and December 2026' or 'four monthly lots of 24 t from Nov 2026'."""
    m = re.search(rf"\b(one|two|three|four|five|six|\d+)\s+(?:equal\s+)?(monthly\s+)?lots?\s+of\s+(\d[\d.,]*)\s*{_UNIT_WORD}",
                  cell.quote, re.I)
    if not m:
        return
    count = _NUMBER_WORDS.get(m.group(1).lower()) or int(m.group(1))
    amount = nz.parse_decimal(m.group(3), decimal_comma=decimal_comma)
    unit = _unit_of(m.group(4))
    months = _ordered_months(cell.quote)
    if len(months) < count and m.group(2) and len(months) == 1:
        y, mo = map(int, months[0].split("-"))
        months = [f"{y + (mo - 1 + k) // 12}-{(mo - 1 + k) % 12 + 1:02d}" for k in range(count)]
    if amount is None or unit is None or len(months) != count:
        return
    _add(fields["shipments"], proposal([{"quantity": {"amount": str(amount), "unit": unit}, "window": mo}
                                        for mo in months], cell))


def _ordered_months(text: str) -> list[str]:
    found = []
    for m in re.finditer(rf"\b{nz._MONTH_WORD}\s+(\d{{4}})\b|\b(\d{{4}})-(\d{{2}})\b(?!-)", text, re.I):
        if m.group(1):
            found.append(f"{m.group(2)}-{nz._MONTHS[m.group(1).lower()[:3]]:02d}")
        else:
            found.append(f"{m.group(3)}-{m.group(4)}")
    return found


def _lot_line(fields, cell: Cell, decimal_comma: bool) -> None:
    """'30,000 t in December 2026' under a Shipments label."""
    m = _AMOUNT_UNIT.search(cell.quote)
    months = _ordered_months(cell.quote)
    if m and len(months) == 1:
        amount = nz.parse_decimal(m.group(1), decimal_comma=decimal_comma)
        lot = {"quantity": {"amount": str(amount), "unit": _unit_of(m.group(2))}, "window": months[0]}
        bucket = fields["shipments"]
        if bucket and bucket[-1].get("_lines"):
            bucket[-1]["value"].append(lot)
            bucket[-1]["evidence"].append(cell.ev())
        else:
            bucket.append({"value": [lot], "evidence": [cell.ev()], "_lines": True})


def _laycan(fields, cell: Cell, decimal_comma: bool, entries: list[Entry], label: str = "") -> None:
    """A laycan or single-shipment month together with the offered quantity."""
    text = cell.quote
    if not re.search(r"laycan|single shipment", f"{label} {text}", re.I):
        return
    window = None
    m = re.search(rf"\b(\d{{1,2}})\s*-\s*(\d{{1,2}})\s+{nz._MONTH_WORD}\s+(\d{{4}})", text, re.I)
    if m:
        mo = nz._MONTHS[m.group(3).lower()[:3]]
        window = f"{m.group(4)}-{mo:02d}-{int(m.group(1)):02d}/{m.group(4)}-{mo:02d}-{int(m.group(2)):02d}"
    else:
        months = _ordered_months(text)
        window = months[0] if len(months) == 1 else None
    if window is None:
        return
    qty_cells = [e for e in entries if role(e.label) == "quantity"]
    q = _quantity(cell, decimal_comma) if _AMOUNT_UNIT.search(text) else None
    cells = [cell]
    if q is None and qty_cells:
        q = _quantity(qty_cells[0].value, decimal_comma, qty_cells[0].label)
        cells += [qty_cells[0].value, qty_cells[0].label_cell]
    if q and q.get("unit"):
        _add(fields["shipments"], proposal([{"quantity": q, "window": window}], *cells))


def _table_lots(table: Table, roles: list[str | None], decimal_comma: bool, fields) -> None:
    lots, cells = [], []
    for row in table.rows:
        lot = _row_lot(row, roles, table.header, decimal_comma)
        if lot is None:
            return
        lots.append(lot[0])
        cells.extend(lot[1])
    if lots:
        _add(fields["shipments"], {"value": lots, "evidence": [c.ev() for c in cells if c is not None]})


def _row_lot(row, roles, header, decimal_comma):
    by_role = {r: (row[i], header[i]) for i, r in enumerate(roles) if r and row[i] is not None}
    if "quantity" not in by_role or "ship_window" not in by_role:
        return None
    qcell, qhead = by_role["quantity"]
    unit_cell = by_role.get("quantity_unit", by_role.get("unit", (None, None)))[0]
    q = _quantity(qcell, decimal_comma, qhead.quote, unit_cell.quote if unit_cell else "")
    wcell = by_role["ship_window"][0]
    months = _ordered_months(wcell.quote)
    if q is None or q["unit"] is None or len(months) != 1:
        return None
    return {"quantity": q, "window": months[0]}, [qcell, qhead, unit_cell, wcell]


def _table_items(table: Table, roles, decimal_comma, price_props, fields) -> list[dict]:
    items: list[dict] = []
    merged: dict[tuple, dict] = {}
    lot_rows: list = []
    for row in table.rows:
        cells = {r: (row[i], table.header[i]) for i, r in enumerate(roles) if r and row[i] is not None}
        if "price" not in cells:
            continue
        pcell, phead = cells["price"]
        unit_cell = cells.get("price_unit", cells.get("unit", (None, None)))[0]
        qty_unit_cell = cells.get("quantity_unit", cells.get("unit", (None, None)))[0]
        cur_cell = cells.get("currency", (None, None))[0]
        p = price_props(pcell, phead, [unit_cell, cur_cell])
        product = cells.get("product")
        key = (product[0].quote if product else None, pcell.quote, cur_cell.quote if cur_cell else None)
        item = merged.get(key)
        if item is None:
            item = defaultdict(list)
            merged[key] = item
            items.append(item)
            if product:
                # with a separate grade column the product cell is the whole description
                desc, grade = ((_text_value(product[0].quote), None) if "grade" in cells
                               else _split_product(product[0].quote))
                _add(item["description"], proposal(desc, product[0]))
                if grade:
                    _add(item["grade"], proposal(grade, product[0]))
            if "grade" in cells:
                _add(item["grade"], proposal(_text_value(cells["grade"][0].quote), cells["grade"][0]))
            if p:
                _add(item["pricing"], p)
        if "quantity" in cells:
            qcell, qhead = cells["quantity"]
            q = _quantity(qcell, decimal_comma, qhead.quote, qty_unit_cell.quote if qty_unit_cell else "")
            if q:
                item.setdefault("_qty", []).append((q, [qcell, qhead, qty_unit_cell]))
        if "lot" in cells:
            lot = _row_lot(row, roles, table.header, decimal_comma)
            if lot:
                lot_rows.append(lot)
        if "incoterm" in cells:
            place = cells.get("place", (None, None))[0]
            _incoterm(fields, cells["incoterm"][0], place_cell=place)
        for r in ("supplier", "origin", "payment"):
            if r in cells:
                name = {"supplier": "supplier_name", "origin": "origin_country", "payment": "payment_terms"}[r]
                _add(fields[name], proposal(_text_value(cells[r][0].quote), cells[r][0]))
        if "moq" in cells or "capacity" in cells:
            for r in ("moq", "capacity"):
                if r not in cells:
                    continue
                c, h = cells[r]
                extra = cells.get(f"{r}_unit", (None, None))[0]
                q = _quantity(c, decimal_comma, h.quote, extra.quote if extra else "")
                if q:
                    if r == "capacity":
                        m = _PERIOD.search(h.quote.replace("_", " "))
                        q["period"] = (m.group(1) or _PERIOD_WORD[m.group(2).lower()]).lower() if m else None
                    _add(fields["min_order" if r == "moq" else "capacity"], proposal(q, c, h, extra))
        if "validity" in cells:
            for d in sorted(nz.dates_in(cells["validity"][0].quote)):
                _add(fields["valid_until"], proposal(d, cells["validity"][0]))
        if "lead_time" in cells:
            c, h = cells["lead_time"]
            if re.fullmatch(r"\d+", c.quote.strip()) and "day" in h.quote.lower():
                _add(fields["lead_time_days"], proposal(int(c.quote.strip()), c, h))
    for item in items:
        qs = item.pop("_qty", [])
        if len(qs) == 1:
            q, cells = qs[0]
            _add(item["quantity"], proposal(q, *cells))
        elif len(qs) > 1 and len({q["unit"] for q, _ in qs}) == 1:
            total = sum(nz.parse_decimal(q["amount"]) for q, _ in qs)
            cells = [c for _, cs in qs for c in cs]
            _add(item["quantity"], proposal({"amount": str(total), "unit": qs[0][0]["unit"]}, *cells))
    if lot_rows:
        _add(fields["shipments"], {"value": [l for l, _ in lot_rows],
                                   "evidence": [c.ev() for _, cs in lot_rows for c in cs if c is not None]})
    return [dict(i) for i in items]
