from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from src.core.schemas import V2_TABLE_NAMES, v2_schema_sql


def duckdb_available() -> bool:
    try:
        import duckdb  # noqa: F401
    except Exception:
        return False
    return True


def _require_duckdb():
    try:
        import duckdb
    except Exception as exc:  # pragma: no cover - exercised by dry-run path
        raise RuntimeError(
            "duckdb is not installed. Install dependencies first, for example: pip install -r requirements.txt"
        ) from exc
    return duckdb


@dataclass
class DuckDBStore:
    db_path: Path

    def connect(self):
        duckdb = _require_duckdb()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        return duckdb.connect(str(self.db_path))

    def initialize_schema(self, conn) -> None:
        for ddl in v2_schema_sql():
            conn.execute(ddl)

    def replace_table(self, conn, table_name: str, df: pd.DataFrame) -> None:
        conn.execute(f"DELETE FROM {table_name}")
        if df.empty:
            return
        temp_view = f"__tmp_{table_name}"
        conn.register(temp_view, df)
        try:
            conn.execute(f"INSERT INTO {table_name} SELECT * FROM {temp_view}")
        finally:
            try:
                conn.unregister(temp_view)
            except Exception:
                pass

    def append_table(self, conn, table_name: str, df: pd.DataFrame) -> None:
        if df.empty:
            return
        temp_view = f"__tmp_append_{table_name}"
        conn.register(temp_view, df)
        try:
            conn.execute(f"INSERT INTO {table_name} SELECT * FROM {temp_view}")
        finally:
            try:
                conn.unregister(temp_view)
            except Exception:
                pass

    def table_counts(self, conn) -> dict[str, int]:
        counts: dict[str, int] = {}
        for table_name in V2_TABLE_NAMES:
            try:
                row = conn.execute(f"SELECT COUNT(*) AS n FROM {table_name}").fetchone()
                counts[table_name] = int(row[0]) if row else 0
            except Exception:
                counts[table_name] = 0
        return counts

    def fetch_table(self, conn, table_name: str) -> pd.DataFrame:
        return conn.execute(f"SELECT * FROM {table_name}").df()
