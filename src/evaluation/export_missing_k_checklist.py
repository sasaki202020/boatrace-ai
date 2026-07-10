from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from src.evaluation.audit_k_result_coverage import audit_k_result_coverage


ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = ROOT / "reports" / "backtest"


def _normalize_date(value: str) -> str:
    digits = "".join(ch for ch in str(value).strip() if ch.isdigit())
    if len(digits) != 8:
        raise ValueError(f"invalid date: {value!r}")
    return digits


def _load_reference_average(date_tag: str) -> float | None:
    candidate = REPORT_ROOT / f"{date_tag}_k_refresh_summary.json"
    if candidate.exists():
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
            value = payload.get("averageSettledPerKDay")
            if value is not None:
                return float(value)
        except Exception:
            pass
    candidate = REPORT_ROOT / f"{date_tag}_backfill_summary.json"
    if candidate.exists():
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
            days_with_k = int(payload.get("kResultDays") or 0)
            settled = int(payload.get("backfillSettledBetCount") or 0)
            if days_with_k > 0:
                return settled / days_with_k
        except Exception:
            pass
    return None


def _load_refresh_meta(date_tag: str) -> dict[str, Any]:
    candidate = REPORT_ROOT / f"{date_tag}_k_refresh_summary.json"
    if candidate.exists():
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
            return {
                "remainingSettledBetCountNeeded": int(payload.get("remainingSettledBetCountNeeded") or 0),
                "estimatedAdditionalKDaysNeeded": payload.get("estimatedAdditionalKDaysNeeded"),
                "backfillSettledBetCountAfter": int(payload.get("backfillSettledBetCountAfter") or 0),
                "canTuneWithBackfill": bool(payload.get("canTuneWithBackfill")),
            }
        except Exception:
            pass
    candidate = REPORT_ROOT / f"{date_tag}_import_refresh_summary.json"
    if candidate.exists():
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
            return {
                "remainingSettledBetCountNeeded": int(payload.get("remainingSettledBetCountNeeded") or 0),
                "estimatedAdditionalKDaysNeeded": payload.get("estimatedAdditionalKDaysNeeded"),
                "backfillSettledBetCountAfter": int(payload.get("backfillSettledBetCountAfter") or 0),
                "canTuneWithBackfill": bool(payload.get("canTuneWithBackfill")),
            }
        except Exception:
            pass
    return {}


def export_missing_k_checklist(*, start_date: str, end_date: str, input_dir: str | None = None) -> dict[str, Any]:
    start8 = _normalize_date(start_date)
    end8 = _normalize_date(end_date)
    date_tag = f"{start8}_{end8}"
    coverage = audit_k_result_coverage(start_date=start8, end_date=end8, input_dir=input_dir)
    coverage_rows = coverage.get("rows") or []
    coverage_by_date = {str(row.get("date")): row for row in coverage_rows}
    missing_dates = list((coverage.get("summary") or {}).get("missingDates") or [])
    avg_gain = _load_reference_average(date_tag) or 0.0
    refresh_meta = _load_refresh_meta(date_tag)
    remaining_needed = int(refresh_meta.get("remainingSettledBetCountNeeded") or 0)
    estimated_days_needed = refresh_meta.get("estimatedAdditionalKDaysNeeded")

    rows: list[dict[str, Any]] = []
    for date8 in missing_dates:
        current = datetime.strptime(date8, "%Y%m%d").date()
        prev_day = (current - timedelta(days=1)).strftime("%Y%m%d")
        next_day = (current + timedelta(days=1)).strftime("%Y%m%d")
        prev_has = bool((coverage_by_date.get(prev_day) or {}).get("hasKFile"))
        next_has = bool((coverage_by_date.get(next_day) or {}).get("hasKFile"))
        if prev_has and next_has:
            priority = "high"
            note = "missing between K-covered days"
        elif prev_has or next_has:
            priority = "medium"
            note = "adjacent to a K-covered day"
        else:
            priority = "low"
            note = "no nearby K coverage in current window"
        rows.append(
            {
                "date": date8,
                "expectedFileName": f"K{date8[2:]}.TXT",
                "exists": False,
                "priority": priority,
                "estimatedSettledGain": round(avg_gain, 2),
                "note": note,
                "placement": "data/inbox/k_results/",
                "afterFetchCommand": "py -m src.pipeline.import_and_refresh_k_results --input-dir data/inbox/k_results --start-date {start} --end-date {end} --jcd all --stake 100".format(start=start8, end=end8),
            }
        )

    rows.sort(key=lambda row: str(row.get("date")))
    summary = {
        "dateRange": date_tag,
        "totalMissing": len(rows),
        "averageSettledPerKDay": round(avg_gain, 4) if avg_gain else 0.0,
        "remainingSettledBetCountNeeded": remaining_needed,
        "estimatedAdditionalKDaysNeeded": estimated_days_needed,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
    }

    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    md_path = REPORT_ROOT / f"{date_tag}_missing_k_checklist.md"
    csv_path = REPORT_ROOT / f"{date_tag}_missing_k_checklist.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["date", "expectedFileName", "exists", "priority", "estimatedSettledGain", "note", "placement", "afterFetchCommand"],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    lines = [
        f"# Missing K Checklist ({date_tag})",
        "",
        f"- totalMissing: {summary['totalMissing']}",
        f"- 300件まであと何件か: {summary['remainingSettledBetCountNeeded']}",
        f"- 推定追加必要日数: {summary['estimatedAdditionalKDaysNeeded']}",
        f"- averageSettledPerKDay: {summary['averageSettledPerKDay']}",
        "- 必要ファイル名: `KYYMMDD.TXT`",
        f"- 対象日付: {start8} 〜 {end8}",
        "- 配置先: `data/inbox/k_results/`",
        "- 取得後に実行するコマンド: `scripts/check_k_inbox.bat` -> `scripts/import_k_results.bat` -> `scripts/import_and_refresh_k_results.bat`",
        "",
        "## 手順",
        "",
        "1. 不足Kファイルを取得する",
        "2. `data/inbox/k_results/` に置く",
        "3. `scripts/import_k_results.bat` を実行する",
        "4. `reports/backtest/k_result_import_manifest.json` を確認する",
        "5. `reports/backtest/20260401_20260425_k_refresh_summary.json` を確認する",
        "6. `backfillSettledBetCount` が増えたか確認する",
        "7. `canTuneWithBackfill` が true になるまで BUY閾値は変更しない",
        "",
        "| date | expectedFileName | exists | priority | estimatedSettledGain | placement | note | afterFetchCommand |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['date']} | {row['expectedFileName']} | {row['exists']} | {row['priority']} | {row['estimatedSettledGain']} | {row['placement']} | {row['note']} | {row['afterFetchCommand']} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    payload = {"summary": summary, "rows": rows, "files": {"md": str(md_path), "csv": str(csv_path)}}
    return payload


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Export a checklist of missing K result files.")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--input-dir", default=None)
    args = parser.parse_args()
    result = export_missing_k_checklist(start_date=args.start_date, end_date=args.end_date, input_dir=args.input_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
