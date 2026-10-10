"""Sourcing candidates: accepted, unexpired offers as per-item inputs for sourcing (issue #3).

A read model only. It never changes an offer, never calls a provider and is not wired into decision.py; the shape
is proposed in CONTRACT_PROPOSAL.md (section "Sourcing candidates").

- **Accepted** means status `saved` or `used_in_plan`. Everything else is excluded with a reason.
- **Expired** offers, and offers whose validity is not stated or unresolved, are excluded.
- **One candidate per item** with a verified material, a normalised quantity and a normalised price; other items
  are excluded with what is missing.
- **Landed price** is the quoted price on its Incoterm, per kg, in the quote currency and, when a dated FX rate
  exists, the reporting currency. It lists which costs the Incoterm includes and which it does not (Incoterms 2020
  allocation; the contract can differ). Duty, port and inland costs are never estimated here.
"""
from __future__ import annotations

import datetime as dt

from .scenario import validity_at
from .terms import USABLE, parse_payment_terms

CONTRACT = "offers-sourcing-candidates/proposed-0.1"
ACCEPTED = ("saved", "used_in_plan")
_DESTINATION = ["import_duty_and_taxes", "destination_port_charges", "inland_freight_to_plant"]
_SPLITS = {
    "EXW": ([], ["export_clearance", "main_freight", "insurance", *_DESTINATION]),
    "FCA": (["export_clearance"], ["main_freight", "insurance", *_DESTINATION]),
    "FAS": (["export_clearance"], ["main_freight", "insurance", *_DESTINATION]),
    "FOB": (["export_clearance"], ["main_freight", "insurance", *_DESTINATION]),
    "CFR": (["export_clearance", "main_freight"], ["insurance", *_DESTINATION]),
    "CPT": (["export_clearance", "main_freight"], ["insurance", *_DESTINATION]),
    "CIF": (["export_clearance", "main_freight", "insurance"], _DESTINATION),
    "CIP": (["export_clearance", "main_freight", "insurance"], _DESTINATION),
    "DAP": (["export_clearance", "main_freight", "delivery_to_named_place"],
            ["import_duty_and_taxes", "unloading", "inland_freight_beyond_named_place"]),
    "DPU": (["export_clearance", "main_freight", "delivery_to_named_place", "unloading"],
            ["import_duty_and_taxes", "inland_freight_beyond_named_place"]),
    "DDP": (["export_clearance", "main_freight", "delivery_to_named_place", "import_duty_and_taxes"],
            ["unloading", "inland_freight_beyond_named_place"]),
}
NOTES = (
    "Read-only. Building candidates does not change any offer's status or version.",
    "landed_price is the quoted price on its Incoterm. Costs listed under not_included are not estimated here.",
    "Incoterm cost splits follow Incoterms 2020; the contract can allocate costs differently.",
    "Amounts are exact decimal strings with their currency; quantities keep their unit and kilograms.",
)


def incoterm_cost_split(code: str | None) -> tuple[list[str], list[str]]:
    """Costs the seller's price includes, and those it does not, for an Incoterms 2020 code."""
    if code not in _SPLITS:
        return [], ["unknown_incoterm"]
    includes, excludes = _SPLITS[code]
    return list(includes), list(excludes)


def _usable(field: dict | None):
    return field.get("value") if field and field.get("status") in USABLE else None


def _evidence(view: dict, item_index: int) -> list[dict]:
    named = [(name, f) for name, f in view["fields"].items()]
    named += [(f"items[{item_index}].{k}", f) for k, f in view["items"][item_index].items()]
    return [{"field": name, "locator": e["locator"], "text_sha256": e["text_sha256"],
             "document_id": view["document"]["document_id"]}
            for name, f in named if f.get("status") in USABLE for e in f.get("evidence") or []]


def _sources(view: dict, item_index: int) -> dict:
    named = [(name, f) for name, f in view["fields"].items()]
    named += [(f"items[{item_index}].{k}", f) for k, f in view["items"][item_index].items()]
    return {name: {"status": f.get("status"), "source": f.get("source")} for name, f in named}


def _fx(fx: dict | None) -> dict | None:
    return {k: fx.get(k) for k in ("base", "quote", "rate", "rate_date", "source")} if fx else None


def _price(pricing: dict, incoterm: dict | None) -> tuple[dict, dict]:
    code = (incoterm or {}).get("code")
    includes, excludes = incoterm_cost_split(code)
    reporting = pricing.get("reporting") or {}
    landed = {"basis": "quoted_price_on_incoterm", "incoterm": code, "place": (incoterm or {}).get("place"),
              "includes": includes, "not_included": excludes}
    if pricing["type"] == "fixed":
        original = pricing["original"]
        price = {"type": "fixed", "unit_price": original["unit_price"], "per_unit": original["per_unit"],
                 "per_kg": pricing["per_kg"]}
        landed.update(status="incoterm_price_only" if code in _SPLITS else "incoterm_unknown",
                      per_kg=pricing["per_kg"],
                      reporting={"currency": reporting.get("currency"), "status": reporting.get("status"),
                                 "per_kg": reporting.get("per_kg"), "fx": _fx(reporting.get("fx"))})
        return price, landed
    price = {"type": "index_linked", "index": pricing.get("index"), "averaging_period": pricing.get("averaging_period"),
             "premium_per_kg": pricing.get("premium_per_kg")}
    landed.update(status="index_linked_final_price_unknown", per_kg=None,
                  reporting={"currency": reporting.get("currency"), "status": reporting.get("status"), "per_kg": None,
                             "premium_per_kg": reporting.get("premium_per_kg"), "fx": _fx(reporting.get("fx"))})
    return price, landed


def _excluded(view: dict, reason: str, detail: str | None, item_index: int | None = None) -> dict:
    return {"offer_id": view["offer_id"], "offer_version": view["version"], "item_index": item_index,
            "reason": reason, "detail": detail}


def _offer_exclusion(view: dict, validity: dict) -> dict | None:
    if view["status"] == "expired" or validity["state"] == "expired":
        return _excluded(view, "expired", f"valid until {validity['valid_until'] or 'unknown'}")
    if view["status"] not in ACCEPTED:
        return _excluded(view, "not_accepted", f"status {view['status']}")
    if validity["state"] == "not_stated":
        return _excluded(view, "validity_not_stated", "valid_until is missing")
    if validity["state"] == "unavailable":
        return _excluded(view, "validity_unavailable", "valid_until is unresolved")
    return None


def sourcing_candidates(views: list[dict], *, as_of: dt.date) -> dict:
    """Offer views (as store.get_offer returns them) to sourcing candidates and exclusions with reasons."""
    candidates, excluded = [], []
    for view in views:
        fields = view["fields"]
        validity = validity_at(fields["valid_until"], as_of)
        if (reason := _offer_exclusion(view, validity)) is not None:
            excluded.append(reason)
            continue
        norm = view.get("normalized") or {}
        terms = norm.get("payment_terms") or parse_payment_terms(fields["payment_terms"])
        capacity = norm.get("capacity")
        for i, item in enumerate(view["items"]):
            norm_item = (norm.get("items") or [])[i] if i < len(norm.get("items") or []) else {}
            quantity, pricing = norm_item.get("quantity") or {}, norm_item.get("pricing") or {}
            missing = [text for ok, text in ((_usable(item.get("material")), "material not verified"),
                                             (quantity.get("status") == "ok", "quantity not normalised"),
                                             (pricing.get("status") == "ok", "price not normalised")) if not ok]
            if missing:
                excluded.append(_excluded(view, "item_incomplete", "; ".join(missing), i))
                continue
            price, landed = _price(pricing, _usable(fields.get("incoterm")))
            candidates.append({
                "candidate_id": f"{view['offer_id']}@v{view['version']}#item{i}",
                "offer_id": view["offer_id"], "offer_version": view["version"], "offer_status": view["status"],
                "item_index": i, "synthetic": bool(view.get("synthetic")),
                "document_id": view["document"]["document_id"],
                "supplier": {"name": _usable(fields.get("supplier_name")),
                             "origin_country": _usable(fields.get("origin_country")),
                             "origin_country_code": norm.get("origin_country_code")},
                "material": _usable(item["material"]), "description": _usable(item.get("description")),
                "grade": _usable(item.get("grade")),
                "quantity": {**quantity["original"], "kg": quantity["kg"]},
                "min_order_kg": (norm.get("min_order") or {}).get("kg"),
                "capacity": ({"kg": capacity["kg"], "period": capacity["original"].get("period")}
                             if capacity and capacity.get("status") == "ok" else None),
                "lead_time_days": _usable(fields.get("lead_time_days")),
                "incoterm": _usable(fields.get("incoterm")),
                "settlement_currency": _usable(fields.get("settlement_currency")),
                "price": price, "landed_price": landed, "validity": validity, "payment_terms": terms,
                "evidence": _evidence(view, i), "field_sources": _sources(view, i),
            })
    candidates.sort(key=lambda c: (c["material"], c["offer_id"], c["item_index"]))
    return {"contract": CONTRACT, "as_of": as_of.isoformat(), "synthetic": any(v.get("synthetic") for v in views),
            "candidates": candidates, "excluded": excluded, "notes": list(NOTES)}
