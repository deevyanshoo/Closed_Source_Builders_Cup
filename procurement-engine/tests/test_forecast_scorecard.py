from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from procure.evaluation.forecast_scorecard import evaluate, load_input, write_run


def row(month: int, *, model="gbm", backend="local", kind="commodity", synthetic=False, split="test", actual=None, p50=None, scale=2.0):
    origin = f"2024-{month:02d}"
    target = f"{2024 + month // 12}-{month % 12 + 1:02d}"
    value = float(month + 1 if actual is None else actual)
    median = float(value - 2 if p50 is None else p50)
    return dict(series_kind=kind, series_id="copper", unit="USD/t", source_id="fixture", is_synthetic=synthetic,
                split=split, origin=origin, target=target, horizon=1, model=model, backend=backend,
                training_cutoff=origin, p0=float(month), p10=median - 1, p50=median, p90=median + 3,
                actual=value, mase_scale=scale)


def document(rows):
    return {"schema_version": "forecast-scorecard.v1", "metadata": {"snapshot": "test", "protocol": "test"}, "predictions": rows}


def test_hand_calculated_metrics_and_direction_counts():
    rows = [row(1, actual=4, p50=2), row(2, actual=2, p50=3), row(3, actual=3, p50=3)]
    result = evaluate(document(rows), bootstrap_replicates=100, seed=7)
    m = result["metrics"][0]
    assert m["mae"] == pytest.approx(1)
    assert m["rmse"] == pytest.approx((5 / 3) ** 0.5)
    assert m["mase"] == pytest.approx(0.5)
    assert m["mase_eligible"] == 3 and m["mase_undefined"] == 0
    assert m["pinball_p50"] == pytest.approx(0.5)
    assert m["coverage_80"] == pytest.approx(1)
    assert m["interval_width"] == 4
    assert m["directional_eligible"] == 1
    assert m["actual_no_change"] == 2
    assert m["prediction_abstention"] == 1
    assert m["directional_hit_rate"] == pytest.approx(1)
    assert m["n_rows"] == 3 and m["n_origins"] == 3


def test_strict_input_rejections(tmp_path):
    bad = [row(1)]
    for key, value in [("horizon", True), ("actual", True), ("p10", float("nan")), ("training_cutoff", "2024-03"), ("target", "2024-12"), ("p10", 100), ("extra", 1)]:
        candidate = [dict(bad[0], **{key: value})]
        with pytest.raises(ValueError):
            evaluate(document(candidate))
    with pytest.raises(ValueError):
        evaluate(document(bad * 2))
    path = tmp_path / "duplicate.json"
    path.write_text('{"schema_version":"forecast-scorecard.v1","schema_version":"forecast-scorecard.v1","metadata":{},"predictions":[]}')
    with pytest.raises(ValueError, match="duplicate"):
        load_input(path)
    path.write_text('{"schema_version":"forecast-scorecard.v1","metadata":{},"predictions":[NaN]}')
    with pytest.raises(ValueError):
        load_input(path)


def test_comparable_cells_and_naive_ambiguity():
    base = row(1)
    with pytest.raises(ValueError, match="comparable"):
        evaluate(document([base, dict(row(1, model="naive"), actual=99.0)]))
    with pytest.raises(ValueError, match="ambiguous"):
        evaluate(document([base, row(1, model="naive", backend="a"), row(1, model="naive", backend="b")]))


def test_baseline_matching_and_separation():
    rows = [row(1), row(2), row(1, model="naive", p50=-2), row(3, model="naive")]
    rows += [row(1, kind="fx"), dict(row(1, synthetic=True), series_id="synthetic-copper"), row(1, split="validation")]
    result = evaluate(document(rows), bootstrap_replicates=100)
    assert len(result["metrics"]) == 5
    commodity = next(m for m in result["metrics"] if m["series_kind"] == "commodity" and m["model"] == "gbm" and not m["is_synthetic"] and m["split"] == "test")
    assert commodity["baseline_matched"] == 1
    assert commodity["baseline_missing"] == 1
    assert commodity["skill"] == pytest.approx(0.5)
    assert commodity["bootstrap_reason"] == "insufficient_blocks"
    assert all(m["skill"] is None for m in result["metrics"] if m["split"] == "validation")


def test_undefined_scale_and_zero_or_abstaining_directions():
    rows = [row(1, scale=None, actual=1, p50=1), row(2, scale=0, actual=4, p50=2)]
    m = evaluate(document(rows))["metrics"][0]
    assert m["mase"] is None and m["mase_eligible"] == 0 and m["mase_undefined"] == 2
    assert m["actual_no_change"] == 1 and m["prediction_abstention"] == 2
    assert m["directional_eligible"] == 0 and m["directional_hit_rate"] is None


def test_zero_baseline_error_and_negative_prices_are_explicit():
    model = row(1, actual=-2, p50=-1)
    baseline = row(1, model="naive", actual=-2, p50=-2)
    metrics = evaluate(document([model, baseline]))["metrics"]
    scored = next(item for item in metrics if item["model"] == "gbm")
    assert scored["baseline_matched"] == 1 and scored["baseline_missing"] == 0
    assert scored["baseline_mae"] == 0
    assert scored["skill"] is None and scored["skill_reason"] == "zero_naive_error"


def test_finite_large_errors_remain_scoreable_when_result_is_finite():
    extreme = dict(row(1), actual=1e200, p0=0.0, p10=0.0, p50=0.0, p90=1e200, mase_scale=1e200)
    scored = evaluate(document([extreme]))["metrics"][0]
    assert scored["mae"] == pytest.approx(1e200)
    assert scored["rmse"] == pytest.approx(1e200)
    assert scored["mase"] == pytest.approx(1.0)
    assert scored["interval_width"] == pytest.approx(1e200)


def test_intrinsically_unrepresentable_results_are_null_with_reason():
    extreme = dict(row(1), actual=-1e308, p0=0.0, p10=-1e308, p50=1e308, p90=1e308, mase_scale=1.0)
    scored = evaluate(document([extreme]))["metrics"][0]
    assert scored["mae"] is None and scored["mae_reason"] == "numeric_overflow"
    assert scored["rmse"] is None and scored["rmse_reason"] == "numeric_overflow"
    assert scored["mase"] is None and scored["mase_reason"] == "numeric_overflow"
    assert scored["interval_width"] is None and scored["interval_width_reason"] == "numeric_overflow"
    assert scored["n_rows"] == 1 and scored["mase_eligible"] == 1
    huge = 10**400
    integer_row = dict(row(1), actual=huge, p0=0, p10=0, p50=0, p90=huge, mase_scale=huge)
    integer_metric = evaluate(document([integer_row]))["metrics"][0]
    assert integer_metric["mae"] is None and integer_metric["mae_reason"] == "numeric_overflow"
    assert integer_metric["mase"] == pytest.approx(1)


def test_numeric_limits_cover_pinball_baseline_skill_and_bootstrap():
    pinball = dict(row(1), actual=1e308, p10=-1e308, p50=-1e308, p90=-1e308, mase_scale=1e-308)
    p = evaluate(document([pinball]))["metrics"][0]
    assert p["pinball_p90"] is None and p["pinball_p90_reason"] == "numeric_overflow"
    assert p["mase"] is None and p["mase_reason"] == "numeric_overflow"

    model = dict(row(1), actual=0.0, p10=0.0, p50=1e308, p90=1e308)
    baseline = dict(row(1, model="naive"), actual=0.0, p10=0.0, p50=1e-308, p90=1e308)
    scored = next(m for m in evaluate(document([model, baseline]))["metrics"] if m["model"] == "gbm")
    assert scored["baseline_mae"] == pytest.approx(1e-308)
    assert scored["skill"] is None and scored["skill_reason"] == "numeric_overflow"

    rows = []
    for month in range(1, 13):
        rows.append(dict(row(month), actual=-1e308, p10=-1e308, p50=1e308, p90=1e308))
        rows.append(dict(row(month, model="naive"), actual=-1e308, p10=-1e308, p50=-1e308, p90=1e308))
    scored = next(m for m in evaluate(document(rows), bootstrap_replicates=100)["metrics"] if m["model"] == "gbm")
    assert scored["model_matched_mae"] is None and scored["model_matched_mae_reason"] == "numeric_overflow"
    assert scored["bootstrap_ci_lower"] is None and scored["bootstrap_ci_lower_reason"] == "numeric_overflow"
    assert scored["bootstrap_reason"] == "numeric_overflow"


def test_seeded_bootstrap_repeatable_and_run_immutable(tmp_path):
    rows = [row(i, p50=i + 1) for i in range(1, 13)] + [row(i, model="naive", p50=i) for i in range(1, 13)]
    payload = document(rows)
    first = evaluate(payload, bootstrap_replicates=100, seed=42)
    second = evaluate(payload, bootstrap_replicates=100, seed=42)
    assert first == second
    m = next(x for x in first["metrics"] if x["model"] == "gbm")
    assert m["bootstrap_ci_lower"] == pytest.approx(1)
    assert m["bootstrap_ci_upper"] == pytest.approx(1)
    input_path = tmp_path / "input.json"
    original = json.dumps(payload).encode()
    input_path.write_bytes(original)
    out = tmp_path / "run"
    write_run(input_path, out, bootstrap_replicates=100, seed=42)
    assert (out / "predictions.json").read_bytes() == original
    assert (out / "metrics.csv").exists() and (out / "manifest.json").exists() and (out / "report.md").exists()
    with pytest.raises(FileExistsError):
        write_run(input_path, out)


def test_cli_requires_new_output_directory(tmp_path):
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(document([row(1)])))
    script = Path(__file__).parents[1] / "scripts" / "evaluate_forecasts.py"
    out = tmp_path / "run"
    command = [sys.executable, str(script), "--input", str(input_path), "--output-dir", str(out)]
    assert subprocess.run(command, capture_output=True).returncode == 0
    assert subprocess.run(command, capture_output=True).returncode != 0


def test_interrupted_run_is_marked_incomplete_and_retry_is_explicit(tmp_path, monkeypatch):
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(document([row(1)])))
    output = tmp_path / "interrupted"
    original_write_text = Path.write_text

    def interrupt_report(path, text, *args, **kwargs):
        if path == output / "report.md":
            raise KeyboardInterrupt("injected interruption")
        return original_write_text(path, text, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "write_text", interrupt_report)
        with pytest.raises(KeyboardInterrupt):
            write_run(input_path, output)
    assert json.loads((output / "RUN_STATE.json").read_text())["status"] == "incomplete"
    with pytest.raises(FileExistsError, match="incomplete"):
        write_run(input_path, output)
    complete = tmp_path / "retry-new-name"
    write_run(input_path, complete)
    complete_state = json.loads((complete / "RUN_STATE.json").read_text())
    assert complete_state["status"] == "complete"
    import hashlib
    for name, expected in complete_state["artifact_sha256"].items():
        assert hashlib.sha256((complete / name).read_bytes()).hexdigest() == expected
    competing = tmp_path / "competing"
    competing.mkdir()
    (competing / "other.txt").write_text("owner data")
    with pytest.raises(FileExistsError):
        write_run(input_path, competing)
    assert (competing / "other.txt").read_text() == "owner data"


def test_report_labels_cannot_forge_rows_or_render_control_text(tmp_path):
    hostile = dict(row(1), series_id="copper | fake | test\n| joined | forged | test", model="<script>alert(1)</script>|gbm", backend="local\r\x1b[31m")
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(document([hostile])))
    output = tmp_path / "run"
    write_run(input_path, output)
    report = (output / "report.md").read_text()
    assert report.count("\n| ") == 2  # heading and exactly one data row
    assert "\n| joined | forged | test" not in report
    assert "<script>" not in report
    assert "\x1b" not in report and "\r" not in report
    assert "&#124;" in report
    assert json.loads((output / "predictions.json").read_text())["predictions"][0] == hostile
