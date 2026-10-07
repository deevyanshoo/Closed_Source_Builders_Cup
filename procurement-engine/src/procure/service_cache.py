"""Validate a pinned DuckDB snapshot and rebuild one mutable local cache copy.

This module never uses the legacy process-wide database connection or starts ingestion.
The caller owns the list of configured materials and the trusted digest.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile

import duckdb


class CacheUnavailable(RuntimeError):
    """A cache cannot safely serve the configured numerical routes."""


_HASH = re.compile(r"[0-9a-f]{64}\Z")
_REVISION = re.compile(r"[0-9a-f]{40}\Z")
_COLUMNS = {
    "prices_monthly": {"material", "month", "usd_per_unit", "unit", "source", "is_synthetic", "fetched_at", "url"},
    "fx_monthly": {"month", "usdinr", "source", "is_synthetic", "fetched_at", "url"},
    "news_items": {"id", "material", "title", "url", "domain", "seen_at", "relevant", "direction", "severity", "horizon_months", "reason", "extractor", "is_sample", "source"},
    "model_metrics": {"material", "model", "horizon", "mape", "directional_acc", "coverage", "n_obs", "evaluated_at"},
    "model_selection": {"material", "selected_model", "best_model", "naive_mape", "best_mape", "improvement", "beats_naive", "residual_scale", "evaluated_at"},
    "recommendations": {"id", "created_at", "kind", "request_json", "result_json"},
    "actions": {"id", "recommendation_id", "type", "status", "payload_json", "reason", "created_at", "updated_at"},
    "ingest_runs": {"id", "source", "status", "rows", "used", "message", "started_at", "finished_at", "url"},
}


def _no_symlink(path: Path) -> None:
    """Reject symlinks in supplied paths, including an existing parent."""
    for part in (path, *path.parents):
        if part.is_symlink():
            raise CacheUnavailable(f"symlink in cache path: {part}")


def _regular_file(path: Path) -> None:
    _no_symlink(path)
    try:
        mode = path.stat().st_mode
    except OSError as exc:
        raise CacheUnavailable(f"missing cache file: {path}") from exc
    if not stat.S_ISREG(mode):
        raise CacheUnavailable(f"cache path is not a regular file: {path}")


def _materials(values: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(values, tuple) or not values or any(not isinstance(v, str) or not v.strip() for v in values):
        raise CacheUnavailable("configured materials are required")
    if len(set(values)) != len(values):
        raise CacheUnavailable("configured materials contain duplicates")
    return values


def _count(con: duckdb.DuckDBPyConnection, query: str, args: list | None = None) -> int:
    return int(con.execute(query, args or []).fetchone()[0])


def validate_database(path: Path, materials: tuple[str, ...]) -> None:
    """Read a compatible DuckDB source without creating tables or mutating settings."""
    path = Path(path)
    _materials(materials)
    _regular_file(path)
    try:
        with path.open("rb") as source:
            source.seek(8)
            if source.read(4) != b"DUCK":
                raise CacheUnavailable("cache is not a native DuckDB database")
        con = duckdb.connect(str(path), read_only=True, config={
            "autoload_known_extensions": "false", "autoinstall_known_extensions": "false",
        })
    except (duckdb.Error, OSError) as exc:
        raise CacheUnavailable("cache is not a readable DuckDB database") from exc
    try:
        for table, required in _COLUMNS.items():
            present = {row[1] for row in con.execute(f"PRAGMA table_info('{table}')").fetchall()}
            if not required.issubset(present):
                raise CacheUnavailable(f"cache schema is missing {table} columns")
        if not _count(con, "SELECT COUNT(*) FROM prices_monthly"):
            raise CacheUnavailable("price history is empty")
        if not _count(con, "SELECT COUNT(*) FROM fx_monthly"):
            raise CacheUnavailable("FX history is empty")
        if _count(con, "SELECT COUNT(*) FROM fx_monthly WHERE month IS NULL OR usdinr IS NULL OR NOT isfinite(usdinr) OR usdinr <= 0 OR source IS NULL OR trim(source) = '' OR fetched_at IS NULL OR trim(fetched_at) = '' OR is_synthetic IS NULL"):
            raise CacheUnavailable("FX history has unusable rows")
        if _count(con, "SELECT COUNT(*) FROM prices_monthly WHERE material IS NULL OR trim(material) = '' OR month IS NULL OR usd_per_unit IS NULL OR NOT isfinite(usd_per_unit) OR usd_per_unit <= 0 OR unit IS NULL OR trim(unit) = '' OR source IS NULL OR trim(source) = '' OR fetched_at IS NULL OR trim(fetched_at) = '' OR is_synthetic IS NULL"):
            raise CacheUnavailable("price history has unusable rows")
        for material in materials:
            if not _count(con, "SELECT COUNT(*) FROM prices_monthly WHERE material = ?", [material]):
                raise CacheUnavailable(f"price history missing configured material: {material}")
            rows = con.execute("SELECT selected_model, best_model, naive_mape, best_mape, residual_scale, evaluated_at FROM model_selection WHERE material = ?", [material]).fetchall()
            if len(rows) != 1:
                raise CacheUnavailable(f"model selection missing or ambiguous: {material}")
            selected, best, naive_mape, best_mape, scale, evaluated_at = rows[0]
            if (not isinstance(selected, str) or not selected.strip() or not isinstance(best, str) or not best.strip()
                    or any(not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in (naive_mape, best_mape))
                    or not isinstance(scale, (int, float)) or not math.isfinite(scale) or scale <= 0
                    or not isinstance(evaluated_at, str) or not evaluated_at.strip()):
                raise CacheUnavailable(f"model selection metadata invalid: {material}")
            if not _count(con, "SELECT COUNT(*) FROM model_metrics WHERE material = ? AND model = ? AND horizon > 0 AND mape IS NOT NULL AND isfinite(mape) AND mape >= 0 AND coverage IS NOT NULL AND isfinite(coverage) AND coverage BETWEEN 0 AND 1 AND n_obs > 0 AND evaluated_at IS NOT NULL AND trim(evaluated_at) <> ''", [material, selected]):
                raise CacheUnavailable(f"selected model metrics missing: {material}")
    except duckdb.Error as exc:
        raise CacheUnavailable("cache schema or data is incompatible") from exc
    finally:
        con.close()


def provenance_labels(path: Path) -> tuple[bool, bool]:
    """Derive snapshot labels from rows rather than trusting operator flags."""
    path = Path(path)
    _regular_file(path)
    try:
        con = duckdb.connect(str(path), read_only=True, config={
            "autoload_known_extensions": "false", "autoinstall_known_extensions": "false",
        })
        try:
            if _count(con, "SELECT COUNT(*) FROM news_items WHERE is_sample IS NULL"):
                raise CacheUnavailable("news provenance label is missing")
            synthetic = con.execute("SELECT COALESCE(BOOL_OR(is_synthetic), FALSE) FROM prices_monthly").fetchone()[0]
            synthetic_fx = con.execute("SELECT COALESCE(BOOL_OR(is_synthetic), FALSE) FROM fx_monthly").fetchone()[0]
            sample = con.execute("SELECT COALESCE(BOOL_OR(is_sample), FALSE) FROM news_items").fetchone()[0]
            return bool(synthetic or synthetic_fx), bool(sample)
        finally:
            con.close()
    except duckdb.Error as exc:
        raise CacheUnavailable("cache provenance is unavailable") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise CacheUnavailable(f"cannot read cache file: {path}") from exc
    return digest.hexdigest()


def _manifest(path: Path, expected_hash: str) -> dict:
    _regular_file(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CacheUnavailable("snapshot manifest is unreadable") from exc
    if (not isinstance(value, dict) or value.get("format") != "closed-source-cache-v1"
            or value.get("engine") != "duckdb" or value.get("file") != "app.duckdb"
            or not isinstance(value.get("sha256"), str) or not _HASH.fullmatch(value["sha256"])
            or value["sha256"] != expected_hash
            or not isinstance(value.get("source_revision"), str) or not _REVISION.fullmatch(value["source_revision"])
            or not isinstance(value.get("synthetic"), bool) or not isinstance(value.get("sample"), bool)):
        raise CacheUnavailable("snapshot manifest or pinned digest is invalid")
    return value


def restore_snapshot(snapshot_dir: Path, data_dir: Path, expected_hash: str,
                     materials: tuple[str, ...]) -> dict:
    """Verify immutable source, then atomically replace task-owned app.duckdb."""
    snapshot_dir, data_dir = Path(snapshot_dir), Path(data_dir)
    _materials(materials)
    if not isinstance(expected_hash, str) or not _HASH.fullmatch(expected_hash):
        raise CacheUnavailable("expected snapshot SHA-256 must be lowercase hex")
    _no_symlink(snapshot_dir)
    _no_symlink(data_dir)
    if not snapshot_dir.is_dir():
        raise CacheUnavailable("snapshot directory is missing")
    source_dir, target_dir = snapshot_dir.resolve(), data_dir.resolve()
    if source_dir == target_dir or source_dir in target_dir.parents or target_dir in source_dir.parents:
        raise CacheUnavailable("snapshot and mutable cache paths overlap")
    target = data_dir / "app.duckdb"
    _no_symlink(target)
    if target.exists() and not target.is_file():
        raise CacheUnavailable("mutable cache target is not a regular file")
    manifest = _manifest(snapshot_dir / "manifest.json", expected_hash)
    source = snapshot_dir / "app.duckdb"
    _regular_file(source)
    if _sha256(source) != expected_hash:
        raise CacheUnavailable("snapshot bytes do not match pinned SHA-256")
    validate_database(source, materials)
    if provenance_labels(source) != (manifest["synthetic"], manifest["sample"]):
        raise CacheUnavailable("snapshot provenance labels do not match source rows")
    try:
        data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".app-", suffix=".duckdb", dir=data_dir)
        temp = Path(temp_name)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as output, source.open("rb") as input_stream:
                shutil.copyfileobj(input_stream, output, length=1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
            if _sha256(temp) != expected_hash:
                raise CacheUnavailable("copied cache bytes do not match pinned SHA-256")
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)
    except OSError as exc:
        raise CacheUnavailable("mutable cache restore failed") from exc
    return {"db_path": str(target), "sha256": expected_hash,
            "source_revision": manifest["source_revision"], "synthetic": manifest["synthetic"],
            "sample": manifest["sample"]}
