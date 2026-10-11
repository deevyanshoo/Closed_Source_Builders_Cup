"""Pure, bounded demo evidence view for caller-owned monthly projections.

This module does not authenticate the caller, read an artifact, fit a model, or
publish a forecast. A trusted caller must derive both bindings and recheck the
current binding immediately before publishing the returned view.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re


_HORIZONS = (1, 3, 6)
_EXPERIMENTAL = ('ridge_alpha_10', 'chronos_bolt_tiny',
                 'chronos2_independent', 'chronos2_family_joint')
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_MONTH = re.compile(r'[0-9]{4}-(0[1-9]|1[0-2])\Z')
_DECIMAL = re.compile(r'[0-9]+(\.[0-9]+)?\Z')
_ROW_KEYS = frozenset(('series_id', 'horizon', 'lead_months', 'observation_end',
                       'issue_month', 'target_month', 'native_price', 'unit',
                       'model', 'status', 'provenance', 'error_references'))
_PROVENANCE_KEYS = frozenset(('source_id', 'source_sha256', 'retrieved_at_utc',
                              'observed_through', 'unit', 'family'))
_CATALOG_KEYS = frozenset(('unit', 'family', 'source_id', 'source_sha256',
                           'retrieved_at_utc', 'error_references'))
_ERROR_KEYS = frozenset(('series_id', 'model', 'horizon', 'unit', 'ledger_sha256',
                         'count', 'p50', 'p80', 'p95'))
_PROJECTION_KEYS = frozenset(('schema', 'generated_at_utc', 'as_of_date_utc',
                              'as_of_month_utc', 'observation_end', 'issue_month',
                              'freshness_state', 'selected_model', 'selected',
                              'candidate_status', 'limits'))


class DemoEvidenceUnavailable(ValueError):
    """Fixed refusal that does not disclose payload, identity, or price details."""


def _refuse() -> None:
    raise DemoEvidenceUnavailable('DEMO_EVIDENCE_UNAVAILABLE')


def _bounded_text(value, limit=128) -> str:
    if type(value) is not str or not value or len(value) > limit:
        _refuse()
    try:
        encoded = value.encode('utf-8')
    except UnicodeError:
        _refuse()
    if len(encoded) > limit:
        _refuse()
    return value


def _plain_keys(value, expected) -> None:
    if type(value) is not dict or len(value) != len(expected):
        _refuse()
    if any(type(key) is not str for key in value):
        _refuse()
    if set(value) != expected:
        _refuse()


def _sha(value) -> str:
    if type(value) is not str or len(value) != 64 or _SHA.fullmatch(value) is None:
        _refuse()
    return value


def _month(value) -> int:
    if type(value) is not str or len(value) != 7 or _MONTH.fullmatch(value) is None:
        _refuse()
    year = int(value[:4])
    if year < 1:
        _refuse()
    return year * 12 + int(value[5:]) - 1


def _month_text(index: int) -> str:
    return f'{index // 12:04d}-{index % 12 + 1:02d}'


def _utc(value) -> datetime:
    if type(value) is not str or len(value) > 40:
        _refuse()
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (ValueError, UnicodeError):
        _refuse()
    if parsed.tzinfo is None or parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        _refuse()
    return parsed


def _decimal(value, *, positive: bool) -> str:
    if type(value) is not str or len(value) > 64 or _DECIMAL.fullmatch(value) is None:
        _refuse()
    try:
        number = Decimal(value)
    except InvalidOperation:
        _refuse()
    if not number.is_finite() or (number <= 0 if positive else number < 0):
        _refuse()
    return value


@dataclass(frozen=True, slots=True)
class ForecastDemoBinding:
    principal_id: str
    workspace_id: str
    context_id: str
    revision: int
    context_digest_sha256: str
    payload_digest_sha256: str

    def __post_init__(self) -> None:
        for value in (self.principal_id, self.workspace_id, self.context_id):
            _bounded_text(value)
        if type(self.revision) is not int or not 1 <= self.revision <= 2147483647:
            _refuse()
        _sha(self.context_digest_sha256)
        _sha(self.payload_digest_sha256)


def require_current_binding(bound: ForecastDemoBinding,
                            current: ForecastDemoBinding) -> None:
    """Caller invokes this again immediately before publication or display."""
    if type(bound) is not ForecastDemoBinding or type(current) is not ForecastDemoBinding or bound != current:
        _refuse()


def _validate_catalog(catalog, generated: datetime) -> None:
    if type(catalog) is not dict or not 1 <= len(catalog) <= 32:
        _refuse()
    for sid, meta in catalog.items():
        _bounded_text(sid)
        _plain_keys(meta, _CATALOG_KEYS)
        for key in ('unit', 'family', 'source_id'):
            _bounded_text(meta[key])
        _sha(meta['source_sha256'])
        if _utc(meta['retrieved_at_utc']) > generated:
            _refuse()
        refs = meta['error_references']
        if type(refs) is not list or len(refs) > 16:
            _refuse()
        for ref in refs:
            _bounded_text(ref)


def _validate_row(row, catalog: dict, anchor: str, issue: str, model: str) -> tuple[str, int]:
    _plain_keys(row, _ROW_KEYS)
    sid = _bounded_text(row['series_id'])
    if sid not in catalog or _bounded_text(row['model']) != model:
        _refuse()
    h = row['horizon']
    if type(h) is not int or h not in _HORIZONS or type(row['lead_months']) is not int or row['lead_months'] != h + 1:
        _refuse()
    if tuple(_bounded_text(row[key]) for key in (
            'observation_end', 'issue_month', 'target_month')) != (
            anchor, issue, _month_text(_month(anchor) + h + 1)):
        _refuse()
    expected_status = 'SELECTED_BASELINE' if model == 'persistence' else 'EXPERIMENTAL_NOT_PROMOTED'
    if _bounded_text(row['status']) != expected_status:
        _refuse()
    _decimal(row['native_price'], positive=True)
    meta = catalog[sid]
    refs = row['error_references']
    if type(refs) is not list or len(refs) > 16:
        _refuse()
    for ref in refs:
        _bounded_text(ref)
    if _bounded_text(row['unit']) != meta['unit'] or refs != meta['error_references']:
        _refuse()
    provenance = row['provenance']
    _plain_keys(provenance, _PROVENANCE_KEYS)
    for key in ('source_id', 'retrieved_at_utc', 'observed_through', 'unit', 'family'):
        _bounded_text(provenance[key])
    _sha(provenance['source_sha256'])
    if provenance != {'source_id': meta['source_id'], 'source_sha256': meta['source_sha256'],
                      'retrieved_at_utc': meta['retrieved_at_utc'],
                      'observed_through': anchor, 'unit': meta['unit'],
                      'family': meta['family']}:
        _refuse()
    return sid, h


def _validate_payload(projection, candidates, catalog, historical_errors, now: datetime) -> None:
    _plain_keys(projection, _PROJECTION_KEYS)
    if _bounded_text(projection['schema']) != 'monthly-projection.v1':
        _refuse()
    generated = _utc(projection['generated_at_utc'])
    if (generated > now or
            _bounded_text(projection['as_of_date_utc']) != generated.date().isoformat() or
            _bounded_text(projection['as_of_month_utc']) != generated.strftime('%Y-%m')):
        _refuse()
    anchor = projection['observation_end']
    issue = projection['issue_month']
    if (_month(anchor) >= _month(generated.strftime('%Y-%m')) or
            _month(anchor) >= _month(now.strftime('%Y-%m')) or
            _month(issue) != _month(anchor) + 1):
        _refuse()
    expected_freshness = ('ISSUE_MONTH_EQUALS_AS_OF_UNVERIFIED'
                          if issue == generated.strftime('%Y-%m') else 'HISTORICAL_NOT_CURRENT')
    if _bounded_text(projection['freshness_state']) != expected_freshness or (
            _bounded_text(projection['selected_model']),
            _bounded_text(projection['candidate_status'])) != (
                'persistence', 'EXPERIMENTAL_NOT_PROMOTED'):
        _refuse()
    _bounded_text(projection['limits'], 512)
    _validate_catalog(catalog, generated)
    selected = projection['selected']
    if type(selected) is not list or len(selected) > 96 or len(selected) != 3 * len(catalog):
        _refuse()
    selected_keys = set()
    for row in selected:
        key = _validate_row(row, catalog, anchor, issue, 'persistence')
        if key in selected_keys:
            _refuse()
        selected_keys.add(key)
    if selected_keys != {(sid, h) for sid in catalog for h in _HORIZONS}:
        _refuse()
    if type(candidates) is not list or len(candidates) > 288:
        _refuse()
    candidate_keys = set()
    for row in candidates:
        _plain_keys(row, _ROW_KEYS)
        if _bounded_text(row['model']) not in _EXPERIMENTAL:
            _refuse()
        sid, h = _validate_row(row, catalog, anchor, issue, row['model'])
        key = (sid, row['model'], h)
        if key in candidate_keys:
            _refuse()
        candidate_keys.add(key)
    if type(historical_errors) is not list or len(historical_errors) > 384:
        _refuse()
    seen_errors = set()
    ledger_digest = None
    for error in historical_errors:
        _plain_keys(error, _ERROR_KEYS)
        sid, model, h = error['series_id'], error['model'], error['horizon']
        if (type(sid) is not str or len(sid) > 128 or sid not in catalog or
                type(model) is not str or model not in ('persistence', *_EXPERIMENTAL) or
                type(h) is not int or h not in _HORIZONS):
            _refuse()
        key = (sid, model, h)
        if key in seen_errors:
            _refuse()
        if model != 'persistence' and (sid, model, h) not in candidate_keys:
            _refuse()
        if model == 'persistence' and (sid, h) not in selected_keys:
            _refuse()
        if (_bounded_text(error['unit']) != catalog[sid]['unit'] or
                type(error['count']) is not int or not 1 <= error['count'] <= 96):
            _refuse()
        current_ledger = _sha(error['ledger_sha256'])
        if 'sha256:' + current_ledger not in catalog[sid]['error_references']:
            _refuse()
        if ledger_digest is None:
            ledger_digest = current_ledger
        elif current_ledger != ledger_digest:
            _refuse()
        values = [_decimal(error[k], positive=False) for k in ('p50', 'p80', 'p95')]
        if not Decimal(values[0]) <= Decimal(values[1]) <= Decimal(values[2]):
            _refuse()
        seen_errors.add(key)


def payload_digest(projection, candidates, catalog, historical_errors, *, now: datetime) -> str:
    """SHA-256 of canonical UTF-8 JSON after bounded schema validation.

    Canonical form uses sort_keys=True, separators=(',', ':'), ensure_ascii=False,
    and allow_nan=False. It binds every selected/candidate point, catalog entry,
    and historical reference. The caller must derive the expected digest from a
    trusted snapshot rather than from untrusted request content.
    """
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None or now.utcoffset().total_seconds() != 0:
        _refuse()
    _validate_payload(projection, candidates, catalog, historical_errors, now)
    try:
        raw = json.dumps({'projection': projection, 'candidates': candidates,
                          'catalog': catalog, 'historical_errors': historical_errors},
                         sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                         allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError):
        _refuse()
    if len(raw) > 256 * 1024:
        _refuse()
    return hashlib.sha256(raw).hexdigest()


def build_demo_view(*, bound: ForecastDemoBinding, current: ForecastDemoBinding,
                    projection, candidates, catalog, historical_errors,
                    series_id: str, horizon: int, now: datetime) -> dict:
    """Produce one read-only point view; current binding must be rechecked later."""
    require_current_binding(bound, current)  # Must precede even type inspection of payload.
    _bounded_text(series_id)
    if type(horizon) is not int or horizon not in _HORIZONS:
        _refuse()
    digest = payload_digest(projection, candidates, catalog, historical_errors, now=now)
    if digest != bound.payload_digest_sha256:
        _refuse()
    if series_id not in catalog:
        _refuse()
    baseline = next(row for row in projection['selected']
                    if row['series_id'] == series_id and row['horizon'] == horizon)
    matching = [row for row in candidates if row['series_id'] == series_id and row['horizon'] == horizon]
    errors = {(row['series_id'], row['model'], row['horizon']): row for row in historical_errors}
    def point(row):
        error = errors.get((series_id, row['model'], horizon))
        descriptive = ({'status': 'UNAVAILABLE'} if error is None else
                       {'status': 'DESCRIPTIVE_EXPOSED_HISTORY_ONLY',
                        'ledger_sha256': error['ledger_sha256'], 'count': error['count'],
                        'unit': error['unit'],
                        'native_abs_error': {k: error[k] for k in ('p50', 'p80', 'p95')}})
        return {'model': row['model'], 'status': row['status'],
                'native_price': row['native_price'], 'unit': row['unit'],
                'descriptive_error': descriptive}
    issue = projection['issue_month']
    freshness = ('ISSUE_MONTH_EQUALS_AS_OF_UNVERIFIED'
                 if issue == now.strftime('%Y-%m') else 'HISTORICAL_NOT_CURRENT')
    meta = catalog[series_id]
    return {'schema': 'monthly-forecast-demo-view.v1',
            'context': {'principal_id': bound.principal_id, 'workspace_id': bound.workspace_id,
                        'context_id': bound.context_id, 'revision': bound.revision,
                        'context_digest_sha256': bound.context_digest_sha256},
            'payload_digest_sha256': digest,
            'calendar': {'generated_at_utc': projection['generated_at_utc'],
                         'as_of_date_utc': projection['as_of_date_utc'],
                         'observation_end': projection['observation_end'],
                         'issue_month': issue,
                         'target_month': baseline['target_month'],
                         'horizon': horizon, 'lead_months': horizon + 1,
                         'freshness': freshness},
            'series_id': series_id,
            'source': {'unit': meta['unit'], 'family': meta['family'],
                       'source_id': meta['source_id'],
                       'source_sha256': meta['source_sha256'],
                       'retrieved_at_utc': meta['retrieved_at_utc'],
                       'publication_at_utc': None,
                       'vintage_basis': 'CALLER_SUPPLIED_CACHED_RETRIEVAL',
                       'latest_vintage_verified': False,
                       'point_in_time_verified': False,
                       'live_market_quote': False},
            'selected': point(baseline),
            'experimental': [point(row) for row in matching],
            'limits': ['Caller-bound evidence view only; no authentication or current market-price claim.',
                       'Experimental candidates are not selected or promoted.',
                       'Historical absolute errors are descriptive, not calibrated confidence or intervals.',
                       'No purchasing action, recommendation, savings or cross-unit conversion.']}
