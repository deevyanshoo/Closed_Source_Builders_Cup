"""Turn a read document into an evidence-checked offer extraction.

1. Propose candidates with Gemini (structured JSON) or, without a key, offline or after a
   failed call, with the rule-based extractor. The extractor used and why is recorded.
2. Check every candidate against the document (evidence.py). Unverifiable values are dropped.
3. Derive the catalogue material from the verified product description, not from the model.
4. Add review flags (hidden content, embedded instructions, expiry, conflicts) and the
   Decimal-normalised values. Flags are information for a reviewer; they never trigger actions.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from ..documents import Document
from . import countries, extract_gemini, extract_rules
from . import normalize as nz
from .evidence import HIDDEN_FLAGS, FieldResult, resolve_field

FIELD_KINDS = {
    "supplier_name": "text", "origin_country": "text", "incoterm": "incoterm", "settlement_currency": "currency",
    "payment_terms": "text", "valid_until": "date", "lead_time_days": "integer", "min_order": "quantity",
    "capacity": "quantity", "shipments": "shipments",
}
ITEM_KINDS = {"description": "text", "grade": "text", "quantity": "quantity", "pricing": "pricing"}
INSTRUCTION_LIKE = re.compile(
    r"ignore (all |any )?(previous|prior|above) instructions|^\s*(system|assistant|developer)\s*:|"
    r"mark (this|the) (offer|quote) (as )?approved|send (the|an|this) rfq|place (an|the) order|approve (this|the) offer",
    re.I | re.M)


@dataclass
class Extraction:
    document: dict
    extractor: str  # rules | gemini
    extractor_detail: dict
    as_of: str
    synthetic: bool
    fields: dict[str, FieldResult]
    items: list[dict[str, FieldResult]]
    review_flags: list[dict]
    normalized: dict = field(default_factory=dict)

    def all_fields(self):
        yield from self.fields.values()
        for item in self.items:
            yield from item.values()

    def to_dict(self) -> dict:
        return {"document": self.document, "extractor": self.extractor, "extractor_detail": self.extractor_detail,
                "as_of": self.as_of, "synthetic": self.synthetic,
                "fields": {k: f.to_dict() for k, f in self.fields.items()},
                "items": [{k: f.to_dict() for k, f in item.items()} for item in self.items],
                "review_flags": self.review_flags, "normalized": self.normalized}


def _propose(doc: Document, mode: str, client) -> tuple[dict, str, dict]:
    detail = {"requested": mode, "call": "none"}
    if mode in ("auto", "gemini"):
        try:
            proposals, record = extract_gemini.extract(doc, client=client)
            return proposals, "gemini", {**detail, **record}
        except extract_gemini.GeminiUnavailable as exc:
            detail["fallback_reason"] = exc.reason
            if exc.attempted:
                detail.update({"call": exc.call, "attempted": True, "stage": exc.stage})
            if exc.error_kind:
                detail["error_kind"] = exc.error_kind
            if exc.auth:
                detail["auth"] = exc.auth
    return extract_rules.extract(doc), "rules", detail


def _material(doc: Document, name: str, description: FieldResult, source: str) -> FieldResult:
    """Catalogue material derived from the verified description, cited with the same evidence."""
    proposals = []
    if description.status == "extracted":
        key = extract_rules.material_for(description.value)
        if key:
            proposals.append({"value": key, "evidence": [{"locator": e.locator, "quote": e.quote}
                                                          for e in description.evidence]})
    result = resolve_field(doc, name, "material", proposals, source)
    if description.status == "extracted" and not proposals:
        result.reasons = ["description does not name a catalogue material"]
    return result


def _flags(doc: Document, fields: dict, items: list[dict], expiry: str) -> list[dict]:
    flags = []
    for s in doc.segments:
        if HIDDEN_FLAGS & set(s.flags):
            flags.append({"flag": "hidden_content", "locator": s.locator, "text": s.text})
        if "formula_without_cached_value" in s.flags:
            flags.append({"flag": "formula_without_cached_value", "locator": s.locator, "text": s.text})
        if INSTRUCTION_LIKE.search(s.text):
            flags.append({"flag": "embedded_instruction", "locator": s.locator, "text": s.text,
                          "handling": "stored as document text only; never executed"})
    for w in doc.warnings:
        flags.append({"flag": w.code, "locator": w.locator or (f"p{w.page}" if w.page else None), "text": w.message})
    if expiry == "expired":
        f = fields["valid_until"]
        flags.append({"flag": "expired", "locator": f.evidence[0].locator, "text": f.value})
    for result in [*fields.values(), *(r for item in items for r in item.values())]:
        if result.status == "conflict":
            name = "price_conflict" if result.name.endswith("pricing") else "field_conflict"
            locators = [e.locator for c in result.candidates if c.verified for e in c.evidence]
            flags.append({"flag": name, "field": result.name, "locator": locators[0] if locators else None,
                          "locators": locators, "text": "; ".join(result.reasons)})
    return flags


def _normalized(fields: dict, items: list[dict], as_of: dt.date, reporting_currency: str, rate_lookup) -> dict:
    def value(result):
        return result.value if result.status in ("extracted", "corrected") else None

    origin = value(fields["origin_country"])
    out = {"as_of": as_of.isoformat(), "reporting_currency": reporting_currency,
           "origin_country_code": countries.code_for(origin),
           "expiry": nz.expiry_status(value(fields["valid_until"]), as_of),
           "min_order": nz.normalize_quantity(value(fields["min_order"])) if value(fields["min_order"]) else None,
           "capacity": nz.normalize_quantity(value(fields["capacity"])) if value(fields["capacity"]) else None,
           "items": []}
    for item in items:
        q, p = value(item["quantity"]), value(item["pricing"])
        out["items"].append({
            "quantity": nz.normalize_quantity(q) if q else None,
            "pricing": nz.normalize_pricing(p, reporting_currency=reporting_currency, as_of=as_of,
                                            rate_lookup=rate_lookup) if p else None,
        })
    return out


def assemble(doc: Document, proposals: dict, source: str, detail: dict, *, as_of: dt.date,
             reporting_currency: str = "USD", rate_lookup=None, synthetic: bool | None = None) -> Extraction:
    fields = {name: resolve_field(doc, name, kind, proposals["fields"].get(name, []), source)
              for name, kind in FIELD_KINDS.items()}
    items = []
    for i, item_props in enumerate(proposals.get("items") or [{}]):
        verified_siblings = {key: resolve_field(doc, f"items[{i}].{key}", ITEM_KINDS[key],
                                                item_props.get(key, []), source)
                             for key in ("description", "grade", "pricing")}
        identity = tuple(verified_siblings[key].value if verified_siblings[key].status == "extracted" else None
                         for key in ("description", "grade", "pricing"))
        item = {key: (resolve_field(doc, f"items[{i}].quantity", kind, item_props.get(key, []), source,
                                    item_identity=identity) if key == "quantity" else verified_siblings[key])
                for key, kind in ITEM_KINDS.items()}
        item["material"] = _material(doc, f"items[{i}].material", item["description"], source)
        items.append(item)
    normalized = _normalized(fields, items, as_of, reporting_currency, rate_lookup)
    is_synthetic = bool(synthetic) or any("SYNTHETIC" in s.text for s in doc.segments)
    return Extraction(
        document={"document_id": doc.document_id, "filename": doc.filename, "media_type": doc.media_type,
                  "byte_size": doc.byte_size, "trust": doc.trust, "metadata": doc.metadata,
                  "warnings": [w.code for w in doc.warnings]},
        extractor=source, extractor_detail=detail, as_of=as_of.isoformat(), synthetic=is_synthetic,
        fields=fields, items=items, review_flags=_flags(doc, fields, items, normalized["expiry"]),
        normalized=normalized)


def extract_offer(doc: Document, *, mode: str = "auto", as_of: dt.date | None = None, client=None,
                  reporting_currency: str = "USD", rate_lookup=None, synthetic: bool | None = None) -> Extraction:
    """mode: auto (Gemini when available, else rules), gemini (same, Gemini preferred) or rules."""
    if mode not in ("auto", "gemini", "rules"):
        raise ValueError(f"unknown extraction mode {mode}")
    proposals, source, detail = _propose(doc, mode, client)
    return assemble(doc, proposals, source, detail, as_of=as_of or dt.date.today(),
                    reporting_currency=reporting_currency, rate_lookup=rate_lookup, synthetic=synthetic)
