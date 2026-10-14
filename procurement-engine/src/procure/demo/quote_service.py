"""Small, immutable-fixture quote boundary for the hosted demonstration.

Document evidence and offer field validation are delegated to the existing procurement
engine. Mutable state, ownership and idempotency belong to the session host.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from procure.documents import DocumentReadError, read_document
from procure.offers import lifecycle as lc
from procure.offers import normalize as nz
from procure.offers.extraction import extract_offer
from procure.offers.fx_rates import FxRate

from .errors import DemoError


_FIXTURE_DIR = Path(__file__).resolve().parents[3] / "demo_fixtures"
_FIXTURES = (
    {"id": "late-copper", "name": "Atacama copper · lower price, late arrival",
     "filename": "SYN-late-copper.pdf", "description": "Synthetic PDF quote; settlement currency needs reviewer confirmation.",
     "sha256": "1256c543568466fe44d1f3791c2861e65bd21f833883f98c5a85eae2784398df"},
    {"id": "express-copper", "name": "Swift copper · on-time USD",
     "filename": "SYN-express-copper.pdf", "description": "Synthetic PDF quote with a 14-day lead time.",
     "sha256": "eb05f6b26257600f4ca1c23a8ba6d542fd21a97775084291257a45f9baad98ba"},
    {"id": "eu-copper", "name": "Europa copper · on-time EUR",
     "filename": "SYN-eu-copper.csv", "description": "Synthetic CSV quote; dated synthetic EUR/USD conversion.",
     "sha256": "8dc9d68ae21f47b03585325f2d2297a0e2710d3ef3a6bdce212faa5bdaf727a5"},
)
_COMPANY_FIXTURES = tuple(json.loads((_FIXTURE_DIR / 'company-quote-manifest.json').read_text()))
_FIXTURES += _COMPANY_FIXTURES
_BY_ID = {entry["id"]: entry for entry in _FIXTURES}
_BY_FILENAME = {entry['filename']: entry for entry in _COMPANY_FIXTURES}
_ALL_BY_FILENAME = {entry['filename']: entry for entry in _FIXTURES}


def company_for_offer(view: dict) -> str | None:
    document = view.get('document') or {}
    entry = _ALL_BY_FILENAME.get(document.get('filename'))
    if not entry or document.get('document_id') != 'sha256:' + entry['sha256']:
        return None
    return entry.get('company_id', 'solara-cables')


def fixture_review_values(fixture_id: str) -> dict:
    """Reviewer suggestions read from intact fictional source bytes, never a model."""
    entry = _BY_ID.get(fixture_id)
    if entry is None:
        raise DemoError('fixture_not_found', 'Synthetic fixture not found', 404)
    if entry not in _COMPANY_FIXTURES:
        return {'settlement_currency': 'USD'} if fixture_id == 'late-copper' else {}
    _, data = fixture_bytes(fixture_id)  # verifies the immutable document hash first
    try:
        rows = list(csv.reader(io.StringIO(data.decode('utf-8-sig'))))
        if len(rows) != 14 or rows[0][0] != 'note' or rows[1] != ['field', 'value']:
            raise ValueError('unexpected synthetic CSV shape')
        source = {}
        for row in rows[2:]:
            if len(row) != 2 or row[0] in source:
                raise ValueError('ambiguous synthetic CSV row')
            source[row[0]] = row[1]
        if set(source) != {'supplier', 'product', 'origin', 'quantity', 'price', 'incoterm',
                           'minimum order quantity', 'supply capacity', 'lead time',
                           'settlement currency', 'payment terms', 'valid until'}:
            raise ValueError('unexpected synthetic CSV fields')

        def amount_unit(value: str) -> tuple[str, str]:
            match = re.fullmatch(r'([0-9]+(?:\.[0-9]+)?) ([a-z]+)', value)
            if not match:
                raise ValueError('invalid exact-unit amount')
            return nz.fmt(Decimal(match[1])), match[2]

        quantity, unit = amount_unit(source['quantity'])
        minimum, min_unit = amount_unit(source['minimum order quantity'])
        capacity_match = re.fullmatch(r'(.+) per month', source['supply capacity'])
        capacity, capacity_unit = amount_unit(capacity_match[1]) if capacity_match else (None, None)
        price_match = re.fullmatch(r'([A-Z]{3}) ([0-9]+(?:\.[0-9]+)?(?:E\+[0-9]+)?) per ([a-z]+)',
                                   source['price'])
        lead_match = re.fullmatch(r'([0-9]+) days after order confirmation', source['lead time'])
        incoterm_match = re.fullmatch(r'([A-Z]{3}) (.+)', source['incoterm'])
        if (not price_match or not lead_match or not incoterm_match
                or unit != min_unit or unit != capacity_unit or unit != price_match[3]
                or unit != entry['unit'] or price_match[1] != source['settlement currency']
                or price_match[1] != entry['currency'] or incoterm_match[1] not in nz.INCOTERMS_2020):
            raise ValueError('synthetic quote values disagree')
        from .company import get_company
        profile = get_company(entry['company_id'])
        materials = [material for material in profile['materials']
                     if material['name'] == source['product'] and material['quantity_unit'] == unit]
        if len(materials) != 1 or materials[0]['material_id'] != entry['material']:
            raise ValueError('synthetic material does not match catalogue')
        valid_until = datetime.strptime(source['valid until'], '%d %B %Y').date().isoformat()
        return {'supplier_name': source['supplier'], 'origin_country': source['origin'],
                'incoterm': {'code': incoterm_match[1], 'place': incoterm_match[2]},
                'settlement_currency': source['settlement currency'], 'valid_until': valid_until,
                'lead_time_days': int(lead_match[1]), 'payment_terms': source['payment terms'],
                'items[0].material': materials[0]['material_id'],
                'items[0].quantity': {'amount': quantity, 'unit': unit},
                'items[0].pricing': {'type': 'fixed', 'unit_price': {
                    'amount': nz.fmt(Decimal(price_match[2])), 'currency': price_match[1]}, 'per_unit': unit},
                'min_order': {'amount': minimum, 'unit': unit},
                'capacity': {'amount': capacity, 'unit': unit, 'period': 'month'}}
    except (UnicodeError, ValueError, KeyError, IndexError, TypeError):
        raise DemoError('fixture_unavailable', 'Synthetic fixture review values are unavailable', 503) from None


def _scenario_entry(view: dict) -> dict | None:
    return _BY_FILENAME.get((view.get('document') or {}).get('filename'))


def _scenario_normalized(view: dict, today: date) -> dict:
    """Demo-only exact same-unit normalization; no mass or package conversion."""
    out = lc.compute_normalized(view, today, rate_lookup=synthetic_rate)
    entry = _scenario_entry(view)
    if not entry:
        return out
    unit = entry['unit']
    for name in ('min_order', 'capacity'):
        value = lc.usable(view['fields'][name])
        if unit != 'kg' and isinstance(value, dict) and value.get('unit') == unit:
            out[name] = {'original': copy.deepcopy(value), 'quantity': value['amount'],
                         'unit': unit, 'status': 'ok'}
    for index, item in enumerate(view['items']):
        quantity = lc.usable(item['quantity'])
        if unit != 'kg' and isinstance(quantity, dict) and quantity.get('unit') == unit:
            out['items'][index]['quantity'] = {
                'original': copy.deepcopy(quantity), 'quantity': quantity['amount'],
                'unit': unit, 'status': 'ok'}
        price = lc.usable(item['pricing'])
        if isinstance(price, dict) and price.get('type') == 'fixed' and price.get('per_unit') == unit:
            money = price.get('unit_price') or {}
            amount = nz._bounded_decimal(money.get('amount'), allow_negative=False)
            if amount is not None and amount > 0:
                currency = money.get('currency')
                rate = synthetic_rate(currency, view['reporting_currency'], today) if currency != view['reporting_currency'] else None
                converted = amount if rate is None else amount * rate.rate if rate.rate is not None else None
                out['items'][index]['pricing'] = {
                    'original': copy.deepcopy(price), 'status': 'ok' if converted is not None else 'fx_unavailable',
                    'type': 'fixed', 'per_unit': {'amount': nz.fmt(amount), 'currency': currency},
                    'reporting': {'status': rate.status if rate else 'not_needed',
                                  'fx': rate.to_dict() if rate and converted is not None else None,
                                  'per_unit': {'amount': nz.fmt(converted), 'currency': view['reporting_currency']}
                                  if converted is not None else None}}
    return out


def list_fixtures() -> list[dict]:
    return [{key: entry[key] for key in ("id", "name", "filename", "description")}
            | {"download_url": f"/api/demo/fixtures/{entry['id']}",
               "review_values": fixture_review_values(entry['id'])} for entry in _FIXTURES]


def fixture_bytes(fixture_id: str) -> tuple[str, bytes]:
    entry = _BY_ID.get(fixture_id)
    if entry is None:
        raise DemoError("fixture_not_found", "Synthetic fixture not found", 404)
    try:
        data = (_FIXTURE_DIR / entry["filename"]).read_bytes()
    except OSError:
        raise DemoError("fixture_unavailable", "Synthetic fixture is unavailable", 503) from None
    if hashlib.sha256(data).hexdigest() != entry["sha256"]:
        raise DemoError("fixture_unavailable", "Synthetic fixture failed its integrity check", 503)
    return entry["filename"], data


def match_fixture(filename: str, data: bytes) -> str:
    if not isinstance(filename, str) or not isinstance(data, bytes):
        raise DemoError("fixture_not_allowed", "Only the listed synthetic fixtures are accepted")
    digest = hashlib.sha256(data).hexdigest()
    for entry in _FIXTURES:
        if filename == entry["filename"] and digest == entry["sha256"]:
            return entry["id"]
    raise DemoError("fixture_not_allowed", "Only the listed synthetic fixtures are accepted")


def synthetic_rate(base: str, quote: str, on: date) -> FxRate:
    """One explicitly dated scenario rate, never a market-data lookup."""
    rates = {('EUR', 'USD'): '1.25', ('USD', 'INR'): '80', ('USD', 'BDT'): '100',
             ('USD', 'KES'): '120', ('USD', 'CHF'): '0.9', ('USD', 'MXN'): '20',
             ('USD', 'KRW'): '1000', ('USD', 'MYR'): '4', ('USD', 'AED'): '4',
             ('USD', 'ZAR'): '18'}
    rate = Decimal(rates[base, quote]) if on.isoformat() == '2026-10-14' and (base, quote) in rates else None
    return FxRate(base, quote, rate, on.isoformat() if rate else None,
                  "synthetic_scenario" if rate else None,
                  "2026-10-14T00:00:00+00:00" if rate else None,
                  "cache" if rate else "unavailable",
                  "Fixed synthetic demonstration assumption" if rate else "No dated synthetic rate for this pair")


def extract_fixture(fixture_id: str, *, mode: str, as_of: date) -> dict:
    if mode not in {"rules", "gemini"} or not isinstance(as_of, date):
        raise DemoError("invalid_request", "Choose rules or Gemini and a valid date", 400)
    filename, data = fixture_bytes(fixture_id)
    try:
        doc = read_document(data, filename=filename)
        extraction = extract_offer(doc, mode=mode, as_of=as_of, reporting_currency="USD",
                                   rate_lookup=synthetic_rate, synthetic=True)
    except DocumentReadError:
        raise DemoError("fixture_unavailable", "Synthetic fixture cannot be read", 503) from None
    except Exception:
        if mode == "gemini":
            raise DemoError("gemini_unavailable", "Gemini extraction is unavailable", 503) from None
        raise
    if mode == "gemini" and not (extraction.extractor == "gemini"
                                  and extraction.extractor_detail.get("call") == "real"):
        raise DemoError("gemini_unavailable", "Gemini extraction is unavailable; select rules explicitly", 503)
    return extraction.to_dict()


def blocking_reasons(payload: dict, today: date) -> list[dict]:
    """Public demo save gate, including exact-unit normalized values."""
    reasons = lc.blocking_reasons(payload, today)
    settlement = lc.usable(payload["fields"]["settlement_currency"])
    if settlement:
        for index, item in enumerate(payload["items"]):
            price = lc.usable(item["pricing"])
            money = price.get("unit_price") if isinstance(price, dict) and price.get("type") == "fixed" else None
            if isinstance(money, dict) and money.get("currency") != settlement:
                reasons.append({"code": "currency_conflict", "field": f"items[{index}].pricing",
                                "message": "Settlement and fixed price currencies disagree"})
    return reasons


def _view(payload: dict, *, today: date) -> dict:
    reasons = blocking_reasons(payload, today)
    status = payload["status"]
    if status in lc.TRANSITIONS["expire"][0] and any(r["code"] == "expired" for r in reasons):
        status = "expired"
    payload["status"] = status
    payload["as_of"] = today.isoformat()
    payload["normalized"]["as_of"] = today.isoformat()
    payload["normalized"]["expiry"] = lc.nz.expiry_status(lc.usable(payload["fields"]["valid_until"]), today)
    payload["blocking_reasons"] = reasons
    payload["allowed_actions"] = lc.allowed_actions(status, reasons)
    return payload


def new_offer(payload: dict, *, offer_id: str, today: date) -> dict:
    if not isinstance(payload, dict) or not isinstance(offer_id, str) or not offer_id:
        raise DemoError("invalid_request", "A valid extraction and offer ID are required", 400)
    view = copy.deepcopy(payload)
    if company_for_offer(view) is None:
        raise DemoError('fixture_not_allowed', 'Only intact listed synthetic documents are accepted', 422)
    if view.get("extractor_detail", {}).get("requested") == "gemini" and not (
            view.get("extractor") == "gemini" and view["extractor_detail"].get("call") == "real"):
        raise DemoError("gemini_unavailable", "Gemini extraction was not confirmed as a real call", 503)
    view.update({"offer_id": offer_id, "version": 1, "synthetic": True,
                 "reporting_currency": "USD", "status": lc.initial_status(view, today)})
    entry = _scenario_entry(view)
    if entry:
        view['reporting_currency'] = entry['reporting_currency']
        view['normalized'] = _scenario_normalized(view, today)
        view['status'] = lc.initial_status(view, today)
    return _view(view, today=today)


def _offer_error(exc: lc.OfferError) -> DemoError:
    return DemoError(exc.code, exc.message, exc.status)


def correct_offer(view: dict, *, field: str, value, today: date) -> dict:
    updated = copy.deepcopy(view)
    if updated.get("status") not in lc.TRANSITIONS["correct"][0]:
        raise DemoError("invalid_transition", "This offer cannot be corrected", 409)
    try:
        kind = lc.field_kind(field)
        current = lc.get_field(updated, field)
        if field == "capacity" and isinstance(value, dict) and value.get("period") is not None and not isinstance(value["period"], str):
            raise DemoError("invalid_value", "capacity period must be a supported text value")
        entry = _scenario_entry(updated)
        if entry and kind in {'quantity', 'pricing', 'material'}:
            unit = entry['unit']
            if kind == 'material':
                problems = [] if field == 'items[0].material' and value == entry['material'] else ['material does not match this fixture']
            elif kind == 'quantity':
                valid = (isinstance(value, dict) and value.get('unit') == unit
                         and set(value) <= {'amount', 'unit', 'period'}
                         and isinstance(value.get('amount'), str)
                         and nz._bounded_decimal(value['amount'], allow_negative=False) is not None
                         and Decimal(value['amount']) > 0
                         and (field != 'capacity' or value.get('period') == 'month')
                         and (field == 'capacity' or 'period' not in value))
                problems = [] if valid else ['quantity must use the exact supported fixture unit']
            else:
                money = value.get('unit_price') if isinstance(value, dict) else None
                valid = (isinstance(value, dict) and set(value) == {'type', 'unit_price', 'per_unit'}
                         and value['type'] == 'fixed' and value['per_unit'] == unit
                         and isinstance(money, dict) and set(money) == {'amount', 'currency'}
                         and money['currency'] == entry['currency']
                         and isinstance(money['amount'], str)
                         and nz._bounded_decimal(money['amount'], allow_negative=False) is not None
                         and Decimal(money['amount']) > 0)
                problems = [] if valid else ['price must use the exact supported fixture unit and currency']
        else:
            problems = lc.value_problems(kind, value, field)
    except lc.OfferError as exc:
        raise _offer_error(exc) from None
    if problems:
        raise DemoError("invalid_value", f"{field}: " + "; ".join(problems))
    previous = {"status": current["status"], "value": copy.deepcopy(current["value"])}
    if "original" in current:
        original = copy.deepcopy(current["original"])
    elif current["status"] == "corrected":
        # Older corrected offers have no recoverable document-backed snapshot.
        original = {"value": None, "status": "unavailable", "source": "unknown", "evidence": []}
    else:
        original = {key: copy.deepcopy(current.get(key)) for key in ("value", "status", "source", "evidence")}
    candidate_evidence = copy.deepcopy(current.get('original_candidate_evidence') or [
        evidence for candidate in current.get('candidates') or []
        for evidence in candidate.get('evidence') or []])
    current.update({"status": "corrected", "value": value, "source": "user", "evidence": [],
                    "evidence_status": "user_entered_without_document_evidence", "previous": previous,
                    "original": original,
                    "original_candidate_evidence": candidate_evidence,
                    "corrected_by": "session_reviewer", "corrected_at": today.isoformat(), "reasons": []})
    updated["normalized"] = _scenario_normalized(updated, today)
    updated["version"] += 1
    updated["status"] = "corrected"
    return _view(updated, today=today)


def save_offer(view: dict, *, today: date) -> dict:
    updated = copy.deepcopy(view)
    updated = _view(updated, today=today)
    if updated["status"] not in lc.TRANSITIONS["save"][0]:
        raise DemoError("invalid_transition", "This offer cannot be saved", 409)
    if updated["blocking_reasons"]:
        raise DemoError("save_blocked", "Resolve the offer's blocking reasons before saving", 409)
    updated["status"] = "saved"
    updated["version"] += 1
    return _view(updated, today=today)
