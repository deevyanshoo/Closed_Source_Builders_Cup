"""Public company-name validation and isolated, reviewable Search query."""
from __future__ import annotations

import re
import unicodedata
from .research import DESTINATION, SHARED_DATA, MAX_QUERY, _unsafe, _public_https

_FORBIDDEN = re.compile(r'\b(?:ignore|instructions?|prompt|system|assistant|search|browse|research|lookup|private|confidential|internal|inventory|supplier|stock|order|quote|rfq|password|token|api.?key)\b', re.I)


def company_query(name: object) -> tuple[str, dict]:
    if not isinstance(name, str):
        raise ValueError('Enter a public company name.')
    name = ' '.join(unicodedata.normalize('NFC', name).split())
    if not 2 <= len(name) <= 100 or not any(ch.isalpha() for ch in name):
        raise ValueError('Enter a public company name of 2 to 100 characters.')
    if any(unicodedata.category(ch)[0] not in ('L', 'M', 'N', 'Z') and ch not in "&.,'’()/-+" for ch in name):
        raise ValueError('Use only a company name, without instructions or private details.')
    if _FORBIDDEN.search(name) or _unsafe(name, None):
        raise ValueError('Use only a public company name, without private details.')
    query = f'Research public sources for {name}: industry, publicly reported materials, operating regions, and supply-chain risks. Cite dated sources; state when any detail is unverified.'
    if len(query) > MAX_QUERY or _unsafe(query, None):
        raise ValueError('Use a shorter public company name.')
    return name, {'query': query, 'destination': DESTINATION, 'shared_data': SHARED_DATA}


def safe_preview_result(result: object) -> bool:
    if not isinstance(result, dict) or result.get('status') != 'completed':
        return False
    sources = result.get('sources')
    return (isinstance(result.get('answer'), str) and bool(result['answer'].strip()) and
            isinstance(sources, list) and 1 <= len(sources) <= 24 and
            all(isinstance(row, dict) and isinstance(row.get('title'), str) and row['title'] and
                isinstance(row.get('source_id'), str) and row['source_id'] and
                _public_https(row.get('url')) for row in sources))
