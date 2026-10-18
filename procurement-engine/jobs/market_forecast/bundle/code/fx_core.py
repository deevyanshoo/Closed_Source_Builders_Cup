"""Chronological features and economically oriented direction labels.

Research-only. The raw-quote forecast interface retains the earlier model
contract while the training label reflects the domestic USD payable direction.
"""

from __future__ import annotations

import bisect
import math
import statistics
from datetime import date


ASSETS = ("EURUSD", "GBPUSD", "USDJPY", "COPPER")
FX_INVERSE = ("EURUSD", "GBPUSD")


def economic_cost_rise(asset, issue_close, target_close):
    if asset not in ASSETS:
        raise ValueError("unknown asset")
    if not all(math.isfinite(float(x)) and float(x) > 0
               for x in (issue_close, target_close)):
        raise ValueError("positive finite closes required")
    if asset in FX_INVERSE:
        return int(float(target_close) < float(issue_close))
    return int(float(target_close) > float(issue_close))


def domestic_cost_return(asset, issue_close, target_close):
    economic_cost_rise(asset, issue_close, target_close)
    if asset in FX_INVERSE:
        return float(issue_close) / float(target_close) - 1
    return float(target_close) / float(issue_close) - 1


def training_cost_weights(training_domestic_returns):
    """Train-only clipped relative move weights, normalized to mean one."""
    values = [abs(float(value)) for value in training_domestic_returns]
    if not values or any(not math.isfinite(value) for value in values):
        raise ValueError("finite nonempty training returns required")
    median = statistics.median(values)
    if median <= 0:
        return [1.0] * len(values)
    weights = [min(4.0, max(0.25, value / median)) for value in values]
    average = statistics.mean(weights)
    return [value / average for value in weights]


def raw_quote_prob_up(asset, economic_probability):
    probability = float(economic_probability)
    if asset not in ASSETS or not 0 <= probability <= 1:
        raise ValueError("asset/probability invalid")
    return 1 - probability if asset in FX_INVERSE else probability


def _log_return(closes, i, width):
    return math.log(closes[i] / closes[i - width]) if i >= width else 0.0


def _corr(a, b):
    if len(a) < 5:
        return 0.0
    am, bm = sum(a) / len(a), sum(b) / len(b)
    av = sum((x - am) ** 2 for x in a)
    bv = sum((x - bm) ** 2 for x in b)
    if av <= 1e-20 or bv <= 1e-20:
        return 0.0
    return sum((x-am)*(y-bm) for x, y in zip(a, b)) / math.sqrt(av*bv)


def _past_return_map(rows, anchor):
    eligible = [(day, close) for day, close in rows if day < anchor]
    return {eligible[i][0]: math.log(eligible[i][1] / eligible[i-1][1])
            for i in range(1, len(eligible))}


def feature_row(asset, anchor, series):
    if asset not in ASSETS:
        raise ValueError("unsupported asset")
    own = series[asset]
    dates = [day for day, _ in own]
    index = bisect.bisect_left(dates, anchor)
    if index >= len(own) or own[index][0] != anchor or index < 25:
        raise ValueError("anchor absent or insufficient own history")
    closes = [close for _, close in own[:index+1]]
    daily = [math.log(closes[j] / closes[j-1]) for j in range(index-19, index+1)]
    vol20 = math.sqrt(sum((x - sum(daily)/20) ** 2 for x in daily) / 20)
    positive = math.sqrt(sum(max(x, 0.0) ** 2 for x in daily) / 20)
    negative = math.sqrt(sum(min(x, 0.0) ** 2 for x in daily) / 20)
    log5 = _log_return(closes, index, 5)
    log20 = _log_return(closes, index, 20)
    result = {f"ret_{width}": _log_return(closes, index, width)
              for width in (1, 2, 3, 5, 10, 20)}
    result.update({
        "vol_20": vol20, "up_semivol_20": positive,
        "down_semivol_20": negative,
        "norm_momentum_5": log5 / max(vol20 * math.sqrt(5), 1e-6),
        "norm_momentum_20": log20 / max(vol20 * math.sqrt(20), 1e-6),
        "norm_reversion_1_5": (-result["ret_1"] + log5 / 5) / max(vol20, 1e-6),
        "trend_efficiency_20": abs(log20) / max(sum(abs(x) for x in daily), 1e-9),
        "trend_consistency_20": sum(x > 0 for x in daily) / 20,
        "ma_gap_5": closes[index] / (sum(closes[index-4:index+1]) / 5) - 1,
        "ma_gap_20": closes[index] / (sum(closes[index-19:index+1]) / 20) - 1,
    })
    for weekday in range(5):
        result[f"weekday_{weekday}"] = float(anchor.weekday() == weekday)
    own_past = _past_return_map(own, anchor)
    prior_usd = []
    for other in ASSETS:
        if other == asset:
            continue
        rows = series[other]
        other_dates = [day for day, _ in rows]
        pos = bisect.bisect_left(other_dates, anchor) - 1
        present = pos >= 5
        prefix = f"cross_{other}_"
        if not present:
            result.update({prefix + "present": 0.0, prefix + "age_days": 99.0,
                           prefix + "ret_1": 0.0, prefix + "ret_5": 0.0,
                           prefix + "corr20": 0.0})
            continue
        other_closes = [close for _, close in rows[:pos+1]]
        result[prefix + "present"] = 1.0
        result[prefix + "age_days"] = float((anchor - rows[pos][0]).days)
        result[prefix + "ret_1"] = _log_return(other_closes, pos, 1)
        result[prefix + "ret_5"] = _log_return(other_closes, pos, 5)
        other_past = _past_return_map(rows, anchor)
        paired = sorted(set(own_past) & set(other_past))[-20:]
        result[prefix + "corr20"] = _corr([own_past[day] for day in paired],
                                           [other_past[day] for day in paired])
        if other in FX_INVERSE:
            prior_usd.append(-result[prefix + "ret_1"])
        elif other == "USDJPY":
            prior_usd.append(result[prefix + "ret_1"])
    result["strict_prior_usd_factor"] = sum(prior_usd) / len(prior_usd) if prior_usd else 0.0
    if not all(math.isfinite(value) for value in result.values()):
        raise ValueError("nonfinite feature")
    return result


def purged_fold(rows, start, end):
    train = [row for row in rows if row["target_date"] < start]
    valid = [row for row in rows if start <= row["anchor_date"] <= end
             and row["target_date"] <= end]
    return train, valid


def prospective_forecast_row(asset, anchor, horizon, anchor_close,
                             economic_probability, mode):
    raw_probability = raw_quote_prob_up(asset, economic_probability)
    return {"asset": asset, "horizon": horizon,
            "anchor_date": anchor.isoformat(), "target_date": None,
            "anchor_close": float(anchor_close), "target_close": None,
            "actual_return": None, "economic_cost_rise_label": None,
            "prob_domestic_cost_up": float(economic_probability),
            "prob_up": raw_probability,
            "predicted_direction": "UP" if raw_probability >= 0.5 else "DOWN",
            "confidence": max(raw_probability, 1 - raw_probability),
            "abstained": None, "mode": mode,
            "research_status": "PROSPECTIVE_OUTCOME_UNOBSERVED"}
