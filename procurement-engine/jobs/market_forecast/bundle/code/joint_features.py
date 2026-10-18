"""Causal daily feature construction; source dates must be exchange-local dates."""

from __future__ import annotations

import bisect
import csv
import math
import statistics
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


def load_daily(path, max_date):
    result = []
    with Path(path).open(newline="") as stream:
        reader = csv.DictReader(stream)
        if not {"date", "close"}.issubset(reader.fieldnames or []):
            raise ValueError("price CSV requires exchange-local date,close")
        previous = None
        for item in reader:
            day = date.fromisoformat(item["date"])
            if day > max_date:
                break
            close = float(item["close"])
            if previous is not None and day <= previous:
                raise ValueError("price dates unsorted or duplicated")
            if not math.isfinite(close) or close <= 0:
                raise ValueError("invalid close")
            result.append((day, close))
            previous = day
    return result


def price_features(closes, i, level="compact"):
    if i < 20 or i >= len(closes):
        raise ValueError("20 prior closes required and anchor must exist")
    anchor = closes[i]
    result = {}
    for lag in (1, 2, 3, 5, 10, 20):
        result[f"ret_{lag}"] = math.log(anchor / closes[i - lag])
    one_day = [math.log(closes[j] / closes[j - 1]) for j in range(i - 19, i + 1)]
    for width in (5, 20):
        chunk = one_day[-width:]
        mean = sum(chunk) / width
        result[f"vol_{width}"] = math.sqrt(sum((x - mean) ** 2 for x in chunk) / width)
        result[f"ma_gap_{width}"] = anchor / (sum(closes[i - width + 1:i + 1]) / width) - 1
    changes = [closes[j] - closes[j - 1] for j in range(i - 13, i + 1)]
    gain = sum(max(change, 0) for change in changes)
    loss = sum(max(-change, 0) for change in changes)
    result["rsi_14"] = (gain - loss) / (gain + loss) if gain + loss else 0.0
    if level == "compact":
        return result
    if level != "expanded":
        raise ValueError("unknown feature level")
    volatility = max(result["vol_20"], 1e-6)
    for lag in (1, 3, 5, 10):
        result[f"vol_normalized_ret_{lag}"] = result[f"ret_{lag}"] / (volatility * math.sqrt(lag))
    window = closes[i - 19:i + 1]
    result["drawdown_20"] = anchor / max(window) - 1
    result["reversal_1_5"] = -result["ret_1"] * result["ret_5"]
    average = sum(one_day) / 20
    result["skew_20"] = sum((r - average) ** 3 for r in one_day) / (20 * volatility ** 3)
    streak = 0
    for change in reversed(changes):
        direction = 1 if change > 0 else -1 if change < 0 else 0
        if direction == 0 or (streak and direction != (1 if streak > 0 else -1)):
            break
        streak += direction
    result["signed_streak_14"] = float(streak)
    return result


def cross_asof(asset, anchor_date, series):
    result = {}
    for other, rows in series.items():
        if other == asset:
            continue
        dates = [day for day, _ in rows]
        pos = bisect.bisect_left(dates, anchor_date) - 1
        result[f"cross_{other}_present"] = float(pos >= 0)
        if pos >= 0:
            result[f"cross_{other}_ret_1"] = (math.log(rows[pos][1] / rows[pos - 1][1])
                                                if pos >= 1 else 0.0)
            result[f"cross_{other}_age_days"] = float((anchor_date - dates[pos]).days)
        else:
            result[f"cross_{other}_ret_1"] = 0.0
            result[f"cross_{other}_age_days"] = 0.0
    return result


def _utc(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("news timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


def news_asof(asset, cutoff, records, price_context=None):
    chosen_20 = []
    for row in records:
        if row["asset"] != asset:
            continue
        available = _utc(row["available_at"])
        if cutoff - timedelta(days=20) <= available <= cutoff:
            chosen_20.append((row, (cutoff - available).total_seconds() / 86400))
    original_count = len(chosen_20)
    latest = {}
    unkeyed = []
    for row, age in chosen_20:
        country = row.get("source_country")
        event_type = row.get("event_type")
        if country and event_type:
            key = (country, event_type)
            previous = latest.get(key)
            if previous is None or (age, str(row.get("event_id", ""))) < (
                    previous[1], str(previous[0].get("event_id", ""))):
                latest[key] = (row, age)
        else:
            unkeyed.append((row, age))
    chosen_20 = unkeyed + list(latest.values())
    result = {}
    result["news_deduped_records_20d"] = float(original_count - len(chosen_20))
    for width in (1, 5, 20):
        chosen = [(row, age) for row, age in chosen_20 if age <= width]
        suffix = f"_{width}d"
        result[f"news_observed{suffix}"] = float(bool(chosen))
        result[f"news_count{suffix}"] = sum(float(row["count"]) for row, _ in chosen)
        result[f"news_urgency_max{suffix}"] = max(
            (float(row["urgency"]) for row, _ in chosen), default=0.0)
    result["news_role_observed"] = float(any(row.get("country_role") for row, _ in chosen_20))
    for half_life in (1, 3, 10):
        weights = [float(row["count"]) * float(row["relevance"]) *
                   math.exp(-math.log(2) * age / half_life) for row, age in chosen_20]
        terms = [weight * float(row["sentiment"]) * float(row["impact"]) *
                 float(row["urgency"]) for weight, (row, _) in zip(weights, chosen_20)]
        total = sum(terms)
        suffix = f"_hl{half_life}"
        result[f"news_signed_impact{suffix}"] = total
        result[f"news_own{suffix}"] = sum(
            term for term, (row, _) in zip(terms, chosen_20)
            if str(row.get("country_role", "")).lower() == "own")
        result[f"news_us{suffix}"] = sum(
            term for term, (row, _) in zip(terms, chosen_20)
            if str(row.get("country_role", "")).upper() == "US")
        result[f"news_x_trend{suffix}"] = total * float((price_context or {}).get("ma_gap_20", 0.0))
        result[f"news_x_vol{suffix}"] = total * float((price_context or {}).get("vol_20", 0.0))
    for name, source_field in (("neutral", "neutral_score"),
                               ("entropy", "entropy"),
                               ("confidence", "confidence")):
        available = [(row, age) for row, age in chosen_20
                     if row.get(source_field) not in (None, "")]
        result[f"news_{name}_observed"] = float(bool(available))
        if available:
            weighted = [(float(row["count"]) * float(row["relevance"]) *
                         math.exp(-math.log(2) * age / 3.0), float(row[source_field]))
                        for row, age in available]
            denominator = sum(weight for weight, _ in weighted)
            result[f"news_{name}_mean"] = (sum(weight * value for weight, value in weighted) /
                                            denominator if denominator else 0.0)
        else:
            result[f"news_{name}_mean"] = 0.0
    return result


def train_regime_medians(training_feature_rows):
    if not training_feature_rows:
        raise ValueError("training features required")
    return (statistics.median(row["vol_20"] for row in training_feature_rows),
            statistics.median(abs(row["ma_gap_20"]) for row in training_feature_rows))
