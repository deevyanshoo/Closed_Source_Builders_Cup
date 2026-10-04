"""Database access. DuckDB by default, SQLite (stdlib) as a fallback.

All timestamps and months are stored as ISO text so the same SQL works on both
engines. Access is serialised with a process-wide lock because the API serves
requests from a thread pool.
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import pandas as pd

from .settings import get_settings

log = logging.getLogger(__name__)

try:  # pragma: no cover - exercised implicitly
    import duckdb  # type: ignore

    ENGINE = "duckdb"
except Exception:  # pragma: no cover
    import sqlite3

    duckdb = None  # type: ignore
    ENGINE = "sqlite"

_LOCK = threading.RLock()
_CONN: Any = None
_CONN_PATH: Path | None = None

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS prices_monthly (
        material TEXT, month TEXT, usd_per_unit DOUBLE, unit TEXT, source TEXT,
        is_synthetic BOOLEAN, fetched_at TEXT, url TEXT)""",
    """CREATE TABLE IF NOT EXISTS fx_monthly (
        month TEXT, usdinr DOUBLE, source TEXT, is_synthetic BOOLEAN, fetched_at TEXT, url TEXT)""",
    """CREATE TABLE IF NOT EXISTS news_items (
        id TEXT, material TEXT, title TEXT, url TEXT, domain TEXT, seen_at TEXT,
        relevant BOOLEAN, direction TEXT, severity DOUBLE, horizon_months INTEGER, reason TEXT,
        extractor TEXT, is_sample BOOLEAN, source TEXT)""",
    """CREATE TABLE IF NOT EXISTS model_metrics (
        material TEXT, model TEXT, horizon INTEGER, mape DOUBLE, directional_acc DOUBLE,
        coverage DOUBLE, n_obs INTEGER, evaluated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS model_selection (
        material TEXT, selected_model TEXT, best_model TEXT, naive_mape DOUBLE, best_mape DOUBLE,
        improvement DOUBLE, beats_naive BOOLEAN, residual_scale DOUBLE, evaluated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS recommendations (
        id TEXT, created_at TEXT, kind TEXT, request_json TEXT, result_json TEXT)""",
    """CREATE TABLE IF NOT EXISTS actions (
        id TEXT, recommendation_id TEXT, type TEXT, status TEXT, payload_json TEXT, reason TEXT,
        created_at TEXT, updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS ingest_runs (
        id TEXT, source TEXT, status TEXT, rows INTEGER, used TEXT, message TEXT,
        started_at TEXT, finished_at TEXT, url TEXT)""",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def _connect() -> Any:
    global _CONN, _CONN_PATH
    path = get_settings().db_path
    if _CONN is not None and _CONN_PATH == path:
        return _CONN
    if _CONN is not None:
        close()
    path.parent.mkdir(parents=True, exist_ok=True)
    if ENGINE == "duckdb":
        _CONN = duckdb.connect(str(path))
    else:  # pragma: no cover
        _CONN = sqlite3.connect(str(path.with_suffix(".sqlite")), check_same_thread=False)
    _CONN_PATH = path
    for stmt in SCHEMA:
        _CONN.execute(stmt)
    log.info("Database ready (%s at %s)", ENGINE, path)
    return _CONN


def close() -> None:
    global _CONN, _CONN_PATH
    with _LOCK:
        if _CONN is not None:
            try:
                _CONN.close()
            except Exception:
                pass
        _CONN, _CONN_PATH = None, None


def execute(sql: str, params: Sequence[Any] | None = None) -> None:
    with _LOCK:
        con = _connect()
        con.execute(sql, list(params or []))
        if ENGINE == "sqlite":  # pragma: no cover
            con.commit()


def executemany(sql: str, rows: Iterable[Sequence[Any]]) -> None:
    rows = [list(r) for r in rows]
    if not rows:
        return
    with _LOCK:
        con = _connect()
        con.executemany(sql, rows)
        if ENGINE == "sqlite":  # pragma: no cover
            con.commit()


def query_df(sql: str, params: Sequence[Any] | None = None) -> pd.DataFrame:
    with _LOCK:
        con = _connect()
        if ENGINE == "duckdb":
            return con.execute(sql, list(params or [])).df()
        return pd.read_sql_query(sql, con, params=list(params or []))  # pragma: no cover


def replace_rows(table: str, df: pd.DataFrame, where: str, params: Sequence[Any]) -> int:
    """Delete rows matching `where`, then insert `df` (columns must match table)."""
    cols = list(df.columns)
    placeholders = ", ".join(["?"] * len(cols))
    with _LOCK:
        execute(f"DELETE FROM {table} WHERE {where}", params)
        executemany(
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})",
            [[_py(v) for v in row] for row in df.itertuples(index=False, name=None)],
        )
    return len(df)


def insert_row(table: str, row: dict[str, Any]) -> None:
    cols = list(row)
    execute(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))})",
        [_py(row[c]) for c in cols],
    )


def _py(v: Any) -> Any:
    """Convert numpy / pandas scalars to plain Python for the DB driver."""
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        return json.dumps(v)
    if isinstance(v, pd.Timestamp):
        return v.isoformat()
    if hasattr(v, "item"):
        try:
            v = v.item()
        except Exception:
            pass
    if isinstance(v, float) and v != v:  # NaN
        return None
    return v
