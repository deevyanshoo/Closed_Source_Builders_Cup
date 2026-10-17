"""Deterministic boundaries for the fictional operator's buyer and inventory tools."""
from copy import deepcopy

import pytest

from procure.demo.company import get_company
from procure.demo.agent_tools import (ToolValidationError, inventory_analytics,
                                      prepare_form, validate_buyer_draft)


def test_form_uses_material_defaults_and_only_explicit_same_unit_overrides():
    company = get_company('loomworks')
    defaults = next(s['buyer_defaults'] for s in company['quote_coverage']['scenarios']
                    if s['material_id'] == 'cotton')
    draft = prepare_form(company, 'cotton', {'quantity': '1200', 'budget': '950000'},
                         'Set cotton quantity to 1200 bale and budget to 950000 BDT')
    assert draft['quantity'] == '1200' and draft['budget'] == '950000'
    assert draft['quantity_unit'] == 'bale' and draft['material'] == 'cotton'
    assert draft['current_stock'] == defaults['current_stock']
    assert 'quantity_kg' not in draft
    assert validate_buyer_draft(company, draft) == draft


def test_form_rejects_foreign_material_wrong_unit_and_invented_values():
    company = get_company('loomworks')
    with pytest.raises(ToolValidationError):
        prepare_form(company, 'copper', {}, 'Fill copper form')
    with pytest.raises(ToolValidationError):
        prepare_form(company, 'cotton', {'quantity': '1200'}, 'Fill cotton form')
    with pytest.raises(ToolValidationError):
        prepare_form(company, 'cotton', {'quantity_unit': 'kg'}, 'Set cotton unit to kg')
    with pytest.raises(ToolValidationError):
        prepare_form(company, 'cotton', {'budget': '20'}, 'Set quantity to 20 bale')
    draft = prepare_form(company, 'cotton', {}, 'Fill the cotton form')
    wrong = deepcopy(draft)
    wrong['reporting_currency'] = 'USD'
    with pytest.raises(ToolValidationError):
        validate_buyer_draft(company, wrong)


def test_inventory_analytics_preserves_units_and_ranks_cover_without_forecasting():
    company = get_company('solara-cables')
    result = inventory_analytics({'company': company, 'company_id': company['company_id'],
                                  'plan': None, 'offers': []})
    copper = next(row for row in result['materials'] if row['material_id'] == 'copper')
    assert copper['unit'] == 'kg' and copper['stock'] == '2000'
    assert copper['monthly_requirement'] == '10000'
    assert copper['stock_cover_days']
    assert copper['shortest_supplier_lead_days'] == 10
    assert result['materials_by_cover'][0]['material_id'] == 'copper'
    assert result['source'] == 'fictional_company_profile'
    assert 'total_stock' not in result


def test_analytics_keeps_current_plan_cost_and_alternatives_in_their_own_currency():
    company = get_company('solara-cables')
    plan = {'plan_id': 'plan-test', 'material': 'copper', 'quantity_unit': 'kg',
            'required_purchase': '8000', 'reporting_currency': 'USD',
            'selected_offer_id': 'eu-copper', 'total_cost': '86000',
            'decision_date': '2026-10-14', 'need_by': '2026-11-05',
            'alternatives': [{'offer_id': 'eu-copper', 'supplier': 'Europa Copper SA [SYNTHETIC]',
                              'feasible': True, 'reasons': [], 'arrival_date': '2026-10-24',
                              'unit_price_per_unit': '10', 'quantity_unit': 'kg',
                              'quantity': '8000', 'total_cost': '86000'}]}
    result = inventory_analytics({'company': company, 'plan': plan})
    assert result['current_plan']['total_cost'] == '86000'
    assert result['current_plan']['reporting_currency'] == 'USD'
    assert result['current_plan']['alternatives'][0]['arrival_date'] == '2026-10-24'
    assert result['current_plan']['alternatives'][0]['quantity_unit'] == 'kg'
