from __future__ import annotations

"""Evaluate the Phase 2 place2 conditional model."""

import argparse
import json
import logging
from pathlib import Path

from src.models.place2_phase2_common import (
    DEFAULT_MODEL_DIR,
    DEFAULT_PHASE1_MODEL_PATH,
    DEFAULT_REPORT_CSV,
    DEFAULT_REPORT_JSON,
    DEFAULT_SPLIT_MANIFEST,
    DEFAULT_TRAINABLE_PATH,
    evaluate_phase2_model,
)


logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_PATH = DEFAULT_MODEL_DIR / "place2_model_phase2_core_relative.joblib"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the Phase 2 place2 conditional model")
    parser.add_argument("--model-path", type=Path, default=DEFAULT_MODEL_PATH)
    parser.add_argument("--trainable-path", type=Path, default=DEFAULT_TRAINABLE_PATH)
    parser.add_argument("--phase1-model-path", type=Path, default=DEFAULT_PHASE1_MODEL_PATH)
    parser.add_argument("--report-json", type=Path, default=DEFAULT_REPORT_JSON)
    parser.add_argument("--report-csv", type=Path, default=DEFAULT_REPORT_CSV)
    parser.add_argument("--split-manifest-path", type=Path, default=DEFAULT_SPLIT_MANIFEST)
    args = parser.parse_args(argv)

    try:
        result = evaluate_phase2_model(
            model_path=args.model_path,
            trainable_path=args.trainable_path,
            phase1_model_path=args.phase1_model_path,
            report_json=args.report_json,
            report_csv=args.report_csv,
            split_manifest_path=args.split_manifest_path,
        )
        print(json.dumps(result["report"], ensure_ascii=False, indent=2))
        print(f"Saved report JSON: {args.report_json}")
        print(f"Saved report CSV: {args.report_csv}")
        print(f"Saved split manifest: {args.split_manifest_path}")
        return 0
    except Exception as exc:
        logger.exception("Phase 2 evaluation failed")
        print(f"[error] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
