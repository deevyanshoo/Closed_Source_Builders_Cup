"""Offline contracts for the read-only hosted news audit script."""
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys


SCRIPT = Path(__file__).with_name('ten_company_news_audit.py')
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location('ten_company_news_audit', SCRIPT)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
NOW = datetime(2026, 10, 15, 15, 17, tzinfo=timezone.utc)


def profile(index):
    return {'company_id': f'company-{index}', 'materials': [{'material_id': 'copper'}],
            'routes': [{'route_id': f'route-{index}'}], 'currencies': ['USD']}


def view(index):
    return {'company_id': f'company-{index}', 'context_version': index + 2,
            'checked_at': '2026-10-15T15:05:00Z', 'cache_state': 'fresh',
            'collection': {'mode': 'scheduled', 'status': 'complete', 'published': True,
                           'slot': '2026-10-15T15:00:00Z', 'completed_at': '2026-10-15T15:04:00Z',
                           'attempted_at': '2026-10-15T15:00:00Z',
                           'news_success_at': '2026-10-15T15:04:00Z',
                           'fx_success_at': '2026-10-15T15:04:00Z'},
            'sources': [{'source_id': 'eia', 'state': 'fresh',
                         'last_checked_at': '2026-10-15T15:04:00Z'}],
            'items': [], 'fx': [],
            'global_watch': {'attempted_at': '2026-10-15T15:00:00Z',
                             'success_at': '2026-10-15T15:04:00Z', 'status': 'fresh',
                             'sources': [{'source_id': 'global_port', 'state': 'fresh',
                                          'last_checked_at': '2026-10-15T15:04:00Z'}],
                             'events': []}}


def test_news_validator_flags_future_clock_and_foreign_binding_without_raw_url():
    payload = view(0)
    payload['items'] = [{'item_id': 'item1', 'title': 'Copper price rises',
                         'url': 'https://news.example/story?private=marker', 'publisher': 'Publisher',
                         'provider': 'Google News RSS', 'source_id': 'eia',
                         'published_at': '2026-10-18T00:00:00Z',
                         'first_seen_at': '2026-10-15T15:03:00Z',
                         'retrieved_at': '2026-10-15T15:04:00Z',
                         'synthetic': False, 'source_tier': 'discovery',
                         'relevance_kind': 'direct_material', 'relevance_strength': 'strong',
                         'ranking_reason': 'Relevant', 'matched_materials': ['foreign'],
                         'matched_routes': [], 'matched_currencies': []}]
    summary, findings = audit.audit_news(payload, profile(0), 2, NOW)
    assert {'future_publication', 'foreign_material'} <= {row['code'] for row in findings}
    assert summary['item_count'] == 1
    assert 'private=marker' not in json.dumps(summary) + json.dumps(findings)


def test_scheduled_company_view_needs_slot_but_not_bundle_published_flag():
    payload = view(0)
    del payload['collection']['published']
    summary, findings = audit.audit_news(payload, profile(0), 2, NOW)
    assert summary['collection']['mode'] == 'scheduled'
    assert 'not_scheduled_snapshot' not in {row['code'] for row in findings}
    payload['collection']['slot'] = None
    _, findings = audit.audit_news(payload, profile(0), 2, NOW)
    assert 'scheduled_slot_before_1500' in {row['code'] for row in findings}


def test_sector_only_stock_price_target_is_flagged_when_promoted():
    payload = view(0)
    payload['items'] = [{'item_id': 'market1', 'title': 'Acme Pharma Stock Price Target Raised',
                         'url': 'https://news.example/story', 'publisher': 'Publisher',
                         'provider': 'Google News RSS', 'source_id': 'google_news_1',
                         'published_at': '2026-10-15T12:00:00Z',
                         'first_seen_at': '2026-10-15T15:03:00Z',
                         'retrieved_at': '2026-10-15T15:04:00Z', 'synthetic': False,
                         'relevance_kind': 'sector', 'relevance_strength': 'related',
                         'ranking_reason': 'Sector and operating context appear together.',
                         'matched_materials': [], 'matched_routes': [],
                         'matched_currencies': []}]
    _, findings = audit.audit_news(payload, profile(0), 2, NOW)
    assert 'sector_stock_target_promoted' in {row['code'] for row in findings}


def test_revision_gate_saves_failed_receipt_before_session_work(tmp_path):
    class Client:
        def __init__(self):
            self.paths = []
        def json(self, stage, path, **_kwargs):
            self.paths.append(path)
            return {'revision': 'wrong-revision'}

    client = Client()
    output = tmp_path / 'receipt.json'
    result = audit.run(client, output, now=NOW)
    assert result['status'] == 'failed'
    assert result['failure']['code'] == 'revision_mismatch'
    assert client.paths == ['/-/health']
    assert json.loads(output.read_text()) == result


def test_runner_uses_only_saved_news_reads_and_redacts_anonymous_session(tmp_path):
    class Client:
        def __init__(self):
            self.calls = []
            self.current = -1
        def json(self, stage, path, *, body=None, csrf=None):
            self.calls.append((path, body))
            if path == '/-/health':
                return {'revision': audit.EXPECTED_REVISION}
            if path == '/-/ready':
                return {'ready': True}
            if path == '/api/demo/companies':
                return {'companies': [profile(index) for index in range(10)]}
            if path == '/api/demo/session' and body is None:
                return {'session_id': 'private-cookie-marker', 'csrf_token': 'private-csrf-marker',
                        'context_version': 1}
            if path == '/api/demo/company':
                self.current += 1
                return {'session_id': 'private-cookie-marker', 'csrf_token': 'private-csrf-marker',
                        'company_id': f'company-{self.current}',
                        'context_version': self.current + 2}
            if path == '/api/demo/news':
                return view(self.current)
            if path == '/api/demo/risks':
                return {'companies': [{'company_id': f'company-{index}', 'alerts': [],
                                       'coverage': {}, 'data_gaps': []}
                                      for index in range(10)]}
            raise AssertionError(path)

    client = Client()
    result = audit.run(client, tmp_path / 'receipt.json', now=NOW)
    paths = [path for path, _ in client.calls]
    assert len(result['companies']) == 10
    assert paths.count('/api/demo/news') == 10
    assert paths.count('/api/demo/risks') == 1
    assert not any('refresh' in path or 'operator' in path for path in paths)
    receipt = (tmp_path / 'receipt.json').read_text()
    assert 'private-cookie-marker' not in receipt
    assert 'private-csrf-marker' not in receipt
