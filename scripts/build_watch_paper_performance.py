from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPORTS_PREDICTIONS_ROOT = ROOT / "reports" / "predictions"
REPORTS_DAILY_ROOT = ROOT / "reports" / "daily"
REPORTS_ANALYSIS_ROOT = ROOT / "reports" / "analysis"


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _save_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _read_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("candidates")
    if not isinstance(rows, list):
        rows = payload.get("rows")
    if not isinstance(rows, list):
        rows = payload.get("items")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _bucket(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        value = str(row.get(key) or "").strip() or "UNKNOWN"
        counts[value] += 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _day_summary(date_text: str) -> dict[str, Any]:
    sheet = _load_json(REPORTS_PREDICTIONS_ROOT / date_text / "prediction_sheet.json")
    review = _load_json(REPORTS_PREDICTIONS_ROOT / date_text / "prediction_review.json")
    daily = _load_json(REPORTS_DAILY_ROOT / date_text / "daily_summary.json")
    rows = _read_rows(sheet)

    summary = sheet.get("summary") if isinstance(sheet.get("summary"), dict) else {}
    review_summary = review.get("summary") if isinstance(review.get("summary"), dict) else {}
    groups = review.get("groups") if isinstance(review.get("groups"), dict) else {}
    watch_group = groups.get("WATCH") if isinstance(groups.get("WATCH"), dict) else {}
    paper_group = groups.get("PAPER") if isinstance(groups.get("PAPER"), dict) else {}

    watch_count = int(summary.get("watchCount") or 0)
    paper_count = int(summary.get("paperCount") or 0)
    watch_hits = int(watch_group.get("hitCount") or 0)
    paper_hits = int(paper_group.get("hitCount") or 0)
    watch_available = int(watch_group.get("resultAvailableCount") or 0)
    paper_available = int(paper_group.get("resultAvailableCount") or 0)
    watch_pnl = float(watch_group.get("pnlSum") or 0.0)
    paper_pnl = float(paper_group.get("pnlSum") or 0.0)

    watch_rows = [row for row in rows if str(row.get("paperDecision") or "").upper() == "WATCH"]
    paper_rows = [row for row in rows if str(row.get("paperDecision") or "").upper() == "PAPER"]

    def _rate(hit: int, available: int) -> float | None:
        if available <= 0:
            return None
        return round(hit / available, 4)

    def _roi(pnl: float, count: int) -> float | None:
        if count <= 0:
            return None
        return round(1.0 + (pnl / (count * 100.0)), 4)

    return {
        "date": date_text,
        "watchCount": watch_count,
        "paperCount": paper_count,
        "watchHitCount": watch_hits,
        "paperHitCount": paper_hits,
        "watchResultAvailableCount": watch_available,
        "paperResultAvailableCount": paper_available,
        "watchHitRate": _rate(watch_hits, watch_available),
        "paperHitRate": _rate(paper_hits, paper_available),
        "watchRoi": _roi(watch_pnl, watch_count),
        "paperRoi": _roi(paper_pnl, paper_count),
        "watchTopStopReason": str(summary.get("topStopReason") or ""),
        "resultsStatus": str(review_summary.get("resultsStatus") or daily.get("results_status") or daily.get("resultsStatus") or ""),
        "resultAvailable": bool(review_summary.get("resultsAvailable") or daily.get("results_available")),
        "stopReasonCounts": _bucket(watch_rows + paper_rows, "stopReason"),
        "oddsStatusCounts": _bucket(watch_rows + paper_rows, "oddsStatus"),
        "consensusGradeCounts": _bucket(watch_rows + paper_rows, "consensusGrade"),
        "venueCounts": _bucket(watch_rows + paper_rows, "venue"),
        "approxProbBands": {
            "lt_0_05": sum(1 for row in watch_rows + paper_rows if float(row.get("approxProb") or 0.0) < 0.05),
            "0_05_to_0_10": sum(1 for row in watch_rows + paper_rows if 0.05 <= float(row.get("approxProb") or 0.0) < 0.10),
            "gte_0_10": sum(1 for row in watch_rows + paper_rows if float(row.get("approxProb") or 0.0) >= 0.10),
        },
        "expectedValueBands": {
            "lt_1_0": sum(1 for row in watch_rows + paper_rows if float(row.get("expectedValue") or 0.0) < 1.0),
            "1_0_to_1_5": sum(1 for row in watch_rows + paper_rows if 1.0 <= float(row.get("expectedValue") or 0.0) < 1.5),
            "gte_1_5": sum(1 for row in watch_rows + paper_rows if float(row.get("expectedValue") or 0.0) >= 1.5),
        },
        "dailySummaryPath": str(REPORTS_DAILY_ROOT / date_text / "daily_summary.json"),
        "predictionSheetPath": str(REPORTS_PREDICTIONS_ROOT / date_text / "prediction_sheet.json"),
        "predictionReviewPath": str(REPORTS_PREDICTIONS_ROOT / date_text / "prediction_review.json"),
    }


def build_report() -> dict[str, Any]:
    days = sorted({p.name for p in REPORTS_PREDICTIONS_ROOT.iterdir() if p.is_dir() and (p / "prediction_sheet.json").exists() and (p / "prediction_review.json").exists()})
    rows = [_day_summary(day) for day in days]
    watch_days = len(rows)
    watch_count = sum(row["watchCount"] for row in rows)
    paper_count = sum(row["paperCount"] for row in rows)
    watch_hits = sum(row["watchHitCount"] for row in rows)
    paper_hits = sum(row["paperHitCount"] for row in rows)
    watch_available = sum(row["watchResultAvailableCount"] for row in rows)
    paper_available = sum(row["paperResultAvailableCount"] for row in rows)
    watch_pnl = sum(float(row["watchRoi"] - 1.0) * row["watchCount"] * 100.0 if row["watchRoi"] is not None else 0.0 for row in rows)
    paper_pnl = sum(float(row["paperRoi"] - 1.0) * row["paperCount"] * 100.0 if row["paperRoi"] is not None else 0.0 for row in rows)

    summary = {
        "days": watch_days,
        "watchDays": watch_days,
        "paperDays": watch_days,
        "watchCount": watch_count,
        "paperCount": paper_count,
        "watchHitCount": watch_hits,
        "paperHitCount": paper_hits,
        "watchResultAvailableCount": watch_available,
        "paperResultAvailableCount": paper_available,
        "watchHitRate": round(watch_hits / watch_available, 4) if watch_available > 0 else None,
        "paperHitRate": round(paper_hits / paper_available, 4) if paper_available > 0 else None,
        "watchRoi": round(1.0 + (watch_pnl / (watch_count * 100.0)), 4) if watch_count > 0 else None,
        "paperRoi": round(1.0 + (paper_pnl / (paper_count * 100.0)), 4) if paper_count > 0 else None,
        "resultsAvailableDays": sum(1 for row in rows if row["resultAvailable"]),
        "unconfirmedDays": sum(1 for row in rows if not row["resultAvailable"]),
        "stopReasonCounts": _bucket([r for row in rows for r in [{"stopReason": k, "count": v} for k, v in row["stopReasonCounts"].items()] for _ in range(r["count"])], "stopReason"),
        "oddsStatusCounts": _bucket([r for row in rows for r in [{"oddsStatus": k, "count": v} for k, v in row["oddsStatusCounts"].items()] for _ in range(r["count"])], "oddsStatus"),
        "consensusGradeCounts": _bucket([r for row in rows for r in [{"consensusGrade": k, "count": v} for k, v in row["consensusGradeCounts"].items()] for _ in range(r["count"])], "consensusGrade"),
        "venueCounts": _bucket([r for row in rows for r in [{"venue": k, "count": v} for k, v in row["venueCounts"].items()] for _ in range(r["count"])], "venue"),
        "approxProbBands": {
            "lt_0_05": sum(row["approxProbBands"]["lt_0_05"] for row in rows),
            "0_05_to_0_10": sum(row["approxProbBands"]["0_05_to_0_10"] for row in rows),
            "gte_0_10": sum(row["approxProbBands"]["gte_0_10"] for row in rows),
        },
        "expectedValueBands": {
            "lt_1_0": sum(row["expectedValueBands"]["lt_1_0"] for row in rows),
            "1_0_to_1_5": sum(row["expectedValueBands"]["1_0_to_1_5"] for row in rows),
            "gte_1_5": sum(row["expectedValueBands"]["gte_1_5"] for row in rows),
        },
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
    }
    return {"summary": summary, "rows": rows}


def _render_md(report: dict[str, Any]) -> str:
    s = report["summary"]
    lines = [
        "# WATCH/PAPER Performance",
        "",
        f"- days: {s.get('days', 0)}",
        f"- watchCount: {s.get('watchCount', 0)}",
        f"- paperCount: {s.get('paperCount', 0)}",
        f"- watchHitCount: {s.get('watchHitCount', 0)}",
        f"- paperHitCount: {s.get('paperHitCount', 0)}",
        f"- watchHitRate: {s.get('watchHitRate')}",
        f"- paperHitRate: {s.get('paperHitRate')}",
        f"- watchRoi: {s.get('watchRoi')}",
        f"- paperRoi: {s.get('paperRoi')}",
        f"- resultsAvailableDays: {s.get('resultsAvailableDays', 0)}",
        f"- unconfirmedDays: {s.get('unconfirmedDays', 0)}",
        "",
        "## StopReason",
    ]
    for key, value in (s.get("stopReasonCounts") or {}).items():
        lines.append(f"- {key}: {value}")
    lines.append("")
    lines.append("## ConsensusGrade")
    for key, value in (s.get("consensusGradeCounts") or {}).items():
        lines.append(f"- {key}: {value}")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build watch/paper performance report.")
    args = parser.parse_args()
    report = build_report()
    out_root = REPORTS_ANALYSIS_ROOT
    json_path = out_root / "watch_paper_performance.json"
    csv_path = out_root / "watch_paper_performance.csv"
    md_path = out_root / "watch_paper_performance.md"
    _save_json(json_path, report)
    _save_text(md_path, _render_md(report))

    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(report["summary"].keys()))
        writer.writeheader()
        writer.writerow(report["summary"])
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
