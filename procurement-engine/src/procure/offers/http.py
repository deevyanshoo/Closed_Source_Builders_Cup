"""HTTP routes for quote intelligence, mounted on the existing FastAPI app under /offers.

New routes only; existing routes are unchanged. Uploads are JSON with base64 content so no
multipart dependency is needed. Mutations need an idempotency key (body field or the
Idempotency-Key header) and the caller's expected_version. Errors are
{"error": {"code", "message", ...}} with a stable code.
"""
from __future__ import annotations

import base64
import binascii
import datetime as dt
from typing import Any, Literal

from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import store
from .lifecycle import OfferError

router = APIRouter(prefix="/offers", tags=["offers"])


class EvidenceIn(BaseModel):
    locator: str
    quote: str | None = None
    text_sha256: str | None = None


class UploadIn(BaseModel):
    filename: str
    content_base64: str
    mode: Literal["auto", "gemini", "rules"] = "auto"
    as_of: dt.date | None = None
    reporting_currency: str = Field("USD", pattern=r"^[A-Z]{3}$")
    synthetic: bool | None = None
    actor: str = "unknown"
    allow_duplicate: bool = False


class Mutation(BaseModel):
    expected_version: int
    actor: str
    idempotency_key: str | None = None
    as_of: dt.date | None = None


class CorrectionIn(Mutation):
    field: str
    value: Any
    evidence: list[EvidenceIn] | None = None
    reason: str | None = None


class RejectIn(Mutation):
    reason: str


class PlanningIn(BaseModel):
    offer_ids: list[str]
    buyer: dict = Field(default_factory=dict)
    actor: str
    idempotency_key: str | None = None
    as_of: dt.date | None = None


class RfqIn(BaseModel):
    buyer: dict = Field(default_factory=dict)
    actor: str
    item_index: int = 0
    idempotency_key: str | None = None
    as_of: dt.date | None = None


def _error(exc: OfferError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"error": exc.to_dict()})


def _call(fn, *args, status_code: int = 200, **kwargs):
    try:
        result = fn(*args, **kwargs)
    except OfferError as exc:
        return _error(exc)
    return JSONResponse(status_code=status_code, content=result)


def _key(body_key: str | None, header_key: str | None) -> str | None:
    return body_key or header_key


@router.post("/extractions", status_code=201)
def upload_and_extract(body: UploadIn):
    try:
        data = base64.b64decode(body.content_base64, validate=True)
    except (binascii.Error, ValueError):
        return _error(OfferError("invalid_request", "content_base64 is not valid base64"))
    return _call(store.create_from_upload, data, body.filename, mode=body.mode, as_of=body.as_of, actor=body.actor,
                 reporting_currency=body.reporting_currency, synthetic=body.synthetic,
                 allow_duplicate=body.allow_duplicate, status_code=201)


@router.get("/extractions/{extraction_id}")
def get_extraction(extraction_id: str):
    return _call(store.get_extraction, extraction_id)


@router.get("")
def list_offers(status: str | None = None, as_of: dt.date | None = Query(None)):
    try:
        return {"offers": store.list_offers(status=status, today=as_of)}
    except OfferError as exc:
        return _error(exc)


@router.post("/planning-input")
def planning_input(body: PlanningIn, idempotency_key: str | None = Header(None)):
    return _call(store.planning_input, body.offer_ids, buyer=body.buyer, actor=body.actor,
                 idempotency_key=_key(body.idempotency_key, idempotency_key))


@router.get("/{offer_id}")
def get_offer(offer_id: str, include_events: bool = False, as_of: dt.date | None = Query(None)):
    return _call(store.get_offer, offer_id, include_events=include_events, today=as_of)


@router.post("/{offer_id}/corrections")
def correct(offer_id: str, body: CorrectionIn, idempotency_key: str | None = Header(None)):
    evidence = [e.model_dump(exclude_none=True) for e in body.evidence] if body.evidence else None
    return _call(store.correct_field, offer_id, field=body.field, value=body.value, evidence=evidence,
                 expected_version=body.expected_version, actor=body.actor, reason=body.reason,
                 idempotency_key=_key(body.idempotency_key, idempotency_key))


@router.post("/{offer_id}/save")
def save(offer_id: str, body: Mutation, idempotency_key: str | None = Header(None)):
    return _call(store.save_offer, offer_id, expected_version=body.expected_version, actor=body.actor,
                 idempotency_key=_key(body.idempotency_key, idempotency_key))


@router.post("/{offer_id}/reject")
def reject(offer_id: str, body: RejectIn, idempotency_key: str | None = Header(None)):
    return _call(store.reject_offer, offer_id, expected_version=body.expected_version, actor=body.actor,
                 reason=body.reason, idempotency_key=_key(body.idempotency_key, idempotency_key))


@router.post("/{offer_id}/rfq-draft")
def rfq_draft(offer_id: str, body: RfqIn, idempotency_key: str | None = Header(None)):
    return _call(store.draft_rfq, offer_id, buyer=body.buyer, actor=body.actor, item_index=body.item_index,
                 idempotency_key=_key(body.idempotency_key, idempotency_key))
