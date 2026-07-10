from __future__ import annotations

"""Evaluate the core vs core+relative win models and write the report."""

import argparse
import json
import logging
from pathlib import Path

from src.models.win_baseline_common import (
    DEFAULT_CORE_FEATURE_SET_PATH,
    DEFAULT_MODEL_DIR,
    DEFAULT_REPORT_DIR,
    DEFAULT_TRAINABLE_PATH,
    augment_features_for_relative_comparison,
    build_relative_comparison_report,
    evaluate_model_bundle,
    load_trainable_frame,
    rows_for_report_dicts,
)


logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_DIR = ROOT / "models" / "win_relative"
DEFAULT_REPORT_JSON = ROOT / "reports" / "model_eval" / "win_model_core_vs_core_relative.json"
DEFAULT_REPORT_CSV = ROOT / "reports" / "model_eval" / "win_model_core_vs_core_relative.csv"


def evaluate_core_vs_core_relative(
    *,
    trainable_path: Path = DEFAULT_TRAINABLE_PATH,
    core_model_path: Path = DEFAULT_MODEL_DIR / "win_baseline_core.joblib",
    core_relative_model_path: Path = DEFAULT_MODEL_DIR / "win_baseline_core_relative.joblib",
) -> tuple[dict, list[dict]]:
    trainable_frame = load_trainable_frame(trainable_path)
    relative_frame = augment_features_for_relative_comparison(trainable_frame)
    core_report, _, _ = evaluate_model_bundle(trainable_frame=trainable_frame, bundle_path=core_model_path)
    relative_report, _, _ = evaluate_model_bundle(trainable_frame=relative_frame, bundle_path=core_relative_model_path)
    return build_relative_comparison_report(
        [core_report, relative_report],
        input_dataset=str(trainable_path),
    ), [core_report, relative_report]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate core vs core-relative win models and write report")
    parser.add_argument("--trainable-path", type=Path, default=DEFAULT_TRAINABLE_PATH)
    parser.add_argument("--core-model-path", type=Path, default=DEFAULT_MODEL_DIR / "win_baseline_core.joblib")
    parser.add_argument("--core-relative-model-path", type=Path, default=DEFAULT_MODEL_DIR / "win_baseline_core_relative.joblib")
    parser.add_argument("--report-json", type=Path, default=DEFAULT_REPORT_JSON)
    parser.add_argument("--report-csv", type=Path, default=DEFAULT_REPORT_CSV)
    args = parser.parse_args(argv)

    try:
        report, feature_reports = evaluate_core_vs_core_relative(
            trainable_path=args.trainable_path,
            core_model_path=args.core_model_path,
            core_relative_model_path=args.core_relative_model_path,
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
        logger.exception("Core vs core-relative evaluation failed")
        print(f"[error] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
