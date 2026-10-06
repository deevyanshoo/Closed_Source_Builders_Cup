"""Small synthetic DuckDB snapshots; no ingestion, provider, or settings mutation."""
from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path

import duckdb
import pytest

from procure import db
from procure.service_cache import CacheUnavailable, restore_snapshot, validate_database

REVISION = "a" * 40


def database(path: Path, materials=("copper", "aluminium")) -> None:
    con = duckdb.connect(str(path))
    try:
        for statement in db.SCHEMA:
            con.execute(statement)
        con.execute("INSERT INTO fx_monthly (month, usdinr, source, is_synthetic, fetched_at) VALUES (?, ?, ?, ?, ?)",
                    ["2026-01-01", 84.0, "fixture", True, "2026-02-01T00:00:00Z"])
        for material in materials:
            con.execute("INSERT INTO prices_monthly (material, month, usd_per_unit, unit, source, is_synthetic, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        [material, "2026-01-01", 10.0, "kg", "fixture", True, "2026-02-01T00:00:00Z"])
            con.execute("INSERT INTO model_selection (material, selected_model, best_model, naive_mape, best_mape, improvement, beats_naive, residual_scale, evaluated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        [material, "naive", "ets", 0.2, 0.1, 0.5, False, 1.0, "2026-02-01T00:00:00Z"])
            con.execute("INSERT INTO model_metrics (material, model, horizon, mape, coverage, n_obs, evaluated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        [material, "naive", 1, 0.2, 0.8, 8, "2026-02-01T00:00:00Z"])
    finally:
        con.close()


def snapshot(tmp_path: Path, materials=("copper", "aluminium")) -> tuple[Path, str]:
    folder = tmp_path / "snapshot"
    folder.mkdir()
    source = folder / "app.duckdb"
    database(source, materials)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    (folder / "manifest.json").write_text(json.dumps({
        "format": "closed-source-cache-v1", "engine": "duckdb", "file": "app.duckdb",
        "sha256": digest, "source_revision": REVISION, "synthetic": True, "sample": False,
    }))
    return folder, digest


def manifest(folder: Path, **changes) -> None:
    path = folder / "manifest.json"
    record = json.loads(path.read_text())
    record.update(changes)
    path.write_text(json.dumps(record))


def test_valid_snapshot_restores_private_copy_and_repeats_from_source(tmp_path):
    folder, digest = snapshot(tmp_path)
    source = folder / "app.duckdb"
    original = source.read_bytes()
    target = tmp_path / "data"
    info = restore_snapshot(folder, target, digest, ("copper", "aluminium"))
    restored = target / "app.duckdb"
    assert info["db_path"] == str(restored)
    assert info["source_revision"] == REVISION
    assert info["synthetic"] is True and info["sample"] is False
    assert hashlib.sha256(restored.read_bytes()).hexdigest() == digest
    assert stat.S_IMODE(restored.stat().st_mode) == 0o600
    assert source.read_bytes() == original
    with restored.open("ab") as handle:
        handle.write(b"ephemeral")
    restore_snapshot(folder, target, digest, ("copper", "aluminium"))
    assert restored.read_bytes() == original
    assert source.read_bytes() == original


@pytest.mark.parametrize("label", [{"synthetic": False}, {"sample": True}])
def test_manifest_provenance_labels_must_match_database(tmp_path, label):
    folder, digest = snapshot(tmp_path, ("copper",))
    manifest(folder, **label)
    target = tmp_path / "data"
    with pytest.raises(CacheUnavailable, match="provenance"):
        restore_snapshot(folder, target, digest, ("copper",))
    assert not (target / "app.duckdb").exists()


@pytest.mark.parametrize("changes", [
    {"engine": "sqlite"}, {"format": "other"}, {"file": "other.duckdb"},
    {"source_revision": None}, {"synthetic": None}, {"sample": "false"},
    {"sha256": "A" * 64},
])
def test_invalid_manifest_fails_without_replacing_existing_target(tmp_path, changes):
    folder, digest = snapshot(tmp_path)
    target = tmp_path / "data"
    target.mkdir()
    existing = target / "app.duckdb"
    existing.write_bytes(b"existing")
    manifest(folder, **changes)
    with pytest.raises(CacheUnavailable):
        restore_snapshot(folder, target, digest, ("copper", "aluminium"))
    assert existing.read_bytes() == b"existing"


def test_missing_or_corrupt_source_and_wrong_pin_fail_closed(tmp_path):
    folder, digest = snapshot(tmp_path)
    target = tmp_path / "data"
    with pytest.raises(CacheUnavailable):
        restore_snapshot(folder, target, "b" * 64, ("copper",))
    (folder / "app.duckdb").write_bytes(b"corrupt")
    with pytest.raises(CacheUnavailable):
        restore_snapshot(folder, target, digest, ("copper",))
    (folder / "app.duckdb").unlink()
    with pytest.raises(CacheUnavailable):
        restore_snapshot(folder, target, digest, ("copper",))
    assert not (target / "app.duckdb").exists()


def test_schema_and_material_coverage_checked_before_replacing_target(tmp_path):
    folder, digest = snapshot(tmp_path, ("copper",))
    target = tmp_path / "data"
    target.mkdir()
    existing = target / "app.duckdb"
    existing.write_bytes(b"keep")
    with pytest.raises(CacheUnavailable):
        restore_snapshot(folder, target, digest, ("copper", "aluminium"))
    assert existing.read_bytes() == b"keep"
    con = duckdb.connect(str(folder / "app.duckdb"))
    con.execute("DELETE FROM model_metrics WHERE material = 'copper'")
    con.close()
    digest = hashlib.sha256((folder / "app.duckdb").read_bytes()).hexdigest()
    manifest(folder, sha256=digest)
    with pytest.raises(CacheUnavailable):
        restore_snapshot(folder, target, digest, ("copper",))
    assert existing.read_bytes() == b"keep"


def test_validate_database_rejects_missing_required_table_and_empty_fx(tmp_path):
    path = tmp_path / "app.duckdb"
    database(path, ("copper",))
    con = duckdb.connect(str(path))
    con.execute("DROP TABLE recommendations")
    con.close()
    with pytest.raises(CacheUnavailable):
        validate_database(path, ("copper",))
    path.unlink()
    database(path, ("copper",))
    con = duckdb.connect(str(path))
    con.execute("DELETE FROM fx_monthly")
    con.close()
    with pytest.raises(CacheUnavailable):
        validate_database(path, ("copper",))


def test_symlink_and_source_target_overlap_are_rejected(tmp_path):
    folder, digest = snapshot(tmp_path)
    target = tmp_path / "data"
    target.symlink_to(folder, target_is_directory=True)
    with pytest.raises(CacheUnavailable):
        restore_snapshot(folder, target, digest, ("copper",))
    target.unlink()
    with pytest.raises(CacheUnavailable):
        restore_snapshot(folder, folder / "nested-data", digest, ("copper",))
    linked = tmp_path / "linked.duckdb"
    linked.symlink_to(folder / "app.duckdb")
    with pytest.raises(CacheUnavailable):
        validate_database(linked, ("copper",))


def test_missing_column_selection_or_selection_metadata_is_unavailable(tmp_path):
    path = tmp_path / "app.duckdb"
    database(path, ("copper",))
    con = duckdb.connect(str(path))
    con.execute("ALTER TABLE model_metrics DROP COLUMN evaluated_at")
    con.close()
    with pytest.raises(CacheUnavailable):
        validate_database(path, ("copper",))
    path.unlink()
    database(path, ("copper",))
    con = duckdb.connect(str(path))
    con.execute("DELETE FROM model_selection WHERE material = 'copper'")
    con.close()
    with pytest.raises(CacheUnavailable):
        validate_database(path, ("copper",))
    con = duckdb.connect(str(path))
    con.execute("INSERT INTO model_selection (material, selected_model, best_model, naive_mape, best_mape, improvement, beats_naive, residual_scale, evaluated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ["copper", "naive", "ets", 0.2, 0.1, 0.5, False, 1.0, None])
    con.close()
    with pytest.raises(CacheUnavailable):
        validate_database(path, ("copper",))


def test_sqlite_file_is_explicitly_unavailable(tmp_path):
    import sqlite3

    path = tmp_path / "app.duckdb"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE prices_monthly (material TEXT)")
    con.close()
    with pytest.raises(CacheUnavailable, match="DuckDB"):
        validate_database(path, ("copper",))


def test_schema_compatible_sqlite_file_is_still_unavailable(tmp_path):
    import sqlite3

    path = tmp_path / "app.duckdb"
    con = sqlite3.connect(path)
    for statement in db.SCHEMA:
        con.execute(statement)
    con.execute("INSERT INTO fx_monthly (month, usdinr, source, is_synthetic, fetched_at) VALUES (?, ?, ?, ?, ?)",
                ["2026-01-01", 84.0, "fixture", True, "2026-02-01T00:00:00Z"])
    con.execute("INSERT INTO prices_monthly (material, month, usd_per_unit, unit, source, is_synthetic, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ["copper", "2026-01-01", 10.0, "kg", "fixture", True, "2026-02-01T00:00:00Z"])
    con.execute("INSERT INTO model_selection (material, selected_model, best_model, naive_mape, best_mape, improvement, beats_naive, residual_scale, evaluated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ["copper", "naive", "ets", 0.2, 0.1, 0.5, False, 1.0, "2026-02-01T00:00:00Z"])
    con.execute("INSERT INTO model_metrics (material, model, horizon, mape, coverage, n_obs, evaluated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ["copper", "naive", 1, 0.2, 0.8, 8, "2026-02-01T00:00:00Z"])
    con.commit()
    con.close()
    with pytest.raises(CacheUnavailable, match="DuckDB"):
        validate_database(path, ("copper",))


def test_source_and_existing_target_symlinks_are_rejected(tmp_path):
    folder, digest = snapshot(tmp_path)
    source = folder / "app.duckdb"
    real_source = folder / "real.duckdb"
    source.rename(real_source)
    source.symlink_to(real_source)
    with pytest.raises(CacheUnavailable):
        restore_snapshot(folder, tmp_path / "data", digest, ("copper",))
    source.unlink()
    real_source.rename(source)
    target = tmp_path / "data"
    target.mkdir()
    (target / "app.duckdb").symlink_to(source)
    with pytest.raises(CacheUnavailable):
        restore_snapshot(folder, target, digest, ("copper",))
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
