"""Pure paired comparison over the existing synthetic, decimal planner."""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal, localcontext

from procure.offers import normalize as nz

from .planner import build_plan
from .company import get_company
from .supply_coverage import assess_supply_coverage


def compare(baseline: dict, original_request: dict, offers: list[dict], delays: dict,
            *, today: date) -> dict:
    scenario_request = deepcopy(original_request)
    combined_delays = deepcopy(original_request.get("delay_days_by_offer") or {})
    combined_delays.update(deepcopy(delays))
    scenario_request["delay_days_by_offer"] = combined_delays
    scenario = build_plan(deepcopy(offers), scenario_request, today=today)
    if baseline["selected_offer_id"] is None or scenario["selected_offer_id"] is None:
        cost_delta = None
    else:
        with localcontext() as context:
            context.prec = nz.ARITHMETIC_PRECISION
            cost_delta = nz.fmt(Decimal(scenario["total_cost"]) - Decimal(baseline["total_cost"]))
    changed = baseline["selected_offer_id"] != scenario["selected_offer_id"]
    if scenario["required_purchase"] == "0":
        outcome = "stock_covers_need"
    elif scenario["selected_offer_id"] is None:
        outcome = "infeasible"
    elif changed:
        outcome = "changed"
    else:
        outcome = "unchanged"
    return with_recovery_coverage(get_company(baseline.get('company_id')), {"synthetic": True, "baseline": deepcopy(baseline), "scenario": scenario,
            "baseline_request": deepcopy(original_request), "scenario_request": scenario_request,
            "cost_delta": cost_delta, "selection_changed": changed, "outcome": outcome,
            "replayed": False}, offers=offers)


def with_recovery_coverage(company: dict, comparison: dict, *, offers: list[dict]) -> dict:
    """Keep both projections bound to the final persisted plan identities."""
    result = deepcopy(comparison)
    for role in ('baseline', 'scenario'):
        coverage = assess_supply_coverage(company, result[role], offers=offers)
        result[role]['supply_coverage'] = deepcopy(coverage)
        result[f'{role}_supply_coverage'] = coverage
    return result
