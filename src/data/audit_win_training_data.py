from __future__ import annotations

"""Audit the win-model training dataset before model development."""

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd

from src.data.data_contracts import TRAIN_NUMERIC_COLUMNS, ResolvedTrainingColumns, ensure_numeric_columns, resolve_training_columns


logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT_PATH = ROOT / "data" / "processed" / "historical_races.csv"
DEFAULT_OUTPUT_DIR = ROOT / "reports" / "data_audit"
OUTPUT_JSON = "win_training_data_audit.json"
OUTPUT_ISSUES = "win_training_data_issues.csv"
FEATURE_COLUMNS_TO_CHECK = ["national_2ren_rate", "local_2ren_rate", "avg_st"]


@dataclass(frozen=True)
class AuditSummary:
    source_row_count: int
    row_count: int
    canonical_row_count: int
    unique_race_count: int
    null_race_id_count: int
    invalid_date_count: int
    raw_duplicate_row_count: int
    raw_duplicate_race_count: int
    duplicate_boat_no_race_count: int
    required_columns_missing: list[str]
    invalid_finish_position_count: int
    invalid_boat_no_count: int
    non_six_boat_race_count: int
    target_win_invalid_race_count: int
    missing_rate_by_feature: dict[str, float]
    min_date: str | None
    max_date: str | None
    time_series_split_possible: bool
    can_train: bool
    aliases_used: dict[str, str]


def _load_csv(path: Path) -> pd.DataFrame:
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "cp932"):
        try:
            return pd.read_csv(path, low_memory=False, encoding=encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    return pd.read_csv(path, low_memory=False)


def _normalize_frame(df: pd.DataFrame) -> ResolvedTrainingColumns:
    """Resolve aliases and coerce audit-critical columns into canonical types."""

    resolved = resolve_training_columns(df)
    out = ensure_numeric_columns(resolved.frame, TRAIN_NUMERIC_COLUMNS)
    if "date" in out.columns:
        out["date"] = pd.to_datetime(out["date"], errors="coerce", format="mixed")
    return ResolvedTrainingColumns(frame=out, aliases_used=resolved.aliases_used, missing_columns=resolved.missing_columns)


def _dedupe_training_frame(df: pd.DataFrame) -> tuple[pd.DataFrame, int, int]:
    """Drop duplicate (race_id, boat_no) rows keeping the latest sorted record."""

    if not {"race_id", "boat_no"}.issubset(df.columns):
        return df.copy(), 0, 0

    work = df.copy()
    work["race_id"] = work["race_id"].astype(str).str.strip()
    work["boat_no"] = pd.to_numeric(work["boat_no"], errors="coerce")
    work = work.sort_values([c for c in ["date", "race_id", "boat_no"] if c in work.columns], kind="mergesort")
    raw_count = len(work)
    dup_mask = work.duplicated(subset=["race_id", "boat_no"], keep="last")
    dup_row_count = int(dup_mask.sum())
    deduped = work.loc[~dup_mask].copy()
    return deduped, raw_count, dup_row_count


def _safe_group_count(df: pd.DataFrame, col: str) -> int:
    if "race_id" not in df.columns or col not in df.columns:
        return 0
    return int((df.groupby("race_id")[col].transform("count") != 6).groupby(df["race_id"]).first().sum())


def _missing_rate(df: pd.DataFrame, columns: Iterable[str]) -> dict[str, float]:
    rates: dict[str, float] = {}
    for col in columns:
        if col in df.columns and len(df) > 0:
            rates[col] = float(pd.to_numeric(df[col], errors="coerce").isna().mean())
        else:
            rates[col] = 1.0
    return rates


def _issue_rows_for_grouped_races(
    df: pd.DataFrame,
    *,
    issue_type: str,
    reason_builder,
    group_col: str = "race_id",
) -> list[dict]:
    rows: list[dict] = []
    if group_col not in df.columns:
        return rows

    grouped = df.groupby(group_col, dropna=False)
    for race_id, grp in grouped:
        reason = reason_builder(grp)
        if reason is None:
            continue
        rows.append(
            {
                "issue_type": issue_type,
                "scope": "race",
                "race_id": None if pd.isna(race_id) else str(race_id),
                "date": _first_value(grp, "date"),
                "row_count": int(len(grp)),
                "details": reason,
                "sample_row_indices": ",".join(map(str, grp.index[:10].tolist())),
            }
        )
    return rows


def _first_value(df: pd.DataFrame, col: str) -> str | None:
    if col not in df.columns or df.empty:
        return None
    value = df[col].iloc[0]
    if pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    return str(value)


def audit_training_data(df: pd.DataFrame) -> tuple[AuditSummary, pd.DataFrame]:
    """Audit a training frame and return (summary, issues_df)."""

    resolved = _normalize_frame(df)
    canonical = resolved.frame.copy()
    source_row_count = int(len(canonical))
    if "race_id" in canonical.columns:
        canonical["race_id"] = canonical["race_id"].astype(str).str.strip()
        canonical.loc[canonical["race_id"].isin({"", "nan", "None", "<NA>"}) | canonical["race_id"].isna(), "race_id"] = pd.NA

    if "boat_no" not in canonical.columns and "lane" in canonical.columns:
        canonical["boat_no"] = pd.to_numeric(canonical["lane"], errors="coerce")
    if "race_number" not in canonical.columns and "race_no" in canonical.columns:
        canonical["race_number"] = pd.to_numeric(canonical["race_no"], errors="coerce")
    if "avg_st" not in canonical.columns and "st" in canonical.columns:
        canonical["avg_st"] = pd.to_numeric(canonical["st"], errors="coerce")

    deduped, raw_count, duplicate_row_count = _dedupe_training_frame(canonical)
    unique_race_count = int(deduped["race_id"].nunique()) if "race_id" in deduped.columns else 0

    issue_rows: list[dict] = []
    required_columns_missing = list(resolved.missing_columns)
    if required_columns_missing:
        for col in required_columns_missing:
            issue_rows.append(
                {
                    "issue_type": "missing_required_column",
                    "scope": "dataset",
                    "race_id": None,
                    "date": None,
                    "row_count": source_row_count,
                    "details": f"missing column: {col}",
                    "sample_row_indices": "",
                }
            )

    null_race_id_count = 0
    if "race_id" in canonical.columns:
        null_race_mask = canonical["race_id"].isna()
        null_race_id_count = int(null_race_mask.sum())
        if null_race_id_count > 0:
            issue_rows.append(
                {
                    "issue_type": "null_race_id_row",
                    "scope": "dataset",
                    "race_id": None,
                    "date": None,
                    "row_count": null_race_id_count,
                    "details": "race_id is null or blank",
                    "sample_row_indices": ",".join(map(str, canonical.index[null_race_mask][:10].tolist())),
                }
            )

    invalid_date_count = 0
    if "date" in canonical.columns:
        invalid_date_mask = canonical["date"].isna()
        invalid_date_count = int(invalid_date_mask.sum())
        if invalid_date_count > 0:
            issue_rows.append(
                {
                    "issue_type": "invalid_date_row",
                    "scope": "dataset",
                    "race_id": None,
                    "date": None,
                    "row_count": invalid_date_count,
                    "details": "date is not parseable",
                    "sample_row_indices": ",".join(map(str, canonical.index[invalid_date_mask][:10].tolist())),
                }
            )

    if duplicate_row_count > 0:
        dup_df = canonical[canonical.duplicated(subset=["race_id", "boat_no"], keep="last")].copy() if {"race_id", "boat_no"}.issubset(canonical.columns) else pd.DataFrame()
        for race_id, grp in dup_df.groupby("race_id", dropna=False):
            issue_rows.append(
                {
                    "issue_type": "duplicate_race_boat_row",
                    "scope": "race",
                    "race_id": None if pd.isna(race_id) else str(race_id),
                    "date": _first_value(grp, "date"),
                    "row_count": int(len(grp)),
                    "details": f"duplicate race_id+boat_no rows: {len(grp)}",
                    "sample_row_indices": ",".join(map(str, grp.index[:10].tolist())),
                }
            )

    duplicate_boat_no_race_count = 0
    if {"race_id", "boat_no"}.issubset(canonical.columns):
        dup_boat_per_race = canonical.dropna(subset=["race_id"]).duplicated(subset=["race_id", "boat_no"], keep=False)
        duplicate_boat_no_race_count = int(
            canonical.loc[dup_boat_per_race & canonical["race_id"].notna(), "race_id"].nunique()
        )

    if "boat_no" in canonical.columns:
        invalid_boat_mask = pd.to_numeric(canonical["boat_no"], errors="coerce").isna() | ~pd.to_numeric(canonical["boat_no"], errors="coerce").between(1, 6)
        invalid_boat_rows = canonical[invalid_boat_mask].copy()
        for race_id, grp in invalid_boat_rows.groupby("race_id", dropna=False):
            issue_rows.append(
                {
                    "issue_type": "invalid_boat_no_row",
                    "scope": "race",
                    "race_id": None if pd.isna(race_id) else str(race_id),
                    "date": _first_value(grp, "date"),
                    "row_count": int(len(grp)),
                    "details": "boat_no outside 1..6 or missing",
                    "sample_row_indices": ",".join(map(str, grp.index[:10].tolist())),
                }
            )
    else:
        invalid_boat_rows = pd.DataFrame()

    if "finish_position" in canonical.columns:
        finish_num = pd.to_numeric(canonical["finish_position"], errors="coerce")
        invalid_finish_mask = finish_num.isna() | ~finish_num.between(1, 6)
        invalid_finish_rows = canonical[invalid_finish_mask].copy()
        for race_id, grp in invalid_finish_rows.groupby("race_id", dropna=False):
            issue_rows.append(
                {
                    "issue_type": "invalid_finish_position_row",
                    "scope": "race",
                    "race_id": None if pd.isna(race_id) else str(race_id),
                    "date": _first_value(grp, "date"),
                    "row_count": int(len(grp)),
                    "details": "finish_position outside 1..6 or missing",
                    "sample_row_indices": ",".join(map(str, grp.index[:10].tolist())),
                }
            )
    else:
        invalid_finish_rows = pd.DataFrame()

    if {"race_id", "boat_no"}.issubset(deduped.columns):
        non_six = deduped.groupby("race_id")["boat_no"].count()
        bad_races = non_six[non_six != 6]
        for race_id, count in bad_races.items():
            grp = deduped[deduped["race_id"] == race_id]
            issue_rows.append(
                {
                    "issue_type": "non_six_boat_race",
                    "scope": "race",
                    "race_id": str(race_id),
                    "date": _first_value(grp, "date"),
                    "row_count": int(len(grp)),
                    "details": f"boat count after dedupe is {int(count)}",
                    "sample_row_indices": ",".join(map(str, grp.index[:10].tolist())),
                }
            )
        non_six_boat_race_count = int(len(bad_races))
    else:
        non_six_boat_race_count = 0

    if {"race_id", "finish_position"}.issubset(deduped.columns):
        target_win = pd.to_numeric(deduped["finish_position"], errors="coerce").eq(1).astype(int)
        deduped = deduped.assign(target_win=target_win)
        win_counts = deduped.groupby("race_id")["target_win"].sum()
        bad_win_races = win_counts[win_counts != 1]
        for race_id, count in bad_win_races.items():
            grp = deduped[deduped["race_id"] == race_id]
            issue_rows.append(
                {
                    "issue_type": "target_win_invalid_race",
                    "scope": "race",
                    "race_id": str(race_id),
                    "date": _first_value(grp, "date"),
                    "row_count": int(len(grp)),
                    "details": f"target_win sum is {int(count)}",
                    "sample_row_indices": ",".join(map(str, grp.index[:10].tolist())),
                }
            )
        target_win_invalid_race_count = int(len(bad_win_races))
    else:
        target_win_invalid_race_count = 0

    date_series = pd.to_datetime(deduped["date"], errors="coerce") if "date" in deduped.columns else pd.Series(dtype="datetime64[ns]")
    min_date = None if date_series.empty or date_series.dropna().empty else date_series.dropna().min().strftime("%Y-%m-%d")
    max_date = None if date_series.empty or date_series.dropna().empty else date_series.dropna().max().strftime("%Y-%m-%d")
    unique_dates = int(date_series.dropna().dt.normalize().nunique()) if not date_series.empty else 0
    time_series_split_possible = unique_dates >= 3

    missing_rates = _missing_rate(deduped, FEATURE_COLUMNS_TO_CHECK)
    for feature, rate in missing_rates.items():
        if rate > 0.0:
            issue_rows.append(
                {
                    "issue_type": "feature_missing_rate",
                    "scope": "dataset",
                    "race_id": None,
                    "date": None,
                    "row_count": int(len(deduped)),
                    "details": f"{feature} missing_rate={rate:.6f}",
                    "sample_row_indices": "",
                }
            )

    can_train = (
        not required_columns_missing
        and null_race_id_count == 0
        and invalid_date_count == 0
        and invalid_boat_rows.empty
        and invalid_finish_rows.empty
        and non_six_boat_race_count == 0
        and target_win_invalid_race_count == 0
        and time_series_split_possible
        and all(rate == 0.0 for rate in missing_rates.values())
    )

    summary = AuditSummary(
        source_row_count=source_row_count,
        row_count=int(len(deduped)),
        canonical_row_count=int(len(deduped)),
        unique_race_count=unique_race_count,
        null_race_id_count=int(null_race_id_count),
        invalid_date_count=int(invalid_date_count),
        raw_duplicate_row_count=int(duplicate_row_count),
        raw_duplicate_race_count=int(canonical.duplicated(subset=["race_id", "boat_no"], keep=False).groupby(canonical["race_id"]).any().sum()) if {"race_id", "boat_no"}.issubset(canonical.columns) else 0,
        duplicate_boat_no_race_count=int(duplicate_boat_no_race_count),
        required_columns_missing=required_columns_missing,
        invalid_finish_position_count=int(len(invalid_finish_rows)),
        invalid_boat_no_count=int(len(invalid_boat_rows)),
        non_six_boat_race_count=non_six_boat_race_count,
        target_win_invalid_race_count=target_win_invalid_race_count,
        missing_rate_by_feature=missing_rates,
        min_date=min_date,
        max_date=max_date,
        time_series_split_possible=time_series_split_possible,
        can_train=can_train,
        aliases_used=resolved.aliases_used,
    )
    issues = pd.DataFrame(issue_rows, columns=["issue_type", "scope", "race_id", "date", "row_count", "details", "sample_row_indices"])
    return summary, issues


def write_audit_outputs(
    summary: AuditSummary,
    issues: pd.DataFrame,
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> tuple[Path, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / OUTPUT_JSON
    csv_path = output_dir / OUTPUT_ISSUES
    json_path.write_text(json.dumps(asdict(summary), ensure_ascii=False, indent=2), encoding="utf-8")
    issues.to_csv(csv_path, index=False, encoding="utf-8")
    return json_path, csv_path


def load_training_data(path: Path = DEFAULT_INPUT_PATH) -> pd.DataFrame:
    return _load_csv(Path(path))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit win training data readiness")
    parser.add_argument("--input-path", type=Path, default=DEFAULT_INPUT_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--fail-on-issues", action="store_true", default=True)
    parser.add_argument("--no-fail-on-issues", action="store_false", dest="fail_on_issues")
    args = parser.parse_args(argv)

    try:
        df = load_training_data(args.input_path)
        summary, issues = audit_training_data(df)
        json_path, csv_path = write_audit_outputs(summary, issues, output_dir=args.output_dir)
        print(json.dumps(asdict(summary), ensure_ascii=False, indent=2))
        print(f"Saved audit JSON: {json_path}")
        print(f"Saved issues CSV: {csv_path}")
        if not summary.can_train and args.fail_on_issues:
            logger.error("Win training data audit failed: %s", json.dumps(asdict(summary), ensure_ascii=False))
            return 1
        return 0
    except Exception as exc:
        logger.exception("Win training data audit failed")
        print(f"[error] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
