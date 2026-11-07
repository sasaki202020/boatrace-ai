from __future__ import annotations

"""Fetch today's official BOATRACE trifecta odds with HTML fallback support."""

import argparse
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from bs4 import BeautifulSoup

from src.odds.fetch_daily_trifecta_odds import build_odds_url, parse_trifecta_odds_table
from src.utils.race_id import canonical_race_id


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; boatrace-ai-mvp/1.0; +https://www.boatrace.jp/)"
INDEX_URL = "https://www.boatrace.jp/owpc/pc/race/index?hd={date8}"
ODDS_URL = "https://www.boatrace.jp/owpc/pc/race/odds3t?hd={date8}&jcd={jcd}&rno={rno}"
TODAY_WIN_PROBA_CSV = ROOT / "data" / "model_outputs" / "today_win_proba.csv"
TODAY_RACES_CSV = ROOT / "data" / "processed" / "today_races.csv"
ODDS_ROOT = ROOT / "data" / "odds"
REPORT_ROOT = ROOT / "reports" / "daily"


@dataclass(frozen=True)
class OddsFetchTarget:
    race_id: str
    date: str
    jcd: str
    race_no: int
    url: str


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch today's official trifecta odds with fallback support.")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="Target date in YYYY-MM-DD.")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--retry-sleep", type=float, default=1.5)
    parser.add_argument("--request-interval", type=float, default=0.5)
    parser.add_argument("--fallback-index-path", type=Path, default=None)
    parser.add_argument("--fallback-odds-dir", type=Path, default=None)
    parser.add_argument("--prediction-path", type=Path, default=TODAY_WIN_PROBA_CSV)
    parser.add_argument("--race-card-path", type=Path, default=TODAY_RACES_CSV)
    return parser.parse_args()


def _normalize_date(value: str) -> str:
    parsed = datetime.strptime(value, "%Y-%m-%d").date()
    return parsed.isoformat()


def _target_dir(target_date: str) -> Path:
    date8 = target_date.replace("-", "")
    path = ODDS_ROOT / date8
    (path / "odds_pages").mkdir(parents=True, exist_ok=True)
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


def _extract_targets_from_index_html(html: str, target_date: str) -> list[OddsFetchTarget]:
    soup = BeautifulSoup(html, "html.parser")
    targets: dict[str, OddsFetchTarget] = {}
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href", ""))
        if "race/odds3t" not in href or "jcd=" not in href or "rno=" not in href:
            continue
        jcd_match = re.search(r"jcd=(\d{1,2})", href)
        rno_match = re.search(r"rno=(\d{1,2})", href)
        if not jcd_match or not rno_match:
            continue
        jcd = int(jcd_match.group(1))
        rno = int(rno_match.group(1))
        race_id = canonical_race_id(target_date, jcd, rno)
        targets[race_id] = OddsFetchTarget(
            race_id=race_id,
            date=target_date,
            jcd=f"{jcd:02d}",
            race_no=rno,
            url=href if href.startswith("http") else f"https://www.boatrace.jp{href}",
        )
    return sorted(targets.values(), key=lambda item: (item.jcd, item.race_no))


def _build_targets_from_local_frame(frame: pd.DataFrame, target_date: str) -> list[OddsFetchTarget]:
    if frame.empty or "race_id" not in frame.columns:
        return []
    work = frame.copy()
    work["race_id"] = work["race_id"].astype(str).str.strip()
    if "date" in work.columns:
        work = work[work["date"].astype(str).str.slice(0, 10) == target_date]
    if work.empty:
        return []
    if "jcd" not in work.columns or "race_no" not in work.columns:
        return []
    work["jcd"] = pd.to_numeric(work["jcd"], errors="coerce")
    work["race_no"] = pd.to_numeric(work["race_no"], errors="coerce")
    work = work.dropna(subset=["jcd", "race_no"]).copy()
    if work.empty:
        return []
    unique = work.drop_duplicates(subset=["race_id", "jcd", "race_no"])
    targets: list[OddsFetchTarget] = []
    for _, row in unique.iterrows():
        jcd = int(row["jcd"])
        race_no = int(row["race_no"])
        race_id = canonical_race_id(target_date, jcd, race_no)
        targets.append(
            OddsFetchTarget(
                race_id=race_id,
                date=target_date,
                jcd=f"{jcd:02d}",
                race_no=race_no,
                url=build_odds_url(jcd, race_no, target_date.replace("-", "")),
            )
        )
    return sorted(targets, key=lambda item: (item.jcd, item.race_no))


def _targets_from_prediction_or_race_card(prediction_path: Path, race_card_path: Path, target_date: str) -> list[OddsFetchTarget]:
    frames: list[pd.DataFrame] = []
    for path in (prediction_path, race_card_path):
        if path.exists():
            try:
                frames.append(pd.read_csv(path, low_memory=False))
            except Exception:
                continue
    for frame in frames:
        targets = _build_targets_from_local_frame(frame, target_date)
        if targets:
            return targets
    return []


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


def _load_or_fetch_odds_html(
    target: OddsFetchTarget,
    *,
    timeout: float,
    retries: int,
    retry_sleep: float,
    fallback_odds_dir: Path | None,
) -> tuple[str, str]:
    session = requests.Session()
    session.headers.update({"User-Agent": DEFAULT_USER_AGENT})
    try:
        html = _fetch_html(session, target.url, timeout, retries, retry_sleep)
        return html, "live"
    except Exception:
        if fallback_odds_dir is not None:
            candidates = [
                fallback_odds_dir / f"{target.race_id}.html",
                fallback_odds_dir / f"{target.jcd}_{target.race_no:02d}.html",
                fallback_odds_dir / f"{target.jcd}-{target.race_no:02d}.html",
            ]
            for candidate in candidates:
                if candidate.exists():
                    return _load_html(candidate), "fallback_html"
    return "", "unavailable"


def _odds_rows_from_fallback_csv(source_csv: Path | None, target_date: str) -> pd.DataFrame:
    if source_csv is None or not source_csv.exists():
        return pd.DataFrame()
    df = pd.read_csv(source_csv, low_memory=False)
    if df.empty:
        return pd.DataFrame()
    if "race_id" not in df.columns:
        return pd.DataFrame()
    df = df.copy()
    df["race_id"] = df["race_id"].astype(str).str.strip()
    if "date" in df.columns:
        df["date"] = df["date"].astype(str)
    if "trifecta" in df.columns and "combo" not in df.columns:
        df["combo"] = df["trifecta"]
    if "odds" not in df.columns:
        return pd.DataFrame()
    if "date" in df.columns:
        filtered = df[df["date"].astype(str).str.slice(0, 10) == target_date].copy()
        if not filtered.empty:
            return filtered
    return df


def _resolve_fallback_csv_path(base_dir: Path | None) -> Path | None:
    if base_dir is None:
        return None
    candidates = [
        base_dir / "today_official_odds.csv",
        base_dir / "trifecta_odds.csv",
        base_dir / "today_trifecta_odds.csv",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _write_synthetic_odds_html(target: OddsFetchTarget, rows: pd.DataFrame) -> str:
    if rows.empty:
        return "<html><body><div>データがありません</div></body></html>"
    table_rows = []
    for _, row in rows.iterrows():
        combo = str(row.get("combo") or row.get("trifecta") or "")
        odds = row.get("odds")
        table_rows.append(f"<tr><td>{combo}</td><td>{odds if pd.notna(odds) else ''}</td></tr>")
    table = "\n".join(table_rows)
    return f"<html><body><h1>{target.race_id}</h1><table>{table}</table></body></html>"


def _write_index_html(path: Path, target_date: str, targets: list[OddsFetchTarget], fetch_mode: str, source_note: str) -> None:
    rows = "\n".join(
        f"<tr><td>{t.jcd}</td><td>{t.race_no}</td><td>{t.race_id}</td><td><a href=\"{t.url}\">odds</a></td></tr>"
        for t in targets
    )
    html = f"""<html><head><meta charset='utf-8'><title>{target_date} odds index</title></head>
<body>
<h1>{target_date} official odds index</h1>
<p>fetch_mode: {fetch_mode}</p>
<p>{source_note}</p>
<table>
<thead><tr><th>jcd</th><th>race_no</th><th>race_id</th><th>url</th></tr></thead>
<tbody>{rows}</tbody>
</table>
</body></html>"""
    _save_html(path, html)


def fetch_today_official_odds(
    *,
    target_date: str,
    timeout: float,
    retries: int,
    retry_sleep: float,
    request_interval: float,
    fallback_index_path: Path | None,
    fallback_odds_dir: Path | None,
    prediction_path: Path,
    race_card_path: Path,
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
    if not targets:
        targets = _targets_from_prediction_or_race_card(prediction_path, race_card_path, target_date)
        if targets:
            target_source = "local_prediction_or_race_card"
    if not targets and fallback_odds_dir is not None:
        fallback_csv = fallback_odds_dir / "trifecta_odds.csv"
        fallback_rows = _odds_rows_from_fallback_csv(fallback_csv, target_date)
        if not fallback_rows.empty:
            target_races = (
                fallback_rows[["race_id"]]
                .drop_duplicates()
                .assign(date=target_date)
            )
            if "race_id" in target_races.columns:
                for race_id in target_races["race_id"].astype(str).tolist():
                    try:
                        _, jcd, rno = race_id.rsplit("-", 2)
                        targets.append(
                            OddsFetchTarget(
                                race_id=canonical_race_id(target_date, int(jcd), int(rno)),
                                date=target_date,
                                jcd=f"{int(jcd):02d}",
                                race_no=int(rno),
                                url=build_odds_url(int(jcd), int(rno), date8),
                            )
                        )
                    except Exception:
                        continue
            target_source = "fallback_csv"

    if not targets:
        raise FileNotFoundError(
            "No odds targets could be determined. Provide live access, fallback HTML, or matching local prediction/race-card files."
        )

    index_path = out_dir / "index_page.html"
    if index_html:
        _save_html(index_path, index_html)
    else:
        _write_index_html(index_path, target_date, targets, index_mode, "No live index HTML was available.")

    odds_pages_dir = out_dir / "odds_pages"
    odds_pages_dir.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, Any]] = []
    fetched_pages = 0
    fallback_pages = 0
    for target in targets:
        html, page_mode = _load_or_fetch_odds_html(
            target,
            timeout=timeout,
            retries=retries,
            retry_sleep=retry_sleep,
            fallback_odds_dir=fallback_odds_dir,
        )
        if not html:
            continue
        if page_mode == "live":
            fetched_pages += 1
        else:
            fallback_pages += 1
        page_path = odds_pages_dir / f"{target.race_id}.html"
        _save_html(page_path, html)
        try:
            parsed_rows = parse_trifecta_odds_table(html, target.race_id)
        except Exception:
            fallback_csv = _resolve_fallback_csv_path(fallback_odds_dir)
            if fallback_csv and fallback_csv.exists():
                fallback_df = pd.read_csv(fallback_csv, low_memory=False)
                fallback_df["race_id"] = fallback_df["race_id"].astype(str).str.strip()
                parsed_rows = fallback_df[fallback_df["race_id"] == target.race_id].to_dict(orient="records")
            else:
                parsed_rows = []
        for row in parsed_rows:
            combo = row.get("combo") or row.get("trifecta")
            odds_value = row.get("odds")
            odds_status = row.get("odds_status", "ok" if pd.notna(odds_value) else "missing")
            records.append(
                {
                    "date": target.date,
                    "race_id": target.race_id,
                    "jcd": target.jcd,
                    "race_no": target.race_no,
                    "combo": combo,
                    "odds": odds_value,
                    "odds_status": odds_status,
                    "source_mode": page_mode,
                    "source_url": target.url,
                    "source_html_path": str(page_path),
                }
            )

    if not records and fallback_odds_dir is not None:
        fallback_csv = _resolve_fallback_csv_path(fallback_odds_dir)
        fallback_rows = _odds_rows_from_fallback_csv(fallback_csv, target_date)
        if not fallback_rows.empty:
            if "jcd" in fallback_rows.columns:
                fallback_rows["jcd"] = pd.to_numeric(fallback_rows["jcd"], errors="coerce")
            if "race_no" in fallback_rows.columns:
                fallback_rows["race_no"] = pd.to_numeric(fallback_rows["race_no"], errors="coerce")
            for target in targets:
                if {"jcd", "race_no"}.issubset(fallback_rows.columns):
                    subset = fallback_rows[
                        (fallback_rows["jcd"] == int(target.jcd))
                        & (fallback_rows["race_no"] == int(target.race_no))
                    ].copy()
                else:
                    subset = fallback_rows[fallback_rows["race_id"].astype(str) == target.race_id].copy()
                if subset.empty:
                    continue
                page_path = odds_pages_dir / f"{target.race_id}.html"
                _save_html(page_path, _write_synthetic_odds_html(target, subset))
                for _, row in subset.iterrows():
                    records.append(
                        {
                            "date": target.date,
                            "race_id": target.race_id,
                            "jcd": target.jcd,
                            "race_no": target.race_no,
                            "combo": row.get("combo") or row.get("trifecta"),
                            "odds": row.get("odds"),
                            "odds_status": row.get("odds_status", "ok"),
                            "source_mode": "fallback_csv",
                            "source_url": "",
                            "source_html_path": str(page_path),
                        }
                    )

    if not records:
        raise RuntimeError("No odds records could be produced.")

    odds_df = pd.DataFrame(records)
    odds_df["race_id"] = odds_df["race_id"].astype(str).str.strip()
    odds_df["combo"] = odds_df["combo"].astype(str)
    odds_df["odds"] = pd.to_numeric(odds_df["odds"], errors="coerce")
    odds_df = odds_df.drop_duplicates(subset=["race_id", "combo"], keep="last").sort_values(["race_id", "combo"]).reset_index(drop=True)

    csv_path = out_dir / "today_official_odds.csv"
    odds_df.to_csv(csv_path, index=False, encoding="utf-8")

    join_source = None
    join_frame = None
    for candidate in (prediction_path, race_card_path):
        if candidate.exists():
            try:
                join_frame = pd.read_csv(candidate, low_memory=False)
                join_source = candidate
                break
            except Exception:
                continue

    joinable_count = 0
    unmatched_count = 0
    if join_frame is not None and "race_id" in join_frame.columns:
        join_ids = set(join_frame["race_id"].astype(str).str.strip().dropna().tolist())
        odds_races = set(odds_df["race_id"].astype(str).str.strip().dropna().tolist())
        joinable_count = len(join_ids & odds_races)
        unmatched_count = len(odds_races - join_ids)
    else:
        joinable_count = int(odds_df["race_id"].nunique())
        unmatched_count = 0

    if index_mode == "live" and fetched_pages > 0:
        fetch_mode = "live"
    elif index_mode == "fallback_html" or fallback_pages > 0:
        fetch_mode = "fallback_html"
    elif target_source == "fallback_csv":
        fetch_mode = "fallback_csv"
    else:
        fetch_mode = "fallback"

    summary = {
        "target_date": target_date,
        "index_page_fetched": bool(index_html),
        "odds_pages_fetched": int(fetched_pages),
        "odds_pages_fallback": int(fallback_pages),
        "odds_records_count": int(len(odds_df)),
        "race_id_joinable_count": int(joinable_count),
        "race_id_unmatched_count": int(unmatched_count),
        "fetch_mode": fetch_mode,
        "target_source": target_source,
        "join_source": str(join_source) if join_source is not None else "",
        "output": {
            "index_page_html": str(index_path),
            "odds_pages_dir": str(odds_pages_dir),
            "csv": str(csv_path),
            "report_json": str(report_dir / "odds_ingest_summary.json"),
        },
        "notes": [
            "live fetch is attempted first",
            "saved HTML fallback is used when available",
            "local fallback_csv is used only when HTML is unavailable",
        ],
    }
    (report_dir / "odds_ingest_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    args = _parse_args()
    target_date = _normalize_date(args.date)
    summary = fetch_today_official_odds(
        target_date=target_date,
        timeout=float(args.timeout),
        retries=int(args.retries),
        retry_sleep=float(args.retry_sleep),
        request_interval=float(args.request_interval),
        fallback_index_path=Path(args.fallback_index_path) if args.fallback_index_path else None,
        fallback_odds_dir=Path(args.fallback_odds_dir) if args.fallback_odds_dir else None,
        prediction_path=Path(args.prediction_path),
        race_card_path=Path(args.race_card_path),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
