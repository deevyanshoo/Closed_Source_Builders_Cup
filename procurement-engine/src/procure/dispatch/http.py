"""Read-only HTTP routes for the dispatch workspace, mounted under /dispatch.

GET only: shipment list, one shipment's timeline, shipments affected by an event, route
evidence for locations, and a before-and-after replay of one bundled synthetic sample event
(``/replay``; ``/replay/samples`` lists them). Nothing is written. ``as_of`` is required and is the information cutoff:
records observed or retrieved after it are not used. ``now`` (for staleness) is optional and
defaults to the server's UTC clock; the response says which was used.

Responses carry the read model's ``status`` (ok, empty, stale, error) and ``reasons``.
HTTP 400 for invalid parameters, 404 for an unknown event or replay sample, 503 when the synthetic sample data is
unavailable, otherwise 200. All data is synthetic and labelled so.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from ..events.ingest import normalize_milestones
from ..route_evidence import evidence_for_locations, normalize_route_evidence
from . import samples
from .read_models import affected_shipments, shipment_timeline
from .replay import replay_event
from .summary import shipment_list

router = APIRouter(prefix="/dispatch", tags=["dispatch"])
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$")
_STATUS_FOR_REASON = {"unknown_event": 404, "unknown_sample": 404, "invalid_now": 400, "invalid_shipment_id": 400,
                      "invalid_location_keys": 400, "as_of_after_now": 400}


def _error(status_code: int, reason: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"status": "error", "reasons": [reason], "synthetic": True})


def _context(as_of: str | None, now: str | None):
    """(samples, normalized milestones, now, now_source) or an error response."""
    if not isinstance(as_of, str) or not _TIMESTAMP.fullmatch(as_of):
        return _error(400, "invalid_as_of")
    if now is not None and not _TIMESTAMP.fullmatch(now):
        return _error(400, "invalid_now")
    try:
        data = samples.load_samples()
    except samples.SampleUnavailable:
        return _error(503, "sample_data_unavailable")
    now_source = "request" if now is not None else "server_clock"
    now = now or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return data, normalize_milestones(data["milestones"]["records"], as_of=as_of), now, now_source


def _respond(result: dict, now: str, now_source: str) -> JSONResponse:
    body = {**result, "now": now, "now_source": now_source, "synthetic": True, "source": samples.source_info()}
    code = 200
    if result.get("status") == "error":
        code = next((_STATUS_FOR_REASON[r] for r in result.get("reasons", []) if r in _STATUS_FOR_REASON), 400)
    return JSONResponse(status_code=code, content=body)


@router.get("/shipments")
def list_shipments(as_of: str | None = Query(None), now: str | None = Query(None)):
    ctx = _context(as_of, now)
    if isinstance(ctx, JSONResponse):
        return ctx
    data, normalized, now, now_source = ctx
    evidence = normalize_route_evidence(data["route_evidence"]["records"], as_of=as_of)
    result = shipment_list(normalized, now=now, snapshot=data["snapshot"], route_evidence=evidence)
    return _respond(result, now, now_source)


@router.get("/shipments/{shipment_id}")
def get_shipment(shipment_id: str, as_of: str | None = Query(None), now: str | None = Query(None)):
    ctx = _context(as_of, now)
    if isinstance(ctx, JSONResponse):
        return ctx
    data, normalized, now, now_source = ctx
    result = shipment_timeline(normalized, shipment_id, now=now, records=data["milestones"]["records"])
    return _respond(result, now, now_source)


@router.get("/events/{event_id}/affected")
def get_affected(event_id: str, as_of: str | None = Query(None), now: str | None = Query(None)):
    ctx = _context(as_of, now)
    if isinstance(ctx, JSONResponse):
        return ctx
    _data, normalized, now, now_source = ctx
    return _respond(affected_shipments(normalized, event_id, now=now), now, now_source)


@router.get("/route-evidence")
def get_route_evidence(as_of: str | None = Query(None), location: list[str] = Query(default_factory=list),
                       now: str | None = Query(None)):
    ctx = _context(as_of, now)
    if isinstance(ctx, JSONResponse):
        return ctx
    data, _normalized, now, now_source = ctx
    if not location:
        return _error(400, "invalid_location_keys")
    evidence = normalize_route_evidence(data["route_evidence"]["records"], as_of=as_of)
    return _respond(evidence_for_locations(evidence, location), now, now_source)


@router.get("/replay/samples")
def list_replay_samples():
    try:
        content = samples.load_replay_samples()
    except samples.SampleUnavailable:
        return _error(503, "sample_data_unavailable")
    return {"status": "ok", "reasons": [], "synthetic": True, "notice": content.get("notice"),
            "samples": [samples.sample_view(s) for s in content["samples"]]}


@router.get("/replay")
def replay_sample(sample: str | None = Query(None), as_of: str | None = Query(None), now: str | None = Query(None)):
    ctx = _context(as_of, now)
    if isinstance(ctx, JSONResponse):
        return ctx
    data, _normalized, now, now_source = ctx
    if not sample:
        return _error(400, "missing_sample")
    try:
        content = samples.load_replay_samples()
    except samples.SampleUnavailable:
        return _error(503, "sample_data_unavailable")
    chosen = next((s for s in content["samples"] if s.get("sample_id") == sample), None)
    if chosen is None:
        return _error(404, "unknown_sample")
    result = replay_event(data["milestones"]["records"], chosen["record"], as_of=as_of, now=now,
                          snapshot=data["snapshot"], route_evidence_records=data["route_evidence"]["records"])
    return _respond({**result, "sample": samples.sample_view(chosen)}, now, now_source)
