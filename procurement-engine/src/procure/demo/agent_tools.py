"""Deterministic, read-only facts and buyer-form boundaries for the demo operator."""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
import re


class ToolValidationError(ValueError):
    """A suggested utility argument is not supported by the trusted company data."""


BUYER_FIELDS = ('material', 'quantity', 'current_stock', 'quantity_unit', 'budget',
                'additional_cost_per_unit', 'decision_date', 'need_by', 'reporting_currency')
EDITABLE_FIELDS = ('quantity', 'current_stock', 'budget', 'additional_cost_per_unit', 'need_by')
_NUMERIC = re.compile(r'(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z')
_LABELS = {
    'quantity': r'(?:quantity|demand|order\s+quantity)',
    'current_stock': r'(?:current\s+stock|stock|on.hand\s+stock)',
    'budget': r'(?:budget|spending\s+limit)',
    'additional_cost_per_unit': r'(?:additional\s+cost(?:\s+per\s+unit)?|extra\s+cost(?:\s+per\s+unit)?)',
    'need_by': r'(?:need\s+by|deadline|required\s+by)',
}


def _number(value, *, positive=False):
    if not isinstance(value, str) or not _NUMERIC.fullmatch(value):
        raise ToolValidationError('Buyer amounts must be nonnegative decimal strings.')
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise ToolValidationError('Invalid buyer amount.') from None
    if not amount.is_finite() or amount > Decimal('1000000000000000') or (positive and amount <= 0):
        raise ToolValidationError('Buyer amount is outside supported bounds.')
    return amount


def _scenario(company, material_id):
    if not isinstance(company, dict) or not isinstance(material_id, str):
        raise ToolValidationError('Unknown company or material.')
    matches = [row for row in company.get('quote_coverage', {}).get('scenarios', [])
               if row.get('material_id') == material_id]
    materials = [row for row in company.get('materials', []) if row.get('material_id') == material_id]
    if len(matches) != 1 or len(materials) != 1:
        raise ToolValidationError('Material is not covered by this company.')
    return matches[0], materials[0]


def validate_buyer_draft(company: dict, data: dict) -> dict:
    """Return canonical PlanRequest buyer fields; never accept offers or kg aliases as authority."""
    if not isinstance(data, dict) or set(data) != set(BUYER_FIELDS):
        raise ToolValidationError('Buyer draft must contain only canonical buyer fields.')
    scenario, material = _scenario(company, data['material'])
    if data['quantity_unit'] != material['quantity_unit']:
        raise ToolValidationError('Buyer unit does not match the material catalogue.')
    if data['reporting_currency'] != company['reporting_currency']:
        raise ToolValidationError('Buyer currency does not match the company workspace.')
    _number(data['quantity'], positive=True)
    _number(data['current_stock'])
    _number(data['budget'])
    _number(data['additional_cost_per_unit'])
    try:
        decision = date.fromisoformat(data['decision_date'])
        need = date.fromisoformat(data['need_by'])
        if (decision.isoformat() != data['decision_date'] or
                need.isoformat() != data['need_by'] or need < decision):
            raise ValueError
    except (TypeError, ValueError):
        raise ToolValidationError('Buyer dates must be valid ISO dates in order.') from None
    if data['decision_date'] != scenario['buyer_defaults']['decision_date']:
        raise ToolValidationError('The fictional decision date is fixed for this scenario.')
    return deepcopy(data)


def _stated_value(message: str, field: str, value: str) -> bool:
    label = _LABELS[field]
    if field == 'need_by':
        pattern = rf'\b{label}\b\s*(?:(?:to|is|of|:|on)\s*)?{re.escape(value)}(?![0-9])'
    else:
        pattern = (rf'\b{label}\b\s*(?:(?:to|is|of|:|at)\s*)?'
                   rf'{re.escape(value)}(?![0-9.,])')
    return re.search(pattern, message, re.IGNORECASE) is not None


def prepare_form(company: dict, material_id: str, values: dict, message: str) -> dict:
    """Defaults plus only field-labelled explicit overrides from the user's message."""
    scenario, material = _scenario(company, material_id)
    if not isinstance(values, dict) or not set(values) <= set(EDITABLE_FIELDS):
        raise ToolValidationError('Only editable buyer fields may be suggested.')
    if not isinstance(message, str):
        raise ToolValidationError('A user request is required.')
    draft = {key: deepcopy(scenario['buyer_defaults'][key]) for key in BUYER_FIELDS}
    for field, value in values.items():
        if not isinstance(value, str) or not _stated_value(message, field, value):
            raise ToolValidationError('Suggested buyer value was not explicitly stated for its field.')
        if field != 'need_by':
            _number(value, positive=field == 'quantity')
        draft[field] = value
    if any(key in values for key in ('quantity', 'current_stock')):
        unit = material['quantity_unit']
        if not re.search(r'(?<!\w)' + re.escape(unit) + r'(?!\w)', message, re.IGNORECASE):
            raise ToolValidationError('The stated quantity must include its exact material unit.')
    if 'budget' in values and company['reporting_currency'] not in message.upper():
        raise ToolValidationError('The stated budget must include the workspace currency.')
    if 'additional_cost_per_unit' in values and (
            material['quantity_unit'] not in message or company['reporting_currency'] not in message.upper()):
        raise ToolValidationError('The stated extra cost must include unit and currency.')
    return validate_buyer_draft(company, draft)


def _decimal_or_none(raw):
    try:
        number = Decimal(str(raw))
    except (TypeError, ValueError, InvalidOperation):
        return None
    return number if number.is_finite() and number >= 0 else None


def _plain(number: Decimal) -> str:
    return format(number.normalize(), 'f')


def inventory_analytics(snapshot: dict) -> dict:
    """Per-material arithmetic only; never sum unlike units or call it a forecast."""
    company = snapshot['company']
    rows = []
    for material in company.get('materials', []):
        material_id = material['material_id']
        stock = _decimal_or_none(material.get('stock'))
        daily = _decimal_or_none(material.get('consumption_per_day'))
        leads = [supplier['lead_time_days'] for supplier in company.get('suppliers', [])
                 if material_id in supplier.get('materials', [])
                 and type(supplier.get('lead_time_days')) is int and supplier['lead_time_days'] >= 0]
        suppliers = [{'supplier_id': supplier['supplier_id'], 'name': supplier['name'],
                      'lead_time_days': supplier['lead_time_days'],
                      'settlement_currency': supplier['settlement_currency']}
                     for supplier in company.get('suppliers', [])
                     if material_id in supplier.get('materials', [])
                     and type(supplier.get('lead_time_days')) is int and supplier['lead_time_days'] >= 0]
        cover = None
        if stock is not None and daily is not None and daily > 0:
            with localcontext() as context:
                context.prec = 28
                cover = _plain(stock / daily)
        shortest = min(leads) if leads else None
        rows.append({'material_id': material_id, 'name': material['name'],
                     'unit': material['quantity_unit'], 'stock': material.get('stock'),
                     'consumption_per_day': material.get('consumption_per_day'),
                     'monthly_requirement': material.get('monthly_requirement'),
                     'stock_cover_days': cover, 'shortest_supplier_lead_days': shortest,
                     'suppliers': suppliers,
                     'stock_cover_at_or_below_lead': (Decimal(cover) <= shortest
                                                       if cover is not None and shortest is not None else None),
                     'synthetic': True})
    ranked = sorted((row for row in rows if row['stock_cover_days'] is not None),
                    key=lambda row: (Decimal(row['stock_cover_days']), row['material_id']))
    plan = snapshot.get('plan') or {}
    plan_metrics = None
    if plan:
        plan_metrics = {
            key: deepcopy(plan.get(key)) for key in
            ('plan_id', 'material', 'quantity_unit', 'required_purchase', 'reporting_currency',
             'selected_offer_id', 'total_cost', 'decision_date', 'need_by')}
        plan_metrics['alternatives'] = [{
            key: deepcopy(row.get(key)) for key in
            ('offer_id', 'supplier', 'feasible', 'reasons', 'arrival_date',
             'unit_price_per_unit', 'quantity_unit', 'quantity', 'total_cost')}
            for row in plan.get('alternatives', [])]
    return {'source': 'fictional_company_profile', 'as_of': company.get('as_of'),
            'company_id': company['company_id'], 'materials': rows,
            'materials_by_cover': ranked, 'current_plan': plan_metrics}
