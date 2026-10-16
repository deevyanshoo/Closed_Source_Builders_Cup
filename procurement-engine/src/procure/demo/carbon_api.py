"""Current-plan carbon comparison and bounded assessment for the hosted demo."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from fastapi import Request

from procure.carbon import CarbonInputError, compare_plan, load_registry
from procure.carbon.assessment import build_assessment, current_assessment

from .sessions import SessionError


def current_carbon_assessment(value, active_plan, validate_plan_current=None) -> dict | None:
    """Return evidence only for this company's current plan and saved inputs."""
    stored = getattr(value, 'carbon_assessment', None)
    plan = active_plan(value)
    record = current_assessment(stored, company_id=value.company_id, plan=plan)
    if record is None:
        return None
    if validate_plan_current is not None:
        try:
            validate_plan_current(value, plan)
        except SessionError:
            return None
    return deepcopy(record)


def carbon_export(value, active_plan, validate_plan_current=None) -> dict | None:
    """Export the exact validated comparison shown by the current assessment."""
    record = current_carbon_assessment(value, active_plan, validate_plan_current)
    return None if record is None else deepcopy(record['result'])


def register_carbon_routes(app, *, session, mutate, body, shape, context, active_plan,
                           validate_plan_current=None, now=None):
    loaded = {}
    clock = now or (lambda: datetime.now(timezone.utc))

    def registry():
        # Loaded on first use so a missing factor file disables only these routes.
        if 'value' not in loaded:
            try:
                loaded['value'] = load_registry()
            except (OSError, ValueError, KeyError):
                raise SessionError('carbon_unavailable', 'Carbon factors are unavailable in this deployment.', 503) from None
        return loaded['value']

    @app.get('/api/demo/carbon/factors')
    async def carbon_factors(request: Request):
        session(request)
        return deepcopy(registry())

    @app.get('/api/demo/carbon/assessment')
    async def carbon_assessment(request: Request):
        value = session(request)
        with value.lock:
            return {'company_id': value.company_id, 'context_version': value.context_version,
                    'assessment': current_carbon_assessment(value, active_plan, validate_plan_current)}

    @app.post('/api/demo/carbon/estimate')
    async def carbon_estimate(request: Request):
        value = mutate(request)
        data = await body(request)
        shape(data, ('plan_id', 'legs_by_offer'), ('expected_context_version', 'mass_by_offer'))
        with value.lock:
            context(value, data)
            plan = active_plan(value)
            if plan is None or plan['plan_id'] != data['plan_id']:
                raise SessionError('stale_plan', 'The comparison changed. Run it again before estimating carbon.', 409)
            if validate_plan_current is not None:
                validate_plan_current(value, plan)
            try:
                result = compare_plan(registry(), plan, data['legs_by_offer'], data.get('mass_by_offer'),
                                      company_id=value.company_id)
                result['context_version'] = value.context_version
                record = build_assessment(company_id=value.company_id, context_version=value.context_version,
                                          plan=plan, comparison=result, legs_by_offer=data['legs_by_offer'],
                                          mass_by_offer=data.get('mass_by_offer'), created_at=clock().isoformat())
            except CarbonInputError as error:
                raise SessionError('invalid_carbon_input', str(error), 422) from None
            except (TypeError, ValueError, OverflowError, RecursionError) as error:
                raise SessionError('invalid_carbon_input', str(error), 422) from None
            value.carbon_assessment = record
            return result

