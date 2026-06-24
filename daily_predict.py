"""全開催場の日次朝予想CLI。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.daily_ops import run_daily_prediction

BASE_DIR = Path(__file__).parent


def main() -> None:
    parser = argparse.ArgumentParser(description="全開催場の日次朝予想")
    parser.add_argument("--date", required=True, help="予想対象日 YYYY-MM-DD")
    parser.add_argument("--places", nargs="+", default=None,
                        help="対象レース場(省略時は開催場を自動検出)")
    parser.add_argument("--overwrite", action="store_true",
                        help="既存 predictions.csv を上書きする")
    parser.add_argument("--model", default=None, help="モデルファイルのパス")
    args = parser.parse_args()

    try:
        result = run_daily_prediction(
            args.date,
            BASE_DIR,
            places=args.places,
            overwrite=args.overwrite,
            model_path=args.model,
        )
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    print(f"status={result['status']}")
    print(f"prediction_rows={result.get('prediction_rows', 0)}")


if __name__ == "__main__":
    main()
