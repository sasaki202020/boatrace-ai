from __future__ import annotations

"""Build a trainable win-training dataset from the cleaned race frame."""

import argparse
import json
import logging
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import pandas as pd

from src.data.audit_win_training_data import _load_csv


logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT_PATH = ROOT / "data" / "processed" / "clean_win_training_data.csv"
DEFAULT_OUTPUT_PATH = ROOT / "data" / "processed" / "trainable_win_training_data.csv"
DEFAULT_REPORT_DIR = ROOT / "reports" / "data_audit"
SUMMARY_JSON_NAME = "trainable_win_training_data_summary.json"
IMPUTE_REQUIRED_COLUMNS = ["national_2ren_rate", "local_2ren_rate", "avg_st"]
GROUP_IMPUTE_KEYS = ["racer_id", "boat_no"]


@dataclass(frozen=True)
class TrainableSummary:
    input_row_count: int
    output_row_count: int
    input_unique_race_count: int
    output_unique_race_count: int
    imputed_value_counts: dict[str, int]
    remaining_missing_rate_by_feature: dict[str, float]
    imputation_strategy: dict[str, str]
    output_path: str


def _require_columns(df: pd.DataFrame, columns: list[str]) -> None:
    missing = [col for col in columns if col not in df.columns]
    if missing:
        raise ValueError(f"trainable win training data build failed: missing required columns {missing}")


def _missing_rate(df: pd.DataFrame, columns: list[str]) -> dict[str, float]:
    rates: dict[str, float] = {}
    for col in columns:
        if col not in df.columns or len(df) == 0:
            rates[col] = 1.0
            continue
        rates[col] = float(pd.to_numeric(df[col], errors="coerce").isna().mean())
    return rates


def _coerce_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    _require_columns(out, ["race_id", "date", "boat_no", "finish_position", "target_win", *IMPUTE_REQUIRED_COLUMNS])
    out["date"] = pd.to_datetime(out["date"], errors="coerce", format="mixed")
    for col in ["boat_no", "finish_position", "target_win", *IMPUTE_REQUIRED_COLUMNS, *GROUP_IMPUTE_KEYS]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    out["race_id"] = out["race_id"].astype("string").str.strip()
    return out


def _impute_column(work: pd.DataFrame, column: str) -> tuple[pd.DataFrame, int, str]:
    out = work.copy()
    original_missing = int(out[column].isna().sum())
    strategy_parts: list[str] = []

    if original_missing == 0:
        return out, 0, "no_imputation_needed"

    for key in GROUP_IMPUTE_KEYS:
        if key not in out.columns:
            continue
        group_median = out.groupby(key)[column].transform("median")
        fill_mask = out[column].isna() & group_median.notna()
        filled = int(fill_mask.sum())
        if filled > 0:
            out.loc[fill_mask, column] = group_median.loc[fill_mask]
            strategy_parts.append(f"{key}_median:{filled}")

    if out[column].isna().any():
        global_median = out[column].median()
        if pd.isna(global_median):
            global_median = 0.0
            strategy_parts.append("global_constant_zero")
        else:
            strategy_parts.append(f"global_median:{float(global_median):.6f}")
        out[column] = out[column].fillna(global_median)

    if out[column].isna().any():
        raise ValueError(f"imputation failed for {column}")

    return out, original_missing, " -> ".join(strategy_parts)


def build_trainable_win_training_data(df: pd.DataFrame) -> tuple[pd.DataFrame, TrainableSummary]:
    """Return a trainable dataset and its summary."""

    work = _coerce_frame(df)
    if work["date"].isna().any():
        raise ValueError("trainable win training data build failed: cleaned dataset still has invalid date values")

    imputed_value_counts: dict[str, int] = {}
    imputation_strategy: dict[str, str] = {}
    for column in IMPUTE_REQUIRED_COLUMNS:
        work, imputed_count, strategy = _impute_column(work, column)
        imputed_value_counts[column] = imputed_count
        imputation_strategy[column] = strategy

    work = work.sort_values(["date", "race_id", "boat_no"], kind="mergesort").reset_index(drop=True)
    summary = TrainableSummary(
        input_row_count=int(len(df)),
        output_row_count=int(len(work)),
        input_unique_race_count=int(pd.Series(df["race_id"]).astype("string").nunique()),
        output_unique_race_count=int(work["race_id"].nunique()),
        imputed_value_counts=imputed_value_counts,
        remaining_missing_rate_by_feature=_missing_rate(work, IMPUTE_REQUIRED_COLUMNS),
        imputation_strategy=imputation_strategy,
        output_path=str(DEFAULT_OUTPUT_PATH),
    )
    return work, summary


def write_outputs(
    trainable_df: pd.DataFrame,
    summary: TrainableSummary,
    *,
    output_path: Path = DEFAULT_OUTPUT_PATH,
    report_dir: Path = DEFAULT_REPORT_DIR,
) -> tuple[Path, Path]:
    output_path = Path(output_path)
    report_dir = Path(report_dir)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    trainable_df.to_csv(output_path, index=False, encoding="utf-8")
    summary_path = report_dir / SUMMARY_JSON_NAME
    output_summary = replace(summary, output_path=str(output_path))
    summary_path.write_text(json.dumps(asdict(output_summary), ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path, summary_path


def load_clean_training_data(path: Path = DEFAULT_INPUT_PATH) -> pd.DataFrame:
    return _load_csv(Path(path))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build trainable win training dataset")
    parser.add_argument("--input-path", type=Path, default=DEFAULT_INPUT_PATH)
    parser.add_argument("--output-path", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    args = parser.parse_args(argv)

    try:
        df = load_clean_training_data(args.input_path)
        trainable_df, summary = build_trainable_win_training_data(df)
        output_path, summary_path = write_outputs(
            trainable_df,
            summary,
            output_path=args.output_path,
            report_dir=args.report_dir,
        )
        print(json.dumps(asdict(replace(summary, output_path=str(output_path))), ensure_ascii=False, indent=2))
        print(f"Saved trainable dataset: {output_path}")
        print(f"Saved summary JSON: {summary_path}")
        return 0
    except Exception as exc:
        logger.exception("Trainable win training data build failed")
        print(f"[error] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
