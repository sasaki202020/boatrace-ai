from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from src.ingest.browser_fetcher import fetch_html_with_browser
from src.ingest.official_fetcher import JCD_TO_VENUE, RACELIST_URL, fetch_racelist_html
from src.ingest.parsers.index_parser import parse_index_html
from src.ingest.parsers.racelist_parser import parse_racelist_html


ROOT = Path(__file__).resolve().parents[2]
RAW_ROOT = ROOT / "data" / "raw" / "official"
NORMALIZED_ROOT = ROOT / "data" / "normalized"
KNOWN_VENUES = [{ "jcd": jcd, "venueName": venue } for jcd, venue in sorted(JCD_TO_VENUE.items())]
DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; boatrace-ai-mvp/1.0; +https://www.boatrace.jp/)"
INDEX_URL = "https://www.boatrace.jp/owpc/pc/race/index?hd={date8}"


def _normalize_date(value: str) -> str:
    token = str(value).strip().lower()
    if token == "today":
        return date.today().strftime("%Y%m%d")
    digits = "".join(ch for ch in token if ch.isdigit())
    if len(digits) != 8:
        raise ValueError(f"invalid date: {value!r}")
    return digits


def _daterange(start_date: str, end_date: str) -> list[str]:
    start = datetime.strptime(start_date, "%Y%m%d").date()
    end = datetime.strptime(end_date, "%Y%m%d").date()
    days: list[str] = []
    current = start
    while current <= end:
        days.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return days


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": DEFAULT_USER_AGENT})
    return session


def _fetch_index_html(date8: str, *, timeout: float = 10.0, retries: int = 1, retry_sleep: float = 0.5) -> tuple[str, str]:
    session = _session()
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


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _existing_raw_venues(date8: str) -> list[dict[str, Any]]:
    day_dir = RAW_ROOT / date8
    venues: list[dict[str, Any]] = []
    if not day_dir.exists():
        return venues
    for venue_dir in sorted([p for p in day_dir.iterdir() if p.is_dir()]):
        jcd = venue_dir.name.zfill(2)
        if not any(venue_dir.glob("racelist_*.html")) and not any(venue_dir.glob("odds3t_*.html")) and not any(venue_dir.glob("result_*.html")):
            continue
        venues.append(
            {
                "jcd": jcd,
                "venueName": JCD_TO_VENUE.get(jcd, jcd),
                "isOpen": True,
                "sourceUrl": venue_dir.as_uri(),
                "fetchedAt": datetime.now().isoformat(timespec="seconds"),
                "discoveryMethod": "existing_raw",
            }
        )
    return venues


def _existing_ui_venues(date8: str) -> list[dict[str, Any]]:
    day_dir = NORMALIZED_ROOT.parent / "ui" / date8
    venues: list[dict[str, Any]] = []
    if not day_dir.exists():
        return venues
    for ui_path in sorted(day_dir.glob("raceyosou_*.json")):
        payload = _load_json(ui_path)
        if not payload:
            continue
        jcd = str(payload.get("jcd") or ui_path.stem.rsplit("_", 1)[-1]).zfill(2)
        venues.append(
            {
                "jcd": jcd,
                "venueName": payload.get("venue") or payload.get("venueName") or JCD_TO_VENUE.get(jcd, jcd),
                "isOpen": True,
                "sourceUrl": ui_path.as_uri(),
                "fetchedAt": datetime.now().isoformat(timespec="seconds"),
                "discoveryMethod": "existing_ui",
            }
        )
    return venues


def _probe_racelist_venues(date8: str, *, force: bool = False) -> list[dict[str, Any]]:
    venues: list[dict[str, Any]] = []
    for idx in range(1, 25):
        jcd = f"{idx:02d}"
        url = RACELIST_URL.format(date8=date8, jcd=jcd, rno=1)
        try:
            probe = fetch_racelist_html(target_date=date8, jcd=jcd, race_no=1, timeout=6.0, retries=0)
            parsed = probe.get("parsed") or {}
            if probe.get("dataStatus") in {"available", "ok"} or parsed.get("boats"):
                venues.append(
                    {
                        "jcd": jcd,
                        "venueName": JCD_TO_VENUE.get(jcd, jcd),
                        "isOpen": True,
                        "sourceUrl": url,
                        "fetchedAt": probe.get("fetchedAt") or datetime.now().isoformat(timespec="seconds"),
                        "discoveryMethod": "racelist_probe",
                    }
                )
        except Exception:
            continue
    return venues


def discover_venues_for_date(target_date: str, *, force: bool = False) -> dict[str, Any]:
    date8 = _normalize_date(target_date)
    norm_dir = NORMALIZED_ROOT / date8
    norm_dir.mkdir(parents=True, exist_ok=True)

    venues: list[dict[str, Any]] = []
    warnings: list[str] = []
    source_url = INDEX_URL.format(date8=date8)
    fetched_at = datetime.now().isoformat(timespec="seconds")
    discovery_method = "official_index"

    cached_path = norm_dir / "venues.json"
    if cached_path.exists() and not force:
        cached = _load_json(cached_path)
        if cached and isinstance(cached.get("venues"), list) and cached.get("venues"):
            return cached

    html, fetch_status = _fetch_index_html(date8)
    if html:
        try:
            parsed = parse_index_html(html, date8)
        except Exception as exc:  # pragma: no cover - defensive
            parsed = {"venues": [], "dataStatus": "missing", "missingReason": ["index_parse_failed"]}
            warnings.append(f"index_parse_error:{exc}")
        if parsed.get("venues"):
            venues = [
                {
                    "jcd": str(row.get("jcd") or "").zfill(2),
                    "venueName": row.get("venueName") or row.get("label") or JCD_TO_VENUE.get(str(row.get("jcd") or "").zfill(2), ""),
                    "isOpen": bool(row.get("isOpen", True)),
                    "sourceUrl": row.get("sourceUrl") or source_url,
                    "fetchedAt": fetched_at,
                    "discoveryMethod": "official_index",
                }
                for row in parsed.get("venues") or []
                if isinstance(row, dict)
            ]
    if not venues:
        venues = _existing_raw_venues(date8)
        if venues:
            discovery_method = "existing_raw"
        else:
            venues = _existing_ui_venues(date8)
            if venues:
                discovery_method = "existing_ui"
    if not venues:
        venues = _probe_racelist_venues(date8, force=force)
        if venues:
            discovery_method = "racelist_probe"
    if not venues:
        venues = [
            {
                "jcd": item["jcd"],
                "venueName": item["venueName"],
                "isOpen": True,
                "sourceUrl": source_url,
                "fetchedAt": fetched_at,
                "discoveryMethod": "fallback_known_venues",
            }
            for item in KNOWN_VENUES
        ]
        discovery_method = "fallback_known_venues"
        warnings.append("fallback_known_venues_used")

    payload = {
        "date": date8,
        "venues": sorted(venues, key=lambda item: item["jcd"]),
        "sourceUrl": source_url,
        "fetchedAt": fetched_at,
        "discoveryMethod": discovery_method,
        "fetchStatus": fetch_status,
        "warnings": warnings,
        "dataStatus": "available" if venues else "missing",
        "missingReason": [] if venues else ["no_venue_discovery"],
    }
    cached_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def discover_venues_for_range(*, start_date: str, end_date: str, force: bool = False) -> dict[str, Any]:
    start8 = _normalize_date(start_date)
    end8 = _normalize_date(end_date)
    days = _daterange(start8, end8)
    rows = [discover_venues_for_date(day, force=force) for day in days]
    return {
        "dateRange": f"{start8}_{end8}",
        "days": len(days),
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "rows": rows,
    }


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Discover BOATRACE venues for a date or date range.")
    parser.add_argument("--date", help="today, YYYYMMDD, or YYYY-MM-DD")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.date:
        print(json.dumps(discover_venues_for_date(args.date, force=args.force), ensure_ascii=False, indent=2))
        return
    if args.start_date and args.end_date:
        print(json.dumps(discover_venues_for_range(start_date=args.start_date, end_date=args.end_date, force=args.force), ensure_ascii=False, indent=2))
        return
    raise SystemExit("either --date or --start-date/--end-date is required")


if __name__ == "__main__":
    main()
