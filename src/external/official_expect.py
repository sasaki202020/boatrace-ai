from __future__ import annotations

"""Fetch and persist BOATRACE official computer predictions as an external baseline."""

import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date as date_cls, datetime
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import requests
from bs4 import BeautifulSoup

from src.ingest.official_fetcher import JCD_TO_VENUE


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; boatrace-ai-mvp/1.0; +https://www.boatrace.jp/)"
INDEX_URL = "https://www.boatrace.jp/owpc/pc/race/index?hd={date8}"
EXPECT_URL = "https://www.boatrace.jp/owpc/pc/race/pcexpect?rno={race_no}&jcd={jcd}&hd={date8}"
DATA_ROOT = ROOT / "data" / "external" / "official_expect"
REPORT_ROOT = ROOT / "reports" / "external" / "official_expect"


@dataclass(frozen=True)
class OfficialExpectVenueTarget:
    jcd: str
    venue: str


@dataclass(frozen=True)
class OfficialExpectRaceTarget:
    date8: str
    jcd: str
    venue: str
    race_no: int
    url: str


def _normalize_date(value: object) -> str:
    if isinstance(value, date_cls):
        return value.strftime("%Y-%m-%d")
    text = str(value).strip()
    if not text:
        raise ValueError("date is empty")
    if re.fullmatch(r"\d{8}", text):
        return datetime.strptime(text, "%Y%m%d").date().isoformat()
    return datetime.strptime(text, "%Y-%m-%d").date().isoformat()


def _date8(value: object) -> str:
    return _normalize_date(value).replace("-", "")


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": DEFAULT_USER_AGENT})
    return session


def _fetch_html(
    session: requests.Session,
    url: str,
    *,
    timeout: float,
    retries: int,
    retry_sleep: float,
) -> tuple[str, str]:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            return response.text, "live"
        except Exception as exc:  # pragma: no cover - network dependent
            last_error = exc
            if attempt >= retries:
                break
            if retry_sleep > 0:
                import time

                time.sleep(retry_sleep * (attempt + 1))
    return "", f"error:{last_error}" if last_error else "unavailable"


def _load_html(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _save_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _target_dir(target_date: str) -> Path:
    date8 = _date8(target_date)
    path = DATA_ROOT / date8
    path.mkdir(parents=True, exist_ok=True)
    return path


def _report_dir(target_date: str) -> Path:
    date8 = _date8(target_date)
    path = REPORT_ROOT / date8
    path.mkdir(parents=True, exist_ok=True)
    return path


def _extract_venue_targets(index_html: str) -> list[OfficialExpectVenueTarget]:
    soup = BeautifulSoup(index_html or "", "html.parser")
    targets: dict[str, OfficialExpectVenueTarget] = {}
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href", ""))
        if "pcexpect" not in href or "jcd=" not in href:
            continue
        jcd_match = re.search(r"jcd=(\d{1,2})", href)
        if not jcd_match:
            continue
        jcd = f"{int(jcd_match.group(1)):02d}"
        targets[jcd] = OfficialExpectVenueTarget(jcd=jcd, venue=JCD_TO_VENUE.get(jcd, jcd))
    return [targets[jcd] for jcd in sorted(targets)]


def _extract_predictions_from_html(html: str) -> list[dict[str, Any]]:
    try:
        soup = BeautifulSoup(html or "", "html.parser")
    except Exception:
        return []

    records: list[dict[str, Any]] = []
    for block in soup.select("tbody.is-fs12"):
        try:
            first_row = block.find("tr")
            if first_row is None:
                continue
            mark_img = first_row.select_one("img[src*='icon_mark1_']")
            if mark_img is None:
                continue
            mark_match = re.search(r"icon_mark1_(\d)", str(mark_img.get("src", "")))
            if not mark_match:
                continue
            lane_cell = first_row.find("td", class_=re.compile(r"is-fs14"))
            if lane_cell is None:
                continue
            lane_match = re.search(r"\d+", lane_cell.get_text(" ", strip=True))
            if not lane_match:
                continue
            name_anchor = first_row.select_one("div.is-fs18.is-fBold a")
            name = ""
            if name_anchor is not None:
                name = name_anchor.get_text(" ", strip=True)
            if not name:
                name_div = first_row.select_one("div.is-fs18.is-fBold")
                if name_div is not None:
                    name = name_div.get_text(" ", strip=True)
            name = re.sub(r"\s+", " ", name).strip()
            if not name:
                continue
            records.append(
                {
                    "mark_rank": int(mark_match.group(1)),
                    "lane": int(lane_match.group(0)),
                    "player_name": name,
                }
            )
        except Exception:
            continue
    records.sort(key=lambda item: (int(item["mark_rank"]), int(item["lane"])))
    return records


def _write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    columns = ["date", "jcd", "venue", "race_no", "mark_rank", "lane", "player_name", "source_url", "fetched_at"]
    df = pd.DataFrame(rows, columns=columns)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8")


def _write_json(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_report(payload: dict[str, Any], path: Path) -> None:
    summary = payload.get("summary", {})
    failures = payload.get("failures", [])
    missing_races = payload.get("missing_races", [])
    lines = [
        f"# BOATRACE official computer predictions",
        "",
        f"- 取得日: {payload.get('date', '')}",
        f"- 取得会場数: {summary.get('venue_count', 0)}",
        f"- 取得レース数: {summary.get('race_count', 0)}",
        f"- 1R〜12Rの全取得: {'はい' if summary.get('all_races_complete') else 'いいえ'}",
        "",
        "## 失敗URL",
    ]
    if failures:
        lines.extend([f"- {item}" for item in failures])
    else:
        lines.append("- なし")
    lines.extend(["", "## 欠損した会場/レース"])
    if missing_races:
        lines.extend([f"- {item}" for item in missing_races])
    else:
        lines.append("- なし")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fetch_expect_race(
    target: OfficialExpectRaceTarget,
    *,
    timeout: float,
    retries: int,
    retry_sleep: float,
) -> dict[str, Any]:
    session = _session()
    html, status = _fetch_html(session, target.url, timeout=timeout, retries=retries, retry_sleep=retry_sleep)
    predictions = _extract_predictions_from_html(html) if html else []
    return {
        "jcd": target.jcd,
        "venue": target.venue,
        "race_no": int(target.race_no),
        "url": target.url,
        "fetch_status": status,
        "html": html,
        "predictions": predictions,
    }


def fetch_official_expect(
    *,
    target_date: str,
    timeout: float = 15.0,
    retries: int = 2,
    retry_sleep: float = 1.0,
    race_nos: Iterable[int] | None = None,
) -> dict[str, Any]:
    normalized_date = _normalize_date(target_date)
    date8 = normalized_date.replace("-", "")
    target_races = list(race_nos) if race_nos is not None else list(range(1, 13))
    session = _session()

    index_url = INDEX_URL.format(date8=date8)
    index_html, index_status = _fetch_html(session, index_url, timeout=timeout, retries=retries, retry_sleep=retry_sleep)
    venues = _extract_venue_targets(index_html) if index_html else []

    fetched_at = datetime.now().isoformat(timespec="seconds")
    rows: list[dict[str, Any]] = []
    failures: list[str] = []
    missing_races: list[str] = []
    venues_payload: list[dict[str, Any]] = []
    venue_payload_map: dict[str, dict[str, Any]] = {}

    if not venues:
        failures.append(index_url)

    race_targets = [
        OfficialExpectRaceTarget(
            date8=date8,
            jcd=venue.jcd,
            venue=venue.venue,
            race_no=int(race_no),
            url=EXPECT_URL.format(date8=date8, jcd=venue.jcd, race_no=int(race_no)),
        )
        for venue in venues
        for race_no in target_races
    ]

    successful_races: set[tuple[str, int]] = set()
    if race_targets:
        max_workers = min(12, len(race_targets))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(_fetch_expect_race, target, timeout=timeout, retries=retries, retry_sleep=retry_sleep): target
                for target in race_targets
            }
            for future in as_completed(futures):
                target = futures[future]
                try:
                    result = future.result()
                except Exception:
                    failures.append(target.url)
                    missing_races.append(f"{target.venue} {target.race_no}R")
                    continue
                html = str(result.get("html") or "")
                predictions = result.get("predictions") or []
                if not html or not predictions:
                    missing_races.append(f"{target.venue} {target.race_no}R")
                    if not html:
                        failures.append(target.url)
                    continue

                venue_payload = venue_payload_map.setdefault(target.jcd, {"jcd": target.jcd, "venue": target.venue, "races": []})
                race_rows = []
                for pred in predictions:
                    row = {
                        "date": normalized_date,
                        "jcd": target.jcd,
                        "venue": target.venue,
                        "race_no": int(target.race_no),
                        "mark_rank": int(pred["mark_rank"]),
                        "lane": int(pred["lane"]),
                        "player_name": str(pred["player_name"]),
                        "source_url": target.url,
                        "fetched_at": fetched_at,
                    }
                    rows.append(row)
                    race_rows.append(
                        {
                            "mark_rank": int(pred["mark_rank"]),
                            "lane": int(pred["lane"]),
                            "player_name": str(pred["player_name"]),
                        }
                    )
                venue_payload["races"].append(
                    {
                        "race_no": int(target.race_no),
                        "source_url": target.url,
                        "fetched_at": fetched_at,
                        "predictions": race_rows,
                    }
                )
                successful_races.add((target.jcd, int(target.race_no)))

    for venue in venues:
        venues_payload.append(
            venue_payload_map.get(venue.jcd, {"jcd": venue.jcd, "venue": venue.venue, "races": []})
        )
    for venue_payload in venues_payload:
        venue_payload["races"] = sorted(venue_payload["races"], key=lambda item: int(item["race_no"]))

    expected_race_count = len(race_targets)
    actual_race_count = len(successful_races)
    summary = {
        "venue_count": len(venues),
        "race_count": actual_race_count,
        "expected_race_count": expected_race_count,
        "all_races_complete": bool(expected_race_count > 0 and actual_race_count == expected_race_count),
        "failure_count": len(failures),
        "missing_race_count": len(missing_races),
    }

    day_dir = _target_dir(normalized_date)
    report_dir = _report_dir(normalized_date)
    csv_path = day_dir / "official_expect.csv"
    json_path = day_dir / "official_expect.json"
    report_path = report_dir / "summary.md"

    _write_csv(rows, csv_path)
    payload = {
        "date": normalized_date,
        "date8": date8,
        "venues": venues_payload,
        "summary": summary,
        "failures": sorted(set(failures)),
        "missing_races": sorted(set(missing_races)),
        "index_url": index_url,
        "index_fetch_status": index_status,
        "generated_at": fetched_at,
    }
    _write_json(payload, json_path)
    _write_report(payload, report_path)

    return {
        "date": normalized_date,
        "date8": date8,
        "summary": summary,
        "csv_path": str(csv_path),
        "json_path": str(json_path),
        "report_path": str(report_path),
        "venues": venues_payload,
        "rows": rows,
        "failures": sorted(set(failures)),
        "missing_races": sorted(set(missing_races)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch BOATRACE official computer predictions and save them as an external baseline.")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="Target date in YYYY-MM-DD or YYYYMMDD.")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--retry-sleep", type=float, default=1.0)
    args = parser.parse_args(argv)

    result = fetch_official_expect(
        target_date=args.date,
        timeout=args.timeout,
        retries=args.retries,
        retry_sleep=args.retry_sleep,
    )
    print(json.dumps(
        {
            "date": result["date"],
            "summary": result["summary"],
            "csv_path": result["csv_path"],
            "json_path": result["json_path"],
            "report_path": result["report_path"],
            "failure_count": len(result["failures"]),
            "missing_race_count": len(result["missing_races"]),
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
