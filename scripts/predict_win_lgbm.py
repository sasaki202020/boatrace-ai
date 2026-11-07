from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.features.build_features import FeatureBuilder
from src.models.win_lgbm import DEFAULT_MODEL_DIR, DEFAULT_PREDICTION_PATH, DEFAULT_TODAY_FEATURE_PATH, predict_feature_set


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Predict win probabilities with a LightGBM win model")
    parser.add_argument("--artifact-path", type=Path, default=None)
    parser.add_argument("--feature-set", choices=["baseline", "relative"], default="baseline")
    parser.add_argument("--feature-path", type=Path, default=DEFAULT_TODAY_FEATURE_PATH)
    parser.add_argument("--today-input", type=Path, default=ROOT / "data" / "processed" / "today_races.csv")
    parser.add_argument("--output-path", type=Path, default=DEFAULT_PREDICTION_PATH)
    parser.add_argument("--rebuild-features", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    artifact_path = args.artifact_path
    if artifact_path is None:
        artifact_path = DEFAULT_MODEL_DIR / f"{args.feature_set}.joblib"
    if args.rebuild_features or not args.feature_path.exists():
        if not args.today_input.exists():
            raise FileNotFoundError(f"today input not found: {args.today_input}")
        FeatureBuilder().build(str(args.today_input), str(args.feature_path), "today")
    try:
        pred = predict_feature_set(artifact_path=artifact_path, feature_path=args.feature_path)
    except ValueError as exc:
        if "missing required feature columns" not in str(exc):
            raise
        if not args.today_input.exists():
            raise
        FeatureBuilder().build(str(args.today_input), str(args.feature_path), "today")
        pred = predict_feature_set(artifact_path=artifact_path, feature_path=args.feature_path)
    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    pred.to_csv(args.output_path, index=False, encoding="utf-8")
    print(f"Saved predictions: {args.output_path}")
    print(f"rows={len(pred)} races={pred['race_id'].nunique() if 'race_id' in pred.columns else 0}")


if __name__ == "__main__":
    main()
