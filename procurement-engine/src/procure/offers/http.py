"""Offer HTTP routes. Direct legacy API mounts fail without a verified ASGI hop context."""
from __future__ import annotations

import base64
import binascii
import datetime as dt
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from . import store
from .context import OfferContext, require_context
from .lifecycle import OfferError

class SignedOfferRoute(APIRoute):
    """Refuse direct legacy mounts before FastAPI reads/parses a request body."""
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def guarded(request: Request):
            if type(request.scope.get('offer_context')) is not OfferContext:
                return JSONResponse(status_code=403, content={'detail': {'code': 'quote_context_required'}})
            return await handler(request)

        return guarded


router = APIRouter(prefix='/offers', tags=['offers'], route_class=SignedOfferRoute)


class StrictBody(BaseModel):
    model_config = ConfigDict(extra='forbid')


class EvidenceIn(StrictBody):
    locator: str
    quote: str | None = None
    text_sha256: str | None = None


class UploadIn(StrictBody):
    filename: str
    content_base64: str
    mode: Literal['rules']
    synthetic: Literal[True]
    reporting_currency: str = Field('USD', pattern=r'^[A-Z]{3}$')


class Mutation(StrictBody):
    expected_version: int


class CorrectionIn(Mutation):
    field: str
    value: Any
    evidence: list[EvidenceIn] | None = None
    reason: str | None = None


def _context(request: Request, *, editor: bool = False) -> OfferContext:
    context = request.scope.get('offer_context')
    if type(context) is not OfferContext:
        raise HTTPException(status_code=403, detail={'code': 'quote_context_required'})
    try:
        return require_context(context, editor=editor)
    except OfferError:
        raise HTTPException(status_code=403, detail={'code': 'forbidden'}) from None


def _error(exc: OfferError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={'error': exc.to_dict()})


def _call(fn, *args, status_code: int = 200, **kwargs):
    try:
        result = fn(*args, **kwargs)
    except OfferError as exc:
        return _error(exc)
    return JSONResponse(status_code=status_code, content=result)


@router.post('/extractions', status_code=201)
def upload_and_extract(request: Request, body: UploadIn):
    context = _context(request, editor=True)
    try:
        data = base64.b64decode(body.content_base64, validate=True)
    except (binascii.Error, ValueError):
        return _error(OfferError('invalid_request', 'content_base64 is not valid base64'))
    if len(data) > 8 << 20:
        return _error(OfferError('invalid_request', 'document is too large'))
    return _call(store.create_from_upload, data, body.filename, context=context, mode='rules',
                 reporting_currency=body.reporting_currency, synthetic=True, status_code=201)


@router.get('/extractions/{extraction_id}')
def get_extraction(request: Request, extraction_id: str):
    return _call(store.get_extraction, extraction_id, context=_context(request))


@router.get('')
def list_offers(request: Request, status: str | None = None, as_of: dt.date | None = Query(None)):
    try:
        return {'offers': store.list_offers(context=_context(request), status=status, today=as_of)}
    except OfferError as exc:
        return _error(exc)


@router.get('/sourcing-candidates')
def sourcing_candidates(request: Request, as_of: dt.date | None = Query(None)):
    return _call(store.sourcing_candidates, context=_context(request), today=as_of)


@router.get('/{offer_id}')
def get_offer(request: Request, offer_id: str, include_events: bool = False,
              as_of: dt.date | None = Query(None)):
    return _call(store.get_offer, offer_id, context=_context(request),
                 include_events=include_events, today=as_of)


@router.post('/{offer_id}/corrections')
def correct(request: Request, offer_id: str, body: CorrectionIn, idempotency_key: str | None = Header(None)):
    context = _context(request, editor=True)
    evidence = [e.model_dump(exclude_none=True) for e in body.evidence] if body.evidence else None
    return _call(store.correct_field, offer_id, context=context, field=body.field, value=body.value,
                 evidence=evidence, expected_version=body.expected_version, reason=body.reason,
                 idempotency_key=idempotency_key)


@router.post('/{offer_id}/save')
def save(request: Request, offer_id: str, body: Mutation, idempotency_key: str | None = Header(None)):
    return _call(store.save_offer, offer_id, context=_context(request, editor=True),
                 expected_version=body.expected_version, idempotency_key=idempotency_key)


@router.post('/planning-input')
def planning_input(request: Request):
    _context(request, editor=True)
    raise HTTPException(status_code=403, detail={'code': 'service_route_unavailable'})


@router.post('/{offer_id}/reject')
def reject(request: Request, offer_id: str):
    _context(request, editor=True)
    raise HTTPException(status_code=403, detail={'code': 'service_route_unavailable'})


@router.post('/{offer_id}/rfq-draft')
def rfq_draft(request: Request, offer_id: str):
    _context(request, editor=True)
    raise HTTPException(status_code=403, detail={'code': 'service_route_unavailable'})
