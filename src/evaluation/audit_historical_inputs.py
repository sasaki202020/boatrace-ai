from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from src.pipeline.backfill_predictions import audit_backfill_inputs


ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = ROOT / "reports" / "backtest"


def audit_historical_inputs(*, start_date: str, end_date: str, jcd: str = "all") -> dict:
    return audit_backfill_inputs(start_date=start_date, end_date=end_date, jcd=jcd, stage="odds")


def _write_csv(path: Path, rows: list[dict]) -> None:
    fieldnames = [
        "date",
        "jcd",
        "venue",
        "hasRawRacelist",
        "hasNormalizedRace",
        "hasUiJson",
        "hasOdds",
        "hasBeforeinfo",
        "hasResult",
        "hasResultTxt",
        "hasParsedResultTxt",
        "resultSource",
        "hasFrozenBets",
        "hasBackfilledBets",
        "canBackfillOddsStage",
        "canSettle",
        "canSettleFromTxt",
        "missingReason",
        "uiPath",
        "frozenPath",
        "backfilledPath",
        "rawRacelistPath",
        "normalizedPath",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Audit historical input availability for backfill/backtest.")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--jcd", default="all")
    args = parser.parse_args()
    result = audit_historical_inputs(start_date=args.start_date, end_date=args.end_date, jcd=args.jcd)
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    date_tag = f"{str(args.start_date).replace('-', '')}_{str(args.end_date).replace('-', '')}"
    csv_path = REPORT_ROOT / f"{date_tag}_historical_input_audit.csv"
    json_path = REPORT_ROOT / f"{date_tag}_historical_input_audit.json"
    _write_csv(csv_path, result.get("rows") or [])
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({**result, "files": {"csv": str(csv_path), "json": str(json_path)}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
