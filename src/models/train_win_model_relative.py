from __future__ import annotations

"""Train the 1st-place core vs core+relative comparison models."""

import argparse
import json
import logging
from pathlib import Path

from src.features.build_relative_features import RELATIVE_FEATURE_COLUMNS
from src.models.win_baseline_common import (
    DEFAULT_CORE_FEATURE_SET_PATH,
    DEFAULT_MODEL_DIR,
    DEFAULT_REPORT_DIR,
    DEFAULT_TRAINABLE_PATH,
    augment_features_for_relative_comparison,
    build_relative_comparison_report,
    load_feature_set_config,
    load_trainable_frame,
    rows_for_summary_csv,
    train_single_feature_set,
)


logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_DIR = ROOT / "models" / "win_relative"
DEFAULT_REPORT_CSV = ROOT / "reports" / "model_eval" / "win_model_core_vs_core_relative.csv"


def _make_core_relative_config(core_config: dict[str, object]) -> dict[str, object]:
    raw_features = [str(item) for item in core_config["features"]] + list(RELATIVE_FEATURE_COLUMNS)
    return {
        "feature_set_name": "win_baseline_core_relative",
        "features": raw_features,
    }


def train_core_vs_core_relative(
    *,
    trainable_path: Path = DEFAULT_TRAINABLE_PATH,
    core_feature_set_path: Path = DEFAULT_CORE_FEATURE_SET_PATH,
    model_dir: Path = DEFAULT_MODEL_DIR,
) -> tuple[list, Path]:
    trainable_frame = load_trainable_frame(trainable_path)
    core_config = load_feature_set_config(core_feature_set_path)
    core_relative_config = _make_core_relative_config(core_config)
    relative_frame = augment_features_for_relative_comparison(trainable_frame)

    core_model_path = Path(model_dir) / "win_baseline_core.joblib"
    core_relative_model_path = Path(model_dir) / "win_baseline_core_relative.joblib"

    core_run, _, _, _ = train_single_feature_set(
        trainable_frame=trainable_frame,
        feature_set_config=core_config,
        feature_set_path=core_feature_set_path,
        model_path=core_model_path,
        relative_features_used=[],
    )
    relative_run, _, _, _ = train_single_feature_set(
        trainable_frame=relative_frame,
        feature_set_config=core_relative_config,
        feature_set_path=core_feature_set_path,
        model_path=core_relative_model_path,
        relative_features_used=list(RELATIVE_FEATURE_COLUMNS),
    )
    return [core_run, relative_run], Path(model_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train core vs core+relative win models")
    parser.add_argument("--trainable-path", type=Path, default=DEFAULT_TRAINABLE_PATH)
    parser.add_argument("--core-feature-set-path", type=Path, default=DEFAULT_CORE_FEATURE_SET_PATH)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--report-csv", type=Path, default=DEFAULT_REPORT_CSV)
    args = parser.parse_args(argv)

    try:
        runs, model_dir = train_core_vs_core_relative(
            trainable_path=args.trainable_path,
            core_feature_set_path=args.core_feature_set_path,
            model_dir=args.model_dir,
        )
        csv_df = rows_for_summary_csv(runs)
        args.report_csv.parent.mkdir(parents=True, exist_ok=True)
        csv_df.to_csv(args.report_csv, index=False, encoding="utf-8")
        report = build_relative_comparison_report(runs, input_dataset=str(args.trainable_path))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"Saved model directory: {model_dir}")
        print(f"Saved comparison CSV: {args.report_csv}")
        return 0
    except Exception as exc:
        logger.exception("Core vs core-relative training failed")
        print(f"[error] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
