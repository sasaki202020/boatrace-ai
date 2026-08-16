from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from src.core.schemas import V2_TABLE_NAMES
from src.evaluation.v2_shadow import build_shadow_summary
from src.evaluation.reason_taxonomy import (
    build_failure_reasons,
    normalize_date_str,
)
from src.storage.duckdb import DuckDBStore, duckdb_available


def _empty_tables() -> dict[str, pd.DataFrame]:
    return {name: pd.DataFrame() for name in V2_TABLE_NAMES}


def _filter_day_frame(df: pd.DataFrame, date_str: str) -> pd.DataFrame:
    if df.empty:
        return df.iloc[0:0].copy()

    work = df.copy()
    matched = False

    for column in ("date", "race_date"):
        if column in work.columns:
            work[column] = work[column].astype(str).map(normalize_date_str)
            matched = True

    if not matched:
        return work.iloc[0:0].copy()

    if "date" in work.columns:
        filtered = work[work["date"] == date_str].copy()
        if not filtered.empty:
            return filtered.reset_index(drop=True)

    if "race_date" in work.columns:
        filtered = work[work["race_date"] == date_str].copy()
        if not filtered.empty:
            return filtered.reset_index(drop=True)

    return work.iloc[0:0].copy()


def _load_day_tables_from_db(db_path: Path, date_str: str) -> tuple[dict[str, pd.DataFrame], list[str]]:
    warnings: list[str] = []
    if not db_path.exists():
        warnings.append(f"duckdb missing: {db_path}")
        return _empty_tables(), warnings
    if not duckdb_available():
        warnings.append("duckdb not installed; DB-backed evaluation skipped")
        return _empty_tables(), warnings

    store = DuckDBStore(db_path=db_path)
    conn = store.connect()
    try:
        tables: dict[str, pd.DataFrame] = {}
        for table_name in V2_TABLE_NAMES:
            try:
                table_df = store.fetch_table(conn, table_name)
            except Exception as exc:
                warnings.append(f"failed to fetch {table_name}: {exc}")
                table_df = pd.DataFrame()
            tables[table_name] = _filter_day_frame(table_df, date_str)
        return tables, warnings
    finally:
        conn.close()


def _as_int(value: object) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except Exception:
        return 0


def _as_float(value: object) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except Exception:
        return 0.0


def _candidate_race_count(df: pd.DataFrame) -> int:
    if df.empty or "race_key" not in df.columns:
        return 0
    return int(df["race_key"].astype(str).nunique())


def _candidate_prob_summary(df: pd.DataFrame, prob_col: str) -> dict[str, Any]:
    if df.empty or prob_col not in df.columns:
        return {"rows": 0, "avg_prob": None}
    series = pd.to_numeric(df[prob_col], errors="coerce").dropna()
    return {
        "rows": int(len(df)),
        "avg_prob": round(float(series.mean()), 4) if not series.empty else None,
    }


def _load_v1_payload(v1_compare_path: Path) -> dict[str, Any]:
    if not v1_compare_path.exists():
        return {}
    try:
        return json.loads(v1_compare_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def evaluate_shadow_day_v2(
    *,
    date_str: str,
    compare_status: str,
    db_path: Path,
    fallback_tables: Mapping[str, pd.DataFrame],
    raw_candidates: pd.DataFrame,
    calibrated_candidates: pd.DataFrame,
    v1_compare_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    evaluation_error: str | None = None
    db_tables: dict[str, pd.DataFrame]
    db_warnings: list[str]

    try:
        db_tables, db_warnings = _load_day_tables_from_db(db_path, date_str)
    except Exception as exc:  # pragma: no cover - defensive fallback
        db_tables = dict(fallback_tables)
        db_warnings = [f"db evaluation failed: {exc}"]
        evaluation_error = str(exc)

    if all(df.empty for df in db_tables.values()):
        db_tables = {name: _filter_day_frame(df, date_str) for name, df in fallback_tables.items()}
        if not db_warnings:
            db_warnings = ["db tables empty; used in-memory fallback"]

    v1_payload = _load_v1_payload(v1_compare_path)
    raw_summary = v1_payload.get("raw_summary", {})
    cal_summary = v1_payload.get("calibrated_summary", {})
    v1_judgement = str(v1_payload.get("judgement", ""))
    v1_race_count = max(_candidate_race_count(raw_candidates), _candidate_race_count(calibrated_candidates))

    raw_sim_exit = _as_int(v1_payload.get("raw_sim_exit"))
    cal_sim_exit = _as_int(v1_payload.get("cal_sim_exit"))
    v1_compareable = bool(v1_payload) and raw_sim_exit == 0 and cal_sim_exit == 0 and "比較保留" not in v1_judgement

    summary = build_shadow_summary(
        date_str=date_str,
        compare_status=compare_status,
        tables=db_tables,
        raw_candidates=raw_candidates,
        calibrated_candidates=calibrated_candidates,
        v1_reference=raw_summary,
        v1_compare=cal_summary,
    )
    summary["results_ready_count"] = summary.get("result_available_races", 0)
    summary["v1_judgement"] = v1_judgement
    summary["v1_compareable"] = v1_compareable
    summary["v1_raw_summary"] = dict(raw_summary)
    summary["v1_calibrated_summary"] = dict(cal_summary)
    summary["reference_only"] = compare_status != "TARGET"
    summary["failure_reasons"] = build_failure_reasons(
        compare_status=compare_status,
        target_races=_as_int(summary.get("target_races")),
        result_available_races=_as_int(summary.get("result_available_races")),
        odds_available_races=_as_int(summary.get("odds_covered_races")),
        compare_possible=bool(summary.get("compare_possible")),
        v1_payload=v1_payload,
        evaluation_error=evaluation_error,
    )
    summary["db_warnings"] = db_warnings
    summary["db_path"] = str(db_path)

    diff = {
        "date": date_str,
        "compare_status": compare_status,
        "reference_only": compare_status != "TARGET",
        "v1_race_count": v1_race_count,
        "v2_race_count": _as_int(summary.get("target_races")),
        "v1_compareable": v1_compareable,
        "v2_compareable": bool(summary.get("compare_possible")),
        "results_ready_count": _as_int(summary.get("results_ready_count")),
        "odds_coverage": summary.get("odds_coverage", 0.0),
        "raw_buy": _as_int(raw_summary.get("buy_count")),
        "cal_buy": _as_int(cal_summary.get("buy_count")),
        "raw_hit": _as_int(raw_summary.get("hit_count")),
        "cal_hit": _as_int(cal_summary.get("hit_count")),
        "raw_roi": round(_as_float(raw_summary.get("roi")), 4),
        "cal_roi": round(_as_float(cal_summary.get("roi")), 4),
        "difference_summary": {
            "race_count_diff": _as_int(summary.get("target_races")) - v1_race_count,
            "candidate_rows_diff": _as_int(summary.get("raw_calibrated_diff", {}).get("candidate_rows_diff")),
            "avg_prob_diff": summary.get("raw_calibrated_diff", {}).get("avg_prob_diff"),
            "roi_diff": round(_as_float(cal_summary.get("roi")) - _as_float(raw_summary.get("roi")), 4),
            "hit_diff": _as_int(cal_summary.get("hit_count")) - _as_int(raw_summary.get("hit_count")),
        },
        "failure_reasons": summary["failure_reasons"],
        "v1_judgement": v1_judgement,
        "v1_raw_summary": dict(raw_summary),
        "v1_calibrated_summary": dict(cal_summary),
        "raw_calibrated_diff": summary.get("raw_calibrated_diff", {}),
    }
    return summary, diff, db_warnings
