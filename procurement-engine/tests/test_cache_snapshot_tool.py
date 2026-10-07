"""Offline export of an already-existing synthetic cache only."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import duckdb

from procure import db
from procure.service_cache import restore_snapshot

TOOL = Path(__file__).resolve().parents[1] / "tools" / "cache_snapshot.py"
REVISION = "a" * 40


def source_database(path: Path, *, metrics: bool = True) -> None:
    con = duckdb.connect(str(path))
    try:
        for statement in db.SCHEMA:
            con.execute(statement)
        con.execute("INSERT INTO fx_monthly (month, usdinr, source, is_synthetic, fetched_at) VALUES (?, ?, ?, ?, ?)",
                    ["2026-01-01", 84.0, "fixture", True, "2026-02-01T00:00:00Z"])
        con.execute("INSERT INTO prices_monthly (material, month, usd_per_unit, unit, source, is_synthetic, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    ["copper", "2026-01-01", 10.0, "kg", "fixture", True, "2026-02-01T00:00:00Z"])
        con.execute("INSERT INTO model_selection (material, selected_model, best_model, naive_mape, best_mape, improvement, beats_naive, residual_scale, evaluated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    ["copper", "naive", "ets", 0.2, 0.1, 0.5, False, 1.0, "2026-02-01T00:00:00Z"])
        if metrics:
            con.execute("INSERT INTO model_metrics (material, model, horizon, mape, coverage, n_obs, evaluated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        ["copper", "naive", 1, 0.2, 0.8, 8, "2026-02-01T00:00:00Z"])
    finally:
        con.close()


def export(source: Path, output: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([
        sys.executable, str(TOOL), "--source", str(source), "--output", str(output),
        "--source-revision", REVISION, "--synthetic", "true", "--sample", "false",
        "--material", "copper", *extra,
    ], capture_output=True, text=True, timeout=20)


def test_exports_pinned_snapshot_without_mutating_source(tmp_path):
    source = tmp_path / "existing" / "app.duckdb"
    source.parent.mkdir()
    source_database(source)
    original = source.read_bytes()
    output = tmp_path / "export" / "snapshot"
    result = export(source, output)
    assert result.returncode == 0, result.stderr
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest == {"format": "closed-source-cache-v1", "engine": "duckdb",
                        "file": "app.duckdb", "sha256": hashlib.sha256(original).hexdigest(),
                        "source_revision": REVISION, "synthetic": True, "sample": False}
    assert (output / "app.duckdb").read_bytes() == original
    assert source.read_bytes() == original
    assert result.stdout.strip() == manifest["sha256"]
    restored = restore_snapshot(output, tmp_path / "mutable", manifest["sha256"], ("copper",))
    assert Path(restored["db_path"]).read_bytes() == original
    assert export(source, output).returncode != 0  # existing snapshot is never overwritten


def test_rejects_incompatible_source_before_creating_output(tmp_path):
    source = tmp_path / "app.duckdb"
    source_database(source, metrics=False)
    output = tmp_path / "snapshot"
    result = export(source, output)
    assert result.returncode != 0
    assert not output.exists()


def test_rejects_overlap_symlink_and_invalid_labels(tmp_path):
    source = tmp_path / "source" / "app.duckdb"
    source.parent.mkdir()
    source_database(source)
    original = source.read_bytes()
    assert export(source, source.parent / "snapshot").returncode != 0
    link = tmp_path / "linked.duckdb"
    link.symlink_to(source)
    assert export(link, tmp_path / "snapshot").returncode != 0
    assert export(source, tmp_path / "snapshot", "--synthetic", "maybe").returncode != 0
    assert source.read_bytes() == original
    assert not (tmp_path / "snapshot").exists()


def test_rejects_false_observed_and_non_sample_labels(tmp_path):
    source = tmp_path / "source" / "app.duckdb"
    source.parent.mkdir()
    source_database(source)
    output = tmp_path / "snapshot"
    assert export(source, output, "--synthetic", "false").returncode != 0
    assert not output.exists()
    con = duckdb.connect(str(source))
    con.execute("INSERT INTO news_items (id, material, title, is_sample) VALUES (?, ?, ?, ?)",
                ["n1", "copper", "fixture", True])
    con.close()
    assert export(source, output).returncode != 0  # --sample false cannot hide sample news
    assert not output.exists()
