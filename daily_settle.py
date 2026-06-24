"""保存済み予想の日次夜答え合わせCLI。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.daily_ops import run_daily_settlement
from src.profitability_analysis import AnalysisPaths, run_analysis

BASE_DIR = Path(__file__).parent


def main() -> None:
    parser = argparse.ArgumentParser(description="日次夜答え合わせ")
    parser.add_argument("--date", required=True, help="答え合わせ対象日 YYYY-MM-DD")
    parser.add_argument("--bankroll-yen", type=int, default=None,
                        help="収益性分析の資金管理計算用bankroll")
    args = parser.parse_args()

    try:
        report = run_daily_settlement(args.date, BASE_DIR)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    print(f"status={report['status']}")
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


if __name__ == "__main__":
    main()
