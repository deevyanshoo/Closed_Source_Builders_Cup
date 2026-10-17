"""Validate recorded real Gemini receipts for truthful display counts."""
from __future__ import annotations

from datetime import datetime
import ipaddress
import re
from urllib.parse import urlsplit


_FLAGS = {'regional_import_exposure', 'public_policy_change', 'review_source'}


def _safe_text(value, limit):
    if not isinstance(value, str):
        return None
    value = re.sub(r'[\x00-\x1f\x7f]', ' ', value).strip()
    return value[:limit] or None


def _public_url(value):
    if not isinstance(value, str) or len(value) > 1200 or any(ord(ch) < 33 for ch in value):
        return None
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        if (parsed.scheme not in {'http', 'https'} or not host or parsed.username or parsed.password
                or parsed.port not in (None, 80, 443) or '\\' in value or host.endswith('.')
                or host.lower() == 'localhost' or host.lower().endswith(('.localhost', '.local', '.internal'))):
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            if len(host.split('.')) < 2 or not all(re.fullmatch(r'[A-Za-z0-9-]+', part) for part in host.split('.')):
                return None
        return value
    except ValueError:
        return None


def _valid(record):
    if not isinstance(record, dict) or record.get('status') != 'success':
        return False
    if record.get('provider') not in {'vertex_ai', 'gemini_vertex_ai'}:
        return False
    if not isinstance(record.get('model_id'), str) or not record['model_id'].startswith('gemini-'):
        return False
    for key in ('input_source_id', 'model_id', 'model_version', 'output'):
        if not _safe_text(record.get(key), 2000):
            return False
    if not isinstance(record.get('evidence_refs'), list) or not record['evidence_refs']:
        return False
    if record['input_source_id'] not in record['evidence_refs']:
        return False
    if (not _public_url(record.get('source_url')) or
        not _safe_text(record.get('source_title'), 220)):
        return False
    try:
        stamp = datetime.fromisoformat(record['called_at'].replace('Z', '+00:00'))
        source_day = datetime.fromisoformat(record['source_as_of']).date()
        return stamp.tzinfo is not None and source_day <= stamp.date()
    except (KeyError, TypeError, ValueError):
        return False


def summarize_gemini_receipts(records: list[dict]) -> dict:
    """Deduplicate real input IDs; never treat deterministic processing as model work."""
    seen = set()
    receipts = []
    flagged = set()
    for record in records if isinstance(records, list) else []:
        if not _valid(record):
            continue
        source_id = record['input_source_id']
        if source_id in seen:
            continue
        seen.add(source_id)
        flags = sorted(set(record.get('model_reviewed_flags') or []) & _FLAGS)
        if flags:
            flagged.add(source_id)
        receipts.append({'input_source_id': source_id, 'model_id': record['model_id'],
                         'model_version': record['model_version'],
                         'provider_call_id': _safe_text(record.get('provider_call_id'), 120),
                         'called_at': record['called_at'], 'status': 'success',
                         'output': _safe_text(record['output'], 1500),
                         'source_title': _safe_text(record['source_title'], 220),
                         'source_url': record['source_url'],
                         'source_as_of': record['source_as_of'],
                         'evidence_refs': [ref for ref in record['evidence_refs'][:8]
                                           if isinstance(ref, str) and len(ref) <= 160],
                         'reviewed_flags': flags})
    return {'schema': 1, 'interpreted_articles': len(seen),
            'flagged_for_review': len(flagged), 'receipts': receipts}
