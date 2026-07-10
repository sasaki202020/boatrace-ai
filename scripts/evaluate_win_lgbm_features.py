from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.models.win_lgbm import DEFAULT_FEATURE_PATH, DEFAULT_HISTORICAL_PATH, DEFAULT_MODEL_DIR, DEFAULT_REPORT_DIR, compare_feature_sets


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare baseline vs relative feature sets for the win model")
    parser.add_argument("--feature-path", type=Path, default=DEFAULT_FEATURE_PATH)
    parser.add_argument("--historical-path", type=Path, default=DEFAULT_HISTORICAL_PATH)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--valid-days", type=int, default=30)
    parser.add_argument("--test-days", type=int, default=30)
    parser.add_argument("--force-rebuild-features", action="store_true")
    parser.add_argument("--random-state", type=int, default=42)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = compare_feature_sets(
        feature_path=args.feature_path,
        historical_path=args.historical_path,
        report_dir=args.report_dir,
        model_dir=args.model_dir,
        valid_days=args.valid_days,
        test_days=args.test_days,
        force_rebuild_features=args.force_rebuild_features,
        random_state=args.random_state,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Saved report: {report['report_paths']['json']}")
    print(f"Saved csv: {report['report_paths']['csv']}")
    print(f"Saved calibration bins: {report['report_paths']['calibration_bins']}")


if __name__ == "__main__":
    main()
