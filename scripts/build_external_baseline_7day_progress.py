from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPO_AUDIT_ROOT = ROOT / "reports" / "repo_audit"
REPORTS_EXTERNAL_COMPARE_ROOT = ROOT / "reports" / "external" / "baseline_compare"
REPORTS_PREDICTIONS_ROOT = ROOT / "reports" / "predictions"
REPORTS_CONSENSUS_ROOT = ROOT / "reports" / "consensus"


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


def _parse_date_text(value: str) -> datetime | None:
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(value, fmt)
        except Exception:
            continue
    return None


def _date_range(start_text: str, end_text: str) -> list[str]:
    start = _parse_date_text(start_text)
    end = _parse_date_text(end_text)
    if start is None or end is None or end < start:
        return []
    dates: list[str] = []
    current = start
    while current <= end:
        dates.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return dates


def _existing_dates(root: Path, filename: str) -> set[str]:
    if not root.exists():
        return set()
    dates: set[str] = set()
    for child in root.iterdir():
        if child.is_dir() and (child / filename).exists():
            digits = "".join(ch for ch in child.name if ch.isdigit())
            if len(digits) >= 8:
                dates.add(digits[:8])
    return dates


def _consensus_counts(dates: set[str]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for date_text in sorted(dates):
        path = REPORTS_CONSENSUS_ROOT / f"{date_text[:4]}-{date_text[4:6]}-{date_text[6:8]}" / "consensus_sheet.json"
        payload = _load_json(path)
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        grade_counts = summary.get("gradeCounts") if isinstance(summary.get("gradeCounts"), dict) else {}
        for grade, value in grade_counts.items():
            counts[str(grade)] += int(value or 0)
    return dict(sorted(counts.items(), key=lambda item: item[0]))


def _merge_source_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        for source in row.get("sources") or []:
            if not isinstance(source, dict):
                continue
            source_name = str(source.get("source") or source.get("name") or "").strip()
            if not source_name:
                continue
            counts[source_name] += 1
            metrics = totals[source_name]
            metrics["raceCount"] += float(source.get("raceCount") or 0)
            metrics["predictionRaceCount"] += float(source.get("predictionRaceCount") or 0)
            metrics["predictionCount"] += float(source.get("predictionCount") or 0)
            metrics["settledPredictionRaceCount"] += float(source.get("settledPredictionRaceCount") or 0)
            metrics["hitCount"] += float(source.get("hitCount") or 0)
            metrics["top1HitCount"] += float(source.get("top1HitCount") or 0)
            metrics["top3HitCount"] += float(source.get("top3HitCount") or 0)
            metrics["top5HitCount"] += float(source.get("top5HitCount") or 0)
            metrics["returnYen"] += float(source.get("returnYen") or 0)
            metrics["roiSum"] += float(source.get("roi") or 0)
            metrics["hitRateSum"] += float(source.get("hitRate") or 0)
            metrics["top1HitRateSum"] += float(source.get("top1HitRate") or 0)
            metrics["top3HitRateSum"] += float(source.get("top3HitRate") or 0)
            metrics["top5HitRateSum"] += float(source.get("top5HitRate") or 0)

    result: dict[str, Any] = {}
    for source_name, metrics in totals.items():
        n = max(counts[source_name], 1)
        prediction_count = int(metrics["predictionCount"])
        race_count = int(metrics["raceCount"])
        result[source_name] = {
            "comparisonReports": counts[source_name],
            "raceCount": race_count,
            "predictionRaceCount": int(metrics["predictionRaceCount"]),
            "predictionCount": prediction_count,
            "settledPredictionRaceCount": int(metrics["settledPredictionRaceCount"]),
            "hitCount": int(metrics["hitCount"]),
            "top1HitCount": int(metrics["top1HitCount"]),
            "top3HitCount": int(metrics["top3HitCount"]),
            "top5HitCount": int(metrics["top5HitCount"]),
            "returnYen": int(metrics["returnYen"]),
            "hitRate": round((metrics["hitCount"] / metrics["predictionRaceCount"]), 4) if metrics["predictionRaceCount"] else None,
            "top1HitRate": round((metrics["top1HitCount"] / metrics["predictionRaceCount"]), 4) if metrics["predictionRaceCount"] else None,
            "top3HitRate": round((metrics["top3HitCount"] / metrics["predictionRaceCount"]), 4) if metrics["predictionRaceCount"] else None,
            "top5HitRate": round((metrics["top5HitCount"] / metrics["predictionRaceCount"]), 4) if metrics["predictionRaceCount"] else None,
            "roi": round((metrics["returnYen"] / (metrics["predictionCount"] * 100.0)), 4) if prediction_count else None,
        }
    return result


def build_report() -> dict[str, Any]:
    compare_files = sorted(REPORTS_EXTERNAL_COMPARE_ROOT.glob("*_external_baselines.json"))
    compare_rows = [_load_json(path) for path in compare_files]
    compared_dates: set[str] = set()
    for row in compare_rows:
        if not isinstance(row, dict):
            continue
        for day in _date_range(str(row.get("startDate") or ""), str(row.get("endDate") or "")):
            compared_dates.add(day)

    prediction_days = _existing_dates(REPORTS_PREDICTIONS_ROOT, "prediction_sheet.json")
    consensus_days = _existing_dates(REPORTS_CONSENSUS_ROOT, "consensus_sheet.json")
    consensus_possible_days = sorted(compared_dates.intersection(prediction_days))
    consensus_days_with_compare = len(compared_dates.intersection(consensus_days))
    source_metrics = _merge_source_metrics([row for row in compare_rows if isinstance(row, dict)])
    consensus_grade_counts = _consensus_counts(set(consensus_possible_days))

    summary = {
        "comparisonReportCount": len(compare_files),
        "comparisonDays": len(compared_dates),
        "comparisonDateList": sorted(compared_dates),
        "predictionSheetDays": len(prediction_days),
        "consensusSheetDays": len(consensus_days),
        "consensusPossibleDays": len(consensus_possible_days),
        "consensusComparedDays": consensus_days_with_compare,
        "consensusGradeCounts": consensus_grade_counts,
        "sourceMetrics": source_metrics,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
    }
    return {"summary": summary, "rows": compare_rows}


def _render_md(report: dict[str, Any]) -> str:
    s = report["summary"]
    lines = [
        "# External Baseline 7 Day Progress",
        "",
        f"- comparisonReportCount: {s.get('comparisonReportCount', 0)}",
        f"- comparisonDays: {s.get('comparisonDays', 0)}",
        f"- predictionSheetDays: {s.get('predictionSheetDays', 0)}",
        f"- consensusSheetDays: {s.get('consensusSheetDays', 0)}",
        f"- consensusPossibleDays: {s.get('consensusPossibleDays', 0)}",
        f"- consensusComparedDays: {s.get('consensusComparedDays', 0)}",
        "",
        "## ConsensusGradeCounts",
    ]
    for grade, value in (s.get("consensusGradeCounts") or {}).items():
        lines.append(f"- {grade}: {value}")
    lines.append("")
    lines.append("## Sources")
    for source, metrics in (s.get("sourceMetrics") or {}).items():
        lines.append(f"- {source}: roi={metrics.get('roi')} top1={metrics.get('top1HitRate')} top3={metrics.get('top3HitRate')} top5={metrics.get('top5HitRate')}")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build external baseline 7 day progress.")
    parser.parse_args()
    report = build_report()
    out_root = REPO_AUDIT_ROOT
    json_path = out_root / "external_baseline_7day_progress.json"
    md_path = out_root / "external_baseline_7day_progress.md"
    _save_json(json_path, report)
    _save_text(md_path, _render_md(report))

    analysis_root = ROOT / "reports" / "external" / "baseline_compare"
    analysis_root.mkdir(parents=True, exist_ok=True)
    with (analysis_root / "external_baseline_7day_progress.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(report["summary"].keys()))
        writer.writeheader()
        writer.writerow(report["summary"])
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
