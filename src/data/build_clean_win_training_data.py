from __future__ import annotations

"""Build a cleaned win-training dataset from the raw vertical race frame."""

import argparse
import json
import logging
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import pandas as pd

from src.data.audit_win_training_data import _load_csv, _normalize_frame


logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT_PATH = ROOT / "data" / "processed" / "historical_races.csv"
DEFAULT_OUTPUT_PATH = ROOT / "data" / "processed" / "clean_win_training_data.csv"
DEFAULT_REPORT_DIR = ROOT / "reports" / "data_audit"
SUMMARY_JSON_NAME = "clean_win_training_data_summary.json"
DROPPED_RACES_CSV_NAME = "clean_win_training_data_dropped_races.csv"
MISSING_RATE_FEATURES = ["national_2ren_rate", "local_2ren_rate", "avg_st"]
RACE_ID_COMPONENT_COLUMNS = ["date", "jcd", "race_number"]
DEDUPLICATION_PRIORITY_COLUMNS = [
    "date",
    "venue",
    "race_number",
    "finish_position",
    "avg_st",
    "national_2ren_rate",
    "local_2ren_rate",
]


@dataclass(frozen=True)
class CleanSummary:
    input_row_count: int
    output_row_count: int
    input_unique_race_count: int
    output_unique_race_count: int
    dropped_race_count: int
    dropped_reason_counts: dict[str, int]
    duplicate_resolution_count: int
    feature_missing_rate_after_cleaning: dict[str, float]
    race_id_normalization_rule: str
    duplicate_resolution_rule: str
    baseline_feature_exclusion_candidates: list[str]
    baseline_feature_exclusion_note: str
    output_path: str


def _require_columns(df: pd.DataFrame, columns: list[str]) -> None:
    missing = [col for col in columns if col not in df.columns]
    if missing:
        raise ValueError(f"clean win training data build failed: missing required columns {missing}")


def _normalize_training_frame(df: pd.DataFrame) -> pd.DataFrame:
    resolved = _normalize_frame(df)
    _require_columns(
        resolved.frame,
        ["race_id", "date", "venue", "race_number", "boat_no", "finish_position"],
    )
    out = resolved.frame.copy()
    out = out.reset_index(drop=True)
    out["_source_row_index"] = out.index
    out["race_id"] = out["race_id"].astype("string").str.strip()
    out.loc[out["race_id"].isin(["", "nan", "None", "<NA>"]), "race_id"] = pd.NA
    out["date"] = pd.to_datetime(out["date"], errors="coerce", format="mixed")
    out["race_number"] = pd.to_numeric(out["race_number"], errors="coerce")
    out["boat_no"] = pd.to_numeric(out["boat_no"], errors="coerce")
    out["finish_position"] = pd.to_numeric(out["finish_position"], errors="coerce")
    if "jcd" in out.columns:
        out["jcd"] = pd.to_numeric(out["jcd"], errors="coerce")
    return out


def _build_normalized_race_id(df: pd.DataFrame) -> pd.Series:
    normalized = pd.Series(pd.NA, index=df.index, dtype="string")
    if all(col in df.columns for col in RACE_ID_COMPONENT_COLUMNS):
        valid_mask = df["date"].notna() & df["jcd"].notna() & df["race_number"].notna()
        normalized.loc[valid_mask] = (
            df.loc[valid_mask, "date"].dt.strftime("%Y%m%d")
            + "_"
            + df.loc[valid_mask, "jcd"].astype(int).astype(str).str.zfill(2)
            + "_"
            + df.loc[valid_mask, "race_number"].astype(int).astype(str).str.zfill(2)
        )
    fallback_mask = normalized.isna() & df["race_id"].notna()
    normalized.loc[fallback_mask] = (
        df.loc[fallback_mask, "race_id"]
        .astype("string")
        .str.strip()
        .str.replace(r"\s+", "", regex=True)
        .str.replace("-", "_", regex=False)
    )
    normalized = normalized.astype("string")
    normalized.loc[normalized.isin(["", "nan", "None", "<NA>"])] = pd.NA
    return normalized


def _missing_rate(df: pd.DataFrame, columns: list[str]) -> dict[str, float]:
    rates: dict[str, float] = {}
    for col in columns:
        if col not in df.columns or len(df) == 0:
            rates[col] = 1.0
            continue
        rates[col] = float(pd.to_numeric(df[col], errors="coerce").isna().mean())
    return rates


def _deduplicate_race_boat_rows(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    work = df.copy()
    work["_valid_finish"] = work["finish_position"].between(1, 6)
    work["_valid_boat"] = work["boat_no"].between(1, 6)
    work["_valid_date"] = work["date"].notna()
    present_priority_cols = [col for col in DEDUPLICATION_PRIORITY_COLUMNS if col in work.columns]
    work["_priority_missing_count"] = work[present_priority_cols].isna().sum(axis=1)
    work = work.sort_values(
        by=[
            "race_id",
            "boat_no",
            "_valid_finish",
            "_valid_boat",
            "_valid_date",
            "_priority_missing_count",
            "_source_row_index",
        ],
        ascending=[True, True, False, False, False, True, False],
        kind="mergesort",
    )
    before_count = len(work)
    deduped = work.drop_duplicates(subset=["race_id", "boat_no"], keep="first").copy()
    duplicate_resolution_count = int(before_count - len(deduped))
    return deduped, duplicate_resolution_count


def _collect_drop_metadata(group: pd.DataFrame, reasons: list[str]) -> dict[str, object]:
    target_win_sum = int(group["target_win"].sum()) if "target_win" in group.columns else None
    return {
        "race_id": group["race_id"].iloc[0],
        "date": group["date"].dropna().iloc[0].strftime("%Y-%m-%d") if "date" in group.columns and group["date"].notna().any() else None,
        "venue": group["venue"].dropna().iloc[0] if "venue" in group.columns and group["venue"].notna().any() else None,
        "race_number": int(group["race_number"].dropna().iloc[0]) if "race_number" in group.columns and group["race_number"].notna().any() else None,
        "row_count": int(len(group)),
        "boat_count": int(group["boat_no"].count()),
        "unique_boat_count": int(group["boat_no"].nunique(dropna=True)),
        "invalid_finish_position_count": int((~group["finish_position"].between(1, 6)).sum()),
        "invalid_boat_no_count": int((~group["boat_no"].between(1, 6)).sum()),
        "target_win_sum": target_win_sum,
        "drop_reasons": "|".join(sorted(set(reasons))),
    }


def build_clean_win_training_data(df: pd.DataFrame) -> tuple[pd.DataFrame, CleanSummary, pd.DataFrame]:
    """Return (clean_df, summary, dropped_races_df) for win-model training."""

    canonical = _normalize_training_frame(df)
    input_row_count = int(len(canonical))
    canonical["race_id"] = _build_normalized_race_id(canonical)
    input_unique_race_count = int(canonical["race_id"].nunique(dropna=True))
    canonical = canonical.loc[canonical["race_id"].notna()].copy()
    canonical["target_win"] = canonical["finish_position"].eq(1).astype(int)

    deduped, duplicate_resolution_count = _deduplicate_race_boat_rows(canonical)
    deduped["target_win"] = deduped["finish_position"].eq(1).astype(int)

    dropped_rows: list[dict[str, object]] = []
    keep_race_ids: list[str] = []
    dropped_reason_counts: dict[str, int] = {}

    for race_id, group in deduped.groupby("race_id", sort=True):
        reasons: list[str] = []
        if int(group["boat_no"].count()) != 6:
            reasons.append("non_six_boat_race")
        if group["boat_no"].isna().any() or not group["boat_no"].between(1, 6).all():
            reasons.append("invalid_boat_no_race")
        if int(group["boat_no"].nunique(dropna=True)) != int(group["boat_no"].count()):
            reasons.append("duplicate_boat_no_race")
        if group["finish_position"].isna().any() or not group["finish_position"].between(1, 6).all():
            reasons.append("invalid_finish_position_race")
        if int(group["target_win"].sum()) != 1:
            reasons.append("target_win_invalid_race")

        if reasons:
            dropped_rows.append(_collect_drop_metadata(group, reasons))
            for reason in reasons:
                dropped_reason_counts[reason] = dropped_reason_counts.get(reason, 0) + 1
            continue

        keep_race_ids.append(str(race_id))

    clean_df = deduped.loc[deduped["race_id"].isin(keep_race_ids)].copy()
    clean_df = clean_df.sort_values(["date", "race_id", "boat_no"], kind="mergesort").reset_index(drop=True)
    clean_df["target_win"] = clean_df["finish_position"].eq(1).astype(int)
    clean_df = clean_df.drop(
        columns=[
            "_source_row_index",
            "_valid_finish",
            "_valid_boat",
            "_valid_date",
            "_priority_missing_count",
        ],
        errors="ignore",
    )

    dropped_races_df = pd.DataFrame(
        dropped_rows,
        columns=[
            "race_id",
            "date",
            "venue",
            "race_number",
            "row_count",
            "boat_count",
            "unique_boat_count",
            "invalid_finish_position_count",
            "invalid_boat_no_count",
            "target_win_sum",
            "drop_reasons",
        ],
    )
    feature_missing_rate_after_cleaning = _missing_rate(clean_df, MISSING_RATE_FEATURES)
    summary = CleanSummary(
        input_row_count=input_row_count,
        output_row_count=int(len(clean_df)),
        input_unique_race_count=input_unique_race_count,
        output_unique_race_count=int(clean_df["race_id"].nunique()),
        dropped_race_count=int(len(dropped_races_df)),
        dropped_reason_counts=dropped_reason_counts,
        duplicate_resolution_count=duplicate_resolution_count,
        feature_missing_rate_after_cleaning=feature_missing_rate_after_cleaning,
        race_id_normalization_rule="Prefer YYYYMMDD_JCD_RR built from date+jcd+race_number; fallback to stripped existing race_id with hyphen converted to underscore.",
        duplicate_resolution_rule="For duplicate race_id+boat_no rows, keep the row with valid finish_position, valid boat_no, parseable date, fewer missing priority fields, then larger source row index.",
        baseline_feature_exclusion_candidates=["national_2ren_rate", "local_2ren_rate"],
        baseline_feature_exclusion_note="national_2ren_rate と local_2ren_rate は cleaned dataset には残すが、欠損率が高いため baseline 学習特徴から一旦除外候補。",
        output_path=str(DEFAULT_OUTPUT_PATH),
    )
    return clean_df, summary, dropped_races_df


def write_outputs(
    clean_df: pd.DataFrame,
    summary: CleanSummary,
    dropped_races_df: pd.DataFrame,
    *,
    output_path: Path = DEFAULT_OUTPUT_PATH,
    report_dir: Path = DEFAULT_REPORT_DIR,
) -> tuple[Path, Path, Path]:
    output_path = Path(output_path)
    report_dir = Path(report_dir)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    clean_df.to_csv(output_path, index=False, encoding="utf-8")
    summary_path = report_dir / SUMMARY_JSON_NAME
    dropped_path = report_dir / DROPPED_RACES_CSV_NAME
    output_summary = replace(summary, output_path=str(output_path))
    summary_path.write_text(json.dumps(asdict(output_summary), ensure_ascii=False, indent=2), encoding="utf-8")
    dropped_races_df.to_csv(dropped_path, index=False, encoding="utf-8")
    return output_path, summary_path, dropped_path


def load_training_data(path: Path = DEFAULT_INPUT_PATH) -> pd.DataFrame:
    return _load_csv(Path(path))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build cleaned win training dataset")
    parser.add_argument("--input-path", type=Path, default=DEFAULT_INPUT_PATH)
    parser.add_argument("--output-path", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    args = parser.parse_args(argv)

    try:
        df = load_training_data(args.input_path)
        clean_df, summary, dropped_races_df = build_clean_win_training_data(df)
        output_path, summary_path, dropped_path = write_outputs(
            clean_df,
            summary,
            dropped_races_df,
            output_path=args.output_path,
            report_dir=args.report_dir,
        )
        print(json.dumps(asdict(summary), ensure_ascii=False, indent=2))
        print(f"Saved cleaned dataset: {output_path}")
        print(f"Saved summary JSON: {summary_path}")
        print(f"Saved dropped races CSV: {dropped_path}")
        return 0
    except Exception as exc:
        logger.exception("Clean win training data build failed")
        print(f"[error] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
