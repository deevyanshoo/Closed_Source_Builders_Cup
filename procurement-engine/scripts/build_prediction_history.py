"""Build config/prediction_history_v1.json from downloaded public files (run once; the hosted app never fetches).

Inputs (not committed):
  --pinksheet  World Bank CMO-Historical-Data-Monthly.xlsx (the file procure/ingest/prices.py downloads)
  --bis        BIS WS_XRU monthly averages CSV:
               https://stats.bis.org/api/v1/data/WS_XRU/M...A?startPeriod=2016-01&format=csv

Both files are parsed by Divyanshu's procure/evaluation/series_catalog.py, which applies its fixed 120-month
window (2016-09 to 2026-08), its month-m-usable-at-end-of-m+1 availability rule and its eligibility checks.
build_catalog() admits only files retrieved by its 9 October analysis date; the BIS file here was retrieved on
16 October, so its two parsers (_wb, _bis) are called directly with the true retrieval time instead.
Values are copied as parsed; nothing is filled, interpolated or spliced. A currency the catalog marks ineligible
is stored as "no history available" with the catalog's reason.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from procure.evaluation import series_catalog  # noqa: E402
from procure.prediction.history import series_values_sha256  # noqa: E402

OUT = ROOT / 'config' / 'prediction_history_v1.json'
PROFILE = 'generic-observed-v1'
WB_URL = ('https://thedocs.worldbank.org/en/doc/74e8be41ceb20fa0da750cda2f6b9e4e-0050012026/related/'
          'CMO-Historical-Data-Monthly.xlsx')
BIS_URL = 'https://stats.bis.org/api/v1/data/WS_XRU/M...A?startPeriod=2016-01&format=csv'
RETRIEVED = {'wb': '2026-10-08T16:31:57Z', 'bis': '2026-10-16T06:35:33Z'}
RIGHTS = {
    'wb': 'World Bank Commodity Price Data: Creative Commons Attribution 4.0 (CC BY 4.0), '
          'https://datacatalog.worldbank.org/public-licenses#cc-by; cite the World Bank as source.',
    'bis': 'BIS statistics: use is unrestricted provided the BIS is cited as source, no endorsement is implied, '
           'no extra charge is made for them, and no warranty is given (https://data.bis.org/help/legal).',
}
COMMODITIES = {'copper': 'Copper', 'aluminium': 'Aluminum', 'wheat': 'Wheat, US HRW', 'sugar': 'Sugar, world',
               'cotton': 'Cotton, A Index', 'sunflower_oil': 'Sunflower oil', 'nickel': 'Nickel'}
CURRENCIES = ('AED', 'BDT', 'BWP', 'CHF', 'CNY', 'EUR', 'INR', 'JPY', 'KES', 'KRW', 'MXN', 'MYR', 'NAD', 'OMR',
              'SGD', 'TZS', 'ZAR')
MATERIAL_MAP = [  # (company, material, series, match, note)
    ('solara-cables', 'copper', 'commodity:copper', 'exact', 'Copper cathode, LME grade A benchmark.'),
    ('forgeline-components', 'copper', 'commodity:copper', 'proxy', 'Copper rod: cathode benchmark, rod premium not included.'),
    ('solara-cables', 'aluminium', 'commodity:aluminium', 'proxy', 'Aluminium conductor: primary ingot benchmark, processing premium not included.'),
    ('harvest-foods', 'wheat', 'commodity:wheat', 'proxy', 'Wheat grain: US hard red winter benchmark; other origins and grades differ.'),
    ('harvest-foods', 'sugar', 'commodity:sugar', 'proxy', 'Refined sugar: world raw sugar benchmark; refined trades at a premium.'),
    ('loomworks', 'cotton', 'commodity:cotton', 'exact', 'Cotton fibre, Cotton A Index.'),
    ('harvest-foods', 'oil', 'commodity:sunflower_oil', 'exact', 'Sunflower oil per tonne; quotes per litre need a stated density.'),
    ('gridspring-batteries', 'nickel', 'commodity:nickel', 'proxy', 'Nickel sulfate: nickel metal benchmark, sulfate processing not included.'),
]


def _sha_text(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _source(raw: bytes, provider: str, source_id: str, url: str, key: str) -> dict:
    return {'file': {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}, 'provider': provider,
            'source_id': source_id, 'source_url': url, 'retrieved_utc': RETRIEVED[key],
            'rights_disposition_sha256': _sha_text(RIGHTS[key]),
            'source_disposition_sha256': _sha_text(f'{provider} {url} retrieved {RETRIEVED[key]}')}


def _text(value: float) -> str:
    return repr(float(value))


def _bis_tokens(raw: bytes) -> dict:
    tokens = {}
    for row in csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))):
        tokens[(row['REF_AREA'], row['CURRENCY'], row['TIME_PERIOD'])] = row['OBS_VALUE']
    return tokens


def build(pinksheet: Path, bis: Path) -> dict:
    wb_raw, bis_raw = pinksheet.read_bytes(), bis.read_bytes()
    wb_source = _source(wb_raw, 'World Bank', 'world-bank-pink-sheet', WB_URL, 'wb')
    bis_source = _source(bis_raw, 'BIS', 'bis-ws-xru-monthly-average', BIS_URL, 'bis')
    wb_records, wb_values = series_catalog._wb(wb_raw, wb_source, PROFILE)
    bis_records, bis_values = series_catalog._bis(bis_raw, bis_source, PROFILE, {})
    series: dict = {}
    by_label = {r['label']: r for r in wb_records if r['locator']['sheet'] == 'Monthly Prices'}
    for key, label in COMMODITIES.items():
        record = by_label[label]
        if not record['forecast_eligible']:
            series[f'commodity:{key}'] = {'status': 'no history available', 'reason': record['eligibility_reason']}
            continue
        observations = [[m, _text(v)] for m, v in sorted(wb_values[record['series_id']].items())]
        series[f'commodity:{key}'] = {
            'status': 'available', 'kind': 'commodity', 'source_id': 'world-bank-pink-sheet',
            'catalog_series_id': record['series_id'], 'source_series': label,
            'unit': 'USD per ' + record['quoted_unit'].split('/', 1)[1], 'observations': observations,
            'values_sha256': series_values_sha256(observations)}
    tokens = _bis_tokens(bis_raw)
    for code in CURRENCIES:
        lines = [r for r in bis_records if r['currency'] == code]
        chosen = next((r for r in lines if r['series_id'] == r.get('representative_series_id')), None)
        if chosen is None or not chosen['forecast_eligible']:
            reasons = sorted({r['eligibility_reason'] for r in lines if r['eligibility_reason']}) or ['not in source']
            series[f'fx:{code}'] = {'status': 'no history available', 'currency': code, 'quote': f'{code} per USD',
                                    'reason': f"BIS series not eligible in the catalog ({', '.join(reasons)}); "
                                              'values are not interpolated or taken from another source.'}
            continue
        area = chosen['locator']['REF_AREA']
        observations = [[m, _text(v)] for m, v in sorted(bis_values[chosen['series_id']].items())]
        for month, value in observations:  # the stored text equals the published token
            if Decimal(value) != Decimal(tokens[(area, code, month)]):
                raise ValueError(f'{code} {month} differs from the BIS token')
        series[f'fx:{code}'] = {
            'status': 'available', 'kind': 'fx', 'currency': code, 'quote': f'{code} per USD',
            'source_id': 'bis-ws-xru-monthly-average', 'catalog_series_id': chosen['series_id'],
            'source_series': f'WS_XRU M.{area}.{code}.A', 'unit': f'{code} per USD', 'observations': observations,
            'values_sha256': series_values_sha256(observations)}
    sources = {
        'world-bank-pink-sheet': {
            'publisher': 'World Bank', 'title': 'Commodity Price Data (The Pink Sheet), monthly prices in nominal US dollars',
            'url': WB_URL, 'page_url': 'https://www.worldbank.org/en/research/commodity-markets',
            'licence': 'Creative Commons Attribution 4.0 (CC BY 4.0)', 'rights': RIGHTS['wb'],
            'attribution': 'Source: World Bank Commodity Price Data (The Pink Sheet), updated 2 October 2026.',
            'retrieved_at': RETRIEVED['wb'], 'file_sha256': wb_source['file']['sha256'], 'file_bytes': len(wb_raw)},
        'bis-ws-xru-monthly-average': {
            'publisher': 'Bank for International Settlements (BIS)',
            'title': 'BIS bilateral exchange rates (WS_XRU), monthly averages, national currency per US dollar',
            'url': BIS_URL, 'page_url': 'https://data.bis.org/topics/XRU',
            'licence': 'BIS statistics terms: unrestricted use with the BIS cited as source', 'rights': RIGHTS['bis'],
            'attribution': 'Source: BIS bilateral exchange rate statistics.',
            'retrieved_at': RETRIEVED['bis'], 'file_sha256': bis_source['file']['sha256'], 'file_bytes': len(bis_raw)},
    }
    return {'snapshot_version': 'prediction-history-v1', 'synthetic': False,
            'parser': 'procure/evaluation/series_catalog.py (_wb, _bis)', 'profile': PROFILE,
            'window': {'start': series_catalog.WINDOW[0], 'end': series_catalog.WINDOW[-1],
                       'months': len(series_catalog.WINDOW)},
            'availability_policy': series_catalog.AVAILABILITY,
            'notes': 'Observed public monthly data, copied as parsed. Month m is treated as usable only from the end of month m+1.',
            'sources': sources, 'series': dict(sorted(series.items())),
            'material_map': [{'company_id': c, 'material_id': m, 'series_id': s, 'match': k, 'note': n}
                             for c, m, s, k, n in MATERIAL_MAP]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--pinksheet', type=Path, required=True)
    parser.add_argument('--bis', type=Path, required=True)
    args = parser.parse_args(argv)
    OUT.write_text(json.dumps(build(args.pinksheet, args.bis), indent=1) + '\n', encoding='utf-8')
    print(f'Wrote {OUT}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
