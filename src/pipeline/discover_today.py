from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import requests

from src.ingest.parsers.index_parser import parse_index_html
from src.pipeline.pipeline_utils import append_log, log_file_for, parse_date, write_json


ROOT = Path(__file__).resolve().parents[2]
RAW_ROOT = ROOT / "data" / "raw" / "official"
NORMALIZED_ROOT = ROOT / "data" / "normalized"
ERRORS_ROOT = ROOT / "reports" / "errors"
INDEX_URL = "https://www.boatrace.jp/owpc/pc/race/index?hd={date8}"
DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; boatrace-ai-mvp/1.0; +https://www.boatrace.jp/)"


def _normalize_date(value: str) -> str:
    token = str(value).strip().lower()
    if token == "today":
        return date.today().strftime("%Y%m%d")
    digits = "".join(ch for ch in token if ch.isdigit())
    if len(digits) != 8:
        raise ValueError(f"invalid date: {value!r}")
    return digits


def _fetch_index_html(date8: str, *, timeout: float = 10.0, retries: int = 2, retry_sleep: float = 0.5) -> tuple[str, str]:
    session = requests.Session()
    session.headers.update({"User-Agent": DEFAULT_USER_AGENT})
    url = INDEX_URL.format(date8=date8)
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            return response.text, "ok"
        except Exception as exc:  # pragma: no cover - network dependent
            last_error = exc
            if attempt >= retries:
                break
            if retry_sleep > 0:
                import time

                time.sleep(retry_sleep * (attempt + 1))
    return "", f"error:{last_error}" if last_error else "unavailable"


def _append_error(date8: str, payload: dict[str, Any]) -> None:
    ERRORS_ROOT.mkdir(parents=True, exist_ok=True)
    path = ERRORS_ROOT / f"{date8}_errors.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def discover_today(*, target_date: str) -> dict[str, Any]:
    date8 = _normalize_date(target_date)
    raw_dir = RAW_ROOT / date8
    norm_dir = NORMALIZED_ROOT / date8
    raw_dir.mkdir(parents=True, exist_ok=True)
    norm_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_file_for("discover_today", parse_date(f"{date8[:4]}-{date8[4:6]}-{date8[6:8]}", default=date.today()))

    url = INDEX_URL.format(date8=date8)
    html, fetch_status = _fetch_index_html(date8)
    (raw_dir / "index.html").write_text(html or "", encoding="utf-8")
    try:
        parsed = parse_index_html(html, date8)
    except Exception as exc:  # pragma: no cover - defensive
        parsed = {"dataStatus": "missing", "venues": [], "missingReason": ["index_parse_failed"]}
        _append_error(
            date8,
            {
                "date": date8,
                "stage": "discover_today",
                "type": "index_parse_error",
                "message": str(exc),
                "url": url,
                "fetchedAt": datetime.now().isoformat(timespec="seconds"),
            },
        )

    venues = []
    fetched_at = datetime.now().isoformat(timespec="seconds")
    for venue in parsed.get("venues") or []:
        venues.append(
            {
                "jcd": str(venue.get("jcd") or "").zfill(2),
                "venueName": venue.get("venueName") or venue.get("label") or "",
                "isOpen": bool(venue.get("isOpen", True)),
                "sourceUrl": venue.get("sourceUrl") or url,
                "fetchedAt": fetched_at,
            }
        )

    payload = {
        "date": date8,
        "venues": venues,
        "sourceUrl": url,
        "fetchedAt": fetched_at,
        "dataStatus": parsed.get("dataStatus") or ("available" if venues else "missing"),
        "missingReason": parsed.get("missingReason") or ([] if venues else ["index_unavailable"]),
        "fetchStatus": fetch_status,
    }
    write_json(norm_dir / "today_venues.json", payload)
    if fetch_status != "ok" or not venues:
        _append_error(
            date8,
            {
                "date": date8,
                "stage": "discover_today",
                "type": "discover_today_error" if fetch_status != "ok" else "discover_today_empty",
                "message": "failed to discover venues" if fetch_status != "ok" else "no venues discovered",
                "url": url,
                "fetchedAt": fetched_at,
                "fetchStatus": fetch_status,
                "missingReason": payload["missingReason"],
            },
        )
    append_log(log_path, f"[discover] venues={len(venues)} status={payload['dataStatus']}")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover today's active BOATRACE venues.")
    parser.add_argument("--date", required=True, help="today, YYYYMMDD, or YYYY-MM-DD")
    args = parser.parse_args()
    print(json.dumps(discover_today(target_date=args.date), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
