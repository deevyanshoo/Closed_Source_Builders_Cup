from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import subprocess
import signal
import time
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
from procure.evaluation import observed_runner
from procure.evaluation.forecast_scorecard import write_run


def months():
    index = pd.date_range("2016-09-01", "2026-08-01", freq="MS")
    return {stamp.strftime("%Y-%m"): float(100 + i) for i, stamp in enumerate(index)}


def protocol():
    frozen = json.loads((Path(__file__).parents[1] / "evaluation/forecast/protocol.json").read_text())
    return {**frozen, "snapshot_sha256": "a" * 64, "protocol_sha256": "b" * 64}


@pytest.mark.parametrize("case,model,expected", [
    ("constant", "naive", "2fdfcb469db62e5b2454713ce0a0068fbfaacb585277824b346cf957eb3c5a86"),
    ("rising", "ets", "363c9589ddfc85cfbabfd2cace739a357dae5bc9fbaa1997efb6cfb3e821d020"),
    ("falling", "ets", "c0fc2788c02221277f9fdb554b2b6231ff7e310cbd84ceca56178cb349af6c99"),
    ("residual", "gbm", "197c4b72a36fe6d0b3f6ba7c2921401007340251caa7de602513d815741b5f52"),
])
def test_admitted_predictor_whole_result_equalities(case, model, expected):
    from procure.evaluation.observed_admission import canonical, digest
    p = {**observed_runner.PROTOCOL_FIELDS, "status": "INFRASTRUCTURE_ONLY_OBSERVED_EXECUTION_HOLD",
         "observed_execution_authorized": False, "snapshot_sha256": "a" * 64,
         "protocol_sha256": "b" * 64, "source_sha256": "c" * 64}
    labels = list(months())
    series = {month: float(100 + i if case == "rising" else 300 - i if case == "falling" else 100)
              for i, month in enumerate(labels)}
    def fake(*args):
        drift = .01 if case == "rising" else -.01 if case == "falling" else 0
        residuals = [-.02, -.01, 0, .01, .02, .03] * 2 if case == "residual" else [0] * 12
        return SimpleNamespace(drift=np.full(7, drift), residuals=np.array(residuals), noise_scale=1.0, backend="fixture")
    spec = {"series_kind": "commodity", "series_id": "copper", "unit": "USD/metric tonne",
            "source_id": "owned_fixture", "is_synthetic": True}
    legacy = forecast_at_origin(series, "2024-03", model, p, spec, "test", fake)
    training = {month: value for month, value in series.items() if month <= "2024-02"}
    rows = observed_runner.forecast_from_training(training, "2024-03", model, p, spec, "test", fake)
    assert all("actual" not in row for row in rows)
    frozen = canonical(rows)
    actuals = {row["target"]: series[row["target"]] for row in rows}
    attached = observed_runner.attach_frozen_actuals(frozen, actuals, expected_prediction_sha256=digest(frozen))
    assert attached == legacy and digest(canonical(attached)) == expected
    with pytest.raises(ValueError, match="consecutive"):
        observed_runner.forecast_from_training(series, "2024-03", model, p, spec, "test", fake)
    with pytest.raises(ValueError, match="foreign/stale"):
        observed_runner.attach_frozen_actuals(frozen, actuals, expected_prediction_sha256="a" * 64)
    with pytest.raises(ValueError, match="actual"):
        forecast_at_origin({**series, "2024-04": False}, "2024-03", model, p, spec, "test", fake)


def _admitted_fake_fit(*args, **kwargs):
    return SimpleNamespace(drift=np.zeros(7), residuals=np.zeros(12), noise_scale=1., backend="fixture",
                           versions={"fitter": "deterministic-test-only"})


def _admitted_call(fixture):
    source, _, _, binding, pp, mp, out = fixture
    return observed_runner.run_offline_evaluation(pp, mp, source, out, "2026-10-08", accepted_run=binding)


@pytest.fixture
def completed_identity_fixture(monkeypatch):
    import tempfile
    from test_forecast_observed_admission import owned_fixture
    # Canonical owned root; keep fault evidence, including displaced outputs.
    root = Path(tempfile.mkdtemp(prefix="oa-v1-r1-", dir="/private/tmp"))
    fixture = owned_fixture(root)
    monkeypatch.setattr(observed_runner, "fit_in_owned_child", _admitted_fake_fit)
    _admitted_call(fixture)
    assert verify_runner_run(fixture[-1], expected_run=fixture[3]) is True
    return fixture


def _preserve_and_copy_output(output):
    import shutil
    preserved = output.with_name("preserved_host_output")
    original = os.lstat(output)
    os.rename(output, preserved)
    shutil.copytree(preserved, output)
    assert (original.st_dev, original.st_ino) != (os.lstat(output).st_dev, os.lstat(output).st_ino)
    before = {str(p.relative_to(preserved)): p.read_bytes() for p in preserved.rglob("*") if p.is_file()}
    after = {str(p.relative_to(output)): p.read_bytes() for p in output.rglob("*") if p.is_file()}
    assert before == after
    return preserved


def test_admitted_identity_same_bytes_replacement_refuses(completed_identity_fixture):
    fixture = completed_identity_fixture
    output = fixture[-1]
    preserved = _preserve_and_copy_output(output)
    # Unbound legacy verification is intentionally only artifact verification.
    assert verify_runner_run(output / "legacy") is True
    with pytest.raises(ValueError, match="identity"):
        verify_runner_run(output, expected_run=fixture[3])
    assert preserved.is_dir() and output.is_dir()


@pytest.mark.parametrize("kind", ["absent", "file", "symlink", "copied"])
def test_admitted_identity_refuses_before_artifact_reads(completed_identity_fixture, monkeypatch, kind):
    fixture = completed_identity_fixture
    output = fixture[-1]
    if kind == "copied":
        preserved = _preserve_and_copy_output(output)
    else:
        preserved = output.with_name("preserved_host_output")
        os.rename(output, preserved)
        if kind == "file":
            output.write_bytes(b"owned replacement")
        elif kind == "symlink":
            output.symlink_to(preserved, target_is_directory=True)
    def forbidden(*args, **kwargs):
        raise AssertionError("replaced output reached artifact reads")
    monkeypatch.setattr(observed_runner, "_owned_json", forbidden)
    with pytest.raises((ValueError, OSError)):
        verify_runner_run(output, expected_run=fixture[3])
    assert preserved.is_dir()


@pytest.mark.parametrize("where", ["parent", "output"])
@pytest.mark.parametrize("field", ["st_dev", "st_ino", "st_mode", "st_uid"])
def test_admitted_identity_each_directory_field_refuses(completed_identity_fixture, monkeypatch, where, field):
    from procure.evaluation import observed_admission as a
    fixture = completed_identity_fixture
    output = fixture[-1]
    wanted = os.lstat(output.parent if where == "parent" else output)
    real_fstat = os.fstat
    def fault(fd):
        info = real_fstat(fd)
        if (info.st_dev, info.st_ino) == (wanted.st_dev, wanted.st_ino):
            values = {name: getattr(info, name) for name in ("st_dev", "st_ino", "st_mode", "st_uid")}
            values[field] += 1
            return SimpleNamespace(**values)
        return info
    def forbidden(*args, **kwargs):
        raise AssertionError("directory identity fault reached artifact reads")
    monkeypatch.setattr(a.os, "fstat", fault)
    monkeypatch.setattr(observed_runner, "_owned_json", forbidden)
    with pytest.raises(ValueError, match="identity"):
        verify_runner_run(output, expected_run=fixture[3])


@pytest.mark.parametrize("where", ["parent", "output"])
@pytest.mark.parametrize("when", ["before", "after"])
def test_admitted_identity_mode_change_refuses(completed_identity_fixture, monkeypatch, where, when):
    fixture = completed_identity_fixture
    output = fixture[-1]
    target = output.parent if where == "parent" else output
    original = observed_runner._admitted_artifacts
    calls = []
    def changed(path):
        result = original(path)
        calls.append(path)
        if len(calls) == 2:  # final successful artifact scan in fresh verifier
            target.chmod(0o750)
        return result
    if when == "before":
        target.chmod(0o750)
    else:
        monkeypatch.setattr(observed_runner, "_admitted_artifacts", changed)
    with pytest.raises(ValueError, match="identity"):
        verify_runner_run(output, expected_run=fixture[3])
    assert target.stat().st_mode & 0o777 == 0o750
    if when == "after":
        assert len(calls) == 2


def test_admitted_identity_final_scan_replacement_refuses(completed_identity_fixture, monkeypatch):
    fixture = completed_identity_fixture
    output = fixture[-1]
    original = observed_runner._admitted_artifacts
    calls = []
    def replaced(path):
        hashes = original(path)
        calls.append(path)
        if len(calls) == 2:
            _preserve_and_copy_output(output)
        return hashes
    monkeypatch.setattr(observed_runner, "_admitted_artifacts", replaced)
    with pytest.raises(ValueError, match="identity"):
        verify_runner_run(output, expected_run=fixture[3])
    assert len(calls) == 2 and output.with_name("preserved_host_output").is_dir()


@pytest.mark.parametrize("fault", ["first_fstat", "last_lstat", "close", "entry_check"])
def test_admitted_identity_stat_close_uncertainty_refuses(completed_identity_fixture, monkeypatch, fault):
    from procure.evaluation import observed_admission as a
    fixture = completed_identity_fixture
    output = fixture[-1]
    wanted = os.lstat(output)
    opened, closed, hit, anchor_postclose = [], [], [], []
    real_open, real_close, real_fstat, real_lstat = os.open, os.close, os.fstat, os.lstat
    def traced_open(*args, **kwargs):
        fd = real_open(*args, **kwargs)
        opened.append(fd)
        return fd
    def traced_close(fd):
        is_output = real_fstat(fd).st_ino == wanted.st_ino
        closed.append(fd)
        real_close(fd)  # simulate uncertainty after the actual single close
        if fault == "close" and is_output and not hit:
            hit.append(True)
            raise OSError("injected run directory close uncertainty")
    def bad_fstat(fd):
        info = real_fstat(fd)
        if fault == "first_fstat" and info.st_ino == wanted.st_ino:
            hit.append(True)
            raise OSError("injected initial directory stat uncertainty")
        return info
    def bad_lstat(path, *args, **kwargs):
        if fault == "last_lstat" and Path(path) == output and anchor_postclose:
            hit.append(True)
            raise OSError("injected postclose stat uncertainty")
        return real_lstat(path, *args, **kwargs)
    original_check = a._RunDirectoryAnchor.check
    checks = []
    def bad_entry(self, **kwargs):
        checks.append(kwargs)
        if kwargs.get("postclose"):
            anchor_postclose.append(True)
        if fault == "entry_check" and len(checks) == 2:
            hit.append(True)
            raise OSError("injected context entry identity uncertainty")
        return original_check(self, **kwargs)
    monkeypatch.setattr(a.os, "open", traced_open)
    monkeypatch.setattr(a.os, "close", traced_close)
    monkeypatch.setattr(a.os, "fstat", bad_fstat)
    monkeypatch.setattr(a.os, "lstat", bad_lstat)
    monkeypatch.setattr(a._RunDirectoryAnchor, "check", bad_entry)
    with pytest.raises((ValueError, OSError)):
        verify_runner_run(output, expected_run=fixture[3])
    assert hit and sorted(opened) == sorted(closed)
    # FD numbers may be reused by separate artifact lifetimes; each open is closed once.


@pytest.mark.parametrize("stage", ["capture", "seal"])
def test_admitted_identity_host_close_uncertainty_prevents_seal(monkeypatch, stage):
    import tempfile
    from test_forecast_observed_admission import owned_fixture
    from procure.evaluation import observed_admission as a
    root = Path(tempfile.mkdtemp(prefix="oa-v1-r1-", dir="/private/tmp"))
    fixture = owned_fixture(root)
    binding, output = fixture[3], fixture[-1]
    original_close = a._RunDirectoryAnchor.close
    hit = []
    def uncertain(self):
        original_close(self)
        state = a._RUN_LIVES[binding]
        captured = state["created_identity"] is not None
        if not hit and captured == (stage == "seal") and state["completion_sha256"] is None:
            hit.append(True)
            raise ValueError("injected host directory close uncertainty")
    monkeypatch.setattr(a._RunDirectoryAnchor, "close", uncertain)
    monkeypatch.setattr(observed_runner, "fit_in_owned_child", _admitted_fake_fit)
    with pytest.raises(ValueError, match="close uncertainty"):
        _admitted_call(fixture)
    assert hit and (output / "FAILURE.json").is_file()
    assert (output / "ADMITTED_COMPLETE.json").exists() == (stage == "seal")
    with pytest.raises(ValueError, match="absent or failed"):
        a._fixture_completion_identity(binding)
    with pytest.raises(ValueError, match="failure entry"):
        verify_runner_run(output, expected_run=binding)
    with pytest.raises(ValueError, match="output entry"):
        _admitted_call(fixture)
    assert (output / "FAILURE.json").is_file()


def test_admitted_identity_host_token_capture_and_seal_are_single_use(monkeypatch):
    import tempfile
    from test_forecast_observed_admission import owned_fixture
    from procure.evaluation import observed_admission as a
    root = Path(tempfile.mkdtemp(prefix="oa-v1-r1-", dir="/private/tmp"))
    fixture = owned_fixture(root)
    run, output = fixture[3], fixture[-1]
    token = a._begin_fixture_run(run)
    with pytest.raises(ValueError, match="identity absent"):
        a._seal_fixture_run(run, token, b"unissued")
    output.mkdir(mode=0o700)
    identity = a._identity(os.lstat(output), True)
    with pytest.raises(ValueError, match="foreign/stale"):
        a._capture_fixture_output(run, object(), identity)
    a._capture_fixture_output(run, token, identity)
    with pytest.raises(ValueError, match="foreign/stale"):
        a._capture_fixture_output(run, token, identity)
    with pytest.raises(ValueError, match="foreign/stale"):
        a._seal_fixture_run(run, object(), b"unissued")
    a._seal_fixture_run(run, token, b"host closure fixture")
    assert a._fixture_completion_identity(run) == (a.fixture_run_record(run)["parent_identity"], identity)
    with pytest.raises(ValueError, match="foreign/stale"):
        a._seal_fixture_run(run, token, b"reissued")
    a._fail_fixture_run(run, token)
    with pytest.raises(ValueError, match="absent or failed"):
        a._fixture_completion_identity(run)
    with pytest.raises(ValueError, match="already attempted"):
        a._begin_fixture_run(run)
    with pytest.raises(ValueError, match="registered"):
        a._fixture_completion_identity(a.RootObservedRunBinding(run.fixture_id))


def test_admitted_public_host_freezes_then_scores_whole_cohorts(tmp_path, monkeypatch):
    from test_forecast_observed_admission import owned_fixture
    fixture = owned_fixture(tmp_path)
    out = fixture[-1]
    events = []
    real_attach = observed_runner.attach_frozen_actuals
    def attach(*args, **kwargs):
        rows = json.loads(args[0])
        split = rows[0]["split"]
        freeze = json.loads((out / (split + "_prediction_freeze.json")).read_bytes())
        assert all("actual" not in r for r in freeze["predictions"])
        if split == "test":
            assert (out / "selection_freeze.json").is_file()
        events.append(("attach", split))
        return real_attach(*args, **kwargs)
    def fit(model, logs, lead, name, refit, **kwargs):
        cutoff = logs.index[-1].strftime("%Y-%m")
        assert list(logs.index.strftime("%Y-%m")) == observed_runner._range("2016-09", cutoff)
        assert len(logs) in range(60, 114) and lead == 7 and refit == 1
        if cutoff >= "2024-02":
            assert (out / "selection_freeze.json").is_file()
        events.append(("fit", cutoff))
        return _admitted_fake_fit()
    monkeypatch.setattr(observed_runner, "fit_in_owned_child", fit)
    monkeypatch.setattr(observed_runner, "attach_frozen_actuals", attach)
    result = _admitted_call(fixture)
    assert result["status"] == "fixture_complete" and result["observed_execution_authorized"] is False
    assert verify_runner_run(out, expected_run=fixture[3])
    ledger = json.loads((out / "candidate_ledger.json").read_bytes())["entries"]
    assert len(ledger) == 96 and sum(r["status"] == "scored" for r in ledger) == 36
    unavailable = [r for r in ledger if r["status"] != "scored"]
    assert len(unavailable) == 60 and all(r["metrics"] is None and r["backend"] is None for r in unavailable)
    assert {r["series_id"] for r in unavailable} == {"USDINR", "CopperINRkg", "AluminiumINRkg", "iron_ore"}
    assert len(json.loads((out / "scored_rows.json").read_bytes())["predictions"]) == 864
    assert len(json.loads((out / "selection_freeze.json").read_bytes())["calibration"]) == 18
    assert len(json.loads((out / "selection_freeze.json").read_bytes())["selection"]) == 6
    assert "procure.forecast.models" not in sys.modules
    assert len([e for e in events if e[0] == "fit"]) == 288
    first_attachment = next(i for i,e in enumerate(events) if e == ("attach", "validation"))
    assert len([e for e in events[:first_attachment] if e[0] == "fit"]) == 144
    # An empty defined failure entry of any type dominates success. Preserve it.
    (out / "FAILURE.json").mkdir()
    with pytest.raises(ValueError, match="failure entry"):
        verify_runner_run(out, expected_run=fixture[3])
    assert (out / "FAILURE.json").is_dir()


def test_admitted_unregistered_run_and_real_fitter_refuse_before_source(tmp_path, monkeypatch):
    from test_forecast_observed_admission import owned_fixture
    from procure.evaluation import observed_admission as a
    fixture = owned_fixture(tmp_path)
    source, _, _, binding, pp, mp, out = fixture
    with pytest.raises(ValueError, match="registered"):
        observed_runner.run_offline_evaluation(pp, mp, source, out, "2026-10-08",
                                              accepted_run=a.RootObservedRunBinding(binding.fixture_id))
    with pytest.raises(ValueError, match="real fitting HOLD"):
        _admitted_call(fixture)
    assert not out.exists()


@pytest.mark.parametrize("kind", ["file", "directory", "broken_symlink"])
def test_admitted_any_existing_output_entry_refuses_without_deletion(tmp_path, monkeypatch, kind):
    from test_forecast_observed_admission import owned_fixture
    fixture = owned_fixture(tmp_path)
    out = fixture[-1]
    if kind == "file":
        out.write_bytes(b"preserve")
    elif kind == "directory":
        out.mkdir()
    else:
        out.symlink_to(tmp_path / "absent")
    monkeypatch.setattr(observed_runner, "fit_in_owned_child", _admitted_fake_fit)
    with pytest.raises(ValueError, match="output entry"):
        _admitted_call(fixture)
    assert os.path.lexists(out)


def test_admitted_wrong_phase_and_foreign_freeze_refuse(tmp_path, monkeypatch):
    from test_forecast_observed_admission import owned_fixture
    fixture = owned_fixture(tmp_path)
    original = observed_runner._FixturePhases.attach
    def premature(self, split):
        with pytest.raises(ValueError, match="wrong phase"):
            original(self, "test")
        freeze = self.output / "validation_prediction_freeze.json"
        doc = json.loads(freeze.read_bytes()); doc["context"]["fixture_id"] = "foreign"
        freeze.write_text(json.dumps(doc))
        return original(self, split)
    monkeypatch.setattr(observed_runner, "fit_in_owned_child", _admitted_fake_fit)
    monkeypatch.setattr(observed_runner._FixturePhases, "attach", premature)
    with pytest.raises(ValueError, match="foreign/stale|identity"):
        _admitted_call(fixture)
    out = fixture[-1]
    assert (out / "FAILURE.json").exists() and not (out / "ADMITTED_COMPLETE.json").exists()


def test_admitted_final_close_error_preserves_nested_but_no_outer_complete(tmp_path, monkeypatch):
    from test_forecast_observed_admission import owned_fixture
    from procure.evaluation import observed_admission as a
    fixture = owned_fixture(tmp_path)
    real_close = a.ObservedNativeRead.close
    def late(read):
        real_close(read)
        raise ValueError("injected final close uncertainty")
    monkeypatch.setattr(a.ObservedNativeRead, "close", late)
    monkeypatch.setattr(observed_runner, "fit_in_owned_child", _admitted_fake_fit)
    with pytest.raises(ValueError, match="close uncertainty"):
        _admitted_call(fixture)
    out = fixture[-1]
    assert verify_runner_run(out / "legacy")
    assert (out / "FAILURE.json").exists() and not (out / "ADMITTED_COMPLETE.json").exists()
    with pytest.raises(ValueError, match="failure entry"):
        verify_runner_run(out, expected_run=fixture[3])


def test_admitted_deadline_failures_cannot_complete(tmp_path, monkeypatch):
    from test_forecast_observed_admission import owned_fixture
    fixture = owned_fixture(tmp_path)
    def deadline(*args, **kwargs):
        raise TimeoutError("fit_deadline")
    monkeypatch.setattr(observed_runner, "fit_in_owned_child", deadline)
    with pytest.raises(TimeoutError, match="fit_deadline"):
        _admitted_call(fixture)
    assert (fixture[-1] / "FAILURE.json").is_file()
    assert not (fixture[-1] / "ADMITTED_COMPLETE.json").exists()


def test_admitted_freeze_symlink_refuses_before_actual_attachment(tmp_path, monkeypatch):
    from test_forecast_observed_admission import owned_fixture
    fixture = owned_fixture(tmp_path)
    original = observed_runner._FixturePhases.attach
    def replaced(self, split):
        freeze = self.output / (split + "_prediction_freeze.json")
        preserved = tmp_path / "owned_copy.json"
        preserved.write_bytes(freeze.read_bytes())
        # Fault injection replaces a success artifact, never a failure marker.
        os.replace(preserved, tmp_path / "owned_copy_final.json")
        displaced = self.output / "preserved_freeze.json"
        os.rename(freeze, displaced)
        freeze.symlink_to(tmp_path / "owned_copy_final.json")
        return original(self, split)
    def forbidden(*args, **kwargs):
        raise AssertionError("symlink freeze reached actual attachment")
    monkeypatch.setattr(observed_runner, "fit_in_owned_child", _admitted_fake_fit)
    monkeypatch.setattr(observed_runner._FixturePhases, "attach", replaced)
    monkeypatch.setattr(observed_runner, "attach_frozen_actuals", forbidden)
    with pytest.raises((ValueError, OSError)):
        _admitted_call(fixture)
    assert (fixture[-1] / "FAILURE.json").exists()


def test_admitted_last_fit_deadline_cannot_escape_at_loop_end(tmp_path, monkeypatch):
    from test_forecast_observed_admission import owned_fixture
    fixture = owned_fixture(tmp_path)
    clock, calls = [0.], [0]
    def fit(*args, **kwargs):
        calls[0] += 1
        if calls[0] == 288:
            clock[0] += 61.
        return _admitted_fake_fit()
    monkeypatch.setattr(observed_runner.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(observed_runner, "fit_in_owned_child", fit)
    with pytest.raises(TimeoutError, match="fit_deadline"):
        _admitted_call(fixture)
    assert not (fixture[-1] / "ADMITTED_COMPLETE.json").exists()


def test_admitted_actual_attachment_rejects_duplicate_targets():
    from procure.evaluation.observed_admission import canonical, digest
    spec = {"series_kind": "commodity", "series_id": "copper", "unit": "USD/metric tonne",
            "source_id": "owned_fixture", "is_synthetic": True}
    training = {m: v for m,v in months().items() if m <= "2024-02"}
    rows = observed_runner.forecast_from_training(training, "2024-03", "naive", protocol(), spec,
                                                  "test", _admitted_fake_fit)
    rows[2] = dict(rows[0])
    raw = canonical(rows)
    with pytest.raises(ValueError, match="duplicate|identity"):
        observed_runner.attach_frozen_actuals(raw, {r["target"]: 100. for r in rows},
                                              expected_prediction_sha256=digest(raw))


def test_admitted_repaired_caller_hashes_cannot_replace_host_closure(tmp_path, monkeypatch):
    from test_forecast_observed_admission import owned_fixture
    fixture = owned_fixture(tmp_path)
    monkeypatch.setattr(observed_runner, "fit_in_owned_child", _admitted_fake_fit)
    _admitted_call(fixture)
    out = fixture[-1]
    (out / "candidate_ledger.json").write_text('{"entries": [], "provider_dl": "out_of_scope"}')
    complete = out / "ADMITTED_COMPLETE.json"
    closure = json.loads(complete.read_bytes())
    closure["artifact_sha256"] = observed_runner._admitted_artifacts(out)
    complete.write_text(json.dumps(closure))
    with pytest.raises(ValueError, match="host-issued|closure"):
        verify_runner_run(out, expected_run=fixture[3])


def test_admitted_unpinned_backend_disqualifies_whole_candidate(tmp_path, monkeypatch):
    from test_forecast_observed_admission import owned_fixture
    fixture = owned_fixture(tmp_path)
    def fit(model, *args, **kwargs):
        result = _admitted_fake_fit()
        if model == "gbm":
            result.backend = "lightgbm"
        return result
    monkeypatch.setattr(observed_runner, "fit_in_owned_child", fit)
    _admitted_call(fixture)
    out = fixture[-1]
    ledger = json.loads((out / "candidate_ledger.json").read_bytes())["entries"]
    candidates = [e for e in ledger if e["series_id"] in {"copper", "aluminum"} and e["model"] == "gbm"]
    assert len(candidates) == 12 and all(e["status"] == "not_evaluated" and e["backend"] is None
        and e["metrics"] is None and e["reason"] == "unsupported_unpinned_backend" for e in candidates)
    assert sum(e["status"] == "scored" for e in ledger) == 24
    rows = json.loads((out / "scored_rows.json").read_bytes())["predictions"]
    assert len(rows) == 576 and {r["model"] for r in rows} == {"naive", "ets"}


def test_admitted_failed_calibration_cannot_leave_partial_candidate_factors(tmp_path, monkeypatch):
    from test_forecast_observed_admission import owned_fixture
    fixture = owned_fixture(tmp_path)
    real = observed_runner.calibrate_intervals
    def failed(rows):
        sample = rows[0]
        if (sample["series_id"], sample["model"], sample["horizon"]) == ("copper", "ets", 3):
            raise ValueError("owned injected calibration failure")
        return real(rows)
    monkeypatch.setattr(observed_runner, "fit_in_owned_child", _admitted_fake_fit)
    monkeypatch.setattr(observed_runner, "calibrate_intervals", failed)
    _admitted_call(fixture)
    freeze = json.loads((fixture[-1] / "selection_freeze.json").read_bytes())
    assert not any(key.startswith("copper:ets:") for key in freeze["calibration"])
    assert freeze["failed_cohorts"]["copper:ets"] == "calibration_unavailable"


def test_admitted_future_actuals_are_scoring_only_under_fixed_snapshot_seed():
    from procure.evaluation.observed_admission import canonical, digest
    seen = []
    def fit(model, logs, *args):
        seen.append((tuple(logs.index.strftime("%Y-%m")), tuple(logs)))
        return SimpleNamespace(drift=np.zeros(7), residuals=np.array([-.02, -.01, 0, .01, .02, .03] * 2),
                               noise_scale=1., backend="fixture")
    p = protocol()
    spec = {"series_kind": "commodity", "series_id": "copper", "unit": "USD/metric tonne",
            "source_id": "owned_fixture", "is_synthetic": True}
    series = months()
    poisoned = {**series, "2024-04": 99999., "2024-06": 88888., "2024-09": 77777.}
    before = forecast_at_origin(series, "2024-03", "gbm", p, spec, "test", fit)
    after = forecast_at_origin(poisoned, "2024-03", "gbm", p, spec, "test", fit)
    assert seen[0] == seen[1] and len(seen[0][0]) == 90 and seen[0][0][-1] == "2024-02"
    assert [{k:v for k,v in r.items() if k != "actual"} for r in before] == [
            {k:v for k,v in r.items() if k != "actual"} for r in after]
    assert [r["actual"] for r in before] != [r["actual"] for r in after]
    # A genuinely changed complete snapshot has a DIFFERENT canonical seed.
    # This fixture comparison holds the snapshot identity fixed, not a PIT claim.
    changed = {**p, "snapshot_sha256": digest(canonical(poisoned))}
    assert canonical_seed(p, "copper", "2024-03") != canonical_seed(changed, "copper", "2024-03")


def test_admitted_replaced_output_is_not_owned_for_failure_writes(tmp_path, monkeypatch):
    from test_forecast_observed_admission import owned_fixture
    fixture = owned_fixture(tmp_path)
    out = fixture[-1]
    displaced = tmp_path / "preserved_incomplete_run"
    calls = []
    def replaced(*args, **kwargs):
        if not calls:
            os.rename(out, displaced)
            out.mkdir(mode=0o700)
        calls.append(1)
        return _admitted_fake_fit()
    monkeypatch.setattr(observed_runner, "fit_in_owned_child", replaced)
    with pytest.raises(ValueError):
        _admitted_call(fixture)
    assert list(out.iterdir()) == []
    assert (displaced / "HOST_PENDING.json").is_file()
    assert not (displaced / "ADMITTED_COMPLETE.json").exists()


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


def test_selection_uses_representable_mean_when_error_sum_overflows():
    rows = []
    for i in range(24):
        origin = f"{2022 + i // 12}-{i % 12 + 1:02d}"
        for model, actual in (("naive", 1e308), ("ets", 9e307)):
            rows.append({"series_id": "copper", "horizon": 1, "origin": origin,
                         "model": model, "p50": 1.0, "actual": actual})
    assert select_models(rows)[("copper", 1)] == "ets"


def test_interval_scaling_avoids_ratio_underflow():
    rows = [{"origin": f"{2022 + i // 12}-{i % 12 + 1:02d}",
             "p10": 1e-308, "p50": 1e308, "p90": 1e308, "actual": 1e308}
            for i in range(24)]
    multiplier, calibrated = calibrate_intervals(rows)
    assert multiplier == 0.8
    assert all(math.isfinite(row["p10"]) and row["p10"] > 0 and
               row["p50"] == 1e308 for row in calibrated)


def test_zero_width_calibration_keeps_exact_median_order():
    rows = [{"origin": f"{2022 + i // 12}-{i % 12 + 1:02d}",
             "p10": 100.0, "p50": 100.0, "p90": 100.0, "actual": 100.0}
            for i in range(24)]
    _, calibrated = calibrate_intervals(rows)
    assert all(row["p10"] == row["p50"] == row["p90"] == 100.0 for row in calibrated)


@pytest.mark.parametrize("field,bad", [
    ("model_leads_from_last_training_month", [1, 3, 6]),
    ("training_cutoff", "origin"),
    ("calibration_grid", [1.0]),
    ("selection_relative_improvement", 0.2),
    ("n_paths", True),
    ("fit_timeout_seconds", 60.0),
    ("availability_proxy", "same_month"),
    ("observed_execution_authorized", 1),
])
def test_protocol_rejects_behavioral_or_type_drift(field, bad):
    altered = protocol()
    altered[field] = bad
    with pytest.raises(ValueError, match="protocol"):
        plan_common_origins({"copper": months()}, "2026-10-08", altered)


def test_every_declared_behavioral_protocol_field_is_bound():
    for field, original in observed_runner.PROTOCOL_FIELDS.items():
        altered = protocol()
        altered[field] = original + ["drift"] if isinstance(original, list) else (
            original + 1 if type(original) is int else original + 0.01 if type(original) is float else original + "-drift")
        with pytest.raises(ValueError, match="protocol"):
            plan_common_origins({"copper": months()}, "2026-10-08", altered)


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
                "bytes": source.stat().st_size, "sheet": "Monthly Prices", "month_range": "2020M01-2020M02",
                "series": {"copper": {"label": "Copper", "column": "B", "unit": "($/mt)"}}}
    with pytest.raises(ValueError, match="HOLD|synthetic"):
        load_verified_native_series(manifest, "copper", source)
    assert load_verified_native_series(manifest, "copper", source, allow_synthetic_fixture=True) == {"2020-01": 10.0, "2020-02": 11.0}
    with pytest.raises(ValueError, match="hash"):
        load_verified_native_series(dict(manifest, sha256="0" * 64), "copper", source, allow_synthetic_fixture=True)
    with pytest.raises(ValueError, match="header|unit"):
        load_verified_native_series(dict(manifest, series={"copper": {"label": "Copper", "column": "B", "unit": "($/dmtu)"}}), "copper", source, allow_synthetic_fixture=True)
    with pytest.raises(ValueError, match="month range"):
        load_verified_native_series(dict(manifest, month_range="2020M01-2020M03"),
                                    "copper", source, allow_synthetic_fixture=True, through_month="2020-01")


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
                "sheet": "Monthly Prices", "month_range": "2024M02-2024M03",
                "series": {"copper": {"label": "Copper", "column": "B", "unit": "($/mt)"}}}
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


def test_sigterm_reaps_owned_child_group_with_inherited_pipe(tmp_path):
    pid_file = tmp_path / "owned.pid"
    child_code = ("import os,signal,subprocess,sys,time;"
                  f"open({str(pid_file)!r},'w').write(str(os.getpid()));"
                  "subprocess.Popen([sys.executable,'-c','import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)']);"
                  "time.sleep(30)")
    parent_code = ("from procure.evaluation.observed_runner import run_bounded_child;"
                   f"run_bounded_child([{sys.executable!r},'-c',{child_code!r}],b'',timeout=60)")
    parent = subprocess.Popen([sys.executable, "-c", parent_code], env={**os.environ, "PYTHONPATH": "src"},
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 5
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert pid_file.exists()
        child_pid = int(pid_file.read_text())
        os.kill(parent.pid, signal.SIGTERM)
        parent.wait(timeout=5)
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
    finally:
        if pid_file.exists():
            try:
                os.killpg(int(pid_file.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=2)
        for pipe in (parent.stdout, parent.stderr):
            if pipe is not None:
                pipe.close()


def test_term_responsive_descendant_closes_inherited_pipe_without_permission_leak(tmp_path):
    pid_file = tmp_path / "leader.pid"
    child_code = ("import os,subprocess,sys;"
                  f"open({str(pid_file)!r},'w').write(str(os.getpid()));"
                  "subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])")
    try:
        with pytest.raises(TimeoutError, match="owned child fit timed out"):
            run_bounded_child([sys.executable, "-c", child_code], b"", timeout=0.5)
        with pytest.raises(ProcessLookupError):
            os.kill(int(pid_file.read_text()), 0)
    finally:
        if pid_file.exists():
            try:
                os.killpg(int(pid_file.read_text()), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass


def test_unverified_group_cleanup_is_fatal_incomplete_and_sanitized(monkeypatch, tmp_path):
    # New registered host caller; the public child boundary is mocked here.
    # Existing lower-level owned-process tests retain actual cleanup coverage.
    from test_forecast_observed_admission import owned_fixture
    fixture = owned_fixture(tmp_path)
    def failed_boundary(*args, **kwargs):
        raise observed_runner.OwnedChildCleanupError("owned_child_cleanup_unverified")
    monkeypatch.setattr(observed_runner, "fit_in_owned_child", failed_boundary)
    with pytest.raises(RuntimeError, match="owned_child_cleanup_unverified") as error:
        _admitted_call(fixture)
    assert str(tmp_path) not in str(error.value)
    out = fixture[-1]
    state = json.loads((out / "FAILURE.json").read_text())
    assert state["status"] == "incomplete" and state["reason"] == "owned_child_cleanup_unverified"
    assert not (out / "ADMITTED_COMPLETE.json").exists()
    assert not (out / "legacy/scorecard").exists()


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


def test_admitted_caller_approved_flags_cannot_open_source_without_binding(monkeypatch, tmp_path):
    """APPROVED text is identity data, never host-issued execution authority."""
    p = {**protocol(), "status": "APPROVED_OBSERVED_EXECUTION",
         "observed_execution_authorized": True}
    manifest = {"eligibility_status": "APPROVED_OBSERVED", "rights_status": "APPROVED",
                "is_synthetic": False, "sha256": p["source_sha256"],
                "series": {s: {"eligibility_status": "APPROVED_OBSERVED"}
                           for s in ("copper", "aluminum")}}
    pp, mp = tmp_path / "protocol.json", tmp_path / "source.json"
    pp.write_text(json.dumps(p)); mp.write_text(json.dumps(manifest))
    def forbidden(*args, **kwargs):
        raise AssertionError("unregistered approved text reached source body boundary")
    monkeypatch.setattr(observed_runner, "load_verified_native_series", forbidden)
    with pytest.raises(ValueError, match="registered.*HOLD"):
        observed_runner.run_offline_evaluation(pp, mp, tmp_path / "fake.xlsx",
                                              tmp_path / "run", "2026-10-08")
    assert not (tmp_path / "run").exists()


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


def test_cli_missing_input_never_echoes_sensitive_path(tmp_path):
    project = Path(__file__).parents[1]
    secret_path = tmp_path / "private-customer-source.xlsx"
    result = subprocess.run([sys.executable, str(project / "scripts/run_observed_forecast_evaluation.py"),
                             "--protocol", str(secret_path), "--source-manifest", str(secret_path),
                             "--source-file", str(secret_path), "--output-dir", str(tmp_path / "out"),
                             "--as-of", "2026-10-08"], capture_output=True, text=True)
    assert result.returncode == 2
    assert str(secret_path) not in result.stderr


def test_failed_test_challenger_removes_both_splits_without_reselection():
    origins = [f"{2022 + i // 12}-{i % 12 + 1:02d}" for i in range(24)]
    validation = []
    for origin in origins:
        for model, median in (("naive", 90.0), ("ets", 99.0), ("gbm", 95.0)):
            validation.append({"series_id": "copper", "horizon": 1, "origin": origin,
                               "model": model, "p50": median, "actual": 100.0, "split": "validation"})
    selected = select_models(validation)
    assert selected[("copper", 1)] == "ets"
    ledger = [{"series_id": "copper", "model": model, "split": split,
               "status": "pending", "reason": None}
              for model in ("naive", "ets", "gbm") for split in ("validation", "test")]
    test = {("copper", "naive"): [{"model": "naive", "split": "test", "series_id": "copper"}],
            ("copper", "gbm"): [{"model": "gbm", "split": "test", "series_id": "copper"}]}
    rows, final = observed_runner._finalize_cohorts(validation, test, {("copper", "ets"): "fit_unavailable"}, ledger)
    assert selected[("copper", 1)] == "ets"  # selection stays frozen despite test failure
    assert not any(row["model"] == "ets" for row in rows)
    assert [item["reason"] for item in final if item["model"] == "ets"] == ["fit_unavailable"] * 2
    assert all(item["status"] == "scored" for item in final if item["model"] in {"naive", "gbm"})


def test_failed_naive_disqualifies_entire_series():
    ledger = [{"series_id": "copper", "model": model, "split": split,
               "status": "pending", "reason": None}
              for model in ("naive", "ets") for split in ("validation", "test")]
    rows, final = observed_runner._finalize_cohorts([{"series_id": "copper", "model": "ets", "split": "validation"}],
                                    {("copper", "ets"): [{"series_id": "copper", "model": "ets", "split": "test"}]},
                                    {("copper", "naive"): "fit_unavailable"}, ledger)
    assert rows == []
    assert all(item["status"] == "not_evaluated" for item in final)


def test_global_interruption_leaves_incomplete_receipt(monkeypatch, tmp_path):
    from test_forecast_observed_admission import owned_fixture
    fixture = owned_fixture(tmp_path)
    def interrupted(*_args, **_kwargs):
        raise InterruptedError("synthetic owned-child interruption")
    monkeypatch.setattr(observed_runner, "fit_in_owned_child", interrupted)
    out = fixture[-1]
    with pytest.raises(RuntimeError, match="fit_interrupted"):
        _admitted_call(fixture)
    assert json.loads((out / "FAILURE.json").read_text())["reason"] == "fit_interrupted"
    assert not (out / "ADMITTED_COMPLETE.json").exists()
