from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from src.evaluation.audit_k_result_coverage import audit_k_result_coverage
from src.evaluation.backtest_range import run_backtest_range
from src.evaluation.compare_prediction_sources import compare_prediction_sources
from src.evaluation.export_missing_k_checklist import export_missing_k_checklist
from src.evaluation.audit_historical_inputs import audit_historical_inputs
from src.pipeline.check_k_inbox import check_k_inbox
from src.ingest.official_k_loader import collect_official_k_results_range
from src.pipeline.collect_historical_inputs import collect_historical_inputs
from src.pipeline.import_k_results import import_k_results
from src.pipeline.refresh_k_backtest import refresh_k_backtest


ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = ROOT / "reports" / "backtest"


def import_and_refresh_k_results(
    *,
    input_dir: str,
    start_date: str,
    end_date: str,
    jcd: str = "all",
    stake: int = 100,
    target_dir: str | None = None,
) -> dict[str, Any]:
    target_dir = target_dir or str(ROOT / "data" / "raw" / "official" / "results")
    date_tag = f"{str(start_date).replace('-', '')}_{str(end_date).replace('-', '')}"
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)

    inbox_check = check_k_inbox(input_dir=input_dir, start_date=start_date, end_date=end_date, target_dir=target_dir)
    coverage_before = audit_k_result_coverage(start_date=start_date, end_date=end_date, input_dir=target_dir)
    backfill_before = run_backtest_range(start_date=start_date, end_date=end_date, jcd=jcd, stake_per_buy=stake, prediction_source="backfill")

    if int((inbox_check.get("summary") or {}).get("importTargetCount") or 0) > 0:
        import_result = import_k_results(input_dir=input_dir, target_dir=target_dir)
    else:
        import_result = {
            "summary": {
                "importedFileCount": 0,
                "skippedFileCount": 0,
                "replacedFileCount": 0,
                "invalidNameFileCount": 0,
                "parseErrorFileCount": 0,
                "totalFiles": 0,
                "generatedAt": datetime.now().isoformat(timespec="seconds"),
            },
            "rows": [],
            "files": {"json": "", "csv": ""},
        }
    coverage_after_import = audit_k_result_coverage(start_date=start_date, end_date=end_date, input_dir=target_dir)

    loader_result = collect_official_k_results_range(start_date=start_date, end_date=end_date, input_dir=target_dir, jcd=jcd)
    collect_result = collect_historical_inputs(start_date=start_date, end_date=end_date, jcd=jcd, stages="result_txt", input_dir=target_dir)
    backfill_after = run_backtest_range(start_date=start_date, end_date=end_date, jcd=jcd, stake_per_buy=stake, prediction_source="backfill")
    refresh_result = refresh_k_backtest(start_date=start_date, end_date=end_date, jcd=jcd, input_dir=target_dir, stake=stake)
    checklist_result = export_missing_k_checklist(start_date=start_date, end_date=end_date, input_dir=target_dir)
    audit_result = audit_historical_inputs(start_date=start_date, end_date=end_date, jcd=jcd)
    compare_result = compare_prediction_sources(start_date=start_date, end_date=end_date, jcd=jcd, stake_per_buy=stake)

    before_summary = coverage_before.get("summary") or {}
    after_summary = coverage_after_import.get("summary") or {}
    backfill_before_summary = backfill_before.get("summary") or {}
    backfill_after_summary = backfill_after.get("summary") or {}
    refresh_summary = refresh_result.get("summary") or {}
    import_summary = import_result.get("summary") or {}
    checklist_summary = checklist_result.get("summary") or {}

    missing_after = list((coverage_after_import.get("summary") or {}).get("missingDates") or [])
    if int((inbox_check.get("summary") or {}).get("importTargetCount") or 0) == 0:
        recommended_next_action = "place_missing_k_files_in_inbox"
    elif missing_after:
        recommended_next_action = "collect_missing_k_files_and_rerun"
    else:
        recommended_next_action = "review_import_manifest"

    summary = {
        "dateRange": date_tag,
        "importedFileCount": int(import_summary.get("importedFileCount") or 0),
        "skippedFileCount": int(import_summary.get("skippedFileCount") or 0),
        "parseErrorFileCount": int(import_summary.get("parseErrorFileCount") or 0),
        "daysWithKFileBefore": int(before_summary.get("daysWithKFile") or 0),
        "daysWithKFileAfter": int(after_summary.get("daysWithKFile") or 0),
        "daysMissingKFileBefore": int(before_summary.get("daysMissingKFile") or 0),
        "daysMissingKFileAfter": int(after_summary.get("daysMissingKFile") or 0),
        "backfillSettledBetCountBefore": int(backfill_before_summary.get("backfillSettledBetCount") or 0),
        "backfillSettledBetCountAfter": int(backfill_after_summary.get("backfillSettledBetCount") or 0),
        "remainingSettledBetCountNeeded": int(refresh_summary.get("remainingSettledBetCountNeeded") or backfill_after_summary.get("remainingSettledBetCountNeeded") or 0),
        "estimatedAdditionalKDaysNeeded": int(refresh_summary.get("estimatedAdditionalKDaysNeeded") or 0) if refresh_summary.get("estimatedAdditionalKDaysNeeded") is not None else None,
        "canTuneWithBackfill": bool(refresh_summary.get("canTuneWithBackfill")),
        "missingKDates": list((coverage_after_import.get("summary") or {}).get("missingDates") or []),
        "resultTxtOkCount": int((coverage_after_import.get("summary") or {}).get("resultTxtOkCount") or 0),
        "resultTxtMissingCount": int((coverage_after_import.get("summary") or {}).get("resultTxtMissingCount") or 0),
        "settlementCoverage": backfill_after_summary.get("settlementCoverage"),
        "resultSourceBreakdown": dict(backfill_after_summary.get("resultSourceBreakdown") or {}),
        "collectionSummary": collect_result.get("summary") or {},
        "loaderSummary": loader_result.get("summary") or {},
        "auditSummary": audit_result.get("summary") or {},
        "compareSummary": compare_result.get("comparison") or {},
        "checklistSummary": checklist_summary,
        "inboxCheck": inbox_check.get("summary") or {},
        "recommendedNextAction": recommended_next_action,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
    }

    output_path = REPORT_ROOT / f"{date_tag}_import_refresh_summary.json"
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "summary": summary,
        "files": {
            "json": str(output_path),
            "importManifest": import_result.get("files", {}).get("json", ""),
            "inboxCheckJson": inbox_check.get("files", {}).get("json", ""),
            "inboxCheckCsv": inbox_check.get("files", {}).get("csv", ""),
            "checklistMd": checklist_result.get("files", {}).get("md", ""),
            "checklistCsv": checklist_result.get("files", {}).get("csv", ""),
        },
    }


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Import K results and refresh backtest readiness.")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--jcd", default="all")
    parser.add_argument("--stake", type=int, default=100)
    parser.add_argument("--target-dir", default=None)
    args = parser.parse_args()
    result = import_and_refresh_k_results(
        input_dir=args.input_dir,
        start_date=args.start_date,
        end_date=args.end_date,
        jcd=args.jcd,
        stake=args.stake,
        target_dir=args.target_dir,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
