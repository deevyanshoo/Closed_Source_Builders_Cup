from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import subprocess
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from procure.evaluation.observed_runner import (
    calibrate_intervals, forecast_at_origin, plan_common_origins,
    select_models, verify_stage1_run, load_verified_native_series,
    run_bounded_child, publish_runner, verify_runner_run,
    canonical_seed, strict_json,
    _publish_incomplete_run,
)
from procure.evaluation.forecast_scorecard import write_run


def months():
    index = pd.date_range("2016-09-01", "2026-08-01", freq="MS")
    return {stamp.strftime("%Y-%m"): float(100 + i) for i, stamp in enumerate(index)}


def protocol():
    return {"as_of": "2026-10-08", "training_start": "2016-09", "validation_start": "2021-09",
            "validation_end": "2023-08", "test_start": "2024-03", "test_end": "2026-02",
            "horizons": [1, 3, 6], "base_seed": 42, "n_paths": 1000,
            "snapshot_sha256": "a" * 64, "protocol_sha256": "b" * 64}


def test_exact_calendar_cutoff_purge_and_common_gap_rejection():
    planned = plan_common_origins({"copper": months(), "aluminum": months()}, "2026-10-08", protocol())
    assert len(planned["validation"]) == 24
    assert planned["validation"][0] == "2021-09" and planned["validation"][-1] == "2023-08"
    assert planned["purged"] == [stamp.strftime("%Y-%m") for stamp in pd.date_range("2023-09-01", "2024-02-01", freq="MS")]
    assert len(planned["test"]) == 24
    assert planned["test"][0] == "2024-03" and planned["test"][-1] == "2026-02"
    broken = months()
    del broken["2025-01"]
    with pytest.raises(ValueError, match="missing|consecutive"):
        plan_common_origins({"copper": months(), "aluminum": broken}, "2026-10-08", protocol())
    with pytest.raises(ValueError, match="as_of"):
        plan_common_origins({"copper": months()}, "2026-10-09", protocol())


def test_forecast_uses_available_history_and_leads_without_target_leakage():
    seen = []
    def fake_fitter(model, log_series, lead, name, refit_every):
        seen.append((model, log_series.index[-1].strftime("%Y-%m"), len(log_series), lead, refit_every))
        return SimpleNamespace(drift=np.zeros(7), residuals=pd.Series(np.zeros(20)), noise_scale=1.0)
    series = months()
    spec = {"series_kind": "commodity", "series_id": "copper", "unit": "USD/mt", "source_id": "fixture", "is_synthetic": True}
    first = forecast_at_origin(series, "2024-03", "naive", protocol(), spec, "test", fake_fitter)
    assert seen == [("naive", "2024-02", 90, 7, 1)]
    assert [(r["horizon"], r["target"]) for r in first] == [(1, "2024-04"), (3, "2024-06"), (6, "2024-09")]
    assert all(r["training_cutoff"] == "2024-02" and r["p0"] == series["2024-02"] for r in first)
    changed = dict(series, **{"2024-04": 9000.0, "2024-06": 8000.0, "2024-09": 7000.0})
    second = forecast_at_origin(changed, "2024-03", "naive", protocol(), spec, "test", fake_fitter)
    for a, b in zip(first, second):
        assert {k: v for k, v in a.items() if k != "actual"} == {k: v for k, v in b.items() if k != "actual"}
    assert [r["actual"] for r in first] != [r["actual"] for r in second]


def test_constant_residuals_repeat_and_training_only_scale():
    series = {month: 100.0 for month in months()}
    def fake(*args):
        return SimpleNamespace(drift=np.zeros(7), residuals=pd.Series(np.zeros(20)), noise_scale=1.0)
    spec = {"series_kind": "commodity", "series_id": "copper", "unit": "USD/mt", "source_id": "fixture", "is_synthetic": True}
    a = forecast_at_origin(series, "2024-03", "naive", protocol(), spec, "test", fake)
    b = forecast_at_origin(series, "2024-03", "naive", protocol(), spec, "test", fake)
    assert a == b
    assert all(r["p10"] == r["p50"] == r["p90"] == 100.0 and r["mase_scale"] == 0 for r in a)


def test_canonical_common_draws_and_model_step_mapping():
    series = months()
    spec = {"series_kind": "commodity", "series_id": "copper", "unit": "USD/mt", "source_id": "fixture", "is_synthetic": True}
    def fake(*args):
        return SimpleNamespace(drift=np.array([0.01] * 7), residuals=pd.Series(np.zeros(20)), noise_scale=1.0)
    rows = forecast_at_origin(series, "2024-03", "naive", protocol(), spec, "test", fake)
    expected = [rows[0]["p0"] * math.exp(0.01 * step) for step in (2, 4, 7)]
    assert [r["p50"] for r in rows] == pytest.approx(expected)
    material = {"base_seed": 42, "origin": "2024-03", "protocol_sha256": "b" * 64,
                "series_id": "copper", "snapshot_sha256": "a" * 64}
    digest = hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).digest()
    assert canonical_seed(protocol(), "copper", "2024-03") == int.from_bytes(digest[:8], "big")
    def unseeded(*args):
        return SimpleNamespace(drift=np.zeros(7), residuals=pd.Series(np.zeros(20)), noise_scale=1.0, backend="lightgbm")
    with pytest.raises(ValueError, match="unsupported_unpinned_backend"):
        forecast_at_origin(series, "2024-03", "gbm", protocol(), spec, "test", unseeded)


def test_interval_calibration_keeps_median_and_ties_to_smallest():
    rows = [{"origin": f"2022-{i:02d}", "p10": 80.0, "p50": 100.0, "p90": 120.0, "actual": 100.0} for i in range(1, 13)]
    rows += [{"origin": f"2023-{i:02d}", "p10": 80.0, "p50": 100.0, "p90": 120.0, "actual": 100.0} for i in range(1, 13)]
    k, calibrated = calibrate_intervals(rows)
    assert k == 0.8
    assert all(r["p50"] == 100 and r["p10"] < 100 < r["p90"] for r in calibrated)


def test_selection_threshold_and_test_values_are_irrelevant():
    origins = [f"2022-{i:02d}" for i in range(1, 13)] + [f"2023-{i:02d}" for i in range(1, 13)]
    rows = []
    for origin in origins:
        for model, median in (("naive", 90), ("ets", 89), ("gbm", 90)):
            rows.append({"series_id": "copper", "horizon": 1, "origin": origin, "model": model, "p50": median, "actual": 100})
    assert select_models(rows)[("copper", 1)] == "naive"
    better = [dict(row, p50=80) if row["model"] == "ets" else row for row in rows]
    assert select_models(better)[("copper", 1)] == "naive"  # worse than naive
    better = [dict(row, p50=99) if row["model"] == "ets" else row for row in rows]
    assert select_models(better)[("copper", 1)] == "ets"


def test_stage1_consumer_requires_complete_four_hashes(tmp_path):
    raw = json.dumps({"schema_version": "forecast-scorecard.v1", "metadata": {}, "predictions": []}).encode()
    input_path = tmp_path / "input.json"
    input_path.write_bytes(raw)
    run = tmp_path / "scorecard"
    write_run(input_path, run)
    assert verify_stage1_run(run, hashlib.sha256(raw).hexdigest())
    (run / "report.md").write_text("tampered")
    with pytest.raises(ValueError, match="hash"):
        verify_stage1_run(run, hashlib.sha256(raw).hexdigest())


def test_synthetic_workbook_loader_requires_exact_hash_header_and_hold_gate(tmp_path):
    from openpyxl import Workbook
    book = Workbook()
    sheet = book.active
    sheet.title = "Monthly Prices"
    sheet["A7"], sheet["A8"] = "2020M01", "2020M02"
    sheet["B5"], sheet["B6"] = "Copper", "($/mt)"
    sheet["B7"], sheet["B8"] = 10.0, 11.0
    source = tmp_path / "fixture.xlsx"
    book.save(source)
    manifest = {"eligibility_status": "HOLD", "is_synthetic": True, "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "bytes": source.stat().st_size, "sheet": "Monthly Prices", "series": {"copper": {"label": "Copper", "column": "B", "unit": "($/mt)"}}}
    with pytest.raises(ValueError, match="HOLD|synthetic"):
        load_verified_native_series(manifest, "copper", source)
    assert load_verified_native_series(manifest, "copper", source, allow_synthetic_fixture=True) == {"2020-01": 10.0, "2020-02": 11.0}
    with pytest.raises(ValueError, match="hash"):
        load_verified_native_series(dict(manifest, sha256="0" * 64), "copper", source, allow_synthetic_fixture=True)
    with pytest.raises(ValueError, match="header|unit"):
        load_verified_native_series(dict(manifest, series={"copper": {"label": "Copper", "column": "B", "unit": "($/dmtu)"}}), "copper", source, allow_synthetic_fixture=True)


def test_workbook_validation_read_stops_before_test_actuals(tmp_path):
    from openpyxl import Workbook
    book = Workbook()
    sheet = book.active
    sheet.title = "Monthly Prices"
    sheet["B5"], sheet["B6"] = "Copper", "($/mt)"
    sheet["A7"], sheet["B7"] = "2024M02", 10.0
    sheet["A8"], sheet["B8"] = "2024M03", "unread test value"
    source = tmp_path / "fixture.xlsx"
    book.save(source)
    manifest = {"eligibility_status": "HOLD", "is_synthetic": True,
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "bytes": source.stat().st_size,
                "sheet": "Monthly Prices", "series": {"copper": {"label": "Copper", "column": "B", "unit": "($/mt)"}}}
    assert load_verified_native_series(manifest, "copper", source, allow_synthetic_fixture=True,
                                       through_month="2024-02") == {"2024-02": 10.0}
    with pytest.raises(ValueError, match="source price"):
        load_verified_native_series(manifest, "copper", source, allow_synthetic_fixture=True)


def test_owned_child_timeout_and_sanitized_environment(tmp_path):
    command = [sys.executable, "-c", "import os,sys;sys.stdout.write(str('HOME' in os.environ))"]
    assert run_bounded_child(command, b"", timeout=2).strip() == b"False"
    pid_file = tmp_path / "owned.pid"
    # The child environment intentionally strips arbitrary inherited values.
    sleeper = [sys.executable, "-c", f"import os,time;open({str(pid_file)!r},'w').write(str(os.getpid()));time.sleep(10)"]
    with pytest.raises(TimeoutError, match="owned child"):
        run_bounded_child(sleeper, b"", timeout=0.5)
    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_strict_json_rejects_duplicate_and_nonfinite(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"a":1,"a":2}')
    with pytest.raises(ValueError, match="duplicate"):
        strict_json(path)
    path.write_text('{"a":NaN}')
    with pytest.raises(ValueError, match="nonfinite"):
        strict_json(path)


def test_runner_publication_binds_nested_scorecard_and_escaped_chart(tmp_path):
    input_doc = {"schema_version": "forecast-scorecard.v1", "metadata": {"synthetic": True}, "predictions": []}
    provenance = {"source_manifest_sha256": "a" * 64, "protocol_sha256": "b" * 64,
                  "seeds": {}, "backends": {}, "versions": {}, "training_cutoffs": {}}
    out = tmp_path / "run"
    publish_runner(out, input_doc, [], {}, {}, provenance)
    assert verify_runner_run(out)
    with pytest.raises(FileExistsError):
        publish_runner(out, input_doc, [], {}, {}, provenance)
    (out / "scorecard" / "report.md").write_text("tamper")
    with pytest.raises(ValueError, match="hash"):
        verify_runner_run(out)
    observed_doc = {"schema_version": "forecast-scorecard.v1", "metadata": {"synthetic": False}, "predictions": []}
    with pytest.raises(ValueError, match="eligibility"):
        publish_runner(tmp_path / "unapproved", observed_doc, [], {}, {}, provenance)
    assert not (tmp_path / "unapproved").exists()


def test_chart_escapes_hostile_label_and_hash_binds_svg(tmp_path):
    row = {"series_kind": "commodity", "series_id": "copper\n<script>alert(1)</script>",
           "unit": "USD/mt", "source_id": "fixture", "is_synthetic": True,
           "split": "test", "origin": "2024-03", "target": "2024-04", "horizon": 1,
           "model": "naive", "backend": "fixture", "training_cutoff": "2024-02",
           "p0": 10.0, "p10": 9.0, "p50": 10.0, "p90": 11.0, "actual": 10.5, "mase_scale": 1.0}
    doc = {"schema_version": "forecast-scorecard.v1", "metadata": {"synthetic": True}, "predictions": [row]}
    provenance = {"source_manifest_sha256": "a" * 64, "protocol_sha256": "b" * 64,
                  "seeds": {}, "backends": {}, "versions": {}, "training_cutoffs": {}}
    out = tmp_path / "run"
    publish_runner(out, doc, [], {}, {}, provenance)
    chart = next((out / "charts").glob("*.svg"))
    svg = chart.read_text()
    assert "SYNTHETIC TEST FIXTURE" in svg
    assert "<script>" not in svg and "&lt;script&gt;" in svg
    assert "\\u000a" in svg and "copper\n" not in svg
    assert verify_runner_run(out)
    rogue = out / "charts" / "rogue.svg"
    rogue.write_text("<svg/>")
    with pytest.raises(ValueError, match="hash"):
        verify_runner_run(out)
    rogue.unlink()
    chart.write_text("tampered")
    with pytest.raises(ValueError, match="hash"):
        verify_runner_run(out)


def test_failed_fit_receipt_never_looks_complete(tmp_path):
    out = tmp_path / "failed"
    _publish_incomplete_run(out, [{"series_id": "copper", "model": "ets", "status": "pending", "reason": None}], "fit_deadline")
    state = json.loads((out / "RUN_STATE.json").read_text())
    assert state == {"status": "incomplete", "reason": "fit_deadline"}
    assert json.loads((out / "candidate_ledger.json").read_text())[0]["status"] == "not_evaluated"
    assert not (out / "scorecard").exists()
    with pytest.raises(ValueError, match="incomplete"):
        verify_runner_run(out)
    with pytest.raises(FileExistsError):
        _publish_incomplete_run(out, [], "fit_unavailable")


def test_cli_static_hold_stops_before_source_open_or_output(tmp_path):
    project = Path(__file__).parents[1]
    command = [sys.executable, str(project / "scripts/run_observed_forecast_evaluation.py"),
               "--protocol", str(project / "evaluation/forecast/protocol.json"),
               "--source-manifest", str(project / "evaluation/forecast/data_manifest.json"),
               "--source-file", str(tmp_path / "nonexistent.xlsx"),
               "--output-dir", str(tmp_path / "out"), "--as-of", "2026-10-08"]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 2
    assert "HOLD" in result.stderr
    assert not (tmp_path / "out").exists()
