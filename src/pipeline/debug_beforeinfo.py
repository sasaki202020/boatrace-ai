from __future__ import annotations

import argparse
import json
from datetime import date
from typing import Any

from src.ingest.official_fetcher import fetch_beforeinfo_html


def _normalize_date(value: str) -> str:
    token = str(value).strip().lower()
    if token == "today":
        return date.today().strftime("%Y%m%d")
    digits = "".join(ch for ch in token if ch.isdigit())
    if len(digits) != 8:
        raise ValueError(f"invalid date: {value!r}")
    return digits


def _contains_keyword(html: str, keyword: str) -> bool:
    return keyword in (html or "")


def debug_beforeinfo(*, target_date: str, jcd: str, race_no: int) -> dict[str, Any]:
    date8 = _normalize_date(target_date)
    result = fetch_beforeinfo_html(target_date=date8, jcd=jcd, race_no=race_no)
    html = result.get("html") or ""
    parsed = result.get("parsed") or {}
    weather = parsed.get("weather")
    start_exhibition = parsed.get("startExhibition") or []
    error_type = result.get("errorType") or ""
    error_message = result.get("errorMessage") or ""
    summary = {
        "date": date8,
        "jcd": str(jcd).zfill(2),
        "rno": int(race_no),
        "url": result.get("url", ""),
        "httpStatus": result.get("fetchStatus", "unavailable"),
        "fetchedAt": result.get("fetchedAt", ""),
        "fallbackUsed": bool(result.get("fallbackUsed", False)),
        "htmlLength": len(html),
        "containsBeforeInfoKeyword": _contains_keyword(html, "beforeinfo") or _contains_keyword(html, "スタート展示"),
        "parsedWeather": weather,
        "parsedStartExhibitionCount": len(start_exhibition),
        "parseWarnings": result.get("parseWarnings") or parsed.get("parseWarnings") or [],
        "errorType": error_type,
        "errorMessage": error_message,
        "rawHtmlPath": result.get("rawHtmlPath", ""),
    }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Debug a single beforeinfo fetch/parse.")
    parser.add_argument("--date", required=True, help="today, YYYYMMDD, or YYYY-MM-DD")
    parser.add_argument("--jcd", required=True, help="venue code")
    parser.add_argument("--rno", required=True, type=int, help="race number")
    args = parser.parse_args()
    payload = debug_beforeinfo(target_date=args.date, jcd=args.jcd, race_no=args.rno)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
