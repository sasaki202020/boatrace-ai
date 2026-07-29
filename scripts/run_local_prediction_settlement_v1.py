from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.feature_forward_v1.local_pipeline import (
    generate_daily_predictions,
    settle_available_predictions,
)
from src.feature_forward_v1.runtime_sync import (
    sync_runtime_official_inputs,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--entry-source", type=Path, action="append", default=[])
    parser.add_argument("--result-source", type=Path, action="append", default=[])
    parser.add_argument("--minimum-token", default="260721")
    args = parser.parse_args()
    input_sync = sync_runtime_official_inputs(
        runtime_root=args.runtime,
        entry_sources=args.entry_source,
        result_sources=args.result_source,
        minimum_token=args.minimum_token,
    )
    b_root = args.runtime / "data/raw/official/entries"
    latest_b = sorted(b_root.glob("B*.TXT"))[-1]
    prediction = generate_daily_predictions(
        b_file=latest_b,
        prediction_root=args.runtime / "data/prospective/predictions",
        model_path=args.artifact_root / "data/commercialization_v1/frozen_candidate/tree_15.joblib",
        history_path=args.artifact_root / "data/offline_model_v3/canonical_race_results.csv",
    )
    settlement = settle_available_predictions(
        prediction_root=args.runtime / "data/prospective/predictions",
        settlement_root=args.runtime / "data/prospective/settlements",
        k_root=args.runtime / "data/raw/official/results",
    )
    report = {
        "inputSync": input_sync,
        "latestB": latest_b.name,
        "prediction": prediction,
        "settlement": settlement,
        "tree15Changed": False,
        "productionWrites": 0,
    }
    status = args.runtime / "reports/prediction_priority/latest_status.json"
    status.parent.mkdir(parents=True, exist_ok=True)
    status.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))
    if settlement["conflicts"]:
        return 2
    return 3 if input_sync["sourceErrors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
