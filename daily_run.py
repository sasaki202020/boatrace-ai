"""日次運用の標準入口。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.daily_ops import run_daily_prediction, run_daily_settlement
from src.profitability_analysis import AnalysisPaths, run_analysis

BASE_DIR = Path(__file__).parent


def main() -> None:
    parser = argparse.ArgumentParser(description="競艇AI 日次運用")
    parser.add_argument("--date", required=True, help="対象日 YYYY-MM-DD")
    parser.add_argument("--phase", choices=["morning", "night", "full"],
                        required=True, help="morning=朝予想 / night=夜答え合わせ / full=両方")
    parser.add_argument("--places", nargs="+", default=None,
                        help="朝予想の対象レース場(省略時は開催場を自動検出)")
    parser.add_argument("--overwrite", action="store_true",
                        help="morning/fullで既存 predictions.csv を上書きする")
    parser.add_argument("--bankroll-yen", type=int, default=None,
                        help="night/full後の収益性分析で使うbankroll")
    args = parser.parse_args()

    try:
        if args.phase in ("morning", "full"):
            morning = run_daily_prediction(
                args.date,
                BASE_DIR,
                places=args.places,
                overwrite=args.overwrite,
            )
            print(f"morning_status={morning['status']}")
        if args.phase in ("night", "full"):
            report = run_daily_settlement(args.date, BASE_DIR)
            print(f"night_status={report['status']}")
            print(f"settled_races={report['settled_races']}")
            print(f"unavailable_races={report['unavailable_races_count']}")
            analysis = run_analysis(
                AnalysisPaths(
                    daily_root=BASE_DIR / "output" / "daily",
                    output_dir=BASE_DIR / "output" / "analysis" / "profitability",
                ),
                bankroll_yen=args.bankroll_yen,
            )
            print(f"profitability_decision={analysis['recommendation']['decision']}")
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
