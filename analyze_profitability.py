"""Build shadow profitability and bankroll-gate analysis from daily artifacts."""
from __future__ import annotations

import argparse
from pathlib import Path

from src.profitability_analysis import AnalysisPaths, run_analysis


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="収益性のshadow分析を作成する")
    parser.add_argument("--daily-root", default="output/daily",
                        help="日次成果物のルート")
    parser.add_argument("--output-dir", default="output/analysis/profitability",
                        help="分析結果の出力先")
    parser.add_argument("--min-sample", type=int, default=100,
                        help="候補判定に必要な最小買い目数")
    parser.add_argument("--min-days", type=int, default=3,
                        help="候補判定に必要な最小日数")
    parser.add_argument("--min-roi-pct", type=float, default=105.0,
                        help="候補判定に必要な最低ROI")
    parser.add_argument("--bankroll-yen", type=int, default=None,
                        help="資金管理の計算用 bankroll。省略時は記録専用")
    parser.add_argument("--ev-threshold", type=float, default=1.0,
                        help="value_filter の期待値しきい値")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    project_root = Path(__file__).resolve().parent
    daily_root = Path(args.daily_root)
    if not daily_root.is_absolute():
        daily_root = project_root / daily_root
    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = project_root / output_dir
    payload = run_analysis(
        AnalysisPaths(daily_root=daily_root, output_dir=output_dir),
        min_sample=args.min_sample,
        min_days=args.min_days,
        min_roi_pct=args.min_roi_pct,
        bankroll_yen=args.bankroll_yen,
        ev_threshold=args.ev_threshold,
    )
    print(
        "analysis_status={status} decision={decision} days={days} settled_rows={rows} output={output}".format(
            status=payload["analysis_status"],
            decision=payload["recommendation"]["decision"],
            days=payload["current"]["days"],
            rows=payload["current"]["settled_rows"],
            output=output_dir,
        )
    )


if __name__ == "__main__":
    main()
