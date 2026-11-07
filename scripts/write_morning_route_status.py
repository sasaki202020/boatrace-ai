from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


REPORTS_DAILY_ROOT = ROOT / "reports" / "daily"
REPORTS_MONITORING_ROOT = ROOT / "reports" / "monitoring"


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _date_dir(date_text: str) -> Path:
    return REPORTS_DAILY_ROOT / date_text


def build_morning_route_status(date_text: str) -> dict:
    source_date = date_text
    daily_dir = _date_dir(source_date)
    preflight = _load_json(daily_dir / "preflight_source_check.json")
    summary = _load_json(daily_dir / "daily_summary.json")
    sheet = _load_json(ROOT / "reports" / "predictions" / source_date / "prediction_sheet.json")
    review = _load_json(ROOT / "reports" / "predictions" / source_date / "prediction_review.json")
    morning_path = REPORTS_MONITORING_ROOT / "morning_route_status.json"
    morning_path.parent.mkdir(parents=True, exist_ok=True)

    source_classification = str(preflight.get("sourceClassification") or "unknown")
    route_executed = bool(sheet) and source_classification == "ready"
    skip_reason = ""
    if source_classification in {"future_date_not_ready", "source_not_ready"}:
        skip_reason = "source_not_ready"
    elif source_classification not in {"ready", "fallback_latest_complete_ops", "fallback_latest_available"}:
        skip_reason = source_classification or "unknown"

    payload = {
        "date": source_date,
        "requestedDate": date_text,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "sourceClassification": source_classification,
        "sourceReady": source_classification == "ready",
        "skipReason": skip_reason,
        "routeExecuted": route_executed,
        "preflight": {
            "httpStatus": preflight.get("httpStatus"),
            "htmlBodyLength": preflight.get("htmlBodyLength"),
            "officialVenueLinkCount": preflight.get("officialVenueLinkCount"),
            "todayVenuesDataStatus": preflight.get("todayVenuesDataStatus"),
        },
        "artifacts": {
            "preRace": (daily_dir / "pre_race_run.json").exists(),
            "oddsRefresh": (daily_dir / "odds_refresh_run.json").exists(),
            "predictionSheet": bool(sheet),
            "predictionReview": bool(review),
            "dailySummary": (daily_dir / "daily_summary.json").exists(),
            "dailyReport": (daily_dir / "daily_report.json").exists(),
        },
        "summary": {
            "latestCompleteOpsDate": str(summary.get("latest_complete_ops_date") or ""),
            "resultsStatus": str(summary.get("results_status") or summary.get("resultsStatus") or "missing"),
            "dailySummaryStatus": "ok" if summary.get("results_status") == "ok" else ("missing" if not summary else str(summary.get("results_status") or "missing")),
        },
    }
    md_path = REPORTS_MONITORING_ROOT / "morning_route_status.md"
    md_path.write_text(
        "\n".join(
            [
                f"# Morning Route Status {source_date}",
                "",
                f"- sourceClassification: {payload['sourceClassification']}",
                f"- sourceReady: {payload['sourceReady']}",
                f"- skipReason: {payload['skipReason'] or '-'}",
                f"- routeExecuted: {payload['routeExecuted']}",
                f"- latestCompleteOpsDate: {payload['summary']['latestCompleteOpsDate'] or '-'}",
                f"- resultsStatus: {payload['summary']['resultsStatus']}",
                f"- predictionSheet: {payload['artifacts']['predictionSheet']}",
                f"- predictionReview: {payload['artifacts']['predictionReview']}",
                f"- dailySummary: {payload['artifacts']['dailySummary']}",
                f"- dailyReport: {payload['artifacts']['dailyReport']}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    morning_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Write morning route status")
    parser.add_argument("--date", required=True, help="Target date YYYY-MM-DD")
    args = parser.parse_args()
    payload = build_morning_route_status(args.date)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
