"""Committed UN Comtrade extract: provenance per series, no invented rows, terms recorded, packaged (#131)."""
import json
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

from procure.exports.snapshot import rows_digest

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = json.loads((ROOT / 'config' / 'export_trade_snapshot_v1.json').read_text())
PRODUCTS = {row['company_id']: row for row in json.loads((ROOT / 'config' / 'export_products_v1.json').read_text())['companies']}
API = 'https://comtradeapi.un.org/public/v1/preview/C/A/HS'


def all_series():
    for company_id, company in SNAPSHOT['companies'].items():
        for role in ('home_exports', 'mirror_home_exports', 'market_imports'):
            for item in company[role]:
                yield company_id, item


def test_terms_and_attribution_are_recorded():
    terms = SNAPSHOT['terms']
    assert terms['use_policy'] == 'https://uncomtrade.org/docs/faqs-on-use-and-re-dissemination/'
    assert terms['attribution'].startswith('Source: UN Comtrade')
    assert 'transformed extract' in terms['summary'] and 'Raw payloads' in terms['summary']
    assert SNAPSHOT['endpoint'] == API and SNAPSHOT['years_requested'] == [2020, 2021, 2022, 2023, 2024]
    assert set(SNAPSHOT['companies']) == set(PRODUCTS)


def test_every_series_has_query_period_time_and_matching_digests():
    count = 0
    for company_id, item in all_series():
        count += 1
        params = item['query']['params']
        assert item['query']['url'] == API + '?' + urllib.parse.urlencode(params, safe=',')
        assert params['cmdCode'] == PRODUCTS[company_id]['hs_heading'] == item['hs_heading']
        assert int(params['period']) == item['period'] and item['period'] in SNAPSHOT['years_requested']
        assert params['flowCode'] == item['flow']
        assert re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', item['retrieved_at'])
        assert re.fullmatch(r'[0-9a-f]{64}', item['raw_payload_sha256'])
        assert item['rows_sha256'] == rows_digest(item['rows'])
        assert item['possibly_truncated'] is False and item['raw_record_count'] >= len(item['rows'])
    assert count == SNAPSHOT['request_count']


def test_rows_only_come_from_the_query_that_returned_them():
    for company_id, item in all_series():
        params = item['query']['params']
        reporters = {int(code) for code in params['reporterCode'].split(',')} if 'reporterCode' in params else None
        partners = {int(code) for code in params['partnerCode'].split(',')} if 'partnerCode' in params else None
        for row in item['rows']:
            assert int(row['period']) == item['period'] and row['flow'] == item['flow']
            assert row['cmd_code'] == item['hs_heading']
            assert reporters is None or row['reporter_code'] in reporters
            assert partners is None or row['partner_code'] in partners
            assert row['primary_value'] is not None  # absent rows stay absent; nothing is filled with zero


def test_candidates_include_existing_export_routes_and_are_not_partner_groups():
    for company_id, company in SNAPSHOT['companies'].items():
        existing = [m['code'] for m in PRODUCTS[company_id]['existing_export_markets']]
        assert set(existing) <= set(company['candidate_codes']), company_id
        assert 0 not in company['candidate_codes'] and company['home_reporter_code'] not in company['candidate_codes']
        assert len(company['candidate_codes']) <= 15 + len(existing)


def test_deploy_package_ships_the_export_config(tmp_path):
    repo = ROOT.parent
    destination = tmp_path / 'package'
    prepared = subprocess.run([sys.executable, str(repo / 'deploy/judge-demo/prepare_package.py'), str(destination)],
                              capture_output=True, text=True)
    assert prepared.returncode == 0, prepared.stderr
    config = destination / 'procurement-engine' / 'config'
    for name in ('export_products_v1.json', 'export_trade_snapshot_v1.json', 'export_fx_v1.json'):
        assert (config / name).read_bytes() == (ROOT / 'config' / name).read_bytes(), name
