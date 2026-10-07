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
    if len(cells) > 1 and all(len(cell) == 1 for cell in cells) and sum(next(iter(cell)) for cell in cells) == target:
        return None
    if style is None and any(nz.ambiguous_number_in(text) for text in texts):
        return "ambiguous_number"
    return "amount"


def _related_cells(first: str, second: str, unit_text: str) -> bool:
    csv_a, csv_b = re.fullmatch(r"r(\d+):c(\d+)", first), re.fullmatch(r"r(\d+):c(\d+)", second)
    if csv_a and csv_b:
        same_row = csv_a.group(1) == csv_b.group(1) and int(csv_b.group(2)) == int(csv_a.group(2)) + 1
        header = csv_a.group(2) == csv_b.group(2) and int(csv_a.group(1)) == int(csv_b.group(1)) + 1
        return (same_row and bool(re.fullmatch(r"[A-Za-z]+", unit_text.strip()))) or header
    xlsx_a = re.fullmatch(r"(?:'[^']+'|[^!]+)!([A-Z]+)(\d+)", first)
    xlsx_b = re.fullmatch(r"(?:'[^']+'|[^!]+)!([A-Z]+)(\d+)", second)
    return bool(xlsx_a and xlsx_b and first.rsplit("!", 1)[0] == second.rsplit("!", 1)[0]
                and xlsx_a.group(1) == xlsx_b.group(1)
                and int(xlsx_a.group(2)) == int(xlsx_b.group(2)) + 1)


def _quantity_unit_problem(amount: Any, unit: Any, refs: list[EvidenceRef]) -> str | None:
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
            if not _related_cells(amount_ref.locator, unit_ref.locator, unit_ref.segment_text):
                continue
            if nz.unit_problem(unit_ref.segment_text):
                return nz.unit_problem(unit_ref.segment_text)
            if unit in nz.units_in(unit_ref.segment_text):
                return None
    return "unit"


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
        if len(nz.windows_ordered_in(clause)) > 1:
            # One shared quantity can describe equal lots. Distinct quantities on the
            # same clause need explicit pairings, which this reader cannot establish.
            quantity_tokens = re.findall(
                r"\b\d[\d.,]*\s*(?:metric\s+tonnes?|metric\s+tons?|tonnes?|kilograms?|kgs?|pounds?|lbs?|MTs?|t)\b",
                clause, re.I)
            if len(quantity_tokens) != 1:
                return ["lot_quantity"]
        row = re.match(r"r(\d+):c", window_ref.locator)
        related = ([EvidenceRef(window_ref.document_id, window_ref.locator, window_ref.quote,
                                window_ref.text_sha256, clause)] if clause != window_ref.segment_text else
                   [ref for ref in refs if re.match(rf"r{row.group(1)}:c", ref.locator)] if row else
                   [ref for ref in refs if ref.locator == window_ref.locator])
        texts = [ref.segment_text for ref in related]
        if _amount_problem(quantity.get("amount"), texts) or _quantity_unit_problem(
                quantity.get("amount"), quantity.get("unit"), related):
            return ["lot_quantity"]
    return []


def value_problems(kind: str, value: Any, refs: list[EvidenceRef]) -> list[str]:
    """Which parts of the value cannot be read from the cited text (empty list means supported)."""
    texts = [ref.segment_text for ref in refs]
    joined = " ".join(texts)
    if value is None:
        return ["empty_value"]
    if kind == "text":
        return [] if isinstance(value, str) and value.strip() and nz.squash(value) in nz.squash(joined) else ["text"]
    if kind == "date":
        return [] if isinstance(value, str) and value in nz.dates_in(joined) else ["date"]
    if kind == "integer":
        number = _decimal(value)
        if number is None or number != number.to_integral_value():
            return ["invalid_value"]
        problem = _amount_problem(value, texts)
        return [] if problem is None else [problem]
    if kind == "currency":
        return [] if isinstance(value, str) and any(value in nz.currencies_in(text) for text in texts) else ["currency"]
    if kind == "quantity":
        if not isinstance(value, dict):
            return ["invalid_value"]
        out = []
        amount_problem = _amount_problem(value.get("amount"), texts)
        if amount_problem:
            out.append(amount_problem)
        unit_problem = _quantity_unit_problem(value.get("amount"), value.get("unit"), refs)
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
            out = _money_supported(value.get("unit_price"), texts)
            amount = value.get("unit_price", {}).get("amount") if isinstance(value.get("unit_price"), dict) else None
        elif value.get("type") == "index_linked":
            if set(value) - {"type", "index", "averaging_period", "premium", "per_unit",
                              "fx_basis", "fx_terms", "fx_conversion", "quotational_period"}:
                return ["invalid_value"]
            out = _money_supported(value.get("premium"), texts)
            amount = value.get("premium", {}).get("amount") if isinstance(value.get("premium"), dict) else None
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
        unit_problem = _quantity_unit_problem(amount, value.get("per_unit"), refs)
        return out + ([unit_problem] if unit_problem else [])
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

def check_candidate(doc: Document, kind: str, proposal: Any) -> Candidate:
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
        missing_parts = value_problems(kind, proposal.get("value"), refs)
        if missing_parts:
            problems.append("value_not_in_evidence")
            problems.extend(f"unsupported_{p}" for p in missing_parts)
            problems.extend(p for p in missing_parts if p in {"ambiguous_number", "ambiguous_unit", "invalid_value"})
    return Candidate(value=proposal.get("value"), evidence=refs, problems=sorted(set(problems)),
                     cited=safe_cited)


def canonical(value: Any) -> str:
    """Comparable form: amounts compared as numbers, so 9412.5 and 9412.50 are the same value."""
    def norm(v):
        if isinstance(v, dict):
            return {k: (str(Decimal(str(x)).normalize()) if k == "amount" and x is not None else norm(x))
                    for k, x in sorted(v.items())}
        if isinstance(v, list):
            return [norm(x) for x in v]
        return v
    return json.dumps(norm(value), sort_keys=True)


def resolve_field(doc: Document, name: str, kind: str, proposals: list[dict], source: str = "rules") -> FieldResult:
    if not isinstance(proposals, list):
        proposals = [proposals]
    candidates = [check_candidate(doc, kind, p) for p in proposals]
    critical = base_name(name) in CRITICAL_FIELDS
    verified = [c for c in candidates if c.verified]
    distinct = {canonical(c.value) for c in verified}
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
