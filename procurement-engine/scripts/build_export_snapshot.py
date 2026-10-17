"""Build config/export_trade_snapshot_v1.json from the keyless UN Comtrade public preview API, once, offline.

Run by hand (`PYTHONPATH=src .venv/bin/python scripts/build_export_snapshot.py`); the hosted app never calls UN
Comtrade. The preview allows one period per call and limits records per call, so every request is one year and
the script waits between calls and backs off on HTTP 429. Every request URL is printed and stored with its series.

For each company in config/export_products_v1.json (one illustrative HS heading):
  (a) home-country exports of the heading by partner, per year (reporter = home, flow = X);
      if the home country reported nothing, partners' imports from it are used instead and labelled mirror;
  (b) candidate markets = top 15 partners by the latest reported value, plus the company's existing export routes;
  (c) each candidate's imports of the heading from the World and from the home country (reporter = candidates,
      flow = M, partner = World and home), per year.
Only the fields used later are kept (a transformed extract), with the SHA-256 of each raw payload and of the
stored rows. Unreported years and partners are left out, never written as zero.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from procure.exports.snapshot import PRODUCTS_FILE, SNAPSHOT_FILE, extract_row, rows_digest, sort_rows  # noqa: E402

API = 'https://comtradeapi.un.org/public/v1/preview/C/A/HS'
YEARS = [2020, 2021, 2022, 2023, 2024]
TOP_PARTNERS = 15
PAUSE_SECONDS = 2.5
PREVIEW_RECORD_LIMIT = 500
FILTERS = {'partner2Code': '0', 'customsCode': 'C00', 'motCode': '0', 'includeDesc': 'true'}
TERMS = {
    'provider': 'UN Comtrade, United Nations Statistics Division',
    'api_documentation': 'https://uncomtrade.org/docs/un-comtrade-api/',
    'use_policy': 'https://uncomtrade.org/docs/faqs-on-use-and-re-dissemination/',
    'checked_on': '2026-10-17',
    'access': 'Keyless public preview API (no subscription key); one period per call.',
    'summary': ('UN Comtrade data are copyrighted by the United Nations. Its re-dissemination FAQ says transformed '
                'data "is no longer subject to copyright restrictions" and that re-dissemination means reusing the '
                'data as-is on other platforms. This file is a transformed extract: selected fields for one HS '
                'heading per company, kept with derived rankings and the SHA-256 of each raw payload. Raw payloads '
                'are not committed.'),
    'attribution': 'Source: UN Comtrade (United Nations Statistics Division), retrieved via the public preview API.',
}
EXCLUDED_PARTNERS = {0}  # World is stored separately as the total, never ranked as a market
# Partner areas that are not a single market ("Areas, nes", bunkers, free zones) are not ranked.
NOT_A_MARKET = re.compile(r'\bnes\b|bunkers|free zones|special categories', re.IGNORECASE)


def fetch(params: dict, log: list) -> tuple[dict, bytes, str, str]:
    url = API + '?' + urllib.parse.urlencode(params, safe=',')
    for attempt in range(6):
        time.sleep(PAUSE_SECONDS)
        print('GET', url, flush=True)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'Accept': 'application/json'}), timeout=60) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            if error.code == 429 and attempt < 5:
                wait = PAUSE_SECONDS * 2 ** (attempt + 1)
                print(f'  429, waiting {wait:.0f}s', flush=True)
                time.sleep(wait)
                continue
            raise
        retrieved = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
        payload = json.loads(raw)
        if payload.get('error'):
            raise RuntimeError(f'{url}: {payload["error"]}')
        log.append(url)
        return payload, raw, url, retrieved
    raise RuntimeError(f'{url}: rate limited')


def series(role: str, params: dict, payload: dict, raw: bytes, url: str, retrieved: str, keep=None) -> dict:
    records = payload.get('data') or []
    rows = sort_rows([extract_row(r) for r in records if keep is None or keep(r)])
    return {'role': role, 'provider': 'UN Comtrade', 'query': {'url': url, 'params': params},
            'period': int(params['period']), 'flow': params['flowCode'], 'hs_heading': params['cmdCode'],
            'retrieved_at': retrieved, 'raw_payload_sha256': hashlib.sha256(raw).hexdigest(),
            'raw_record_count': len(records), 'possibly_truncated': len(records) >= PREVIEW_RECORD_LIMIT,
            'rows': rows, 'rows_sha256': rows_digest(rows)}


def ranked_partners(rows_by_year: dict, field: str) -> list[int]:
    latest = max((year for year, rows in rows_by_year.items() if rows), default=None)
    if latest is None:
        return []
    desc = 'partnerDesc' if field == 'partnerCode' else 'reporterDesc'
    rows = [r for r in rows_by_year[latest] if r[field] not in EXCLUDED_PARTNERS and r['primaryValue']
            and not NOT_A_MARKET.search(r.get(desc) or '')]
    rows.sort(key=lambda r: (-r['primaryValue'], r[field]))
    return [r[field] for r in rows[:TOP_PARTNERS]]


def build_company(company: dict, log: list) -> dict:
    home, hs = company['home_reporter_code'], company['hs_heading']
    out = {'company_id': company['company_id'], 'home_reporter_code': home, 'hs_heading': hs, 'home_exports': [],
           'mirror_home_exports': [], 'market_imports': []}
    raw_exports = {}
    for year in YEARS:
        params = {'reporterCode': str(home), 'period': str(year), 'cmdCode': hs, 'flowCode': 'X', **FILTERS}
        payload, raw, url, retrieved = fetch(params, log)
        raw_exports[year] = (params, payload, raw, url, retrieved)
    by_year = {year: item[1].get('data') or [] for year, item in raw_exports.items()}
    candidates = ranked_partners(by_year, 'partnerCode')
    discovery = 'home_exports'
    mirror = {}
    if not candidates:
        discovery = 'mirror_importers'
        for year in YEARS:
            params = {'partnerCode': str(home), 'period': str(year), 'cmdCode': hs, 'flowCode': 'M', **FILTERS}
            mirror[year] = (params, *fetch(params, log))
        candidates = ranked_partners({y: v[1].get('data') or [] for y, v in mirror.items()}, 'reporterCode')
    existing = [m['code'] for m in company['existing_export_markets']]
    markets = list(dict.fromkeys(candidates + [code for code in existing if code != home]))
    out.update(candidate_discovery=discovery, candidate_codes=markets, existing_export_codes=existing)
    keep_markets = set(markets) | {0}
    for year, (params, payload, raw, url, retrieved) in raw_exports.items():
        out['home_exports'].append(series('home_exports', params, payload, raw, url, retrieved,
                                          keep=lambda r: r['partnerCode'] in keep_markets))
    for year, (params, payload, raw, url, retrieved) in mirror.items():
        out['mirror_home_exports'].append(series('mirror_home_exports', params, payload, raw, url, retrieved,
                                                 keep=lambda r: r['reporterCode'] in keep_markets))
    for year in YEARS:
        params = {'reporterCode': ','.join(str(code) for code in markets), 'period': str(year), 'cmdCode': hs,
                  'flowCode': 'M', 'partnerCode': f'0,{home}', **FILTERS}
        payload, raw, url, retrieved = fetch(params, log)
        out['market_imports'].append(series('market_imports', params, payload, raw, url, retrieved))
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--config-dir', type=Path, default=ROOT / 'config')
    parser.add_argument('--company', action='append', help='limit to these company ids (testing only)')
    args = parser.parse_args(argv)
    products = json.loads((args.config_dir / PRODUCTS_FILE).read_text(encoding='utf-8'))
    log: list[str] = []
    companies = [c for c in products['companies'] if not args.company or c['company_id'] in args.company]
    snapshot = {'snapshot_version': 'export-trade-snapshot-v1', 'products_version': products['mapping_version'],
                'built_on': datetime.now(timezone.utc).date().isoformat(), 'years_requested': YEARS,
                'endpoint': API, 'filters': FILTERS, 'terms': TERMS,
                'companies': {c['company_id']: build_company(c, log) for c in companies},
                'request_count': len(log)}
    (args.config_dir / SNAPSHOT_FILE).write_text(json.dumps(snapshot, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f'{len(log)} requests; wrote {SNAPSHOT_FILE}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
