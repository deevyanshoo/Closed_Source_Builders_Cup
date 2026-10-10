"""Persistent offers in the Python service's DuckDB database, with an audit trail.

Every change records who, when, the old and new value and the status move. Mutating calls
need an idempotency key: repeating a request returns the stored response and has no second
effect, and reusing a key for a different request is refused. Each change also checks the
caller's `expected_version`, so a stale screen cannot overwrite newer work.

Reads, checks and writes for one change happen under the database lock, so concurrent
requests in this process are applied one at a time. This is the local store for the Python
service; the shared PostgreSQL store belongs to the platform (see CONTRACT_PROPOSAL.md).
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import math
from contextlib import contextmanager

from .. import db
from ..documents import Document, DocumentReadError, Segment, read_document
from . import lifecycle as lc
from . import normalize as nz
from .evidence import check_candidate
from .extraction import extract_offer
from .lifecycle import OfferError
from .context import OfferContext, require_context

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS offer_documents (
        document_id TEXT, filename TEXT, media_type TEXT, byte_size BIGINT, segments_json TEXT,
        warnings_json TEXT, metadata_json TEXT, created_at TEXT, workspace_id TEXT)""",
    """CREATE TABLE IF NOT EXISTS offer_extractions (
        id TEXT, document_id TEXT, extractor TEXT, payload_json TEXT, created_at TEXT, workspace_id TEXT)""",
    """CREATE TABLE IF NOT EXISTS offers (
        id TEXT, extraction_id TEXT, document_id TEXT, status TEXT, version INTEGER, synthetic BOOLEAN,
        payload_json TEXT, created_at TEXT, updated_at TEXT, workspace_id TEXT)""",
    """CREATE TABLE IF NOT EXISTS offer_events (
        id TEXT, offer_id TEXT, seq INTEGER, occurred_at TEXT, actor TEXT, action TEXT, field TEXT, old_json TEXT,
        new_json TEXT, from_status TEXT, to_status TEXT, idempotency_key TEXT, workspace_id TEXT)""",
    """CREATE TABLE IF NOT EXISTS offer_requests (
        idempotency_key TEXT, action TEXT, request_hash TEXT, response_json TEXT, created_at TEXT, workspace_id TEXT)""",
]
TABLES = ('offer_documents', 'offer_extractions', 'offers', 'offer_events', 'offer_requests')
LEGACY_BUYER_INPUTS = ("need_by_month", "current_stock_kg", "monthly_consumption_kg")


def _today() -> dt.date:
    """Server clock for mutations; store callers may inject a date in local tests."""
    return dt.date.today()


def ensure_schema() -> None:
    # One process-wide migration critical section, including PRAGMA and ALTER.
    with db._LOCK:
        for stmt in SCHEMA:
            db.execute(stmt)
        # Historical rows remain NULL and inaccessible. Never infer a tenant.
        for table in TABLES:
            columns = set(db.query_df(f"PRAGMA table_info('{table}')")['name'])
            if 'workspace_id' not in columns:
                db.execute(f'ALTER TABLE {table} ADD COLUMN workspace_id TEXT')


def _rows(sql: str, params=None) -> list[dict]:
    df = db.query_df(sql, params or [])
    return df.astype(object).where(df.notna(), None).to_dict(orient="records")


@contextmanager
def _transaction():
    """Use the locked DuckDB connection for all writes in one local operation."""
    if db.ENGINE != "duckdb":
        raise OfferError("invalid_request", "atomic offer mutations require DuckDB")
    con = db._connect()
    con.execute("BEGIN TRANSACTION")
    try:
        yield
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise


# ---------- idempotency ----------

def _idempotent(context: OfferContext, action: str, scope: dict, key: str, fn, *, expire_ids=(), today: dt.date) -> dict:
    require_context(context, editor=True)
    if not isinstance(key, str) or not 8 <= len(key) <= 128:
        raise OfferError("invalid_request", "idempotency_key must be 8 to 128 characters")
    request_hash = hashlib.sha256(json.dumps({"workspace_id": context.workspace_id,
                                             "principal_id": context.principal_id, "action": action, **scope}, sort_keys=True, default=str)
                                  .encode()).hexdigest()
    with db._LOCK:  # one change at a time: check, apply and record the key together
        ensure_schema()
        # Ownership must precede replay, expiry and version checks.
        for offer_id in dict.fromkeys(expire_ids):
            _load(context, offer_id)
        seen = _rows("SELECT * FROM offer_requests WHERE workspace_id = ? AND idempotency_key = ?",
                     [context.workspace_id, key])
        if seen:
            if seen[0]["request_hash"] != request_hash or seen[0]["action"] != action:
                raise OfferError("idempotency_conflict", "this idempotency_key was already used for a different request")
            return {**json.loads(seen[0]["response_json"]), "replayed": True}
        # Expiry is a separate authoritative system effect, never part of a caller's failed mutation.
        for offer_id in dict.fromkeys(expire_ids):
            _expire_due_locked(context, offer_id, today)
        with _transaction():
            result = fn()
            db.insert_row("offer_requests", {"workspace_id": context.workspace_id,
                                             "idempotency_key": key, "action": action, "request_hash": request_hash,
                                             "response_json": json.dumps(result, default=str, allow_nan=False),
                                             "created_at": db.now_iso()})
        return {**result, "replayed": False}


# ---------- load and write ----------

def _load(context: OfferContext, offer_id: str) -> tuple[dict, dict]:
    require_context(context, allow_system=True)
    ensure_schema()
    rows = _rows("SELECT * FROM offers WHERE workspace_id = ? AND id = ?", [context.workspace_id, offer_id])
    if not rows:
        raise OfferError("not_found", "offer not found")
    return rows[0], json.loads(rows[0]["payload_json"])


def _event(context: OfferContext, offer_id: str, *, action: str, from_status, to_status, field=None, old=None, new=None,
           key=None) -> None:
    require_context(context, allow_system=True)
    _load(context, offer_id)
    seq = int(db.query_df("SELECT COUNT(*) AS n FROM offer_events WHERE workspace_id = ? AND offer_id = ?",
                          [context.workspace_id, offer_id])["n"].iloc[0]) + 1
    db.insert_row("offer_events", {"workspace_id": context.workspace_id, "id": db.new_id(), "offer_id": offer_id,
                                   "seq": seq, "occurred_at": db.now_iso(),
                                   "actor": 'system' if context.role == 'system' else context.principal_id,
                                   "action": action, "field": field,
                                   "old_json": json.dumps(old, default=str), "new_json": json.dumps(new, default=str),
                                   "from_status": from_status, "to_status": to_status, "idempotency_key": key})


def _write(context: OfferContext, row: dict, payload: dict, status: str) -> int:
    require_context(context, allow_system=True)
    if row.get('workspace_id') != context.workspace_id:
        raise OfferError('not_found', 'offer not found')
    version = int(row["version"]) + 1
    db.execute("UPDATE offers SET status = ?, version = ?, payload_json = ?, updated_at = ? WHERE workspace_id = ? AND id = ? AND version = ?",
               [status, version, json.dumps(payload, default=str), db.now_iso(), context.workspace_id, row["id"], row["version"]])
    return version


def _expire_due_locked(context: OfferContext, offer_id: str, today: dt.date) -> None:
    """Persist one due expiry under the lock, independently of any requested mutation."""
    row, payload = _load(context, offer_id)
    sources, target = lc.TRANSITIONS["expire"]
    if row["status"] not in sources or not any(r["code"] == "expired" for r in lc.blocking_reasons(payload, today)):
        return
    with _transaction():
        _write(context, row, payload, target)
        system_context = OfferContext('system', context.workspace_id, 'system')
        _event(system_context, row["id"], action="expire", from_status=row["status"], to_status=target,
               new={"valid_until": lc.usable(payload["fields"]["valid_until"]), "checked_on": today.isoformat()})


def expire_due(offer_id: str, *, context: OfferContext, today: dt.date | None = None) -> dict:
    """Internal system transition; GET and list paths only preview expiry."""
    today = today or _today()
    require_context(context, system=True)
    with db._LOCK:
        _expire_due_locked(context, offer_id, today)
        row, payload = _load(context, offer_id)
        return _view(row, payload, today)


def _view(row: dict, payload: dict, today: dt.date, events: list | None = None) -> dict:
    reasons = lc.blocking_reasons(payload, today)
    status = ("expired" if row["status"] in lc.TRANSITIONS["expire"][0]
              and any(r["code"] == "expired" for r in reasons) else row["status"])
    # Date previews must describe the same as-of date as their status. Keep the
    # saved pricing/FX normalization frozen; reading an offer must not refresh it.
    normalized = {**payload["normalized"], "as_of": today.isoformat(),
                  "expiry": nz.expiry_status(lc.usable(payload["fields"]["valid_until"]), today)}
    out = {"offer_id": row["id"], "status": status, "version": int(row["version"]),
           "synthetic": bool(row["synthetic"]), "extraction_id": row["extraction_id"],
           "document": payload["document"], "extractor": payload["extractor"],
           "extractor_detail": payload.get("extractor_detail"), "as_of": today.isoformat(),
           "fields": payload["fields"], "items": payload["items"], "review_flags": payload["review_flags"],
           "normalized": normalized, "blocking_reasons": reasons,
           "allowed_actions": lc.allowed_actions(status, reasons),
           "created_at": row["created_at"], "updated_at": row["updated_at"]}
    if events is not None:
        out["events"] = events
    return out


def _events(context: OfferContext, offer_id: str) -> list[dict]:
    return [{"seq": int(e["seq"]), "at": e["occurred_at"], "actor": e["actor"], "action": e["action"], "field": e["field"],
             "old": json.loads(e["old_json"]), "new": json.loads(e["new_json"]), "from_status": e["from_status"],
             "to_status": e["to_status"]}
            for e in _rows("SELECT * FROM offer_events WHERE workspace_id = ? AND offer_id = ? ORDER BY seq",
                           [context.workspace_id, offer_id])]


def _document(context: OfferContext, document_id: str) -> Document:
    require_context(context, allow_system=True)
    rows = _rows("SELECT * FROM offer_documents WHERE workspace_id = ? AND document_id = ?",
                 [context.workspace_id, document_id])
    if not rows:
        raise OfferError("not_found", "document not found")
    r = rows[0]
    segments = tuple(Segment(**{**s, "flags": tuple(s.get("flags") or ())}) for s in json.loads(r["segments_json"]))
    return Document(document_id=r["document_id"], filename=r["filename"], media_type=r["media_type"],
                    byte_size=int(r["byte_size"]), segments=segments, metadata=json.loads(r["metadata_json"]))


def _check_transition(row: dict, payload: dict, action: str, today: dt.date) -> str:
    sources, target = lc.TRANSITIONS[action]
    if row["status"] not in sources:
        raise OfferError("invalid_transition", f"cannot {action} an offer that is {row['status']}",
                         status=row["status"], blocking_reasons=lc.blocking_reasons(payload, today))
    return target


def _check_version(row: dict, expected_version: int) -> None:
    if expected_version != int(row["version"]):
        raise OfferError("stale_version", f"offer is at version {row['version']}, request was for {expected_version}",
                         current_version=int(row["version"]))


# ---------- public operations ----------

def create_from_upload(data: bytes, filename: str, *, mode: str = "auto", as_of: dt.date | None = None,
                       context: OfferContext | None = None, reporting_currency: str = "USD", synthetic: bool | None = None,
                       allow_duplicate: bool = False, client=None) -> dict:
    """Read, extract and store a quote. The same document uploaded again returns its existing offer."""
    require_context(context, editor=True)
    today = as_of or dt.date.today()
    try:
        doc = read_document(data, filename=filename)
    except DocumentReadError as exc:
        raise OfferError("document_unreadable", exc.message, reason=exc.code) from exc
    ensure_schema()
    if not allow_duplicate:
        existing = _rows("SELECT id FROM offers WHERE workspace_id = ? AND document_id = ? ORDER BY created_at LIMIT 1",
                         [context.workspace_id, doc.document_id])
        if existing:
            offer = get_offer(existing[0]["id"], context=context, today=today)
            return {"offer": offer, "extraction_id": offer["extraction_id"], "deduplicated": True}
    extraction = extract_offer(doc, mode=mode, as_of=today, client=client, reporting_currency=reporting_currency,
                               synthetic=synthetic)
    ex = extraction.to_dict()
    payload = {key: ex[key] for key in ("document", "extractor", "extractor_detail", "as_of", "synthetic", "fields",
                                        "items", "review_flags", "normalized")}
    payload["reporting_currency"] = reporting_currency
    status = lc.initial_status(payload, today)
    now = db.now_iso()
    existing_id = None
    with db._LOCK:
        with _transaction():
            if not allow_duplicate:
                existing = _rows("SELECT id FROM offers WHERE workspace_id = ? AND document_id = ? ORDER BY created_at LIMIT 1",
                                 [context.workspace_id, doc.document_id])
                existing_id = existing[0]["id"] if existing else None
            if existing_id is None:
                if not _rows("SELECT document_id FROM offer_documents WHERE workspace_id = ? AND document_id = ?",
                             [context.workspace_id, doc.document_id]):
                    db.insert_row("offer_documents", {
                        "workspace_id": context.workspace_id,
                        "document_id": doc.document_id, "filename": doc.filename, "media_type": doc.media_type,
                        "byte_size": doc.byte_size, "segments_json": json.dumps([dataclasses.asdict(s) for s in doc.segments]),
                        "warnings_json": json.dumps([dataclasses.asdict(w) for w in doc.warnings]),
                        "metadata_json": json.dumps(doc.metadata, default=str), "created_at": now})
                extraction_id, offer_id = db.new_id(), db.new_id()
                db.insert_row("offer_extractions", {"workspace_id": context.workspace_id,
                                                    "id": extraction_id, "document_id": doc.document_id,
                                                    "extractor": extraction.extractor, "payload_json": json.dumps(ex),
                                                    "created_at": now})
                db.insert_row("offers", {"workspace_id": context.workspace_id,
                                         "id": offer_id, "extraction_id": extraction_id, "document_id": doc.document_id,
                                         "status": status, "version": 1, "synthetic": extraction.synthetic,
                                         "payload_json": json.dumps(payload), "created_at": now, "updated_at": now})
                _event(context, offer_id, action="created", from_status=None, to_status=status,
                       new={"extraction_id": extraction_id, "extractor": extraction.extractor,
                            "call": extraction.extractor_detail.get("call")})
    if existing_id is not None:
        offer = get_offer(existing_id, context=context, today=today)
        return {"offer": offer, "extraction_id": offer["extraction_id"], "deduplicated": True}
    return {"offer": get_offer(offer_id, context=context, today=today), "extraction_id": extraction_id, "deduplicated": False}


def get_offer(offer_id: str, *, context: OfferContext | None = None, include_events: bool = False,
              today: dt.date | None = None) -> dict:
    require_context(context)
    today = today or _today()
    with db._LOCK:
        row, payload = _load(context, offer_id)
        return _view(row, payload, today, _events(context, offer_id) if include_events else None)


def list_offers(*, context: OfferContext | None = None, status: str | None = None,
                today: dt.date | None = None) -> list[dict]:
    require_context(context)
    today = today or _today()
    ensure_schema()
    out = []
    for r in _rows("SELECT id FROM offers WHERE workspace_id = ? ORDER BY created_at DESC", [context.workspace_id]):
        view = get_offer(r["id"], context=context, today=today)
        if status is None or view["status"] == status:
            items = view["items"]
            out.append({"offer_id": view["offer_id"], "status": view["status"], "version": view["version"],
                        "synthetic": view["synthetic"], "filename": view["document"]["filename"],
                        "supplier_name": lc.usable(view["fields"]["supplier_name"]),
                        "materials": [lc.usable(i["material"]) for i in items],
                        "blocking_reasons": len(view["blocking_reasons"]), "updated_at": view["updated_at"]})
    return out


def get_extraction(extraction_id: str, *, context: OfferContext | None = None) -> dict:
    require_context(context)
    ensure_schema()
    rows = _rows("SELECT * FROM offer_extractions WHERE workspace_id = ? AND id = ?",
                 [context.workspace_id, extraction_id])
    if not rows:
        raise OfferError("not_found", "extraction not found")
    linked = _rows("SELECT id FROM offers WHERE workspace_id = ? AND extraction_id = ? AND document_id = ?",
                   [context.workspace_id, extraction_id, rows[0]['document_id']])
    if not linked:
        raise OfferError('not_found', 'extraction not found')
    doc = _document(context, rows[0]["document_id"])
    return {"extraction_id": extraction_id, "extraction": json.loads(rows[0]["payload_json"]),
            "segments": [{"locator": s.locator, "text": s.text, "text_sha256": s.text_sha256, "flags": list(s.flags),
                          "page": s.page, "line": s.line, "sheet": s.sheet, "cell": s.cell, "row": s.row,
                          "column": s.column} for s in doc.segments]}


def correct_field(offer_id: str, *, context: OfferContext | None = None, field: str, value, evidence: list | None,
                  expected_version: int, idempotency_key: str, today: dt.date | None = None,
                  reason: str | None = None) -> dict:
    require_context(context, editor=True)
    today = today or _today()
    scope = {"offer_id": offer_id, "field": field, "value": value, "evidence": evidence,
             "expected_version": expected_version, "reason": reason}

    def apply():
        row, payload = _load(context, offer_id)
        kind = lc.field_kind(field)
        current = lc.get_field(payload, field)
        problems = lc.value_problems(kind, value, field)
        if problems:
            raise OfferError("invalid_value", f"{field}: " + "; ".join(problems), field=field, problems=problems)
        target = _check_transition(row, payload, "correct", today)
        _check_version(row, expected_version)
        refs, evidence_status = [], "user_entered_without_document_evidence"
        if evidence:
            candidate = check_candidate(_document(context, row["document_id"]), kind, {"value": value, "evidence": evidence})
            if not candidate.verified:
                raise OfferError("evidence_not_verified", f"{field}: the cited text does not show this value",
                                 field=field, problems=candidate.problems)
            refs = [dataclasses.asdict(e) for e in candidate.evidence]
            evidence_status = "verified_against_document"
        old = {"status": current["status"], "value": current["value"]}
        current.update({"status": "corrected", "value": value, "source": "user", "evidence": refs,
                        "evidence_status": evidence_status, "corrected_by": context.principal_id,
                        "corrected_at": db.now_iso(),
                        "previous": old, "reasons": [reason] if reason else []})
        payload["normalized"] = lc.compute_normalized(payload, today)
        new = {"status": "corrected", "value": value, "evidence_status": evidence_status}
        row["version"] = _write(context, row, payload, target)
        _event(context, offer_id, action="correct", field=field, old=old, new=new, from_status=row["status"],
               to_status=target, key=idempotency_key)
        row["status"] = target
        return _view({**row, "updated_at": db.now_iso()}, payload, today)

    return _idempotent(context, "correct", scope, idempotency_key, apply, expire_ids=[offer_id], today=today)


def save_offer(offer_id: str, *, context: OfferContext | None = None, expected_version: int, idempotency_key: str,
               today: dt.date | None = None) -> dict:
    require_context(context, editor=True)
    today = today or _today()

    def apply():
        row, payload = _load(context, offer_id)
        target = _check_transition(row, payload, "save", today)
        _check_version(row, expected_version)
        reasons = lc.blocking_reasons(payload, today)
        if reasons:
            raise OfferError("save_blocked", "the offer cannot be saved until these are resolved",
                             blocking_reasons=reasons)
        row["version"] = _write(context, row, payload, target)
        _event(context, offer_id, action="save", from_status=row["status"], to_status=target, key=idempotency_key)
        row["status"] = target
        return _view({**row, "updated_at": db.now_iso()}, payload, today)

    return _idempotent(context, "save", {"offer_id": offer_id, "expected_version": expected_version},
                       idempotency_key, apply, expire_ids=[offer_id], today=today)


def reject_offer(offer_id: str, *, context: OfferContext | None = None, expected_version: int, reason: str, idempotency_key: str,
                 today: dt.date | None = None) -> dict:
    require_context(context, editor=True)
    today = today or _today()
    if not isinstance(reason, str) or not reason.strip():
        raise OfferError("invalid_request", "a rejection reason is required")

    def apply():
        row, payload = _load(context, offer_id)
        target = _check_transition(row, payload, "reject", today)
        _check_version(row, expected_version)
        row["version"] = _write(context, row, payload, target)
        _event(context, offer_id, action="reject", new={"reason": reason}, from_status=row["status"],
               to_status=target, key=idempotency_key)
        row["status"] = target
        return _view({**row, "updated_at": db.now_iso()}, payload, today)

    scope = {"offer_id": offer_id, "expected_version": expected_version, "reason": reason}
    return _idempotent(context, "reject", scope, idempotency_key, apply, expire_ids=[offer_id], today=today)


# ---------- planning input and RFQ draft ----------

def _plan_entry(row: dict, payload: dict, today: dt.date) -> dict:
    fields = payload["fields"]
    norm = lc.compute_normalized(payload, today)
    items = []
    for i, item in enumerate(payload["items"]):
        q, p = norm["items"][i]["quantity"], norm["items"][i]["pricing"]
        price = None
        if p and p.get("type") == "fixed" and p.get("status") == "ok":
            price = {"type": "fixed", "per_kg": p["per_kg"], "reporting": p["reporting"]}
        elif p and p.get("type") == "index_linked" and p.get("status") == "ok":
            price = {"type": "index_linked", "index": p["index"], "averaging_period": p["averaging_period"],
                     "premium_per_kg": p["premium_per_kg"], "reporting": p["reporting"]}
            price.update({key: p["original"][key] for key in ("fx_basis", "fx_terms", "fx_conversion", "quotational_period")
                          if key in p["original"]})
        items.append({"index": i, "material": lc.usable(item["material"]),
                      "description": lc.usable(item["description"]), "grade": lc.usable(item["grade"]),
                      "quantity_kg": q["kg"] if q else None, "price": price})
    evidence = [{"field": name, "locator": e["locator"], "text_sha256": e["text_sha256"]}
                for name, f in lc.all_fields(payload) if f["status"] in lc.USABLE for e in f.get("evidence") or []]
    provenance = {}
    for name, f in lc.all_fields(payload):
        refs = [{"locator": e["locator"], "text_sha256": e["text_sha256"]} for e in f.get("evidence") or []]
        provenance[name] = {"status": f["status"], "source": f.get("source"),
                            "evidence_status": f.get("evidence_status") or (
                                "verified_against_document" if f["status"] == "extracted" and refs else "unverified"),
                            "corrected_by": f.get("corrected_by"), "evidence_refs": refs}
    capacity = norm["capacity"]
    return {"offer_id": row["id"], "version": int(row["version"]), "synthetic": bool(row["synthetic"]),
            "document_id": payload["document"]["document_id"],
            "supplier_name": lc.usable(fields["supplier_name"]), "origin_country": lc.usable(fields["origin_country"]),
            "origin_country_code": norm["origin_country_code"], "incoterm": lc.usable(fields["incoterm"]),
            "settlement_currency": lc.usable(fields["settlement_currency"]),
            "payment_terms": lc.usable(fields["payment_terms"]), "valid_until": lc.usable(fields["valid_until"]),
            "lead_time_days": lc.usable(fields["lead_time_days"]),
            "min_order_kg": norm["min_order"]["kg"] if norm["min_order"] else None,
            "capacity": ({"kg": capacity["kg"], "period": (lc.usable(fields["capacity"]) or {}).get("period")}
                         if capacity else None),
            "shipments": lc.usable(fields["shipments"]), "items": items, "evidence_refs": evidence,
            "field_provenance": provenance}


def _legacy_float(value, field: str, *, positive: bool) -> float:
    number = nz._bounded_decimal(value, allow_negative=False)
    if number is None or (positive and number <= 0):
        raise OfferError("invalid_value", f"{field} must be a finite {'positive' if positive else 'nonnegative'} decimal",
                         field=field)
    converted = float(number)
    if not math.isfinite(converted) or (positive and converted <= 0):
        raise OfferError("invalid_value", f"{field} cannot be represented by the legacy planner", field=field)
    return converted


def _validate_buyer(buyer: dict) -> None:
    if not isinstance(buyer, dict):
        raise OfferError("invalid_request", "buyer must be an object")
    try:
        json.dumps(buyer, allow_nan=False)
    except (TypeError, ValueError):
        raise OfferError("invalid_value", "buyer must contain finite JSON values") from None
    for field, positive in (("current_stock_kg", False), ("monthly_consumption_kg", True)):
        if buyer.get(field) not in (None, ""):
            _legacy_float(buyer[field], field, positive=positive)
    need = buyer.get("need_by_month")
    if need not in (None, "") and (isinstance(need, bool) or not str(need).isdigit() or not 1 <= int(need) <= 6):
        raise OfferError("invalid_value", "need_by_month must be 1 to 6", field="need_by_month")
    risk = buyer.get("risk_appetite")
    if risk not in (None, "", "low", "medium", "high"):
        raise OfferError("invalid_value", "risk_appetite must be low, medium or high", field="risk_appetite")


def _legacy_request(entry: dict, item: dict, buyer: dict) -> dict:
    """A request for the existing monthly /recommend engine, or the reason it cannot be built."""
    reasons = [f"buyer input missing: {k}" for k in LEGACY_BUYER_INPUTS if buyer.get(k) in (None, "")]
    lead = entry["lead_time_days"]
    if item["material"] is None:
        reasons.append("material not verified")
    if item["quantity_kg"] is None:
        reasons.append("quantity not verified")
    if lead is None:
        reasons.append("lead_time_days not stated")
    months = math.ceil(lead / 30) if lead is not None else None
    if months is not None and months > 3:
        reasons.append(f"lead time of {lead} days exceeds the legacy engine's 3-month limit")
    need = buyer.get("need_by_month")
    if need not in (None, "") and (not str(need).isdigit() or not 1 <= int(need) <= 6):
        reasons.append("need_by_month must be 1 to 6")
    base = {"offer_id": entry["offer_id"], "item_index": item["index"]}
    if reasons:
        return {**base, "status": "unavailable", "reason": "; ".join(reasons), "request": None, "assumptions": []}
    request = {"material": item["material"],
               "quantity_kg": _legacy_float(item["quantity_kg"], "quantity_kg", positive=True),
               "need_by_month": int(need),
               "current_stock_kg": _legacy_float(buyer["current_stock_kg"], "current_stock_kg", positive=False),
               "monthly_consumption_kg": _legacy_float(buyer["monthly_consumption_kg"],
                                                       "monthly_consumption_kg", positive=True),
               "supplier_lead_time_months": months, "risk_appetite": buyer.get("risk_appetite") or "medium"}
    assumptions = [f"lead_time_days={lead} rounded up to whole months ({months}) for the legacy monthly engine",
                   "the legacy engine works in float kilograms and INR market price paths; the offer price is shown "
                   "beside its result but is not an input to it"]
    if not buyer.get("risk_appetite"):
        assumptions.append("risk_appetite not given; legacy default medium used")
    return {**base, "status": "ready", "reason": None, "request": request, "assumptions": assumptions}


def planning_input(offer_ids: list[str], *, context: OfferContext | None = None, buyer: dict, idempotency_key: str,
                   today: dt.date | None = None) -> dict:
    """Saved offers as a planning input (decimal strings, units, evidence) and mark them used_in_plan."""
    require_context(context, editor=True)
    today = today or _today()
    if not isinstance(offer_ids, list) or not offer_ids or any(not isinstance(i, str) or not i for i in offer_ids):
        raise OfferError("invalid_request", "offer_ids must be a non-empty list")
    if len(set(offer_ids)) != len(offer_ids):
        raise OfferError("invalid_request", "offer_ids must not repeat an offer")
    _validate_buyer(buyer)

    def apply():
        loaded = []
        for offer_id in offer_ids:
            row, payload = _load(context, offer_id)
            _check_transition(row, payload, "use_in_plan", today)
            loaded.append((row, payload))
        entries, legacy = [], []
        for row, payload in loaded:
            entry = _plan_entry(row, payload, today)
            entries.append(entry)
            legacy.extend(_legacy_request(entry, item, buyer or {}) for item in entry["items"])
            if row["status"] != "used_in_plan":
                version = _write(context, row, payload, "used_in_plan")
                _event(context, row["id"], action="use_in_plan", from_status=row["status"],
                       to_status="used_in_plan", new={"version": version}, key=idempotency_key)
        return {"contract": "offers-planning-input/proposed-0.1", "as_of": today.isoformat(),
                "synthetic": any(e["synthetic"] for e in entries), "buyer": buyer or {}, "offers": entries,
                "legacy_recommend_requests": legacy,
                "limitations": ["Offer prices, MOQ, capacity and payment terms are not inputs to the legacy "
                                "/recommend engine; a multi-supplier allocation needs the planning service's "
                                "PlanningRequest (see CONTRACT_PROPOSAL.md)."]}

    scope = {"offer_ids": offer_ids, "buyer": buyer}
    return _idempotent(context, "planning_input", scope, idempotency_key, apply, expire_ids=offer_ids, today=today)


def sourcing_candidates(*, context: OfferContext | None = None, today: dt.date | None = None) -> dict:
    """Read-only: every stored offer viewed on `today`, as sourcing candidates and exclusions (see sourcing.py)."""
    require_context(context)
    from .sourcing import sourcing_candidates as build

    today = today or _today()
    ensure_schema()
    with db._LOCK:
        views = [_view(*_load(context, r["id"]), today) for r in _rows(
            "SELECT id FROM offers WHERE workspace_id = ? ORDER BY created_at", [context.workspace_id])]
    return build(views, as_of=today)


def draft_rfq(offer_id: str, *, context: OfferContext | None = None, buyer: dict, idempotency_key: str, item_index: int = 0,
              today: dt.date | None = None) -> dict:
    """Global recommendations/actions are not tenant scoped; refuse before any lookup/import/effect."""
    require_context(context, editor=True)
    raise OfferError('service_route_unavailable', 'RFQ draft is unavailable for scoped offers')
