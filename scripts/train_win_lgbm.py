from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.models.win_lgbm import DEFAULT_FEATURE_PATH, DEFAULT_HISTORICAL_PATH, DEFAULT_MODEL_DIR, train_feature_set


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train a win model with LightGBM")
    parser.add_argument("--feature-set", choices=["baseline", "relative"], default="baseline")
    parser.add_argument("--feature-path", type=Path, default=DEFAULT_FEATURE_PATH)
    parser.add_argument("--historical-path", type=Path, default=DEFAULT_HISTORICAL_PATH)
    parser.add_argument("--model-path", type=Path, default=None)
    parser.add_argument("--valid-days", type=int, default=30)
    parser.add_argument("--test-days", type=int, default=30)
    parser.add_argument("--force-rebuild-features", action="store_true")
    parser.add_argument("--random-state", type=int, default=42)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    model_path = args.model_path
    if model_path is None:
        model_path = DEFAULT_MODEL_DIR / f"{args.feature_set}.joblib"
    result = train_feature_set(
        feature_set=args.feature_set,
        feature_path=args.feature_path,
        historical_path=args.historical_path,
        model_path=model_path,
        valid_days=args.valid_days,
        test_days=args.test_days,
        force_rebuild_features=args.force_rebuild_features,
        random_state=args.random_state,
    )
    print(f"Saved model: {result['artifact_path']}")
    print(result["metrics"]["test"])


if __name__ == "__main__":
    main()
