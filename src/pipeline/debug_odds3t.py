from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from src.ingest.official_fetcher import fetch_odds3t_html
from src.utils.race_id import canonical_race_id


def _parse_target_date(value: str) -> str:
    token = str(value or "").strip().lower()
    if token == "today":
        return date.today().strftime("%Y-%m-%d")
    digits = "".join(ch for ch in token if ch.isdigit())
    if len(digits) != 8:
        raise ValueError(f"invalid date: {value!r}")
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"


def debug_odds3t(*, target_date: str, jcd: str, rno: int) -> dict[str, object]:
    date_iso = _parse_target_date(target_date)
    jcd_norm = f"{int(jcd):02d}"
    race_no = int(rno)
    race_id = canonical_race_id(date_iso, jcd_norm, race_no)
    result = fetch_odds3t_html(target_date=date_iso, jcd=jcd_norm, race_no=race_no, race_id=race_id, timeout=15.0, retries=1, retry_sleep=0.5)
    raw_html_path = Path(result.get("rawHtmlPath") or "")
    html = result.get("html") or ""
    parsed = result.get("parsed") or {}
    sample_combos = result.get("sampleCombos") or []
    report = {
        "date": date_iso,
        "jcd": jcd_norm,
        "rno": race_no,
        "raceId": race_id,
        "url": result.get("url"),
        "httpStatus": result.get("fetchStatus"),
        "fetchedAt": result.get("fetchedAt"),
        "fallbackUsed": bool(result.get("fallbackUsed")),
        "htmlLength": len(html),
        "containsOddsKeyword": bool(result.get("containsOddsKeyword")),
        "parsedOddsCount": int(result.get("parsedOddsCount") or len(parsed)),
        "sampleCombos": sample_combos[:8],
        "errorType": result.get("errorType") or "",
        "errorMessage": result.get("errorMessage") or "",
        "rawHtmlPath": str(raw_html_path) if raw_html_path else str(result.get("rawHtmlPath") or ""),
        "missingReason": result.get("missingReason") or [],
        "tableCount": int(result.get("tableCount") or 0),
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose a single odds3t fetch/parse result.")
    parser.add_argument("--date", required=True, help="today, YYYYMMDD, or YYYY-MM-DD")
    parser.add_argument("--jcd", required=True, help="venue code")
    parser.add_argument("--rno", required=True, type=int, help="race number")
    args = parser.parse_args()
    print(json.dumps(debug_odds3t(target_date=args.date, jcd=args.jcd, rno=args.rno), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
