from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.evaluation.run_batch_evaluation_v2 import (
    DEFAULT_COMPARISON_TARGETS,
    DEFAULT_DB_PATH,
    DEFAULT_OUTPUT_DIR,
    run_batch_evaluation_v2,
    select_batch_dates,
    write_batch_outputs,
)


DEFAULT_HISTORICAL_PATH = Path("data/processed/historical_races.csv")
DEFAULT_ODDS_ROOT = Path("data/odds")
DEFAULT_RAW_CANDIDATES = Path("data/strategy_outputs/skip_decisions.csv")
DEFAULT_CAL_CANDIDATES = Path("data/strategy_outputs/skip_decisions_with_calibrated_prob.csv")
DEFAULT_V1_COMPARE_DIR = Path("reports/comparison")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run batch v2 evaluation for selected comparison days.")
    parser.add_argument("--mode", choices=["target-only", "include-hold", "explicit-dates"], default="target-only")
    parser.add_argument("--dates", default="", help="Comma separated dates for explicit-dates mode")
    parser.add_argument("--limit", type=int, default=0, help="Limit the number of selected dates")
    parser.add_argument("--skip-existing", action="store_true", help="Skip dates already present in batch_results.csv")
    parser.add_argument("--dry-run", action="store_true", help="Only show selected dates")
    parser.add_argument("--comparison-targets", type=Path, default=DEFAULT_COMPARISON_TARGETS)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--historical-path", type=Path, default=DEFAULT_HISTORICAL_PATH)
    parser.add_argument("--odds-root", type=Path, default=DEFAULT_ODDS_ROOT)
    parser.add_argument("--raw-candidates", type=Path, default=DEFAULT_RAW_CANDIDATES)
    parser.add_argument("--cal-candidates", type=Path, default=DEFAULT_CAL_CANDIDATES)
    parser.add_argument("--v1-compare-dir", type=Path, default=DEFAULT_V1_COMPARE_DIR)
    return parser.parse_args()


def _as_repo_path(path: Path) -> Path:
    return path if path.is_absolute() else REPO_ROOT / path


def main() -> int:
    args = parse_args()
    explicit_dates = [item.strip() for item in args.dates.split(",") if item.strip()]

    output_dir = _as_repo_path(args.output_dir)
    selected_days = select_batch_dates(
        mode=args.mode,
        explicit_dates=explicit_dates,
        limit=args.limit,
        skip_existing=args.skip_existing,
        comparison_targets_path=_as_repo_path(args.comparison_targets),
        existing_result_csv=output_dir / "batch_results.csv",
    )

    if args.dry_run:
        print("=== v2 Batch Dry Run ===")
        print(f"mode: {args.mode}")
        print(f"selected_count: {len(selected_days)}")
        if selected_days.empty:
            print("selected_dates: []")
        else:
            for row in selected_days.to_dict(orient="records"):
                print(f"- {row.get('date')} [{str(row.get('status', '')).upper()}]")
        return 0

    results, failures, summary = run_batch_evaluation_v2(
        selected_days=selected_days,
        db_path=_as_repo_path(args.db_path),
        historical_path=_as_repo_path(args.historical_path),
        odds_root=_as_repo_path(args.odds_root),
        raw_candidates_path=_as_repo_path(args.raw_candidates),
        cal_candidates_path=_as_repo_path(args.cal_candidates),
        v1_compare_dir=_as_repo_path(args.v1_compare_dir),
        dry_run=False,
    )

    results_csv, summary_json, failures_csv = write_batch_outputs(
        output_dir=output_dir,
        results=results,
        failures=failures,
        summary=summary,
    )

    print("=== v2 Batch Evaluation ===")
    print(f"selected_count: {len(selected_days)}")
    print(f"run_count: {summary.get('run_count')}")
    print(f"success_count: {summary.get('success_count')}")
    print(f"hold_count: {summary.get('hold_count')}")
    print(f"fail_count: {summary.get('fail_count')}")
    print(f"raw_buy: {summary.get('raw', {}).get('buy')}")
    print(f"cal_buy: {summary.get('calibrated', {}).get('buy')}")
    print(f"raw_hit: {summary.get('raw', {}).get('hit')}")
    print(f"cal_hit: {summary.get('calibrated', {}).get('hit')}")
    print(f"raw_roi: {summary.get('raw', {}).get('roi')}")
    print(f"cal_roi: {summary.get('calibrated', {}).get('roi')}")
    print("Saved:")
    print(f"- {results_csv}")
    print(f"- {summary_json}")
    print(f"- {failures_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
