"""Read-only carbon estimate routes for the hosted demo, kept out of app.py."""
from __future__ import annotations

from copy import deepcopy

from fastapi import Request

from procure.carbon import CarbonInputError, compare_plan, load_registry

from .sessions import SessionError


def carbon_export(value, active_plan) -> dict | None:
    """The stored estimate only while it still describes this company's active plan."""
    stored = getattr(value, 'carbon_estimate', None)
    plan = active_plan(value)
    if not stored or plan is None or stored['company_id'] != value.company_id or stored['plan_id'] != plan.get('plan_id'):
        return None
    return deepcopy(stored['result'])


def register_carbon_routes(app, *, session, mutate, body, shape, context, active_plan):
    registry = load_registry()

    @app.get('/api/demo/carbon/factors')
    async def carbon_factors(request: Request):
        session(request)
        return deepcopy(registry)

    @app.post('/api/demo/carbon/estimate')
    async def carbon_estimate(request: Request):
        value = mutate(request)
        data = await body(request)
        shape(data, ('plan_id', 'legs_by_offer'), ('expected_context_version',))
        with value.lock:
            context(value, data)
            plan = active_plan(value)
            if plan is None or plan['plan_id'] != data['plan_id']:
                raise SessionError('stale_plan', 'The comparison changed. Run it again before estimating carbon.', 409)
            try:
                result = compare_plan(registry, plan, data['legs_by_offer'])
            except CarbonInputError as error:
                raise SessionError('invalid_carbon_input', str(error), 422) from None
            value.carbon_estimate = {'company_id': value.company_id, 'plan_id': plan['plan_id'],
                                     'result': deepcopy(result)}
            return result

