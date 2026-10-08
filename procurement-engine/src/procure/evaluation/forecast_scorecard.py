"""Score precomputed, frozen monthly forecast rows without external dependencies."""

from __future__ import annotations

import csv
import hashlib
import html
import json
import math
import os
import random
from collections import defaultdict
from decimal import Decimal, localcontext
from pathlib import Path
from statistics import mean

SCHEMA_VERSION = "forecast-scorecard.v1"
FIELDS = frozenset({
    "series_kind", "series_id", "unit", "source_id", "is_synthetic", "split",
    "origin", "target", "horizon", "model", "backend", "training_cutoff",
    "p0", "p10", "p50", "p90", "actual", "mase_scale",
})
TEXT = ("series_id", "unit", "source_id", "model", "backend")
NUMBERS = ("p0", "p10", "p50", "p90", "actual")
GROUP = ("series_kind", "series_id", "unit", "source_id", "is_synthetic", "split", "model", "backend", "horizon")
CELL = ("series_kind", "series_id", "split", "origin", "target", "horizon")
IDENTITY = ("series_kind", "series_id", "split", "origin", "target", "horizon", "model", "backend")
COMPARABLE = ("unit", "source_id", "is_synthetic", "actual", "p0", "mase_scale", "training_cutoff")


def _duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _bad_constant(value):
    raise ValueError(f"nonfinite JSON constant: {value}")


def load_input(path: str | Path) -> dict:
    """Load strict JSON, rejecting duplicate keys and nonstandard constants."""
    return json.loads(Path(path).read_bytes(), object_pairs_hook=_duplicate_keys, parse_constant=_bad_constant)


def _month(value, name):
    if not isinstance(value, str) or len(value) != 7 or value[4] != "-" or not value[:4].isdigit() or not value[5:].isdigit():
        raise ValueError(f"{name} must be YYYY-MM")
    year, month = int(value[:4]), int(value[5:])
    if year < 1 or not 1 <= month <= 12:
        raise ValueError(f"{name} must be a valid YYYY-MM")
    return year * 12 + month - 1


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or (isinstance(value, float) and not math.isfinite(value)):
        raise ValueError(f"{name} must be finite numeric (excluding bool)")


def _validate(doc):
    if not isinstance(doc, dict) or set(doc) != {"schema_version", "metadata", "predictions"}:
        raise ValueError("input requires exactly schema_version, metadata, predictions")
    if doc["schema_version"] != SCHEMA_VERSION or not isinstance(doc["metadata"], dict) or not isinstance(doc["predictions"], list):
        raise ValueError("invalid schema_version, metadata, or predictions")
    # Metadata is caller-supplied; still reject non-JSON values and nonfinite numbers.
    try:
        json.dumps(doc, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("input must contain finite JSON values") from exc
    seen = set()
    cells = {}
    naive_cells = set()
    for index, row in enumerate(doc["predictions"]):
        if not isinstance(row, dict) or set(row) != FIELDS:
            raise ValueError(f"row {index} requires exact fields")
        if row["series_kind"] not in {"commodity", "fx", "joined"} or row["split"] not in {"validation", "test"}:
            raise ValueError(f"row {index}: invalid kind or split")
        if type(row["is_synthetic"]) is not bool:
            raise ValueError(f"row {index}: is_synthetic must be bool")
        for field in TEXT:
            if not isinstance(row[field], str) or not row[field].strip():
                raise ValueError(f"row {index}: {field} must be nonempty text")
        if type(row["horizon"]) is not int or row["horizon"] < 1:
            raise ValueError(f"row {index}: horizon must be a positive integer")
        origin = _month(row["origin"], "origin")
        target = _month(row["target"], "target")
        cutoff = _month(row["training_cutoff"], "training_cutoff")
        if target != origin + row["horizon"] or cutoff > origin:
            raise ValueError(f"row {index}: target/cutoff chronology invalid")
        for field in NUMBERS:
            _number(row[field], field)
        scale = row["mase_scale"]
        if scale is not None:
            _number(scale, "mase_scale")
            if scale < 0:
                raise ValueError("mase_scale must be nonnegative")
        if not row["p10"] <= row["p50"] <= row["p90"]:
            raise ValueError("quantiles must be ordered")
        identity = tuple(row[field] for field in IDENTITY)
        if identity in seen:
            raise ValueError(f"duplicate prediction identity: {identity}")
        seen.add(identity)
        cell = tuple(row[field] for field in CELL)
        comparable = tuple(row[field] for field in COMPARABLE)
        if cell in cells and cells[cell] != comparable:
            raise ValueError(f"inconsistent comparable cell: {cell}")
        cells[cell] = comparable
        if row["model"] == "naive":
            if cell in naive_cells:
                raise ValueError(f"ambiguous naive backends for cell: {cell}")
            naive_cells.add(cell)


def _pinball(actual, prediction, q):
    error = _decimal(actual) - _decimal(prediction)
    return max(q * error, (q - 1) * error)


def _decimal(value):
    return Decimal(str(value))


def _decimal_mean(values):
    values = list(values)
    return sum(values, Decimal(0)) / Decimal(len(values))


def _emit(item, name, value, undefined_reason=None):
    """Put a decimal metric into finite JSON or identify its unavailable reason."""
    if value is None:
        item[name], item[name + "_reason"] = None, undefined_reason
        return
    converted = float(value)
    if not math.isfinite(converted) or (value != 0 and converted == 0):
        item[name], item[name + "_reason"] = None, "numeric_overflow"
    else:
        item[name], item[name + "_reason"] = converted, None


def _percentile(values, fraction):
    ordered = sorted(values)
    position = Decimal(len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _bootstrap(pairs, seed, replicates, block_length):
    """Paired mean absolute-error reduction, resampling chronological origin blocks."""
    by_origin = defaultdict(list)
    for row, naive in pairs:
        actual = _decimal(row["actual"])
        by_origin[row["origin"]].append(abs(_decimal(naive["p50"]) - actual) - abs(_decimal(row["p50"]) - actual))
    origin_differences = [_decimal_mean(by_origin[origin]) for origin in sorted(by_origin)]
    n = len(origin_differences)
    if n < 2 * block_length:
        return None, None, "insufficient_blocks"
    rng = random.Random(seed)
    samples = []
    for _ in range(replicates):
        selected = []
        while len(selected) < n:
            start = rng.randrange(n - block_length + 1)
            selected.extend(origin_differences[start:start + block_length])
        samples.append(_decimal_mean(selected[:n]))
    return _percentile(samples, Decimal("0.025")), _percentile(samples, Decimal("0.975")), None


def evaluate(doc: dict, *, bootstrap_replicates: int = 1000, seed: int = 0, block_length: int = 6) -> dict:
    """Return finite JSON-ready metrics; input predictions are never changed."""
    if type(bootstrap_replicates) is not int or not 100 <= bootstrap_replicates <= 10000:
        raise ValueError("bootstrap_replicates must be an integer from 100 to 10000")
    if type(seed) is not int or not 0 <= seed <= 2**32 - 1:
        raise ValueError("seed must be an unsigned 32-bit integer")
    if type(block_length) is not int or block_length != 6:
        raise ValueError("v1 block_length must be 6")
    _validate(doc)
    grouped = defaultdict(list)
    naive = {}
    for row in doc["predictions"]:
        grouped[tuple(row[field] for field in GROUP)].append(row)
        if row["model"] == "naive":
            naive[tuple(row[field] for field in CELL)] = row
    metrics = []
    with localcontext() as context:
        context.prec = 80
        for group in sorted(grouped, key=lambda key: tuple(str(item) for item in key)):
            rows = sorted(grouped[group], key=lambda row: (row["origin"], row["target"]))
            n = len(rows)
            errors = [_decimal(row["p50"]) - _decimal(row["actual"]) for row in rows]
            eligible = [abs(error) / _decimal(row["mase_scale"]) for error, row in zip(errors, rows) if row["mase_scale"] is not None and row["mase_scale"] > 0]
            direction_rows = [(row, (row["actual"] > row["p0"]) - (row["actual"] < row["p0"]), (row["p50"] > row["p0"]) - (row["p50"] < row["p0"])) for row in rows]
            direction_eligible = [(actual_direction, prediction_direction) for _, actual_direction, prediction_direction in direction_rows if actual_direction and prediction_direction]
            item = dict(zip(GROUP, group))
            item.update(n_rows=n, n_origins=len({row["origin"] for row in rows}),
                        mase_eligible=len(eligible), mase_undefined=n - len(eligible),
                        coverage_80=mean(row["p10"] <= row["actual"] <= row["p90"] for row in rows),
                        actual_no_change=sum(actual_direction == 0 for _, actual_direction, _ in direction_rows),
                        prediction_abstention=sum(prediction_direction == 0 for _, _, prediction_direction in direction_rows),
                        directional_eligible=len(direction_eligible),
                        directional_hit_rate=mean(actual == predicted for actual, predicted in direction_eligible) if direction_eligible else None,
                        baseline_matched=0, baseline_missing=n if group[5] == "test" and group[6] != "naive" else 0,
                        skill=None, skill_reason="not_applicable", bootstrap_reason="not_applicable")
            _emit(item, "mae", _decimal_mean(abs(error) for error in errors))
            _emit(item, "rmse", _decimal_mean(error * error for error in errors).sqrt())
            _emit(item, "mase", _decimal_mean(eligible) if eligible else None, "no_positive_scale" if not eligible else None)
            for label, quantile in (("p10", Decimal("0.1")), ("p50", Decimal("0.5")), ("p90", Decimal("0.9"))):
                _emit(item, "pinball_" + label, _decimal_mean(_pinball(row["actual"], row[label], quantile) for row in rows))
            _emit(item, "interval_width", _decimal_mean(_decimal(row["p90"]) - _decimal(row["p10"]) for row in rows))
            _emit(item, "baseline_mae", None, "not_applicable")
            _emit(item, "model_matched_mae", None, "not_applicable")
            _emit(item, "bootstrap_ci_lower", None, "not_applicable")
            _emit(item, "bootstrap_ci_upper", None, "not_applicable")
            if item["split"] == "test" and item["model"] != "naive":
                pairs = [(row, naive[tuple(row[field] for field in CELL)]) for row in rows if tuple(row[field] for field in CELL) in naive]
                item["baseline_matched"] = len(pairs)
                item["baseline_missing"] = n - len(pairs)
                if pairs:
                    baseline_mae = _decimal_mean(abs(_decimal(base["p50"]) - _decimal(row["actual"])) for row, base in pairs)
                    model_mae = _decimal_mean(abs(_decimal(row["p50"]) - _decimal(row["actual"])) for row, _ in pairs)
                    _emit(item, "baseline_mae", baseline_mae)
                    _emit(item, "model_matched_mae", model_mae)
                    if baseline_mae > 0:
                        _emit(item, "skill", Decimal(1) - model_mae / baseline_mae)
                        if item["skill_reason"] is None:
                            item["skill_reason"] = "available"
                    else:
                        item["skill_reason"] = "zero_naive_error"
                    lower, upper, reason = _bootstrap(pairs, seed, bootstrap_replicates, block_length)
                    _emit(item, "bootstrap_ci_lower", lower, reason)
                    _emit(item, "bootstrap_ci_upper", upper, reason)
                    item["bootstrap_reason"] = item["bootstrap_ci_lower_reason"] or item["bootstrap_ci_upper_reason"]
                else:
                    item["skill_reason"] = "no_matched_baseline"
                    item["bootstrap_reason"] = "no_matched_baseline"
            metrics.append(item)
    result = {"schema_version": SCHEMA_VERSION, "metadata": doc["metadata"], "config": {"bootstrap_replicates": bootstrap_replicates, "seed": seed, "block_length": block_length}, "metrics": metrics}
    json.dumps(result, allow_nan=False)
    return result


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_run(input_path: str | Path, output_dir: str | Path, *, bootstrap_replicates: int = 1000, seed: int = 0) -> dict:
    """Create a new run; RUN_STATE.json alone declares publication complete."""
    source = Path(input_path)
    raw = source.read_bytes()
    doc = json.loads(raw, object_pairs_hook=_duplicate_keys, parse_constant=_bad_constant)
    result = evaluate(doc, bootstrap_replicates=bootstrap_replicates, seed=seed)
    output = Path(output_dir)
    def existing_run_error():
        state = output / "RUN_STATE.json"
        try:
            status = json.loads(state.read_text(encoding="utf-8")).get("status")
        except (OSError, ValueError, AttributeError):
            status = "incomplete_or_unknown"
        return FileExistsError(f"{status} run/destination already exists at {output}; use a new output directory")

    if output.exists() or output.is_symlink():
        raise existing_run_error()
    config_bytes = json.dumps(result["config"], sort_keys=True, separators=(",", ":")).encode()
    module_path = Path(__file__)
    script_path = Path(__file__).parents[3] / "scripts" / "evaluate_forecasts.py"
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "stage": "offline_metric_infrastructure_only",
        "input_sha256": _sha(raw),
        "input_bytes": len(raw),
        "config_sha256": _sha(config_bytes),
        "module_sha256": _sha(module_path.read_bytes()),
        "cli_sha256": _sha(script_path.read_bytes()) if script_path.exists() else None,
        "metadata": doc["metadata"],
        "metadata_is_caller_supplied": True,
        "config": result["config"],
        "row_count": len(doc["predictions"]),
    }
    try:
        output.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise existing_run_error() from exc
    state_path = output / "RUN_STATE.json"
    state_path.write_text('{"status":"incomplete"}\n', encoding="utf-8")
    prediction_path = output / "predictions.json"
    prediction_path.write_bytes(raw)
    prediction_path.chmod(0o444)
    with (output / "metrics.csv").open("w", newline="", encoding="utf-8") as handle:
        if result["metrics"]:
            writer = csv.DictWriter(handle, fieldnames=list(result["metrics"][0]))
            writer.writeheader()
            writer.writerows(result["metrics"])
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    lines = ["# Forecast scorecard", "", "Stage 1: pure offline metric infrastructure over caller-supplied frozen predictions. No observed-data eligibility, leakage-free protocol, model training, forecast accuracy, or deployment is established by this run.", "", "Metadata is caller-supplied and unverified. No source publication vintages are verified; overlapping targets and small samples limit uncertainty interpretation.", "", f"Input SHA-256: `{manifest['input_sha256']}`. Rows: {manifest['row_count']}. Groups: {len(result['metrics'])}.", "", "The CSV reports MAE/RMSE in original units, training-scale MASE when defined, pinball loss, inclusive interval coverage/width, strict-direction hit rate, and naive skill on matched test cells only. Missing matches and undefined denominators remain explicit. Bootstrap intervals are paired absolute-error reduction over chronological origin blocks of six months.", "", "| Kind | Series | Split | Model / backend | Horizon | Rows / origins | MAE | MASE | 80% coverage | Naive matched / missing | Skill |", "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    def display(value, reason=None):
        if value is None:
            return "undefined" + (f" ({reason})" if reason else "")
        return f"{value:.4g}" if isinstance(value, float) else str(value)
    def markdown_cell(value):
        escaped = html.escape(str(value), quote=True).replace("|", "&#124;")
        return "".join(f"\\u{ord(char):04x}" if ord(char) < 32 or 0x7f <= ord(char) <= 0x9f or char in "\u2028\u2029" else char for char in escaped)
    for metric in result["metrics"]:
        lines.append("| " + " | ".join(map(markdown_cell, [metric["series_kind"], metric["series_id"], metric["split"],
            metric["model"] + " / " + metric["backend"], str(metric["horizon"]),
            f"{metric['n_rows']} / {metric['n_origins']}", display(metric["mae"], metric["mae_reason"]),
            display(metric["mase"], metric["mase_reason"]), display(metric["coverage_80"]),
            f"{metric['baseline_matched']} / {metric['baseline_missing']}", display(metric["skill"], metric["skill_reason"])])) + " |")
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    artifacts = {name: _sha((output / name).read_bytes()) for name in ("predictions.json", "metrics.csv", "manifest.json", "report.md")}
    complete_temp = output / ".RUN_STATE.tmp"
    complete_temp.write_text(json.dumps({"status": "complete", "artifact_sha256": artifacts}, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(complete_temp, state_path)
    return manifest
