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
from decimal import Decimal
from typing import Any

from ..documents import Document
from ..settings import materials_config
from . import normalize as nz

CRITICAL_FIELDS = frozenset({"supplier_name", "material", "quantity", "pricing", "settlement_currency",
                             "incoterm", "valid_until"})
HIDDEN_FLAGS = frozenset({"low_visibility", "partly_low_visibility", "hidden_sheet", "hidden_row", "hidden_column"})
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

def _amount_supported(amount: str, texts: list[str]) -> bool:
    try:
        target = Decimal(str(amount))
    except Exception:  # noqa: BLE001
        return False
    if any(target in nz.numbers_in(t) for t in texts):
        return True
    # a total written as separate numeric cells, for example two lots of 50 t for 100 t
    cells = [nz.parse_decimal(t) for t in texts if re.fullmatch(r"[\d.,]+", t.strip())]
    cells = [c for c in cells if c is not None]
    return len(cells) > 1 and sum(cells) == target


def _unit_supported(unit: str, joined: str) -> bool:
    return unit in nz.units_in(joined)


def _money_supported(money: dict, texts: list[str], joined: str) -> list[str]:
    out = []
    if not isinstance(money, dict) or not _amount_supported(money.get("amount"), texts):
        out.append("amount")
    if not isinstance(money, dict) or money.get("currency") not in nz.currencies_in(joined):
        out.append("currency")
    return out


def value_problems(kind: str, value: Any, texts: list[str]) -> list[str]:
    """Which parts of the value cannot be read from the cited text (empty list means supported)."""
    joined = " | ".join(texts)
    if value is None:
        return ["empty_value"]
    if kind == "text":
        return [] if nz.squash(value) in nz.squash(joined) else ["text"]
    if kind == "date":
        return [] if value in nz.dates_in(joined) else ["date"]
    if kind == "integer":
        return [] if Decimal(str(value)) in nz.numbers_in(joined) else ["number"]
    if kind == "currency":
        return [] if value in nz.currencies_in(joined) else ["currency"]
    if kind == "quantity":
        out = [] if _amount_supported(value.get("amount"), texts) else ["amount"]
        out += [] if _unit_supported(value.get("unit"), joined) else ["unit"]
        if value.get("period") and value["period"].casefold() not in joined.casefold():
            out.append("period")
        return out
    if kind == "incoterm":
        out = [] if value.get("code") in nz.incoterms_in(joined) else ["code"]
        place = (value.get("place") or "").split(",")[0].strip()
        return out + ([] if not place or nz.squash(place) in nz.squash(joined) else ["place"])
    if kind == "pricing":
        if value.get("type") == "fixed":
            out = _money_supported(value.get("unit_price"), texts, joined)
        elif value.get("type") == "index_linked":
            out = _money_supported(value.get("premium"), texts, joined)
            index_word = (value.get("index") or "").split()[:1]
            if not index_word or nz.squash(index_word[0]) not in nz.squash(joined):
                out.append("index")
        else:
            return ["pricing_type"]
        return out + ([] if _unit_supported(value.get("per_unit"), joined) else ["unit"])
    if kind == "shipments":
        out = []
        for lot in value or []:
            q = lot.get("quantity") or {}
            if not _amount_supported(q.get("amount"), texts) or not _unit_supported(q.get("unit"), joined):
                out.append("lot_quantity")
        return out or ([] if value else ["empty_value"])
    if kind == "material":
        cfg = materials_config()["materials"].get(value)
        if cfg is None:
            return ["material_not_in_catalog"]
        words = [cfg["display_name"].split()[0], *cfg.get("keywords", []), value.replace("_", " ")]
        return [] if any(nz.squash(w) in nz.squash(joined) for w in words) else ["material"]
    return ["unknown_kind"]


# ---------- the check ----------

def check_candidate(doc: Document, kind: str, proposal: dict) -> Candidate:
    problems, refs = [], []
    cited = proposal.get("evidence") or []
    if not cited:
        problems.append("no_evidence")
    for item in cited:
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
        missing_parts = value_problems(kind, proposal.get("value"), [r.segment_text for r in refs])
        if missing_parts:
            problems.append("value_not_in_evidence")
            problems.extend(f"unsupported_{p}" for p in missing_parts)
    return Candidate(value=proposal.get("value"), evidence=refs, problems=sorted(set(problems)),
                     cited=[dict(c) for c in cited])


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
