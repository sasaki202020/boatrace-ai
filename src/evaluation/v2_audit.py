from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import pandas as pd


def _date_range_summary(df: pd.DataFrame, date_col: str = "date") -> dict[str, Any]:
    if df.empty or date_col not in df.columns:
        return {"min_date": None, "max_date": None}
    series = df[date_col].astype(str).str.replace(r"\D", "", regex=True).str[:8]
    valid = series[series.str.len() == 8]
    if valid.empty:
        return {"min_date": None, "max_date": None}
    return {"min_date": valid.min(), "max_date": valid.max()}


def build_migration_audit(tables: Mapping[str, pd.DataFrame], warnings: list[str] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "tables": {},
        "warnings": warnings or [],
    }

    for name, df in tables.items():
        table_report: dict[str, Any] = {
            "rows": int(len(df)),
            "columns": list(df.columns),
            "duplicate_rows": 0,
        }
        if not df.empty:
            key_cols = [col for col in ["race_id", "ticket_id", "snapshot_ts", "entry_id"] if col in df.columns]
            if key_cols:
                table_report["duplicate_rows"] = int(df.duplicated(subset=key_cols).sum())
            if "date" in df.columns:
                table_report.update(_date_range_summary(df, "date"))
            elif "race_date" in df.columns:
                table_report.update(_date_range_summary(df, "race_date"))
        payload["tables"][name] = table_report

    return payload
