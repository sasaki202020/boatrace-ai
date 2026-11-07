from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from src.ingest.official_fetcher import fetch_result_html
from src.ingest.official_k_loader import find_k_file_for_date
from src.ingest.parsers.official_k_result_parser import parse_official_k_result_file


def _normalize_date(value: str) -> str:
    token = str(value).strip().lower()
    if token == "today":
        return date.today().strftime("%Y%m%d")
    digits = "".join(ch for ch in token if ch.isdigit())
    if len(digits) != 8:
        raise ValueError(f"invalid date: {value!r}")
    return digits


def _extract_snippet(text: str, keyword: str, *, window: int = 120) -> str | None:
    idx = text.find(keyword)
    if idx < 0:
        return None
    start = max(0, idx - window)
    end = min(len(text), idx + window)
    snippet = text[start:end]
    return " ".join(snippet.split())


def _clean_candidates(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return []


def debug_result(*, target_date: str, jcd: str, rno: int) -> dict:
    date8 = _normalize_date(target_date)
    result = fetch_result_html(target_date=date8, jcd=str(jcd).zfill(2), race_no=int(rno), race_id=f"{date8}-{str(jcd).zfill(2)}-{int(rno):02d}")
    parsed = result.get("parsed") or {}
    html = str(result.get("html") or "")
    snippets = parsed.get("textSnippets") or {}
    return {
        "url": result.get("url", ""),
        "httpStatus": result.get("fetchStatus", "unavailable"),
        "fetchedAt": result.get("fetchedAt", ""),
        "fallbackUsed": bool(result.get("fallbackUsed") or result.get("resultFallbackUsed")),
        "htmlLength": len(html),
        "allTextLength": parsed.get("allTextLength") or len(html),
        "containsResultKeyword": any(keyword in html for keyword in ("結果", "3連単", "着")) or bool(parsed),
        "containsTrifecta": bool(parsed.get("containsTrifecta")),
        "containsPayout": bool(parsed.get("containsPayout")),
        "containsFinish": bool(parsed.get("containsFinish")),
        "containsRefund": bool(parsed.get("containsRefund")),
        "containsCancel": bool(parsed.get("containsCancel")),
        "detectedVariant": parsed.get("detectedVariant") or "",
        "parserPathUsed": parsed.get("parserPathUsed") or "",
        "detectedTables": parsed.get("detectedTables") or 0,
        "tableHeaders": parsed.get("tableHeaders") or [],
        "textAroundTrifecta": snippets.get("3連単") or _extract_snippet(html, "3連単"),
        "textAroundPayout": snippets.get("払戻金") or _extract_snippet(html, "払戻金"),
        "textAroundFinishOrder": snippets.get("着") or _extract_snippet(html, "着"),
        "textAroundResult": snippets.get("レース結果") or _extract_snippet(html, "レース結果"),
        "candidateCombos": _clean_candidates(parsed.get("candidateCombos")),
        "candidatePayouts": _clean_candidates(parsed.get("candidatePayouts")),
        "candidateFinishNumbers": _clean_candidates(parsed.get("candidateFinishNumbers")),
        "parsedFinishOrder": parsed.get("finishOrder") or parsed.get("finish_order") or [],
        "parsedTrifectaCombo": parsed.get("trifectaCombo") or parsed.get("trifecta_combo"),
        "parsedTrifectaPayout": parsed.get("trifectaPayout") or parsed.get("trifecta_payout"),
        "normalizedCombo": parsed.get("normalizedCombo") or parsed.get("trifectaCombo") or parsed.get("trifecta_combo"),
        "normalizedPayout": parsed.get("normalizedPayout") or parsed.get("trifectaPayout") or parsed.get("trifecta_payout"),
        "parsedRaceStatus": parsed.get("raceStatus") or parsed.get("race_status") or result.get("dataStatus") or "missing",
        "raceStatusNormalized": parsed.get("raceStatusNormalized") or parsed.get("raceStatus") or parsed.get("race_status"),
        "parserFailureReason": parsed.get("parserFailureReason") or "",
        "suggestedParserRoute": parsed.get("suggestedParserRoute") or "",
        "parseWarnings": result.get("parseWarnings") or parsed.get("parseWarnings") or [],
        "errorType": result.get("errorType") or "",
        "errorMessage": result.get("errorMessage") or "",
        "rawHtmlPath": result.get("rawHtmlPath") or result.get("resultRawPath") or "",
    }


def debug_result_txt(*, target_date: str, jcd: str, rno: int, input_dir: str | None = None) -> dict:
    date8 = _normalize_date(target_date)
    file_path = find_k_file_for_date(date8, input_dir=input_dir)
    if file_path is None:
        return {
            "source": "txt",
            "date": date8,
            "jcd": str(jcd).zfill(2),
            "rno": int(rno),
            "kFilePath": "",
            "blockFound": False,
            "raceFound": False,
            "parsedFinishOrder": [],
            "parsedTrifectaCombo": None,
            "parsedTrifectaPayout": None,
            "parsedRaceStatus": "missing",
            "parseWarnings": ["result_txt_missing"],
        }
    parsed = parse_official_k_result_file(file_path, date8=date8)
    target_jcd = str(jcd).zfill(2)
    target_rno = int(rno)
    race = next(
        (
            item
            for item in parsed.get("races") or []
            if isinstance(item, dict)
            and str(item.get("jcd") or "").zfill(2) == target_jcd
            and int(item.get("rno") or item.get("raceNo") or 0) == target_rno
        ),
        None,
    )
    return {
        "source": "txt",
        "date": date8,
        "jcd": target_jcd,
        "rno": target_rno,
        "kFilePath": str(file_path),
        "blockFound": bool(parsed.get("blocks")),
        "raceFound": race is not None,
        "parsedFinishOrder": (race or {}).get("finishOrder") or [],
        "parsedTrifectaCombo": (race or {}).get("trifectaCombo"),
        "parsedTrifectaPayout": (race or {}).get("trifectaPayout"),
        "parsedRaceStatus": (race or {}).get("raceStatus") or "missing",
        "parseWarnings": (race or {}).get("parseWarnings") or parsed.get("parseWarnings") or [],
    }


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Debug a single BOAT RACE result fetch and parse.")
    parser.add_argument("--date", required=True, help="today, YYYYMMDD, or YYYY-MM-DD")
    parser.add_argument("--jcd", required=True, help="venue code")
    parser.add_argument("--rno", required=True, type=int, help="race number")
    parser.add_argument("--source", choices=["html", "txt"], default="html")
    parser.add_argument("--input-dir", default=None)
    args = parser.parse_args()
    if args.source == "txt":
        payload = debug_result_txt(target_date=_normalize_date(args.date), jcd=args.jcd, rno=args.rno, input_dir=args.input_dir)
    else:
        payload = debug_result(target_date=_normalize_date(args.date), jcd=args.jcd, rno=args.rno)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
