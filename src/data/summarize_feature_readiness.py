from __future__ import annotations

"""Summarize baseline feature readiness for the win model without training."""

import argparse
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from src.data.audit_win_training_data import _load_csv


logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TRAINABLE_PATH = ROOT / "data" / "processed" / "trainable_win_training_data.csv"
DEFAULT_CLEAN_PATH = ROOT / "data" / "processed" / "clean_win_training_data.csv"
DEFAULT_REPORT_PATH = ROOT / "reports" / "data_audit" / "feature_readiness_summary.json"
DEFAULT_DETAIL_PATH = ROOT / "reports" / "data_audit" / "feature_readiness_detail.csv"
DEFAULT_CORE_CONFIG_PATH = ROOT / "config" / "feature_sets" / "win_baseline_core.json"
DEFAULT_EXTENDED_CONFIG_PATH = ROOT / "config" / "feature_sets" / "win_baseline_extended.json"

TARGET_AND_LEAK_COLUMNS = {"target_win", "finish_position"}
META_COLUMNS = {"race_id", "date", "union_key"}
IDENTIFIER_COLUMNS = {"racer_id"}
REDUNDANT_COLUMNS = {"venue"}
CORE_INCLUDE_COLUMNS = {"jcd", "race_number", "boat_no", "exhibition_time"}
EXTENDED_ONLY_COLUMNS = {"avg_st", "national_2ren_rate", "local_2ren_rate"}
ALWAYS_EXCLUDE_COLUMNS = {"racer_class", "national_win_rate", "local_win_rate", "motor_2ren_rate", "boat_2ren_rate"}


@dataclass(frozen=True)
class FeatureDecision:
    feature_name: str
    dtype: str
    unique_count: int
    null_rate: float
    imputed_rate: float | None
    include_in_core: bool
    include_in_extended: bool
    reason: str


def _load_frame(path: Path) -> pd.DataFrame:
    return _load_csv(Path(path))


def _prepare_clean_lookup(clean_df: pd.DataFrame) -> pd.DataFrame:
    required = {"race_id", "boat_no"}
    missing = required - set(clean_df.columns)
    if missing:
        raise ValueError(f"clean dataset missing required columns for imputation trace: {sorted(missing)}")

    lookup = clean_df.copy()
    lookup["race_id"] = lookup["race_id"].astype("string").str.strip()
    lookup["boat_no"] = pd.to_numeric(lookup["boat_no"], errors="coerce")
    if lookup.duplicated(subset=["race_id", "boat_no"]).any():
        raise ValueError("clean dataset has duplicate race_id+boat_no rows; imputation trace cannot be aligned safely")
    return lookup


def _build_imputation_map(trainable_df: pd.DataFrame, clean_df: pd.DataFrame | None) -> dict[str, float | None]:
    imputation_map: dict[str, float | None] = {col: None for col in trainable_df.columns}
    if clean_df is None:
        return imputation_map

    train = trainable_df.copy()
    train["race_id"] = train["race_id"].astype("string").str.strip()
    train["boat_no"] = pd.to_numeric(train["boat_no"], errors="coerce")

    clean_lookup = _prepare_clean_lookup(clean_df)
    merged = train.merge(clean_lookup, on=["race_id", "boat_no"], how="left", suffixes=("", "__clean"), validate="one_to_one")
    n_rows = len(merged)
    if n_rows == 0:
        return imputation_map

    for col in trainable_df.columns:
        clean_col = f"{col}__clean"
        if clean_col not in merged.columns:
            imputation_map[col] = None
            continue
        clean_missing = merged[clean_col].isna()
        train_non_missing = merged[col].notna()
        imputation_map[col] = float((clean_missing & train_non_missing).mean())
    return imputation_map


def _decide_feature(feature_name: str, null_rate: float, imputed_rate: float | None) -> tuple[bool, bool, str]:
    if feature_name in TARGET_AND_LEAK_COLUMNS:
        return False, False, "教師列または目的変数リーク列のため baseline には使わない。"
    if feature_name in META_COLUMNS:
        return False, False, "時系列分割や結合には使うが、baseline 学習特徴には使わないメタ列。"
    if feature_name in IDENTIFIER_COLUMNS:
        return False, False, "高基数の識別子であり、baseline でそのまま使うと過学習しやすいため除外。"
    if feature_name in REDUNDANT_COLUMNS:
        return False, False, "venue は文字列列で jcd と情報が重複するため、baseline では jcd を採用する。"
    if feature_name in ALWAYS_EXCLUDE_COLUMNS:
        return False, False, "欠損または補完前欠損が極端に大きく、現時点では baseline 候補にしない。"
    if feature_name in CORE_INCLUDE_COLUMNS:
        return True, True, "欠損がなく構造的に安定しているため core に採用する。"
    if feature_name == "avg_st":
        return False, True, "avg_st は補完率が中程度で情報価値はあるが、raw 完全列ではないため extended のみに入れる。"
    if feature_name in {"national_2ren_rate", "local_2ren_rate"}:
        return False, True, "補完率が非常に高いため core から外し、比較用に extended のみに入れる。"
    if null_rate > 0.0:
        return False, False, "null が残っている列は baseline feature set に固定しない。"
    if imputed_rate is not None and imputed_rate > 0.0:
        return False, False, "補完が必要だった列だが、現時点の採用優先度は低いため baseline から外す。"
    return False, False, "現時点では baseline 用途が明確でないため不採用。"


def summarize_feature_readiness(
    trainable_df: pd.DataFrame,
    clean_df: pd.DataFrame | None = None,
) -> tuple[dict[str, Any], pd.DataFrame, dict[str, Any], dict[str, Any]]:
    """Return summary JSON payload, detail dataframe, and core/extended configs."""

    if "race_id" not in trainable_df.columns or "boat_no" not in trainable_df.columns:
        raise ValueError("trainable dataset must contain race_id and boat_no")

    imputation_map = _build_imputation_map(trainable_df, clean_df)
    details: list[FeatureDecision] = []
    for feature_name in trainable_df.columns:
        series = trainable_df[feature_name]
        null_rate = float(series.isna().mean())
        unique_count = int(series.nunique(dropna=True))
        dtype = str(series.dtype)
        imputed_rate = imputation_map.get(feature_name)
        include_in_core, include_in_extended, reason = _decide_feature(feature_name, null_rate, imputed_rate)
        details.append(
            FeatureDecision(
                feature_name=feature_name,
                dtype=dtype,
                unique_count=unique_count,
                null_rate=null_rate,
                imputed_rate=imputed_rate,
                include_in_core=include_in_core,
                include_in_extended=include_in_extended,
                reason=reason,
            )
        )

    detail_df = pd.DataFrame([asdict(item) for item in details]).sort_values("feature_name", kind="mergesort").reset_index(drop=True)
    if "imputed_rate" in detail_df.columns:
        detail_df["imputed_rate"] = detail_df["imputed_rate"].where(detail_df["imputed_rate"].notna(), None)
    core_features = detail_df.loc[detail_df["include_in_core"], "feature_name"].tolist()
    extended_features = detail_df.loc[detail_df["include_in_extended"], "feature_name"].tolist()

    feature_records = detail_df.astype(object).where(pd.notna(detail_df), None).to_dict(orient="records")
    avg_st_row = next(record for record in feature_records if record["feature_name"] == "avg_st")
    summary = {
        "source_dataset": str(DEFAULT_TRAINABLE_PATH),
        "row_count": int(len(trainable_df)),
        "feature_count": int(len(detail_df)),
        "core_feature_count": int(len(core_features)),
        "extended_feature_count": int(len(extended_features)),
        "core_features": core_features,
        "extended_features": extended_features,
        "avg_st_decision": {
            "feature_name": "avg_st",
            "include_in_core": bool(avg_st_row["include_in_core"]),
            "include_in_extended": bool(avg_st_row["include_in_extended"]),
            "reason": avg_st_row["reason"],
            "imputed_rate": avg_st_row["imputed_rate"],
        },
        "features": feature_records,
    }
    core_config = {
        "feature_set_name": "win_baseline_core",
        "source_dataset": str(DEFAULT_TRAINABLE_PATH),
        "features": core_features,
        "description": "高信頼列のみで構成した 1着 baseline core feature set。",
    }
    extended_config = {
        "feature_set_name": "win_baseline_extended",
        "source_dataset": str(DEFAULT_TRAINABLE_PATH),
        "features": extended_features,
        "description": "core に補完列候補を加えた 1着 baseline extended feature set。",
    }
    return summary, detail_df, core_config, extended_config


def write_outputs(
    summary: dict[str, Any],
    detail_df: pd.DataFrame,
    core_config: dict[str, Any],
    extended_config: dict[str, Any],
    *,
    summary_path: Path = DEFAULT_REPORT_PATH,
    detail_path: Path = DEFAULT_DETAIL_PATH,
    core_config_path: Path = DEFAULT_CORE_CONFIG_PATH,
    extended_config_path: Path = DEFAULT_EXTENDED_CONFIG_PATH,
) -> tuple[Path, Path, Path, Path]:
    summary_path = Path(summary_path)
    detail_path = Path(detail_path)
    core_config_path = Path(core_config_path)
    extended_config_path = Path(extended_config_path)

    summary_path.parent.mkdir(parents=True, exist_ok=True)
    detail_path.parent.mkdir(parents=True, exist_ok=True)
    core_config_path.parent.mkdir(parents=True, exist_ok=True)
    extended_config_path.parent.mkdir(parents=True, exist_ok=True)

    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    detail_df.to_csv(detail_path, index=False, encoding="utf-8")
    core_config_path.write_text(json.dumps(core_config, ensure_ascii=False, indent=2), encoding="utf-8")
    extended_config_path.write_text(json.dumps(extended_config, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary_path, detail_path, core_config_path, extended_config_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize baseline feature readiness for the win model")
    parser.add_argument("--trainable-path", type=Path, default=DEFAULT_TRAINABLE_PATH)
    parser.add_argument("--clean-path", type=Path, default=DEFAULT_CLEAN_PATH)
    parser.add_argument("--summary-path", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--detail-path", type=Path, default=DEFAULT_DETAIL_PATH)
    parser.add_argument("--core-config-path", type=Path, default=DEFAULT_CORE_CONFIG_PATH)
    parser.add_argument("--extended-config-path", type=Path, default=DEFAULT_EXTENDED_CONFIG_PATH)
    args = parser.parse_args(argv)

    try:
        trainable_df = _load_frame(args.trainable_path)
        clean_df = _load_frame(args.clean_path) if Path(args.clean_path).exists() else None
        summary, detail_df, core_config, extended_config = summarize_feature_readiness(trainable_df, clean_df)
        summary_path, detail_path, core_path, extended_path = write_outputs(
            summary,
            detail_df,
            core_config,
            extended_config,
            summary_path=args.summary_path,
            detail_path=args.detail_path,
            core_config_path=args.core_config_path,
            extended_config_path=args.extended_config_path,
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print(f"Saved feature readiness summary: {summary_path}")
        print(f"Saved feature readiness detail: {detail_path}")
        print(f"Saved core feature set: {core_path}")
        print(f"Saved extended feature set: {extended_path}")
        return 0
    except Exception as exc:
        logger.exception("Feature readiness summarization failed")
        print(f"[error] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
