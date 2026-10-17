"""Budget-at-risk: the tested benchmark ranges from the price outlook, turned into money for one plan quantity.

Pure and Decimal only. Values from the committed JSON enter through str(float), which gives the stored digits
exactly. Amounts stay unrounded; `display` rounds to the currency's ISO 4217 minor unit at the edge. Only kg and
tonne convert; any other unit needs a stated mass basis, which is never guessed. FX uses the dated snapshot rate in
the direction its unit states and is held at that month; the FX outlook carries the currency uncertainty.
"""
from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation

LABEL = 'benchmark cost exposure, not a supplier quote, landed cost or recommendation'
FX_NOTE = 'FX held at {month} rate; see the FX outlook for currency uncertainty.'
NO_RANGE = 'no tested range for this horizon'
TESTED = {'RANGE_ONLY', 'PREDICTION'}
KG_PER = {'kg': Decimal(1), 'tonne': Decimal(1000), 'mt': Decimal(1000), 't': Decimal(1000)}
# ISO 4217 minor units that differ from 2, for display rounding only.
MINOR_UNITS = {'JPY': 0, 'KRW': 0, 'VND': 0, 'CLP': 0, 'ISK': 0, 'UGX': 0, 'XAF': 0, 'XOF': 0,
               'BHD': 3, 'IQD': 3, 'JOD': 3, 'KWD': 3, 'LYD': 3, 'OMR': 3, 'TND': 3}
_DIVIDE = Context(prec=34)


def _number(value) -> Decimal:
    return Decimal(str(value))


def _plain(value: Decimal) -> str:
    return format(value.normalize(), 'f') if value == value.to_integral_value() else format(value, 'f')


def _quantity(text) -> Decimal:
    try:
        value = Decimal(str(text))
    except InvalidOperation:
        raise ValueError('Quantity must be a decimal number.') from None
    if not value.is_finite() or value <= 0 or value.adjusted() > 12:
        raise ValueError('Quantity must be a positive decimal number.')
    return value


def display(amount: Decimal, currency: str) -> str:
    places = MINOR_UNITS.get(currency, 2)
    rounded = amount.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN)
    return f'{rounded:,.{places}f}'


def _money(amount: Decimal, today: Decimal | None, currency: str) -> dict:
    row = {'amount': format(amount, 'f'), 'display': display(amount, currency)}
    if today is not None:
        change = amount - today
        percent = _DIVIDE.divide(change * 100, today)
        row.update(change=format(change, 'f'), change_display=display(change, currency),
                   change_percent=format(percent.quantize(Decimal('0.01'), rounding=ROUND_HALF_EVEN), 'f'))
    return row


def _converter(fx_entry: dict | None, currency: str):
    """Return (convert, fx_view) or (None, reason)."""
    if currency == 'USD':
        return (lambda usd: usd), None
    if not fx_entry or fx_entry.get('series_id', f'fx:{currency}') != f'fx:{currency}':
        return None, f'No dated FX history for {currency} in the snapshot, so no amount is converted.'
    rate, quote = _number(fx_entry['latest_value']), fx_entry['unit']
    if quote == f'{currency} per USD':
        convert = lambda usd: usd * rate  # noqa: E731
    elif quote == f'USD per {currency}':
        convert = lambda usd: _DIVIDE.divide(usd, rate)  # noqa: E731
    else:
        return None, f'The FX series for {currency} is quoted as {quote}, which does not match {currency}.'
    source = fx_entry['source']
    return convert, {'rate': format(rate, 'f'), 'quote': quote, 'month': fx_entry['data_date'],
                     'series_id': f'fx:{currency}', 'source': source['attribution'],
                     'publisher': source['publisher'], 'url': source['url']}


def budget_at_risk(*, outlook_entry: dict, fx_entry: dict | None, quantity: str, unit: str,
                   reporting_currency: str) -> dict:
    qty = _quantity(quantity)
    base = {'label': LABEL, 'material_id': outlook_entry.get('material_id'), 'currency': reporting_currency,
            'quantity': {'value': _plain(qty), 'unit': unit}, 'horizons': []}
    series = outlook_entry.get('series')
    if outlook_entry.get('status') != 'available' or not series:
        return {**base, 'status': outlook_entry.get('status') or 'no_series',
                'reason': outlook_entry.get('message') or 'No benchmark series is mapped to this material.'}
    base.update(match=outlook_entry.get('match'), note=outlook_entry.get('note'), series_id=series.get('series_id'),
                benchmark_unit=series['unit'], data_date=series['data_date'], issue_month=series['issue_month'],
                freshness_state=series['freshness_state'], source=dict(series['source']))
    benchmark_mass = series['unit'].rsplit(' per ', 1)[-1]
    if unit not in KG_PER or benchmark_mass not in KG_PER:
        basis = ' (for example oil per litre needs a stated density)' if unit == 'litre' else ''
        return {**base, 'status': 'unit_basis_required',
                'reason': f'The plan quantity is in {unit} and the benchmark is {series["unit"]}; a stated mass per '
                          f'{unit} is needed{basis}. No mass is assumed.'}
    convert, fx = _converter(fx_entry, reporting_currency)
    if convert is None:
        return {**base, 'status': 'fx_unavailable', 'reason': fx}
    mass = qty * KG_PER[unit] / KG_PER[benchmark_mass]
    base['quantity'].update(benchmark_quantity=_plain(mass), benchmark_unit=benchmark_mass)
    today = convert(mass * _number(series['latest_value']))
    rows = []
    for h in series['horizons']:
        row = {'horizon': h['horizon'], 'target_month': h['target_month'], 'status': h['status'], 'label': h['label']}
        if h['status'] not in TESTED or h.get('p10') is None or h.get('p90') is None:
            rows.append({**row, 'reason': NO_RANGE})
            continue
        backtest = h['backtest']
        uses_model = h['status'] == 'PREDICTION'
        rows.append({**row,
                     'low': _money(convert(mass * _number(h['p10'])), today, reporting_currency),
                     'mid': _money(convert(mass * _number(h['p50'])), today, reporting_currency),
                     'high': _money(convert(mass * _number(h['p90'])), today, reporting_currency),
                     'hit_rate': {'value': str(backtest['coverage'] if uses_model else backtest['naive_coverage']),
                                  'basis': 'model interval coverage' if uses_model else 'naive band coverage',
                                  'test_origins': backtest['n_origins']}})
    return {**base, 'status': 'available', 'fx': fx, 'fx_note': FX_NOTE.format(month=fx['month']) if fx else None,
            'today': _money(today, None, reporting_currency), 'horizons': rows}
