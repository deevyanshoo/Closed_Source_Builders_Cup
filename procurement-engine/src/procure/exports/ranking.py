"""Rank candidate export markets from the committed UN Comtrade extract. Pure: no I/O, Decimal arithmetic.

Every number behind a rank is returned with its year and basis. A year or partner UN Comtrade did not return is
"not reported", never zero. Values the importer did not report can be shown from the home country's exports as a
labelled mirror. Unit values are historical customs proxies and are suppressed when net weight is missing,
estimated or in mixed units. Sorting uses one visible measure at a time; there is no composite score.
"""
from __future__ import annotations

from decimal import Context, Decimal

LABEL = 'observed historical trade, not buyer demand, a confirmed buyer or a price quote'
SORTS = ('market_size', 'growth', 'home_share', 'margin')
DEFAULT_YEARS = (2020, 2021, 2022, 2023, 2024)
MIRROR = 'mirror (reported by exporter)'
_CTX = Context(prec=28)


def _d(value) -> Decimal:
    return Decimal(str(value))


def _plain(value: Decimal) -> str:
    return format(value.normalize(), 'f') if value == value.to_integral_value() else format(value, 'f')


def _rows(company: dict, role: str) -> list[dict]:
    return [r for s in company.get(role, []) for r in s['rows']]


def _by_year(rows, *, reporter=None, partner=None) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    for r in rows:
        if (reporter is None or r['reporter_code'] == reporter) and (partner is None or r['partner_code'] == partner) \
                and r['primary_value'] is not None:
            out.setdefault(int(r['period']), []).append(r)
    return out


def _basis(row: dict) -> str:
    if row.get('cif_value') is not None and row['cif_value'] == row['primary_value']:
        return 'CIF'
    return 'FOB' if row.get('fob_value') is not None else 'not stated'


def _unit_value(rows: list[dict], year: int) -> dict:
    def suppressed(reason):
        return {'status': 'unit value suppressed', 'reason': reason, 'year': year}
    if len({r['qty_unit_code'] for r in rows}) > 1:
        return suppressed('mixed quantity units in the reported rows')
    if len(rows) != 1:
        return suppressed('more than one reported row for this market and year')
    row = rows[0]
    weight = row.get('net_wgt')
    if weight is None or _d(weight) <= 0:
        return suppressed('net weight not reported')
    if row.get('is_net_wgt_estimated'):
        return suppressed('net weight estimated by UN Comtrade; no reliability measure is published with it')
    value = _CTX.divide(_d(row['primary_value']), _d(weight))
    return {'status': 'available', 'usd_per_kg': _plain(value), 'year': year, 'basis': _basis(row),
            'net_weight_kg': str(weight), 'label': 'historical customs unit value'}


def _growth(world: dict[int, list[dict]]) -> dict:
    years = sorted(world)
    if len(years) < 3:
        return {'status': 'growth unavailable', 'reason': f'needs at least 3 reported years; {len(years)} reported'}
    first, last = _d(world[years[0]][0]['primary_value']), _d(world[years[-1]][0]['primary_value'])
    if first <= 0:
        return {'status': 'growth unavailable', 'reason': f'first reported value ({years[0]}) is not positive'}
    cagr = _CTX.power(_CTX.divide(last, first), _CTX.divide(Decimal(1), Decimal(years[-1] - years[0]))) - 1
    return {'status': 'available', 'cagr': _plain(cagr.quantize(Decimal('1e-12'))), 'from_year': years[0],
            'to_year': years[-1], 'years_used': years}


def _market(company: dict, product: dict, code: int, years: tuple[int, ...]) -> dict:
    home = company['home_reporter_code']
    imports = _rows(company, 'market_imports')
    world = _by_year(imports, reporter=code, partner=0)
    from_home = _by_year(imports, reporter=code, partner=home)
    exports = _by_year(_rows(company, 'home_exports'), reporter=home, partner=code)
    mirror_rows = _by_year(_rows(company, 'mirror_home_exports'), reporter=code, partner=home)
    names = [r['reporter_name'] for rows in (world, from_home, mirror_rows) for y in rows for r in rows[y]] + \
            [r['partner_name'] for y in exports for r in exports[y]]
    name = next((n for n in names if n), str(code))
    reported = sorted(world)
    market = {'code': code, 'name': name, 'years_reported': reported,
              'years_not_reported': [y for y in years if y not in world],
              'existing_export_market': code in company.get('existing_export_codes', [])}
    if reported:
        latest = reported[-1]
        row = world[latest][0]
        market['import_value'] = {'status': 'available', 'value_usd': str(row['primary_value']), 'year': latest,
                                  'basis': _basis(row), 'source': 'reported by importer'}
        market['unit_value'] = _unit_value(world[latest], latest)
    else:
        market['import_value'] = {'status': 'not reported',
                                  'reason': f'{name} did not report imports of this heading in {years[0]} to {years[-1]}.'}
        market['unit_value'] = {'status': 'unit value suppressed', 'reason': 'imports not reported', 'year': None}
    if exports:
        year = max(exports)
        market['mirror'] = {'value_usd': str(exports[year][0]['primary_value']), 'year': year, 'label': MIRROR,
                            'meaning': f"{product['home_country']} reported exports to {name}"}
    else:
        market['mirror'] = None
    market['growth'] = _growth(world)
    common = sorted(set(world) & set(from_home))
    if common:
        y = common[-1]
        share = _CTX.divide(_d(from_home[y][0]['primary_value']), _d(world[y][0]['primary_value']))
        market['home_share'] = {'status': 'available', 'share': _plain(share), 'year': y, 'basis': 'reported by importer'}
    elif set(world) & set(exports):
        y = sorted(set(world) & set(exports))[-1]
        share = _CTX.divide(_d(exports[y][0]['primary_value']), _d(world[y][0]['primary_value']))
        market['home_share'] = {'status': 'available', 'share': _plain(share), 'year': y, 'basis': MIRROR}
    else:
        market['home_share'] = {'status': 'unavailable', 'reason': 'no year with both total imports and imports from the home country'}
    return market


def _key(sort: str, margins: dict):
    def value(market):
        if sort == 'market_size':
            item = market['import_value']
            return _d(item['value_usd']) if item['status'] == 'available' else None
        if sort == 'growth':
            return _d(market['growth']['cagr']) if market['growth']['status'] == 'available' else None
        if sort == 'home_share':
            return _d(market['home_share']['share']) if market['home_share']['status'] == 'available' else None
        item = margins.get(market['code'])
        return _d(item['contribution']) if item and item.get('status') == 'available' else None

    def key(market):
        v = value(market)
        return (v is None, -(v or 0), market['name'])
    return key


def rank_markets(company: dict, product: dict, *, sort: str = 'market_size', margins: dict | None = None,
                 years: tuple[int, ...] = DEFAULT_YEARS) -> dict:
    if sort not in SORTS:
        raise ValueError(f'Unknown sort {sort!r}; choose one of {", ".join(SORTS)}.')
    margins = margins or {}
    markets = [_market(company, product, code, tuple(years)) for code in company['candidate_codes']]
    for market in markets:
        market['margin'] = margins.get(market['code'])
    markets.sort(key=_key(sort, margins))
    return {'label': LABEL, 'sort': sort, 'sorts': list(SORTS), 'product': dict(product),
            'candidate_discovery': company.get('candidate_discovery'), 'markets': markets}
