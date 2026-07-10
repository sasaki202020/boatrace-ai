from __future__ import annotations

"""Fetch today's BOATRACE official race-card HTML and build today_races.csv.

The fetch path is:
1. live official index page
2. saved HTML fallback for the index page
3. saved HTML fallback for each race page
4. optional legacy TXT fallback if explicitly provided

The script is designed so the saved HTML fallback can be dropped into
data/raw/official/web_entries/YYYYMMDD/... and re-run without live access.
"""

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from bs4 import BeautifulSoup

from src.data.parse_official_entries_html import parse_official_entries_html
from src.utils.race_id import canonical_race_id


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; boatrace-ai-mvp/1.0; +https://www.boatrace.jp/)"
INDEX_URL = "https://www.boatrace.jp/owpc/pc/race/index?hd={date8}"
RACE_URL = "https://www.boatrace.jp/owpc/pc/race/racelist?hd={date8}&jcd={jcd}&rno={rno}"
TODAY_RACES_CSV = ROOT / "data" / "processed" / "today_races.csv"
OFFICIAL_WEB_ROOT = ROOT / "data" / "raw" / "official" / "web_entries"
REPORT_ROOT = ROOT / "reports" / "daily"


@dataclass(frozen=True)
class EntriesFetchTarget:
    race_id: str
    date: str
    jcd: str
    race_no: int
    url: str


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch today's official race cards with fallback support.")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="Target date in YYYY-MM-DD.")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--retry-sleep", type=float, default=1.5)
    parser.add_argument("--fallback-index-path", type=Path, default=None)
    parser.add_argument("--fallback-syusso-dir", type=Path, default=None)
    parser.add_argument("--legacy-entry-txt-dir", type=Path, default=None)
    return parser.parse_args()


def _normalize_date(value: str) -> str:
    return datetime.strptime(value, "%Y-%m-%d").date().isoformat()


def _target_dir(target_date: str) -> Path:
    date8 = target_date.replace("-", "")
    path = OFFICIAL_WEB_ROOT / date8
    (path / "syusso_pages").mkdir(parents=True, exist_ok=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _report_dir(target_date: str) -> Path:
    path = REPORT_ROOT / target_date
    path.mkdir(parents=True, exist_ok=True)
    return path


def _load_html(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _save_html(path: Path, html: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8", errors="ignore")


def _fetch_html(session: requests.Session, url: str, timeout: float, retries: int, retry_sleep: float) -> str:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            return response.text
        except Exception as exc:  # pragma: no cover - network dependent
            last_error = exc
            if attempt >= retries:
                break
            if retry_sleep > 0:
                import time

                time.sleep(retry_sleep * (attempt + 1))
    if last_error is None:
        raise RuntimeError(f"failed to fetch {url}")
    raise last_error


def _extract_targets_from_index_html(html: str, target_date: str) -> list[EntriesFetchTarget]:
    soup = BeautifulSoup(html, "html.parser")
    targets: dict[str, EntriesFetchTarget] = {}
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href", ""))
        if "racelist" not in href or "jcd=" not in href or "rno=" not in href:
            continue
        jcd_match = re.search(r"jcd=(\d{1,2})", href)
        rno_match = re.search(r"rno=(\d{1,2})", href)
        if not jcd_match or not rno_match:
            continue
        jcd = int(jcd_match.group(1))
        rno = int(rno_match.group(1))
        race_id = canonical_race_id(target_date, jcd, rno)
        targets[race_id] = EntriesFetchTarget(
            race_id=race_id,
            date=target_date,
            jcd=f"{jcd:02d}",
            race_no=rno,
            url=href if href.startswith("http") else f"https://www.boatrace.jp{href}",
        )
    return sorted(targets.values(), key=lambda item: (item.jcd, item.race_no))


def _load_or_fetch_index_html(
    target_date: str,
    *,
    timeout: float,
    retries: int,
    retry_sleep: float,
    fallback_index_path: Path | None,
) -> tuple[str, str]:
    url = INDEX_URL.format(date8=target_date.replace("-", ""))
    session = requests.Session()
    session.headers.update({"User-Agent": DEFAULT_USER_AGENT})
    try:
        html = _fetch_html(session, url, timeout, retries, retry_sleep)
        return html, "live"
    except Exception:
        if fallback_index_path and fallback_index_path.exists():
            return _load_html(fallback_index_path), "fallback_html"
    return "", "unavailable"


def _load_or_fetch_race_html(
    target: EntriesFetchTarget,
    *,
    timeout: float,
    retries: int,
    retry_sleep: float,
    fallback_syusso_dir: Path | None,
) -> tuple[str, str]:
    session = requests.Session()
    session.headers.update({"User-Agent": DEFAULT_USER_AGENT})
    try:
        html = _fetch_html(session, target.url, timeout, retries, retry_sleep)
        return html, "live"
    except Exception:
        if fallback_syusso_dir is not None:
            candidates = [
                fallback_syusso_dir / f"{target.race_id}.html",
                fallback_syusso_dir / f"{target.jcd}_{target.race_no:02d}.html",
                fallback_syusso_dir / f"{target.jcd}-{target.race_no:02d}.html",
            ]
            for candidate in candidates:
                if candidate.exists():
                    return _load_html(candidate), "fallback_html"
    return "", "unavailable"


def _build_targets_from_legacy_txt(txt_dir: Path | None, target_date: str) -> list[EntriesFetchTarget]:
    if txt_dir is None or not txt_dir.exists():
        return []
    candidates = sorted(txt_dir.glob("B*.TXT"))
    for candidate in candidates:
        try:
            df = pd.read_csv(candidate, low_memory=False)
        except Exception:
            continue
        if df.empty or "date" not in df.columns or "jcd" not in df.columns or "race_no" not in df.columns:
            continue
        work = df.copy()
        work["date"] = work["date"].astype(str).str.slice(0, 10)
        work = work[work["date"] == target_date]
        if work.empty:
            continue
        targets: list[EntriesFetchTarget] = []
        for _, row in work.drop_duplicates(subset=["jcd", "race_no"]).iterrows():
            try:
                jcd = int(pd.to_numeric(row["jcd"], errors="coerce"))
                race_no = int(pd.to_numeric(row["race_no"], errors="coerce"))
            except Exception:
                continue
            race_id = canonical_race_id(target_date, jcd, race_no)
            targets.append(
                EntriesFetchTarget(
                    race_id=race_id,
                    date=target_date,
                    jcd=f"{jcd:02d}",
                    race_no=race_no,
                    url=RACE_URL.format(date8=target_date.replace("-", ""), jcd=f"{jcd:02d}", rno=race_no),
                )
            )
        if targets:
            return sorted(targets, key=lambda item: (item.jcd, item.race_no))
    return []


def fetch_today_official_entries(
    *,
    target_date: str,
    timeout: float,
    retries: int,
    retry_sleep: float,
    fallback_index_path: Path | None,
    fallback_syusso_dir: Path | None,
    legacy_entry_txt_dir: Path | None,
) -> dict[str, Any]:
    date8 = target_date.replace("-", "")
    out_dir = _target_dir(target_date)
    report_dir = _report_dir(target_date)

    index_html, index_mode = _load_or_fetch_index_html(
        target_date,
        timeout=timeout,
        retries=retries,
        retry_sleep=retry_sleep,
        fallback_index_path=fallback_index_path,
    )

    targets = _extract_targets_from_index_html(index_html, target_date)
    target_source = "index_page"
    if not targets and fallback_syusso_dir is not None:
        fallback_index = fallback_syusso_dir / "index_page.html"
        if fallback_index.exists():
            index_html = _load_html(fallback_index)
            index_mode = "fallback_html"
            targets = _extract_targets_from_index_html(index_html, target_date)
            target_source = "fallback_html"
    if not targets:
        targets = _build_targets_from_legacy_txt(legacy_entry_txt_dir, target_date)
        if targets:
            target_source = "legacy_txt"

    if not targets:
        raise FileNotFoundError(
            "No entry targets could be determined. Provide live access, saved HTML fallback, or matching legacy TXT files."
        )

    index_path = out_dir / "index_page.html"
    if index_html:
        _save_html(index_path, index_html)
    else:
        _save_html(index_path, f"<html><body><h1>{target_date} entries index unavailable</h1></body></html>")

    syusso_dir = out_dir / "syusso_pages"
    syusso_dir.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, Any]] = []
    fetched_pages = 0
    fallback_pages = 0
    for target in targets:
        html, page_mode = _load_or_fetch_race_html(
            target,
            timeout=timeout,
            retries=retries,
            retry_sleep=retry_sleep,
            fallback_syusso_dir=fallback_syusso_dir,
        )
        if not html:
            continue
        if page_mode == "live":
            fetched_pages += 1
        else:
            fallback_pages += 1
        page_path = syusso_dir / f"{target.race_id}.html"
        _save_html(page_path, html)
        parsed = parse_official_entries_html(html, target_date=target.date, jcd=target.jcd, race_no=target.race_no)
        if parsed.empty and legacy_entry_txt_dir is not None:
            # Do not silently invent data; keep the page for manual inspection.
            continue
        for row in parsed.to_dict(orient="records"):
            records.append(row)

    if not records:
        raise RuntimeError("No entry records could be produced.")

    today_df = pd.DataFrame(records)
    today_df["race_id"] = today_df["race_id"].astype(str).str.strip()
    today_df["lane"] = pd.to_numeric(today_df.get("lane"), errors="coerce")
    today_df = today_df.drop_duplicates(subset=["race_id", "lane"], keep="last").sort_values(["race_id", "lane"]).reset_index(drop=True)

    csv_path = TODAY_RACES_CSV
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    today_df.to_csv(csv_path, index=False, encoding="utf-8")

    summary = {
        "target_date": target_date,
        "index_page_fetched": bool(index_html),
        "syusso_pages_fetched": int(fetched_pages),
        "syusso_pages_fallback": int(fallback_pages),
        "race_records_count": int(len(today_df)),
        "unique_race_count": int(today_df["race_id"].nunique()) if not today_df.empty else 0,
        "fetch_mode": "live" if index_mode == "live" and fetched_pages > 0 else ("fallback_html" if index_mode == "fallback_html" or fallback_pages > 0 else "fallback"),
        "target_source": target_source,
        "output": {
            "index_page_html": str(index_path),
            "syusso_pages_dir": str(syusso_dir),
            "csv": str(csv_path),
            "report_json": str(report_dir / "entries_ingest_summary.json"),
        },
        "notes": [
            "live fetch is attempted first",
            "saved HTML fallback is used when available",
            "legacy TXT fallback is used only when HTML is unavailable",
        ],
    }
    (report_dir / "entries_ingest_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    args = _parse_args()
    target_date = _normalize_date(args.date)
    summary = fetch_today_official_entries(
        target_date=target_date,
        timeout=float(args.timeout),
        retries=int(args.retries),
        retry_sleep=float(args.retry_sleep),
        fallback_index_path=Path(args.fallback_index_path) if args.fallback_index_path else None,
        fallback_syusso_dir=Path(args.fallback_syusso_dir) if args.fallback_syusso_dir else None,
        legacy_entry_txt_dir=Path(args.legacy_entry_txt_dir) if args.legacy_entry_txt_dir else None,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
