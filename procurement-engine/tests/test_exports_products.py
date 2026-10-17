"""Illustrative HS mapping per demo company, checked against company.py and the HS 2022 reference (#131)."""
import json
import re
from pathlib import Path

from procure.demo.company import get_company, list_companies

PRODUCTS = json.loads((Path(__file__).resolve().parents[1] / 'config' / 'export_products_v1.json').read_text())


def test_every_demo_company_has_one_labelled_heading_and_its_home_country():
    rows = {row['company_id']: row for row in PRODUCTS['companies']}
    assert set(rows) == {summary['company_id'] for summary in list_companies()}
    assert PRODUCTS['hs_revision'] == 'HS 2022 (UN Comtrade classification H6)'
    assert PRODUCTS['hs_reference']['url'].startswith('https://comtradeapi.un.org/')
    for company_id, row in rows.items():
        company = get_company(company_id)
        assert row['home_country'] == company['locations'][0]['country'], company_id
        assert re.fullmatch(r'\d{4}(\d{2})?', row['hs_heading']), company_id
        assert row['note'] == 'illustrative mapping for a fictional company'
        assert row['reason'] and row['hs_description'] and isinstance(row['home_reporter_code'], int)
        assert all(market['code'] != row['home_reporter_code'] for market in row['existing_export_markets'])


def test_headings_follow_the_reviewed_choices():
    headings = {row['company_id']: row['hs_heading'] for row in PRODUCTS['companies']}
    assert headings == {'solara-cables': '8544', 'terrapack': '3920', 'loomworks': '5208', 'harvest-foods': '1905',
                        'meridian-pharma': '3004', 'forgeline-components': '8708', 'gridspring-batteries': '8507',
                        'circuitpeak-systems': '8473', 'atlas-petrochem': '2710', 'stonebridge-materials': '2523'}
    circuit = next(row for row in PRODUCTS['companies'] if row['company_id'] == 'circuitpeak-systems')
    assert '8473.30' in circuit['reason'] and '8542 is its input' in circuit['reason']
