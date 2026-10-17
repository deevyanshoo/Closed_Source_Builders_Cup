"""Attainable contribution-margin scenario for one export market. Pure, Decimal, user inputs only.

The market price side is the historical customs unit value from the ranking, converted with the dated BIS
snapshot rate (held at its month, in the direction its quote states). Imports are usually CIF-valued, so freight
and insurance are already inside that value and are deducted once as the exporter's cost. For an FOB-valued
market, freight is added once to reach a delivered value and deducted once. Nothing is defaulted: a missing input,
a suppressed unit value or a missing FX rate means no figure and an exact list of what is missing.
"""
from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation

LABEL = 'scenario from your inputs and historical customs unit values, not a buyer price, quote or forecast'
TARIFF_UNAVAILABLE = 'tariff unavailable, check the official schedule'
INPUTS = {
    'unit_cost_per_kg': 'unit production cost ({c} per kg)',
    'freight_insurance_per_kg': 'freight and insurance to this market ({c} per kg)',
    'tariff_rate_percent': 'tariff rate on the customs value (percent)',
    'capacity_kg_per_year': 'available export capacity (kg per year)',
    'target_share_percent': "target share of this market's import volume (percent)",
}
_CTX = Context(prec=28)


def _plain(value: Decimal) -> str:
    value = value.normalize()
    return format(value, 'f') if value != 0 else '0'


def _money(value: Decimal) -> str:
    """Display only: 2 decimal places with separators; fields keep the exact value."""
    return f'{value.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN):,.2f}'


def _qty(value: Decimal) -> str:
    text = _plain(value)
    whole, _, fraction = text.partition('.')
    return f'{int(whole):,}' + (f'.{fraction}' if fraction else '') if whole.lstrip('-').isdigit() else text


def _parse(name: str, text) -> Decimal | None:
    if text is None or str(text).strip() == '':
        return None
    try:
        value = Decimal(str(text).strip())
    except InvalidOperation:
        raise ValueError(f'{name} must be a decimal number.') from None
    if not value.is_finite() or value < 0 or value.adjusted() > 15:
        raise ValueError(f'{name} must be a finite number of 0 or more.')
    if name == 'target_share_percent' and value > 100:
        raise ValueError('target_share_percent must be between 0 and 100.')
    return value


def _fx(fx: dict | None, currency: str):
    if currency == 'USD':
        return (lambda usd: usd), None
    if not fx:
        return None, None
    rate = Decimal(str(fx['rate']))
    if fx['quote'] == f'{currency} per USD':
        convert = lambda usd: usd * rate  # noqa: E731
    elif fx['quote'] == f'USD per {currency}':
        convert = lambda usd: _CTX.divide(usd, rate)  # noqa: E731
    else:
        return None, None
    return convert, {'rate': str(fx['rate']), 'quote': fx['quote'], 'month': fx['month'],
                     'publisher': fx['publisher'], 'url': fx['url']}


def margin_scenario(*, market: dict, inputs: dict, fx: dict | None, reporting_currency: str) -> dict:
    c = reporting_currency
    values = {name: _parse(name, (inputs or {}).get(name)) for name in INPUTS}
    shown = [{'name': name, 'label': label.format(c=c), 'value': None if values[name] is None else _plain(values[name]),
              'source': 'user supplied'} for name, label in INPUTS.items()]
    unit_value = market.get('unit_value') or {}
    base = {'label': LABEL, 'market': {'code': market.get('code'), 'name': market.get('name')}, 'currency': c,
            'inputs': shown, 'unit_value': dict(unit_value), 'notes': []}
    missing = [name for name in INPUTS if values[name] is None]
    if values['tariff_rate_percent'] is None:
        base['notes'].append(TARIFF_UNAVAILABLE)
    if unit_value.get('status') != 'available':
        missing.append(f"historical customs unit value ({unit_value.get('reason', 'not available')})")
    elif not unit_value.get('net_weight_kg'):
        missing.append('market import volume (net weight)')
    convert, fx_view = _fx(fx, c)
    if convert is None:
        missing.append(f'dated FX rate for {c}')
    if missing:
        return {**base, 'status': 'unavailable', 'missing': missing, 'fx': fx_view}
    usd = Decimal(unit_value['usd_per_kg'])
    price = convert(usd)
    cost, freight = values['unit_cost_per_kg'], values['freight_insurance_per_kg']
    tariff_rate, capacity, share = values['tariff_rate_percent'], values['capacity_kg_per_year'], values['target_share_percent']
    basis = unit_value.get('basis')
    fx_text = '' if fx_view is None else f" x {fx_view['rate']} {fx_view['quote']} ({fx_view['month']})"
    usd_text = f'{usd.quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)}'
    lines = [f"unit value: {usd_text} USD/kg{fx_text} = {_money(price)} {c}/kg ({basis}, {unit_value['year']})"]
    if basis == 'FOB':
        delivered = price + freight
        base['notes'].append('FOB-valued imports exclude freight and insurance: freight is added once to reach a '
                             'delivered value and deducted once as your cost.')
        lines.append(f'delivered value: {_money(price)} + {_money(freight)} freight and insurance = {_money(delivered)} {c}/kg')
        customs = delivered
        cost_line = f'{_money(cost)} (unit cost) - {_money(freight)} (shipping)'
    else:
        customs = price
        cost_line = f'{_money(cost)} (unit cost) - {_money(freight)} (freight and insurance)'
    tariff = customs * tariff_rate / 100
    margin = customs - cost - freight - tariff
    lines.append(f'tariff: {_plain(tariff_rate)}% x {_money(customs)} = {_money(tariff)} {c}/kg')
    lines.append(f'margin per kg: {_money(customs)} - {cost_line} - {_money(tariff)} (tariff) = {_money(margin)} {c}/kg')
    market_kg = Decimal(unit_value['net_weight_kg'])
    volume = min(capacity, market_kg * share / 100)
    lines.append(f'volume: min({_qty(capacity)} kg capacity, {_plain(share)}% x {_qty(market_kg)} kg market imports) = {_qty(volume)} kg')
    contribution = margin * volume
    lines.append(f'contribution: {_money(margin)} x {_qty(volume)} = {_money(contribution)} {c}')
    if margin < 0:
        base['notes'].append('At these inputs each kg is sold at a loss against the historical unit value.')
    return {**base, 'status': 'available', 'missing': [], 'fx': fx_view, 'basis': basis,
            'unit_value_converted': _plain(price), 'margin_per_kg': _plain(margin), 'volume_kg': _plain(volume),
            'contribution': _plain(contribution), 'formula': (
                'margin per kg = market unit value (CIF, converted) - unit cost - freight and insurance - tariff on the '
                'customs value; contribution = margin per kg x min(capacity, target share x market import volume)'),
            'arithmetic': lines}
