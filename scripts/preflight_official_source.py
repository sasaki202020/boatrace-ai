from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT
REPORTS_ROOT = REPO_ROOT / "reports" / "daily"
INDEX_URL = "https://www.boatrace.jp/owpc/pc/race/index?hd={date8}"
DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; boatrace-ai-mvp/1.0; +https://www.boatrace.jp/)"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ingest.parsers.index_parser import parse_index_html
from src.pipeline.pipeline_utils import parse_date
from src.utils.date_paths import normalize_date_str


def _normalize_date(value: str) -> str:
    return normalize_date_str(value)


def _fetch_index_html(date8: str, *, timeout: float = 10.0) -> tuple[str, int | None, str]:
    session = requests.Session()
    session.headers.update({"User-Agent": DEFAULT_USER_AGENT})
    url = INDEX_URL.format(date8=date8.replace("-", ""))
    try:
        response = session.get(url, timeout=timeout)
        response.raise_for_status()
        return response.text or "", int(response.status_code), url
    except Exception:
        try:
            response = session.get(url, timeout=timeout)
            return response.text or "", int(response.status_code), url
        except Exception:
            return "", None, url


def _classify_source(target_date: str, html: str, fetch_status: int | None, parsed: dict[str, Any]) -> str:
    venues = parsed.get("venues") or []
    if not isinstance(venues, list):
        venues = []
    venue_count = len([v for v in venues if isinstance(v, dict)])
    if date.fromisoformat(target_date) > date.today():
        return "future_date_not_ready"
    if fetch_status is None:
        return "official_index_unavailable"
    if not html.strip():
        return "official_index_empty"
    if venue_count > 0:
        return "ready"
    missing_reason = parsed.get("missingReason") or []
    if not isinstance(missing_reason, list):
        missing_reason = [str(missing_reason)]
    html_lower = html.lower()
    if any(marker in html_lower for marker in ["開催はありません", "レースはありません", "no races scheduled", "no race"]):
        return "no_races_scheduled"
    if "index_parse_failed" in {str(item) for item in missing_reason}:
        return "official_index_parse_failed"
    return "unknown"


def run_preflight(*, target_date: str) -> dict[str, Any]:
    normalized = _normalize_date(target_date)
    date8 = normalized.replace("-", "")
    html, http_status, source_url = _fetch_index_html(date8)
    parsed = parse_index_html(html, date8)
    venue_count = len([v for v in (parsed.get("venues") or []) if isinstance(v, dict)])
    classification = _classify_source(normalized, html, http_status, parsed)
    today_venues = {
        "date": date8,
        "venues": parsed.get("venues") or [],
        "sourceUrl": source_url,
        "fetchedAt": datetime.now().isoformat(timespec="seconds"),
        "dataStatus": parsed.get("dataStatus") or ("available" if venue_count > 0 else "missing"),
        "missingReason": parsed.get("missingReason") or ([] if venue_count > 0 else ["index_parse_failed"]),
        "fetchStatus": "ok" if http_status is not None else "unavailable",
    }

    raw_dir = REPO_ROOT / "data" / "raw" / "official" / date8
    normalized_dir = REPO_ROOT / "data" / "normalized" / date8
    raw_dir.mkdir(parents=True, exist_ok=True)
    normalized_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "index.html").write_text(html or "", encoding="utf-8")
    (normalized_dir / "today_venues.json").write_text(
        json.dumps(today_venues, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    report_dir = REPORTS_ROOT / normalized
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "preflight_source_check.json"
    md_path = report_dir / "preflight_source_check.md"

    payload = {
        "date": normalized,
        "officialIndexUrl": source_url,
        "httpStatus": http_status,
        "htmlBodyLength": len(html or ""),
        "officialVenueLinkCount": venue_count,
        "todayVenues": today_venues,
        "todayVenuesDataStatus": today_venues["dataStatus"],
        "sourceClassification": classification,
        "sourceReady": classification == "ready",
        "classificationReason": "official index venue/link exists" if classification == "ready" else "official source not ready",
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_lines = [
        f"# Preflight Official Source ({normalized})",
        "",
        f"- officialIndexUrl: {source_url}",
        f"- httpStatus: {http_status if http_status is not None else 'none'}",
        f"- htmlBodyLength: {len(html or '')}",
        f"- officialVenueLinkCount: {venue_count}",
        f"- todayVenuesDataStatus: {today_venues['dataStatus']}",
        f"- sourceClassification: {classification}",
        f"- sourceReady: {classification == 'ready'}",
        "",
        "## MissingReason",
    ]
    missing_reason = today_venues.get("missingReason") or []
    if missing_reason:
        md_lines.extend(f"- {item}" for item in missing_reason)
    else:
        md_lines.append("- none")
    md_lines.extend(
        [
            "",
            "## TodayVenues",
            f"- venueCount: {venue_count}",
            f"- dataStatus: {today_venues['dataStatus']}",
        ]
    )
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    payload["jsonPath"] = str(json_path)
    payload["mdPath"] = str(md_path)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Preflight BOATRACE official source readiness.")
    parser.add_argument("--date", required=True, help="YYYY-MM-DD, YYYYMMDD, or today")
    args = parser.parse_args()
    result = run_preflight(target_date=args.date)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
