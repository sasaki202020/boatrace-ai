from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from src.evaluation.audit_historical_inputs import audit_historical_inputs
from src.evaluation.backtest_range import run_backtest_range
from src.evaluation.compare_prediction_sources import compare_prediction_sources
from src.pipeline.backfill_predictions import backfill_predictions
from src.pipeline.collect_historical_inputs import collect_historical_inputs


ROOT = Path(__file__).resolve().parents[2]
REPORTS_ROOT = ROOT / "reports" / "backtest"


def prepare_backtest_dataset(*, start_date: str, end_date: str, jcd: str = "all") -> dict[str, Any]:
    audit = audit_historical_inputs(start_date=start_date, end_date=end_date, jcd=jcd)
    collect = collect_historical_inputs(start_date=start_date, end_date=end_date, jcd=jcd, stages="racelist,odds,result")
    backfill = backfill_predictions(start_date=start_date, end_date=end_date, jcd=jcd, stage="odds")
    backtest = run_backtest_range(start_date=start_date, end_date=end_date, jcd=jcd, stake_per_buy=100, prediction_source="backfill")
    compare = compare_prediction_sources(start_date=start_date, end_date=end_date, jcd=jcd, stake_per_buy=100)
    tuning = backtest.get("files", {}).get("tuning")
    summary = {
        "dateRange": f"{str(start_date).replace('-', '')}_{str(end_date).replace('-', '')}",
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "audit": audit.get("summary") or audit,
        "collect": collect.get("summary") or collect,
        "backfill": backfill.get("summary") or backfill,
        "backtest": backtest.get("summary") or backtest,
        "compare": compare.get("summary") if isinstance(compare, dict) else compare,
        "tuningPath": tuning,
    }
    REPORTS_ROOT.mkdir(parents=True, exist_ok=True)
    out_path = REPORTS_ROOT / f"{str(start_date).replace('-', '')}_{str(end_date).replace('-', '')}_prepare_summary.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"summary": summary, "files": {"summary": str(out_path)}}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Prepare historical backtest dataset and run shadow evaluations.")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--jcd", default="all")
    args = parser.parse_args()
    result = prepare_backtest_dataset(start_date=args.start_date, end_date=args.end_date, jcd=args.jcd)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
