from __future__ import annotations

"""Train the 1st-place baseline comparison models (core vs extended)."""

import argparse
import json
import logging
from pathlib import Path

from src.models.win_baseline_common import (
    DEFAULT_CORE_FEATURE_SET_PATH,
    DEFAULT_EXTENDED_FEATURE_SET_PATH,
    DEFAULT_MODEL_DIR,
    DEFAULT_TRAINABLE_PATH,
    build_comparison_report,
    load_feature_set_config,
    load_trainable_frame,
    rows_for_summary_csv,
    train_single_feature_set,
)


logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_DIR = ROOT / "reports" / "model_eval"
DEFAULT_MODEL_DIR = ROOT / "models" / "win_baseline"


def train_baseline_models(
    *,
    trainable_path: Path = DEFAULT_TRAINABLE_PATH,
    core_feature_set_path: Path = DEFAULT_CORE_FEATURE_SET_PATH,
    extended_feature_set_path: Path = DEFAULT_EXTENDED_FEATURE_SET_PATH,
    model_dir: Path = DEFAULT_MODEL_DIR,
) -> tuple[list, Path]:
    trainable_frame = load_trainable_frame(trainable_path)
    core_config = load_feature_set_config(core_feature_set_path)
    extended_config = load_feature_set_config(extended_feature_set_path)

    core_model_path = Path(model_dir) / "win_baseline_core.joblib"
    extended_model_path = Path(model_dir) / "win_baseline_extended.joblib"

    core_run, _, _, _ = train_single_feature_set(
        trainable_frame=trainable_frame,
        feature_set_config=core_config,
        feature_set_path=core_feature_set_path,
        model_path=core_model_path,
    )
    extended_run, _, _, _ = train_single_feature_set(
        trainable_frame=trainable_frame,
        feature_set_config=extended_config,
        feature_set_path=extended_feature_set_path,
        model_path=extended_model_path,
    )
    return [core_run, extended_run], Path(model_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train baseline win models for core and extended feature sets")
    parser.add_argument("--trainable-path", type=Path, default=DEFAULT_TRAINABLE_PATH)
    parser.add_argument("--core-feature-set-path", type=Path, default=DEFAULT_CORE_FEATURE_SET_PATH)
    parser.add_argument("--extended-feature-set-path", type=Path, default=DEFAULT_EXTENDED_FEATURE_SET_PATH)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--report-csv", type=Path, default=DEFAULT_REPORT_DIR / "win_model_baseline_core_vs_extended.csv")
    args = parser.parse_args(argv)

    try:
        runs, model_dir = train_baseline_models(
            trainable_path=args.trainable_path,
            core_feature_set_path=args.core_feature_set_path,
            extended_feature_set_path=args.extended_feature_set_path,
            model_dir=args.model_dir,
        )
        csv_df = rows_for_summary_csv(runs)
        args.report_csv.parent.mkdir(parents=True, exist_ok=True)
        csv_df.to_csv(args.report_csv, index=False, encoding="utf-8")
        comparison = build_comparison_report(runs, input_dataset=str(args.trainable_path))
        print(json.dumps(comparison, ensure_ascii=False, indent=2))
        print(f"Saved model directory: {model_dir}")
        print(f"Saved comparison CSV: {args.report_csv}")
        return 0
    except Exception as exc:
        logger.exception("Baseline model training failed")
        print(f"[error] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

