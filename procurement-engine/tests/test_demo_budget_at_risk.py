"""Budget-at-risk API: bound to company, context version and the current plan id; read-only (#130)."""
from datetime import date
from decimal import Decimal
from threading import Lock
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from procure.demo.price_outlook import (current_plan, get_budget_at_risk, get_fx_outlook, get_price_outlook,
                                        outlook_export, price_outlook_router)

TODAY = date(2026, 10, 16)


def plan(material='copper', quantity='8000', unit='kg', currency='USD', plan_id='plan-1'):
    return {'plan_id': plan_id, 'material_id': material, 'quantity': quantity, 'unit': unit,
            'reporting_currency': currency}


def test_current_plan_reads_the_session_request_and_plan_id_without_changing_them():
    session = SimpleNamespace(last_plan={'plan_id': 'p-9', 'rows': []}, lock=Lock(),
                              plan_request={'material': 'wheat', 'quantity': '4020', 'quantity_unit': 'tonne',
                                            'reporting_currency': 'KES'})
    before = (dict(session.last_plan), dict(session.plan_request))
    assert current_plan(session) == plan('wheat', '4020', 'tonne', 'KES', 'p-9')
    assert (session.last_plan, session.plan_request) == before
    legacy = SimpleNamespace(last_plan={'plan_id': 'p-1'}, plan_request={'material': 'copper', 'quantity_kg': '8000',
                                                                         'reporting_currency': 'USD'})
    assert current_plan(legacy) == plan(plan_id='p-1')
    assert current_plan(SimpleNamespace(last_plan=None, plan_request=None)) is None


def test_shape_for_the_copper_plan():
    view = get_budget_at_risk('solara-cables', 'copper', plan=plan(), now=TODAY, context_version=3)
    assert view['kind'] == 'budget' and view['status'] == 'available'
    assert (view['company_id'], view['context_version'], view['plan_id'], view['material_id']) == \
        ('solara-cables', 3, 'plan-1', 'copper')
    assert Decimal(view['today']['amount']) == Decimal('114608')
    assert [h['horizon'] for h in view['horizons']] == [1, 3, 6]
    assert view['label'] == 'benchmark cost exposure, not a supplier quote, landed cost or recommendation'


def test_no_plan_or_a_plan_for_another_material_gives_no_figure():
    for current in (None, plan(material='aluminium')):
        view = get_budget_at_risk('solara-cables', 'copper', plan=current, now=TODAY, context_version=1)
        assert view['status'] == 'no_current_plan' and view['reason'] == 'Run a comparison first.'
        assert view['plan_id'] is None and 'today' not in view and view['horizons'] == []


def test_a_stale_plan_id_after_rerunning_the_comparison_gives_no_figure():
    view = get_budget_at_risk('solara-cables', 'copper', plan=plan(plan_id='plan-2'), now=TODAY, context_version=1,
                              expected_plan_id='plan-1')
    assert view['status'] == 'no_current_plan' and view['plan_id'] is None and 'today' not in view
    assert 'comparison changed' in view['reason']


def test_unit_series_fx_and_company_paths():
    oil = get_budget_at_risk('harvest-foods', 'oil', plan=plan('oil', '61500', 'litre', 'KES'), now=TODAY,
                             context_version=1)
    assert oil['status'] == 'unit_basis_required' and 'density' in oil['reason']
    wheat = get_budget_at_risk('harvest-foods', 'wheat', plan=plan('wheat', '4020', 'tonne', 'KES'), now=TODAY,
                               context_version=1)
    assert wheat['status'] == 'available' and wheat['fx']['quote'] == 'KES per USD'
    assert Decimal(wheat['today']['amount']) == Decimal('4020') * Decimal('330.0') * Decimal(wheat['fx']['rate'])
    polymer = get_budget_at_risk('solara-cables', 'polymer', plan=plan('polymer'), now=TODAY, context_version=1)
    assert polymer['status'] == 'no_series'
    tzs = get_budget_at_risk('harvest-foods', 'wheat', plan=plan('wheat', '4', 'tonne', 'TZS'), now=TODAY,
                             context_version=1)
    assert tzs['status'] == 'fx_unavailable'
    assert get_budget_at_risk('nobody', 'copper', plan=plan(), now=TODAY, context_version=1)['status'] == 'unknown_company'
    other = get_budget_at_risk('terrapack', 'copper', plan=plan(), now=TODAY, context_version=1)
    assert other['status'] == 'unknown_material' and 'today' not in other
    bad = get_budget_at_risk('solara-cables', 'copper', plan=plan(quantity='-5'), now=TODAY, context_version=1)
    assert bad['status'] == 'invalid_quantity' and 'today' not in bad


def test_router_follows_the_session_company_plan_and_context_a_to_b_to_a():
    state = SimpleNamespace(company_id='solara-cables', context_version=1, lock=Lock(),
                            last_plan={'plan_id': 'a-1'},
                            plan_request={'material': 'copper', 'quantity': '8000', 'quantity_unit': 'kg',
                                          'reporting_currency': 'USD'})
    app = FastAPI()

    def session(request: Request):
        return state
    app.include_router(price_outlook_router(session=session, now=lambda: TODAY))
    with TestClient(app) as client:
        def get(**params):
            return client.get('/api/demo/budget-at-risk', params={'material_id': 'copper', **params}).json()
        first = get(plan_id='a-1')
        state.company_id, state.context_version, state.last_plan, state.plan_request = 'harvest-foods', 2, None, None
        second = get(plan_id='a-1')
        state.company_id, state.context_version = 'solara-cables', 3
        third = get()
        state.last_plan = {'plan_id': 'a-2'}
        state.plan_request = {'material': 'copper', 'quantity': '10000', 'quantity_unit': 'kg', 'reporting_currency': 'USD'}
        stale = get(plan_id='a-1')
        fresh = get(plan_id='a-2')
    assert (first['company_id'], first['context_version'], first['plan_id'], first['status']) == \
        ('solara-cables', 1, 'a-1', 'available')
    assert (second['company_id'], second['context_version'], second['status']) == ('harvest-foods', 2, 'no_current_plan')
    assert (third['company_id'], third['status'], third['plan_id']) == ('solara-cables', 'no_current_plan', None)
    assert stale['status'] == 'no_current_plan' and 'today' not in stale
    assert fresh['plan_id'] == 'a-2' and Decimal(fresh['today']['amount']) == Decimal('143260')


def test_export_carries_the_budget_with_the_same_scope():
    price = get_price_outlook('solara-cables', 'copper', now=TODAY, context_version=4)
    fx = get_fx_outlook('solara-cables', now=TODAY, context_version=4)
    budget = get_budget_at_risk('solara-cables', 'copper', plan=plan(), now=TODAY, context_version=4)
    exported = outlook_export(price, fx, budget)
    assert exported['budget']['plan_id'] == 'plan-1' and exported['budget']['today']['amount'] == budget['today']['amount']
    assert outlook_export(price, fx)['budget'] is None
    other = get_budget_at_risk('solara-cables', 'copper', plan=plan(), now=TODAY, context_version=5)
    try:
        outlook_export(price, fx, other)
    except ValueError as error:
        assert 'scope' in str(error)
    else:
        raise AssertionError('mixed-context budget accepted')
