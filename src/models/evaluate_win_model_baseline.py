from __future__ import annotations

"""Evaluate the baseline win models and write the comparison report."""

import argparse
import json
import logging
from pathlib import Path

from src.models.win_baseline_common import (
    DEFAULT_CORE_FEATURE_SET_PATH,
    DEFAULT_EXTENDED_FEATURE_SET_PATH,
    DEFAULT_MODEL_DIR,
    DEFAULT_REPORT_CSV,
    DEFAULT_REPORT_JSON,
    DEFAULT_TRAINABLE_PATH,
    evaluate_model_bundle,
    load_trainable_frame,
    rows_for_report_dicts,
)


logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]


def evaluate_baseline_models(
    *,
    trainable_path: Path = DEFAULT_TRAINABLE_PATH,
    core_model_path: Path = DEFAULT_MODEL_DIR / "win_baseline_core.joblib",
    extended_model_path: Path = DEFAULT_MODEL_DIR / "win_baseline_extended.joblib",
) -> tuple[dict, list[dict]]:
    trainable_frame = load_trainable_frame(trainable_path)
    core_report, _, _ = evaluate_model_bundle(trainable_frame=trainable_frame, bundle_path=core_model_path)
    extended_report, _, _ = evaluate_model_bundle(trainable_frame=trainable_frame, bundle_path=extended_model_path)
    report = {
        "report_type": "win_model_baseline_core_vs_extended",
        "input_dataset": str(trainable_path),
        "feature_sets": [core_report, extended_report],
        "comparison": {
            "test_metric_delta_extended_minus_core": {
                key: float(extended_report["metrics"]["test"][key]) - float(core_report["metrics"]["test"][key])
                for key in ["log_loss", "brier_score", "calibration_error", "top1_accuracy", "top1_win_rate"]
            }
        },
    }
    return report, [core_report, extended_report]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate baseline win models and write report")
    parser.add_argument("--trainable-path", type=Path, default=DEFAULT_TRAINABLE_PATH)
    parser.add_argument("--core-model-path", type=Path, default=DEFAULT_MODEL_DIR / "win_baseline_core.joblib")
    parser.add_argument("--extended-model-path", type=Path, default=DEFAULT_MODEL_DIR / "win_baseline_extended.joblib")
    parser.add_argument("--report-json", type=Path, default=DEFAULT_REPORT_JSON)
    parser.add_argument("--report-csv", type=Path, default=DEFAULT_REPORT_CSV)
    args = parser.parse_args(argv)

    try:
        report, feature_reports = evaluate_baseline_models(
            trainable_path=args.trainable_path,
            core_model_path=args.core_model_path,
            extended_model_path=args.extended_model_path,
        )
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        csv_df = rows_for_report_dicts(feature_reports)
        args.report_csv.parent.mkdir(parents=True, exist_ok=True)
        csv_df.to_csv(args.report_csv, index=False, encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"Saved report JSON: {args.report_json}")
        print(f"Saved report CSV: {args.report_csv}")
        return 0
    except Exception as exc:
        logger.exception("Baseline model evaluation failed")
        print(f"[error] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
