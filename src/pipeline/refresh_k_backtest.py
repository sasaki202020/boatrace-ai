from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from src.evaluation.audit_historical_inputs import audit_historical_inputs
from src.evaluation.audit_k_result_coverage import audit_k_result_coverage
from src.evaluation.backtest_range import run_backtest_range
from src.evaluation.compare_prediction_sources import compare_prediction_sources
from src.ingest.official_k_loader import collect_official_k_results_range
from src.pipeline.collect_historical_inputs import collect_historical_inputs


ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = ROOT / "reports" / "backtest"


def _normalize_date(value: str) -> str:
    token = str(value).strip().lower()
    digits = "".join(ch for ch in token if ch.isdigit())
    if len(digits) != 8:
        raise ValueError(f"invalid date: {value!r}")
    return digits


def _additional_days_needed(*, remaining_settled: int, days_with_k_file: int, backfill_settled_bet_count: int) -> tuple[int | None, float | None]:
    if days_with_k_file <= 0:
        return None, None
    average = backfill_settled_bet_count / max(1, days_with_k_file)
    if average <= 0:
        return None, round(average, 4)
    return int(math.ceil(remaining_settled / average)), round(average, 4)


def refresh_k_backtest(
    *,
    start_date: str,
    end_date: str,
    jcd: str = "all",
    input_dir: str | None = None,
    stake: int = 100,
) -> dict[str, Any]:
    start8 = _normalize_date(start_date)
    end8 = _normalize_date(end_date)
    date_tag = f"{start8}_{end8}"
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)

    coverage_before = audit_k_result_coverage(start_date=start8, end_date=end8, input_dir=input_dir)
    backtest_before = run_backtest_range(start_date=start8, end_date=end8, jcd=jcd, stake_per_buy=stake, prediction_source="backfill")

    loader_result = collect_official_k_results_range(start_date=start8, end_date=end8, input_dir=input_dir, jcd=jcd)
    collect_result = collect_historical_inputs(start_date=start8, end_date=end8, jcd=jcd, stages="result_txt", input_dir=input_dir)
    audit_result = audit_historical_inputs(start_date=start8, end_date=end8, jcd=jcd)
    backtest_after = run_backtest_range(start_date=start8, end_date=end8, jcd=jcd, stake_per_buy=stake, prediction_source="backfill")
    comparison_result = compare_prediction_sources(start_date=start8, end_date=end8, jcd=jcd, stake_per_buy=stake)
    coverage_after = audit_k_result_coverage(start_date=start8, end_date=end8, input_dir=input_dir)

    before_summary = backtest_before.get("summary") or {}
    after_summary = backtest_after.get("summary") or {}
    coverage_summary = coverage_after.get("summary") or {}
    collection_summary = collect_result.get("summary") or {}
    tuning = backtest_after.get("tuning") or {}
    missing_k_dates = list(coverage_summary.get("missingDates") or [])
    days_with_k_file = int(coverage_summary.get("daysWithKFile") or 0)
    backfill_before = int(before_summary.get("backfillSettledBetCount") or 0)
    backfill_after = int(after_summary.get("backfillSettledBetCount") or 0)
    remaining_settled = max(0, 300 - backfill_after)
    estimated_additional_days_needed, average_settled_per_k_day = _additional_days_needed(
        remaining_settled=remaining_settled,
        days_with_k_file=days_with_k_file,
        backfill_settled_bet_count=backfill_after,
    )

    recommended_next_action = (
        "collect_missing_k_files_and_rerun"
        if missing_k_dates
        else "add_more_k_files_before_tuning"
        if backfill_after < 300
        else "review_backfill_tuning_readiness"
    )

    summary = {
        "task": "TASK-017B",
        "dateRange": date_tag,
        "jcd": jcd,
        "inputDir": input_dir or "",
        "stake": stake,
        "daysWithKFile": days_with_k_file,
        "daysMissingKFile": int(coverage_summary.get("daysMissingKFile") or 0),
        "missingKDates": missing_k_dates,
        "resultTxtOkCount": int(coverage_summary.get("resultTxtOkCount") or 0),
        "resultTxtMissingCount": int(coverage_summary.get("resultTxtMissingCount") or 0),
        "resultTxtParseErrorCount": int(coverage_summary.get("resultTxtParseErrorCount") or 0),
        "parsedResultTxtRaceCount": int(coverage_summary.get("parsedResultTxtRaceCount") or 0),
        "backfillSettledBetCountBefore": backfill_before,
        "backfillSettledBetCountAfter": backfill_after,
        "settlementCoverage": after_summary.get("settlementCoverage"),
        "canTuneWithBackfill": bool(after_summary.get("canTuneWithBackfill")),
        "remainingSettledBetCountNeeded": int(after_summary.get("remainingSettledBetCountNeeded") or remaining_settled),
        "averageSettledPerKDay": average_settled_per_k_day,
        "estimatedAdditionalKDaysNeeded": estimated_additional_days_needed,
        "resultSourceBreakdown": dict(after_summary.get("resultSourceBreakdown") or {}),
        "tuningReadiness": tuning,
        "collectionSummary": collection_summary,
        "auditSummary": audit_result.get("summary") or {},
        "loaderSummary": loader_result.get("summary") or {},
        "comparisonSummary": (comparison_result.get("comparison") or {}).get("sources") or comparison_result.get("comparison") or {},
        "recommendedNextAction": recommended_next_action,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
    }
    summary["goalStatus"] = (
        "can_tune_with_backfill"
        if summary["canTuneWithBackfill"]
        else "needs_more_k_files"
    )

    output_path = REPORT_ROOT / f"{date_tag}_k_refresh_summary.json"
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "dateRange": date_tag,
                "daysWithKFile": summary["daysWithKFile"],
                "missingKDates": summary["missingKDates"],
                "backfillSettledBetCountBefore": summary["backfillSettledBetCountBefore"],
                "backfillSettledBetCountAfter": summary["backfillSettledBetCountAfter"],
                "remainingSettledBetCountNeeded": summary["remainingSettledBetCountNeeded"],
                "estimatedAdditionalKDaysNeeded": summary["estimatedAdditionalKDaysNeeded"],
                "canTuneWithBackfill": summary["canTuneWithBackfill"],
                "recommendedNextAction": summary["recommendedNextAction"],
                "files": {"json": str(output_path)},
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    return {"summary": summary, "files": {"json": str(output_path)}}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Refresh K result backtest coverage and readiness.")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--jcd", default="all")
    parser.add_argument("--input-dir", default=None)
    parser.add_argument("--stake", type=int, default=100)
    args = parser.parse_args()
    refresh_k_backtest(start_date=args.start_date, end_date=args.end_date, jcd=args.jcd, input_dir=args.input_dir, stake=args.stake)


if __name__ == "__main__":
    main()
