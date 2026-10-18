"""Frozen, local copper×FX procurement proxy; no executable prices or orders.

Each input row is one selected held-out forecast. A complete decision requires
COPPER and one FX row on the same issue date, target date, horizon, and mode.
The observed target closes are used only for retrospective scoring, never for
the ex-ante choice. The result is a synthetic counterfactual, not savings.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path


HERE = Path(__file__).resolve().parent
CONSTANTS = HERE / "decision_constants.json"
FX_ASSETS = ("EURUSD", "GBPUSD", "USDJPY")
ACTIONS = ("BUY_NOW", "SPLIT", "WAIT")


def load_constants():
    values = json.loads(CONSTANTS.read_text())
    if values["schema"] != "hybrid-daily-decision-simulation.v1":
        raise ValueError("unexpected decision constants schema")
    return values


def domestic_per_usd(asset, quoted_close):
    close = float(quoted_close)
    if not math.isfinite(close) or close <= 0:
        raise ValueError("FX close must be positive and finite")
    if asset in ("EURUSD", "GBPUSD"):
        return 1.0 / close
    if asset == "USDJPY":
        return close
    raise ValueError(f"unsupported FX orientation: {asset}")


def landed_unit(copper_usd_per_lb, domestic_fx, terms):
    copper = float(copper_usd_per_lb)
    fx = float(domestic_fx)
    if not all(math.isfinite(value) and value > 0 for value in (copper, fx)):
        raise ValueError("price and FX must be positive and finite")
    commodity_usd_per_ton = copper * terms["pounds_per_metric_ton"]
    usd_per_ton = commodity_usd_per_ton * (1 + terms["duty_rate"])
    usd_per_ton += terms["shipping_usd_per_metric_ton"]
    return usd_per_ton * fx * (1 + terms["fx_spread_bps"] / 10000)


def purchase_cost(quantity_tons, copper_usd_per_lb, purchase_fx, issue_fx, terms):
    quantity = float(quantity_tons)
    if not math.isfinite(quantity) or quantity < 0:
        raise ValueError("quantity must be finite and nonnegative")
    if quantity == 0:
        return 0.0
    return quantity * landed_unit(copper_usd_per_lb, purchase_fx, terms) + (
        terms["order_fee_usd_converted_at_issue_fx"] * issue_fx)


def _calendar(issue, target, terms):
    if target <= issue:
        raise ValueError("target must follow issue")
    arrival = target + timedelta(days=terms["lead_time_calendar_days"])
    end = arrival + timedelta(days=terms["calendar_days_after_target_arrival"])
    days = [issue + timedelta(days=offset)
            for offset in range(1, (end - issue).days + 1)]
    return arrival, days


def _opening_inventory(issue, target, scenario, terms):
    if scenario == "service_stress":
        return terms["stress_opening_cover_calendar_days"] * terms["daily_demand_metric_tons"]
    if scenario == "ample_cover":
        arrival, _ = _calendar(issue, target, terms)
        pre_arrival_days = (arrival - issue).days - 1
        return ((pre_arrival_days + terms["base_opening_safety_stock_calendar_days"])
                * terms["daily_demand_metric_tons"])
    raise ValueError(f"unknown scenario: {scenario}")


def _simulate_action(action, issue, target, issue_copper, issue_fx,
                     target_copper, target_fx, scenario, terms):
    if action not in ACTIONS:
        raise ValueError("unknown action")
    target_arrival, days = _calendar(issue, target, terms)
    opening = _opening_inventory(issue, target, scenario, terms)
    quantity = terms["quantity_metric_tons"]
    fraction = terms["actions"][action]
    now_quantity, later_quantity = quantity * fraction, quantity * (1 - fraction)
    issue_arrival = issue + timedelta(days=terms["lead_time_calendar_days"])
    purchases = purchase_cost(now_quantity, issue_copper, issue_fx, issue_fx, terms)
    purchases += purchase_cost(later_quantity, target_copper, target_fx, issue_fx, terms)
    issue_unit = landed_unit(issue_copper, issue_fx, terms)
    inventory = float(opening)
    unmet = 0.0
    stockout_days = 0
    carry = 0.0
    maximum_inventory = inventory
    daily_demand = terms["daily_demand_metric_tons"]
    for day in days:
        if day == issue_arrival:
            inventory += now_quantity
        if day == target_arrival:
            inventory += later_quantity
        maximum_inventory = max(maximum_inventory, inventory)
        missing = max(0.0, daily_demand - inventory)
        if missing > 0:
            stockout_days += 1
            unmet += missing
        inventory = max(0.0, inventory - daily_demand)
        carry += inventory * issue_unit * terms["annual_carry_rate"] / 365
    shortage = unmet * issue_unit * terms["shortage_penalty_issue_landed_unit_multiplier"]
    total = purchases + carry + shortage
    reasons = []
    if unmet > 0:
        reasons.append("SERVICE_SHORTAGE")
    if maximum_inventory > terms["storage_capacity_metric_tons"]:
        reasons.append("STORAGE_CAPACITY")
    if total > terms["cash_budget_domestic"]:
        reasons.append("CASH_BUDGET")
    return {"action": action, "feasible": not reasons, "reasons": reasons,
            "cost_domestic": total, "purchase_cost_domestic": purchases,
            "carry_cost_domestic": carry, "shortage_cost_domestic": shortage,
            "unmet_tons": unmet, "stockout_days": stockout_days,
            "ending_inventory_tons": inventory, "maximum_inventory_tons": maximum_inventory,
            "evaluation_days": len(days), "end_date": days[-1].isoformat(),
            "target_arrival_date": target_arrival.isoformat()}


def evaluate_action_set(issue, target, fx_asset, issue_copper_close,
                        issue_fx_close, target_copper_close, target_fx_close,
                        scenario, terms):
    issue_fx = domestic_per_usd(fx_asset, issue_fx_close)
    target_fx = domestic_per_usd(fx_asset, target_fx_close)
    opening = _opening_inventory(issue, target, scenario, terms)
    _, days = _calendar(issue, target, terms)
    result = {"scenario": scenario, "domestic_currency": fx_asset[:3] if fx_asset != "USDJPY" else "JPY",
              "opening_inventory_tons": opening,
              "demand_total_tons": len(days) * terms["daily_demand_metric_tons"],
              "ordered_quantity_tons": terms["quantity_metric_tons"],
              "issue_unit_landed_domestic_per_ton": landed_unit(issue_copper_close, issue_fx, terms),
              "target_unit_landed_domestic_per_ton": landed_unit(target_copper_close, target_fx, terms)}
    for action in ACTIONS:
        result[action] = _simulate_action(action, issue, target, issue_copper_close,
                                          issue_fx, target_copper_close, target_fx,
                                          scenario, terms)
    return result


def _as_bool(value):
    if isinstance(value, bool):
        return value
    if value in ("True", "true", "1"):
        return True
    if value in ("False", "false", "0"):
        return False
    raise ValueError("invalid boolean prediction field")


def _check_row(row, expected_asset):
    if row["asset"] != expected_asset:
        raise ValueError("mismatched asset")
    values = {}
    for name in ("anchor_close", "target_close", "predicted_return", "confidence"):
        values[name] = float(row[name])
        if not math.isfinite(values[name]):
            raise ValueError("nonfinite prediction field")
    if values["anchor_close"] <= 0 or values["target_close"] <= 0:
        raise ValueError("nonpositive observed close")
    if not 0 <= values["confidence"] <= 1 or values["predicted_return"] <= -1:
        raise ValueError("invalid predicted confidence or return")
    values["abstained"] = _as_bool(row["abstained"])
    if row["predicted_direction"] not in ("UP", "DOWN"):
        raise ValueError("invalid classifier direction")
    values["predicted_direction"] = row["predicted_direction"]
    return values


def evaluate_pair(copper_row, fx_row, scenario, terms):
    fx_asset = fx_row["asset"]
    if fx_asset not in FX_ASSETS:
        raise ValueError("unsupported FX asset")
    for key in ("anchor_date", "target_date", "horizon", "mode"):
        if copper_row[key] != fx_row[key]:
            raise ValueError(f"unpaired {key}")
    issue, target = date.fromisoformat(copper_row["anchor_date"]), date.fromisoformat(copper_row["target_date"])
    cu = _check_row(copper_row, "COPPER")
    fx = _check_row(fx_row, fx_asset)
    predicted = evaluate_action_set(
        issue, target, fx_asset, cu["anchor_close"], fx["anchor_close"],
        cu["anchor_close"] * (1 + cu["predicted_return"]),
        fx["anchor_close"] * (1 + fx["predicted_return"]), scenario, terms)
    realized = evaluate_action_set(
        issue, target, fx_asset, cu["anchor_close"], fx["anchor_close"],
        cu["target_close"], fx["target_close"], scenario, terms)
    key = {"scenario": scenario, "mode": copper_row["mode"], "fx_asset": fx_asset,
           "horizon": int(copper_row["horizon"]), "issue_date": issue.isoformat(),
           "target_date": target.isoformat(), "price_basis": terms["price_basis"]}
    out = {**key, "predicted": predicted, "realized": realized,
           "model_action": None, "model_savings_vs_controls": None,
           "oracle_regret_domestic": None, "operational_fallback": "SPLIT",
           "fallback_feasible": realized["SPLIT"]["feasible"],
           "operational_action": "SPLIT",
           "operational_cost_domestic": realized["SPLIT"]["cost_domestic"],
           "operational_service_unmet_tons": realized["SPLIT"]["unmet_tons"],
           "operational_feasible": realized["SPLIT"]["feasible"],
           "predicted_benefit_bps": None,
           "model_decision_cost_domestic": None,
           "model_service_unmet_tons": None}
    feasible = [name for name in ACTIONS if predicted[name]["feasible"]]
    if len(feasible) == 1:
        only = feasible[0]
        out["status"] = "SERVICE_RULE_ACTION"
        out["service_rule_basis"] = "ONLY_ONE_EX_ANTE_FEASIBLE_ACTION_NO_MODEL_EDGE"
        out["operational_action"] = only
        out["operational_cost_domestic"] = realized[only]["cost_domestic"]
        out["operational_service_unmet_tons"] = realized[only]["unmet_tons"]
        out["operational_feasible"] = realized[only]["feasible"]
        return out
    if cu["abstained"] or fx["abstained"]:
        out["status"] = "ABSTAIN_LOW_CONFIDENCE"
        return out
    if any((item["predicted_direction"] == "UP") != (item["predicted_return"] > 0)
           for item in (cu, fx)):
        out["status"] = "ABSTAIN_SIGN_DISAGREEMENT"
        return out
    if not feasible:
        out["status"] = "ABSTAIN_NO_FEASIBLE_ACTION"
        return out
    if not predicted["SPLIT"]["feasible"]:
        out["status"] = "ABSTAIN_SPLIT_BASELINE_INFEASIBLE"
        return out
    best = min(feasible, key=lambda name: (predicted[name]["cost_domestic"], ACTIONS.index(name)))
    issue_purchase = terms["quantity_metric_tons"] * predicted["issue_unit_landed_domestic_per_ton"]
    benefit = ((predicted["SPLIT"]["cost_domestic"] - predicted[best]["cost_domestic"])
               / issue_purchase * 10000)
    out["predicted_benefit_bps"] = benefit
    if best == "SPLIT" or benefit < terms["minimum_predicted_benefit_bps_of_issue_purchase"]:
        out["status"] = "ABSTAIN_NO_PREDICTED_EDGE"
        return out
    out["status"] = "MODEL_PROXY_ACTION"
    out["model_action"] = best
    chosen = realized[best]
    out["operational_action"] = best
    out["operational_cost_domestic"] = chosen["cost_domestic"]
    out["operational_service_unmet_tons"] = chosen["unmet_tons"]
    out["operational_feasible"] = chosen["feasible"]
    out["model_decision_cost_domestic"] = chosen["cost_domestic"]
    out["model_service_unmet_tons"] = chosen["unmet_tons"]
    if chosen["feasible"]:
        out["model_savings_vs_controls"] = {
            name: (realized[name]["cost_domestic"] - chosen["cost_domestic"])
            if realized[name]["feasible"] else None for name in ACTIONS}
        oracle = min(realized[name]["cost_domestic"] for name in ACTIONS
                     if realized[name]["feasible"])
        out["oracle_regret_domestic"] = chosen["cost_domestic"] - oracle
    else:
        out["status"] = "MODEL_ACTION_REALIZED_INFEASIBLE"
    return out


def evaluate_rows(rows, terms):
    groups = defaultdict(dict)
    for row in rows:
        key = tuple(row[field] for field in ("mode", "horizon", "anchor_date", "target_date"))
        if row["asset"] in groups[key]:
            raise ValueError(f"duplicate asset forecast: {key}, {row['asset']}")
        groups[key][row["asset"]] = row
    output = []
    missing = []
    for key, assets in sorted(groups.items()):
        if "COPPER" not in assets:
            missing.append({"key": key, "reason": "NO_MATCHED_COPPER"})
            continue
        for fx_asset in FX_ASSETS:
            if fx_asset not in assets:
                missing.append({"key": key, "fx_asset": fx_asset, "reason": "NO_MATCHED_FX"})
                continue
            for scenario in ("ample_cover", "service_stress"):
                output.append(evaluate_pair(assets["COPPER"], assets[fx_asset], scenario, terms))
    return output, missing


def summarize(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[(row["scenario"], row["mode"], row["fx_asset"], row["horizon"])].append(row)
    output = []
    for key, group in sorted(groups.items()):
        acted = [row for row in group if row["model_action"] is not None]
        valid = [row for row in acted if row["model_savings_vs_controls"] is not None]
        paired = {}
        whole_policy = {}
        for action in ACTIONS:
            savings = [row["model_savings_vs_controls"][action] for row in valid
                       if row["model_savings_vs_controls"][action] is not None]
            valid_active = [row for row in valid if row["realized"][action]["feasible"]
                            and row["realized"][action]["cost_domestic"] > 0]
            paired[action] = {"n": len(savings),
                              "mean_simulated_cost_delta_domestic": sum(savings) / len(savings) if savings else None,
                              "mean_cost_saving_percent": (sum(
                                  100 * (row["realized"][action]["cost_domestic"] -
                                         row["operational_cost_domestic"])
                                  / row["realized"][action]["cost_domestic"]
                                  for row in valid_active) / len(valid_active)) if valid_active else None}
            matched_all = [row for row in group if row["operational_feasible"]
                           and row["realized"][action]["feasible"]
                           and row["realized"][action]["cost_domestic"] > 0]
            whole_policy[action] = {
                "n": len(matched_all),
                "mean_cost_saving_percent": (sum(
                    100 * (row["realized"][action]["cost_domestic"] -
                           row["operational_cost_domestic"])
                    / row["realized"][action]["cost_domestic"]
                    for row in matched_all) / len(matched_all)) if matched_all else None,
                "excluded_policy_infeasible": sum(not row["operational_feasible"] for row in group),
                "excluded_control_infeasible": sum(not row["realized"][action]["feasible"] for row in group),
            }
        output.append({"scenario": key[0], "mode": key[1], "fx_asset": key[2],
                       "horizon": key[3], "eligible_joint_dates": len(group),
                       "model_action_count": len(acted),
                       "model_action_coverage": len(acted) / len(group),
                       "realized_feasible_model_action_count": len(valid),
                       "status_counts": dict(Counter(row["status"] for row in group)),
                       "all_fixed_actions_feasible_count": sum(
                           all(row["realized"][action]["feasible"] for action in ACTIONS)
                           for row in group),
                       "whole_policy_pairwise_vs_fixed": whole_policy,
                       "whole_policy_feasible_count": sum(row["operational_feasible"] for row in group),
                       "whole_policy_unmet_tons": sum(row["operational_service_unmet_tons"] for row in group),
                       "non_model_action_count": sum(row["model_action"] is None for row in group),
                       "fallback_split_count": sum(
                           row["model_action"] is None and row["operational_action"] == "SPLIT"
                           for row in group),
                       "fallback_split_infeasible_count": sum(
                           row["model_action"] is None and row["operational_action"] == "SPLIT"
                           and not row["operational_feasible"] for row in group),
                       "paired_simulated_cost_delta_vs_fixed": paired,
                       "mean_oracle_regret_domestic": (sum(row["oracle_regret_domestic"] for row in valid)
                                                         / len(valid) if valid else None),
                       "total_model_unmet_tons": sum(row["model_service_unmet_tons"] or 0 for row in acted),
                       "label": "SIMULATED_COUNTERFACTUAL_PROXY_ONLY"})
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--predictions", required=True, help="selected held-out forecast CSV")
    parser.add_argument("--output-dir", default=str(HERE))
    args = parser.parse_args()
    with open(args.predictions, newline="") as stream:
        rows = list(csv.DictReader(stream))
    terms = load_constants()
    decisions, missing = evaluate_rows(rows, terms)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / "business_decisions.json").write_text(json.dumps(decisions, indent=2, allow_nan=False) + "\n")
    (output / "business_summary.json").write_text(json.dumps({
        "schema": "hybrid-daily-decision-results.v1",
        "constants_sha256": hashlib.sha256(CONSTANTS.read_bytes()).hexdigest(),
        "predictions_sha256": hashlib.sha256(Path(args.predictions).read_bytes()).hexdigest(),
        "joint_decision_count": len(decisions), "unmatched_groups": missing,
        "summary": summarize(decisions),
        "limits": ["Simulated rolling futures and daily FX closes, no executable supplier price or realized saving.",
                   "Service uses fixed hypothetical demand, inventory and lead days; supplier arrival is unobserved.",
                   "News-mode decisions are retrospective semantic ablations without verified point-in-time feature versions."]
    }, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"joint_decisions": len(decisions), "unmatched_groups": len(missing),
                      "summary_groups": len(summarize(decisions))}))


if __name__ == "__main__":
    main()
