from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from datetime import date, datetime
from itertools import permutations
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from bs4 import BeautifulSoup

from src.data_fetch.fetch_official import SAVE_DIRS, fetch_and_save
from src.ingest.official_txt_parser import OfficialTxtParser
from src.utils.race_id import canonical_race_id, normalize_race_id


ROOT = Path(__file__).resolve().parents[2]
TODAY_RACES_CSV = ROOT / "data" / "processed" / "today_races.csv"
TODAY_WIN_PROBA_CSV = ROOT / "data" / "model_outputs" / "today_win_proba.csv"
SKIP_DECISIONS_CSV = ROOT / "data" / "strategy_outputs" / "skip_decisions.csv"
ODDS_ROOT = ROOT / "data" / "odds"
ENTRIES_DIR = ROOT / SAVE_DIRS["entries"]
DEFAULT_SOURCE = "https://www.boatrace.jp/owpc/pc/race/odds3t"
DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; boatrace-ai-mvp/1.0; +https://www.boatrace.jp/)"
UNPUBLISHED_PAGE_MARKERS = (
    "データがありません",
    "表示条件を変更してもう一度処理を行ってください",
    "表示条件を変更してもう一度",
    "ログインページ",
    "/owpc/pc/login",
)
CANCELLED_PAGE_MARKERS = (
    "開催中止",
    "中止",
    "発売中止",
    "打ち切り",
    "打切",
)

JCD_TO_STADIUM = {
    "01": "桐生",
    "02": "戸田",
    "03": "江戸川",
    "04": "平和島",
    "05": "多摩川",
    "06": "浜名湖",
    "07": "蒲郡",
    "08": "常滑",
    "09": "津",
    "10": "三国",
    "11": "びわこ",
    "12": "住之江",
    "13": "尼崎",
    "14": "鳴門",
    "15": "丸亀",
    "16": "児島",
    "17": "宮島",
    "18": "徳山",
    "19": "下関",
    "20": "若松",
    "21": "芦屋",
    "22": "福岡",
    "23": "唐津",
    "24": "大村",
}

ALL_TRIFECTA_COMBOS = ["-".join(map(str, combo)) for combo in permutations(range(1, 7), 3)]


@dataclass(frozen=True)
class FetchTarget:
    race_id: str
    date: str
    jcd: str
    stadium: str
    race_no: int

    @property
    def hd(self) -> str:
        return self.date.replace("-", "")

    @property
    def source_key(self) -> tuple[str, str, int]:
        return self.date, self.jcd, int(self.race_no)


def normalize_combo(value: str | tuple[int, int, int] | list[int]) -> str:
    if isinstance(value, (tuple, list)):
        return "-".join(str(int(v)) for v in value)
    parts = [p.strip() for p in str(value).replace(" ", "").split("-") if p.strip()]
    if len(parts) != 3:
        raise ValueError(f"invalid trifecta combo: {value}")
    return "-".join(str(int(p)) for p in parts)


def build_odds_url(jcd: str | int, race_no: int, hd: str) -> str:
    return f"{DEFAULT_SOURCE}?jcd={int(jcd):02d}&rno={int(race_no)}&hd={hd}"


def _normalize_odds_value(raw_text: str) -> tuple[float | None, str]:
    text = str(raw_text).strip().replace(",", "")
    if not text:
        return None, "missing"
    if text in {"-", "—", "―"}:
        return None, "not_offered"
    try:
        return float(text), "ok"
    except ValueError:
        return None, text


def _looks_like_unpublished_page(html: str) -> bool:
    text = html or ""
    if not text:
        return False
    return all(marker in text for marker in UNPUBLISHED_PAGE_MARKERS[:2]) and any(
        marker in text for marker in UNPUBLISHED_PAGE_MARKERS[2:]
    )


def _looks_like_cancelled_page(html: str) -> bool:
    text = html or ""
    if not text:
        return False
    return any(marker in text for marker in CANCELLED_PAGE_MARKERS)


def _today_jst_date() -> date:
    try:
        return pd.Timestamp.now(tz="Asia/Tokyo").date()
    except Exception:
        return datetime.now().date()


def _resolve_race_status(
    *,
    target_date: str,
    fetch_status: str,
    failed_reason: str,
    used_cache: bool,
    status_hint: str | None = None,
) -> tuple[str, str]:
    normalized_fetch_status = str(fetch_status or "").strip().lower()
    reason = str(failed_reason or "").strip()
    status_hint = str(status_hint or "").strip().lower()
    if status_hint in {"available", "unpublished", "fetch_failed", "finished", "cancelled"}:
        if status_hint in {"available", "finished"}:
            return status_hint, reason
        return status_hint, reason or status_hint
    lowered_reason = reason.lower()
    if any(marker.lower() in lowered_reason for marker in CANCELLED_PAGE_MARKERS):
        return "cancelled", reason or "race cancelled"
    if normalized_fetch_status == "pending_unpublished" or "real_odds_pending_unpublished" in lowered_reason:
        return "unpublished", reason or "real_odds_pending_unpublished"
    if normalized_fetch_status in {"failed", "error"}:
        return "fetch_failed", reason or "fetch failed"
    if normalized_fetch_status in {"success", "partial_missing", "cached"}:
        target_dt = pd.to_datetime(target_date, errors="coerce")
        if pd.isna(target_dt):
            return "available", reason
        return ("finished" if target_dt.date() < _today_jst_date() else "available"), reason
    if normalized_fetch_status:
        return "fetch_failed", reason or f"unexpected fetch_status:{normalized_fetch_status}"
    return "fetch_failed", reason or "fetch status missing"


def _is_retryable_table_not_ready_error(error: Exception | str) -> bool:
    text = str(error).lower()
    return (
        "unexpected row count 2" in text
        or "unexpected row count 1" in text
        or "no matching table structure" in text
    )


def _save_debug_html(target_date: str, target: FetchTarget, html: str) -> Path:
    debug_dir = ROOT / "reports" / "daily" / target_date / "odds_debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    path = debug_dir / f"failed_{target.jcd}{int(target.race_no):02d}.html"
    path.write_text(html, encoding="utf-8", errors="ignore")
    return path


def _parse_candidate_table(table: Any, race_id: str) -> dict[str, dict[str, Any]]:
    rows = table.find_all("tr")
    if len(rows) < 21:
        raise ValueError(f"{race_id}: unexpected row count {len(rows)}")

    header_cells = rows[0].find_all(["th", "td"])
    first_boats = [c.get_text(strip=True) for c in header_cells[0::2] if c.get_text(strip=True)]
    if len(first_boats) != 6:
        raise ValueError(f"{race_id}: header first-boat count {len(first_boats)} != 6")

    parsed: dict[str, dict[str, Any]] = {}
    data_rows = rows[1:21]
    if len(data_rows) != 20:
        raise ValueError(f"{race_id}: unexpected data row count {len(data_rows)}")

    for block_index in range(5):
        block = data_rows[block_index * 4 : (block_index + 1) * 4]
        row0 = [td.get_text(strip=True) for td in block[0].find_all("td")]
        if len(row0) != 18:
            raise ValueError(f"{race_id}: block {block_index} row0 len {len(row0)} != 18")

        second_by_col: dict[int, str] = {}
        for col in range(6):
            first = first_boats[col]
            second = row0[col * 3]
            third = row0[col * 3 + 1]
            odds_text = row0[col * 3 + 2]
            second_by_col[col] = second
            if len({first, second, third}) != 3:
                continue
            combo = normalize_combo((int(first), int(second), int(third)))
            odds_value, odds_status = _normalize_odds_value(odds_text)
            parsed[combo] = {"odds": odds_value, "odds_status": odds_status, "raw_odds_text": odds_text}

        for row in block[1:]:
            values = [td.get_text(strip=True) for td in row.find_all("td")]
            if len(values) != 12:
                raise ValueError(f"{race_id}: block {block_index} row len {len(values)} != 12")
            for col in range(6):
                first = first_boats[col]
                second = second_by_col[col]
                third = values[col * 2]
                odds_text = values[col * 2 + 1]
                if len({first, second, third}) != 3:
                    continue
                combo = normalize_combo((int(first), int(second), int(third)))
                odds_value, odds_status = _normalize_odds_value(odds_text)
                parsed[combo] = {"odds": odds_value, "odds_status": odds_status, "raw_odds_text": odds_text}

    return parsed


def parse_trifecta_odds_table(html: str, race_id: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        raise ValueError(f"{race_id}: odds table not found")

    best_records: dict[str, dict[str, Any]] | None = None
    errors: list[str] = []
    for table in tables:
        try:
            parsed = _parse_candidate_table(table, race_id)
        except Exception as exc:
            errors.append(str(exc))
            continue
        if best_records is None or len(parsed) > len(best_records):
            best_records = parsed

    if not best_records:
        detail = errors[0] if errors else "no matching table structure"
        raise ValueError(f"{race_id}: odds table not found ({detail})")

    records: list[dict[str, Any]] = []
    for combo in ALL_TRIFECTA_COMBOS:
        payload = best_records.get(combo, {"odds": None, "odds_status": "missing", "raw_odds_text": ""})
        records.append(
            {
                "race_id": race_id,
                "combo": combo,
                "odds": payload["odds"],
                "odds_status": payload["odds_status"],
                "raw_odds_text": payload["raw_odds_text"],
            }
        )
    return records


def parse_odds_table(html: str, race_id: str) -> list[dict[str, Any]]:
    rows = parse_trifecta_odds_table(html, race_id)
    return [{"race_id": row["race_id"], "trifecta": row["combo"], "odds": row["odds"]} for row in rows]


def fetch_html(
    session: requests.Session,
    url: str,
    timeout: float,
    retries: int,
    retry_sleep: float,
) -> str:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            return response.text
        except Exception as exc:
            last_error = exc
            if attempt >= retries:
                break
            time.sleep(retry_sleep * (attempt + 1))
    if last_error is None:
        raise RuntimeError(f"failed to fetch {url}")
    raise last_error


def _targets_from_today_races(target_date: str) -> list[FetchTarget]:
    if not TODAY_RACES_CSV.exists():
        return []
    df = pd.read_csv(TODAY_RACES_CSV, low_memory=False)
    required = {"race_id", "date", "jcd", "race_no"}
    if not required.issubset(df.columns):
        return []
    use_cols = sorted((required | {"venue"}) & set(df.columns))
    work = df[use_cols].copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    work = work[work["date"] == target_date].copy()
    if work.empty:
        return []
    work["jcd"] = pd.to_numeric(work["jcd"], errors="coerce")
    work["race_no"] = pd.to_numeric(work["race_no"], errors="coerce")
    work = work.dropna(subset=["jcd", "race_no"]).copy()
    work = work[work["jcd"].between(1, 24) & work["race_no"].between(1, 12)].copy()
    work["jcd"] = work["jcd"].astype(int).astype(str).str.zfill(2)
    work["race_no"] = work["race_no"].astype(int)
    work["race_id"] = work.apply(lambda r: canonical_race_id(target_date, r["jcd"], int(r["race_no"])), axis=1)
    work["stadium"] = work["jcd"].map(JCD_TO_STADIUM)
    work = work.drop_duplicates(subset=["date", "jcd", "race_no"]).sort_values(["jcd", "race_no"])
    return [
        FetchTarget(
            race_id=str(row.race_id),
            date=str(row.date),
            jcd=str(row.jcd),
            stadium=str(row.stadium),
            race_no=int(row.race_no),
        )
        for row in work.itertuples(index=False)
    ]


def _targets_from_demo_predictions(target_date: str) -> list[FetchTarget]:
    demo_path = ROOT / "reports" / "demo" / target_date / "demo_predictions.csv"
    if not demo_path.exists():
        return []
    try:
        df = pd.read_csv(demo_path, low_memory=False)
    except Exception:
        return []

    race_id_col = next((col for col in ("race_id", "レースID") if col in df.columns), None)
    date_col = next((col for col in ("date", "予測日") if col in df.columns), None)
    jcd_col = next((col for col in ("jcd", "場コード") if col in df.columns), None)
    race_no_col = next((col for col in ("race_no", "レース番号") if col in df.columns), None)
    if race_id_col is None:
        return []

    work_cols = [race_id_col]
    if date_col is not None:
        work_cols.append(date_col)
    if jcd_col is not None:
        work_cols.append(jcd_col)
    if race_no_col is not None:
        work_cols.append(race_no_col)
    work = df[work_cols].copy()
    if date_col is not None:
        work[date_col] = pd.to_datetime(work[date_col], errors="coerce").dt.strftime("%Y-%m-%d")
        work = work[work[date_col] == target_date].copy()
    if work.empty:
        return []

    rows: list[FetchTarget] = []
    seen: set[str] = set()
    for _, row in work.iterrows():
        raw_race_id = str(row.get(race_id_col, "") or "").strip()
        if not raw_race_id:
            continue
        try:
            normalized = normalize_race_id(raw_race_id)
        except Exception:
            if jcd_col is None or race_no_col is None:
                continue
            try:
                normalized = canonical_race_id(target_date, row.get(jcd_col), row.get(race_no_col))
            except Exception:
                continue
        if normalized in seen:
            continue
        try:
            date8, jcd, race_no = normalized.split("-")
            rows.append(
                FetchTarget(
                    race_id=normalized,
                    date=f"{date8[:4]}-{date8[4:6]}-{date8[6:8]}",
                    jcd=f"{int(jcd):02d}",
                    stadium=JCD_TO_STADIUM.get(f"{int(jcd):02d}", ""),
                    race_no=int(race_no),
                )
            )
            seen.add(normalized)
        except Exception:
            continue
    return sorted(rows, key=lambda t: (t.jcd, t.race_no))


def _targets_from_prediction_outputs(target_date: str) -> list[FetchTarget]:
    prediction_sources = [SKIP_DECISIONS_CSV, TODAY_WIN_PROBA_CSV]
    for path in prediction_sources:
        if not path.exists():
            continue
        try:
            df = pd.read_csv(path, low_memory=False)
        except Exception:
            continue
        if "race_id" not in df.columns:
            continue
        work = df[["race_id"] + ([ "date" ] if "date" in df.columns else [])].copy()
        if "date" in work.columns:
            work["date"] = pd.to_datetime(work["date"], errors="coerce").dt.strftime("%Y-%m-%d")
            work = work[work["date"] == target_date].copy()
        if work.empty:
            continue
        race_ids = sorted({str(rid).strip() for rid in work["race_id"].dropna().astype(str) if str(rid).strip()})
        targets: list[FetchTarget] = []
        for race_id in race_ids:
            parts = race_id.split("-")
            if len(parts) != 3:
                continue
            date8, jcd, race_no = parts
            if date8 != target_date.replace("-", ""):
                continue
            try:
                race_no_int = int(race_no)
            except ValueError:
                continue
            jcd_norm = str(int(jcd)).zfill(2)
            targets.append(
                FetchTarget(
                    race_id=race_id,
                    date=target_date,
                    jcd=jcd_norm,
                    stadium=JCD_TO_STADIUM.get(jcd_norm, ""),
                    race_no=race_no_int,
                )
            )
        if targets:
            targets = sorted(targets, key=lambda t: (t.jcd, t.race_no))
            return targets
    return []


def _targets_from_pending_unpublished_status(target_date: str) -> list[FetchTarget]:
    status_path = ODDS_ROOT / target_date.replace("-", "") / "race_status.csv"
    if not status_path.exists():
        return []
    try:
        df = pd.read_csv(status_path, low_memory=False)
    except Exception:
        return []
    required = {"race_id", "date", "jcd", "race_no", "fetch_status"}
    if not required.issubset(df.columns):
        return []
    work = df[list(required)].copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    work = work[(work["date"] == target_date) & (work["fetch_status"].astype(str).str.lower() == "pending_unpublished")].copy()
    if work.empty:
        return []
    work["jcd"] = pd.to_numeric(work["jcd"], errors="coerce")
    work["race_no"] = pd.to_numeric(work["race_no"], errors="coerce")
    work = work.dropna(subset=["jcd", "race_no"]).copy()
    work = work[work["jcd"].between(1, 24) & work["race_no"].between(1, 12)].copy()
    work["jcd"] = work["jcd"].astype(int).astype(str).str.zfill(2)
    work["race_no"] = work["race_no"].astype(int)
    work["race_id"] = work.apply(lambda r: canonical_race_id(target_date, r["jcd"], int(r["race_no"])), axis=1)
    work["stadium"] = work["jcd"].map(JCD_TO_STADIUM)
    work = work.drop_duplicates(subset=["date", "jcd", "race_no"]).sort_values(["jcd", "race_no"])
    return [
        FetchTarget(
            race_id=str(row.race_id),
            date=str(row.date),
            jcd=str(row.jcd),
            stadium=str(row.stadium),
            race_no=int(row.race_no),
        )
        for row in work.itertuples(index=False)
    ]


def _entry_txt_path(target_date: str) -> Path:
    dt = pd.to_datetime(target_date, errors="raise")
    return ENTRIES_DIR / f"B{dt.strftime('%y%m%d')}.TXT"


def _ensure_entry_txt(target_date: str) -> Path | None:
    path = _entry_txt_path(target_date)
    if path.exists():
        return path
    dt = pd.to_datetime(target_date, errors="raise").to_pydatetime()
    try:
        fetch_and_save("entries", dt, str(ENTRIES_DIR))
    except Exception:
        return path if path.exists() else None
    return path if path.exists() else None


def _targets_from_entry_txt(target_date: str) -> list[FetchTarget]:
    path = _ensure_entry_txt(target_date)
    if path is None or not path.exists():
        return []
    parser = OfficialTxtParser()
    parsed = parser.parse(str(path), raw_kind="kbn_txt")
    df = parsed["dataframe"].copy()
    for col in ("date", "jcd", "race_no"):
        if col not in df.columns:
            return []
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    df = df[df["date"] == target_date].copy()
    if df.empty:
        return []
    df["jcd"] = pd.to_numeric(df["jcd"], errors="coerce")
    df["race_no"] = pd.to_numeric(df["race_no"], errors="coerce")
    df = df.dropna(subset=["jcd", "race_no"]).copy()
    df = df[df["jcd"].between(1, 24) & df["race_no"].between(1, 12)].copy()
    df["jcd"] = df["jcd"].astype(int).astype(str).str.zfill(2)
    df["race_no"] = df["race_no"].astype(int)
    df["stadium"] = df["jcd"].map(JCD_TO_STADIUM)
    df["race_id"] = df.apply(lambda r: canonical_race_id(target_date, r["jcd"], int(r["race_no"])), axis=1)
    races = df[["race_id", "date", "jcd", "stadium", "race_no"]].drop_duplicates().sort_values(["jcd", "race_no"])
    return [
        FetchTarget(
            race_id=str(row.race_id),
            date=str(row.date),
            jcd=str(row.jcd),
            stadium=str(row.stadium),
            race_no=int(row.race_no),
        )
        for row in races.itertuples(index=False)
    ]


def build_targets(target_date: str, *, pending_only: bool = False) -> tuple[list[FetchTarget], str]:
    if pending_only:
        targets = _targets_from_pending_unpublished_status(target_date)
        if targets:
            return targets, "pending_unpublished_race_status"
    demo_targets = _targets_from_demo_predictions(target_date)
    if demo_targets:
        return demo_targets, "demo_predictions"
    targets = _targets_from_prediction_outputs(target_date)
    if targets:
        return targets, "prediction_outputs"
    targets = _targets_from_today_races(target_date)
    if targets:
        return targets, "today_races_csv"
    targets = _targets_from_entry_txt(target_date)
    if targets:
        return targets, "official_entries_txt"
    fallback = [
        FetchTarget(
            race_id=canonical_race_id(target_date, jcd, race_no),
            date=target_date,
            jcd=f"{jcd:02d}",
            stadium=JCD_TO_STADIUM[f"{jcd:02d}"],
            race_no=race_no,
        )
        for jcd in range(1, 25)
        for race_no in range(1, 13)
    ]
    return fallback, "venue_race_full_scan"


def save_daily_odds(
    rows: list[dict[str, Any]],
    target_date: str,
    report: dict[str, Any],
    race_targets: list[dict[str, Any]],
    failures: list[dict[str, Any]],
) -> dict[str, Path]:
    date_key = target_date.replace("-", "")
    out_dir = ODDS_ROOT / date_key
    out_dir.mkdir(parents=True, exist_ok=True)

    odds_path = out_dir / "trifecta_odds.csv"
    report_path = out_dir / "fetch_report.json"
    targets_path = out_dir / "race_targets.csv"
    race_status_path = out_dir / "race_status.csv"
    failures_path = out_dir / "failures.csv"
    legacy_failures_path = out_dir / "failed_races.csv"
    latest_path = ODDS_ROOT / "today_trifecta_odds.csv"
    latest_failures_path = ODDS_ROOT / "today_trifecta_odds_failures.csv"
    latest_race_status_path = ODDS_ROOT / "today_trifecta_odds_race_status.csv"
    live_odds_path = ROOT / "data" / "strategy_outputs" / "live_odds.csv"

    odds_columns = [
        "race_id",
        "date",
        "jcd",
        "stadium",
        "race_no",
        "combo",
        "odds",
        "fetched_at",
        "source",
        "source_url",
        "odds_status",
        "raw_odds_text",
    ]
    target_columns = [
        "race_id",
        "date",
        "jcd",
        "stadium",
        "race_no",
        "target_source",
        "fetch_status",
        "status",
        "status_reason",
        "used_cache",
        "missing_odds_cells",
        "failed_reason",
        "fetched_at",
        "source_url",
    ]
    failure_columns = [
        "race_id",
        "date",
        "jcd",
        "stadium",
        "race_no",
        "fetch_status",
        "status",
        "status_reason",
        "source_url",
        "error",
    ]
    live_columns = ["race_id", "trifecta", "odds", "odds_source", "fetched_at", "source_url"]
    target_lookup = {
        str(row["race_id"]): {
            "odds_fetch_status": str(row.get("fetch_status", "") or ""),
            "odds_fetch_status_normalized": str(row.get("status", "") or ""),
            "odds_fetch_status_reason": str(row.get("status_reason", "") or ""),
            "odds_fetch_used_cache": bool(row.get("used_cache", False)),
            "odds_missing_odds_cells": int(row.get("missing_odds_cells", 0) or 0),
            "odds_target_source": str(row.get("target_source", "") or ""),
            "odds_fetch_failed_reason": str(row.get("failed_reason", "") or ""),
        }
        for row in race_targets
        if str(row.get("race_id", "")).strip()
    }

    df = pd.DataFrame(rows, columns=odds_columns)
    if not df.empty:
        meta_df = pd.DataFrame(
            [
                {"race_id": race_id, **meta}
                for race_id, meta in target_lookup.items()
            ]
        )
        df = df.merge(meta_df, on="race_id", how="left")
    if not df.empty:
        df = df.sort_values(["race_id", "combo"]).reset_index(drop=True)
    df.to_csv(odds_path, index=False)
    df.to_csv(latest_path, index=False)

    live_df = pd.DataFrame(
        [
            {
                "race_id": row["race_id"],
                "trifecta": row["combo"],
                "odds": row["odds"],
                "odds_source": "real_live" if pd.notna(row["odds"]) else "missing",
                "fetched_at": row["fetched_at"],
                "source_url": row["source_url"],
                "odds_fetch_status": target_lookup.get(str(row["race_id"]), {}).get("odds_fetch_status", ""),
                "odds_fetch_used_cache": target_lookup.get(str(row["race_id"]), {}).get("odds_fetch_used_cache", False),
                "odds_missing_odds_cells": target_lookup.get(str(row["race_id"]), {}).get("odds_missing_odds_cells", 0),
            }
            for row in rows
        ],
        columns=live_columns + ["odds_fetch_status", "odds_fetch_used_cache", "odds_missing_odds_cells"],
    )
    if live_df.empty:
        live_df = pd.DataFrame(columns=live_columns)
    else:
        live_df = live_df.sort_values(["race_id", "trifecta"]).drop_duplicates(
            subset=["race_id", "trifecta"], keep="last"
        )
    live_odds_path.parent.mkdir(parents=True, exist_ok=True)
    live_df.to_csv(live_odds_path, index=False)

    pd.DataFrame(race_targets, columns=target_columns).to_csv(targets_path, index=False)
    pd.DataFrame(race_targets, columns=target_columns).to_csv(race_status_path, index=False)
    pd.DataFrame(failures, columns=failure_columns).to_csv(failures_path, index=False)
    pd.DataFrame(failures, columns=failure_columns).to_csv(legacy_failures_path, index=False)
    pd.DataFrame(failures, columns=failure_columns).to_csv(latest_failures_path, index=False)
    pd.DataFrame(race_targets, columns=target_columns).to_csv(latest_race_status_path, index=False)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "odds_path": odds_path,
        "report_path": report_path,
        "targets_path": targets_path,
        "race_status_path": race_status_path,
        "failures_path": failures_path,
        "legacy_failures_path": legacy_failures_path,
        "latest_path": latest_path,
        "live_odds_path": live_odds_path,
    }


def _load_existing_odds_rows(target_date: str) -> pd.DataFrame:
    path = ODDS_ROOT / target_date.replace("-", "") / "trifecta_odds.csv"
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except Exception:
        return pd.DataFrame()


def _complete_cached_races(existing_df: pd.DataFrame) -> set[str]:
    if existing_df.empty or "race_id" not in existing_df.columns:
        return set()
    work = existing_df.copy()
    work["odds"] = pd.to_numeric(work.get("odds"), errors="coerce")
    counts = work.groupby("race_id").agg(row_count=("race_id", "size"), missing_count=("odds", lambda s: int(s.isna().sum())))
    return {
        str(race_id)
        for race_id, row in counts.iterrows()
        if int(row["row_count"]) >= 120 and int(row["missing_count"]) == 0
    }


def run_for_date(
    target_date: str,
    timeout: float = 15.0,
    retries: int = 2,
    retry_sleep: float = 1.5,
    request_interval: float = 0.6,
    settle_retry_sleep: float = 0.0,
    settle_retry_rounds: int = 0,
    pending_retry_rounds: int = 0,
    pending_retry_sleep: float = 0.0,
    unpublished_retry_rounds: int = 1,
    unpublished_retry_sleep: float = 180.0,
    pending_only: bool = False,
    refresh: bool = False,
) -> dict[str, Any]:
    pd.to_datetime(target_date, errors="raise")
    targets, target_source = build_targets(target_date, pending_only=pending_only)
    session = requests.Session()
    session.headers.update({"User-Agent": DEFAULT_USER_AGENT})

    fetched_at = datetime.now().isoformat(timespec="seconds")
    row_map: dict[tuple[str, str], dict[str, Any]] = {}
    target_status_map: dict[str, dict[str, Any]] = {}
    failure_map: dict[str, dict[str, Any]] = {}
    target_lookup = {target.race_id: target for target in targets}
    existing_df = _load_existing_odds_rows(target_date) if not refresh else pd.DataFrame()
    cached_race_ids = _complete_cached_races(existing_df)
    if not existing_df.empty and "race_id" in existing_df.columns:
        for _, row in existing_df[existing_df["race_id"].astype(str).isin(cached_race_ids)].iterrows():
            row_dict = row.to_dict()
            row_map[(str(row_dict.get("race_id", "")), str(row_dict.get("combo", "")))] = row_dict

    def _record_race_status(
        target: FetchTarget,
        *,
        fetch_status: str,
        used_cache: bool,
        missing_odds_cells: int,
        failed_reason: str,
        url: str,
        status_hint: str | None = None,
    ) -> None:
        status, status_reason = _resolve_race_status(
            target_date=target.date,
            fetch_status=fetch_status,
            failed_reason=failed_reason,
            used_cache=used_cache,
            status_hint=status_hint,
        )
        target_status_map[target.race_id] = {
            "race_id": target.race_id,
            "date": target.date,
            "jcd": target.jcd,
            "stadium": target.stadium,
            "race_no": int(target.race_no),
            "target_source": target_source,
            "fetch_status": fetch_status,
            "status": status,
            "status_reason": status_reason,
            "used_cache": used_cache,
            "missing_odds_cells": int(missing_odds_cells),
            "failed_reason": failed_reason,
            "fetched_at": fetched_at,
            "source_url": url,
        }
        if status in {"unpublished", "fetch_failed", "cancelled"}:
            failure_map[target.race_id] = {
                "race_id": target.race_id,
                "date": target.date,
                "jcd": target.jcd,
                "stadium": target.stadium,
                "race_no": int(target.race_no),
                "fetch_status": fetch_status,
                "status": status,
                "status_reason": status_reason,
                "source_url": url,
                "error": status_reason or failed_reason,
            }
        else:
            failure_map.pop(target.race_id, None)

    def _fetch_round(target_subset: list[FetchTarget], *, request_interval_local: float) -> None:
        for index, target in enumerate(target_subset):
            url = build_odds_url(target.jcd, target.race_no, target.hd)
            if target.race_id in cached_race_ids and target.race_id not in target_status_map:
                _record_race_status(
                    target,
                    fetch_status="cached",
                    used_cache=True,
                    missing_odds_cells=0,
                    failed_reason="",
                    url=url,
                )
                continue
            try:
                html = fetch_html(session, url=url, timeout=timeout, retries=retries, retry_sleep=retry_sleep)
                if _looks_like_unpublished_page(html):
                    _save_debug_html(target_date, target, html)
                    _record_race_status(
                        target,
                        fetch_status="pending_unpublished",
                        used_cache=False,
                        missing_odds_cells=120,
                        failed_reason="real_odds_pending_unpublished:データがありません",
                        url=url,
                        status_hint="unpublished",
                    )
                    if request_interval_local > 0 and index < len(target_subset) - 1:
                        time.sleep(request_interval_local)
                    continue
                if _looks_like_cancelled_page(html):
                    _save_debug_html(target_date, target, html)
                    _record_race_status(
                        target,
                        fetch_status="failed",
                        used_cache=False,
                        missing_odds_cells=120,
                        failed_reason="real_odds_cancelled:開催中止",
                        url=url,
                        status_hint="cancelled",
                    )
                    if request_interval_local > 0 and index < len(target_subset) - 1:
                        time.sleep(request_interval_local)
                    continue
                parsed_rows = parse_trifecta_odds_table(html, target.race_id)
                missing_count = 0
                for record in parsed_rows:
                    odds_value = record["odds"]
                    if odds_value is None:
                        missing_count += 1
                    row_map[(target.race_id, record["combo"])] = {
                        "race_id": target.race_id,
                        "date": target.date,
                        "jcd": target.jcd,
                        "stadium": target.stadium,
                        "race_no": int(target.race_no),
                        "combo": record["combo"],
                        "odds": odds_value,
                        "fetched_at": fetched_at,
                        "source": DEFAULT_SOURCE,
                        "source_url": url,
                        "odds_status": record["odds_status"],
                        "raw_odds_text": record["raw_odds_text"],
                    }
                _record_race_status(
                    target,
                    fetch_status="success" if missing_count == 0 else "partial_missing",
                    used_cache=False,
                    missing_odds_cells=int(missing_count),
                    failed_reason="",
                    url=url,
                    status_hint=None,
                )
            except Exception as exc:
                reason = str(exc)
                if _is_retryable_table_not_ready_error(exc):
                    _save_debug_html(target_date, target, html if "html" in locals() else reason)
                    _record_race_status(
                        target,
                        fetch_status="pending_unpublished",
                        used_cache=False,
                        missing_odds_cells=120,
                        failed_reason=f"real_odds_pending_unpublished:{reason}",
                        url=url,
                        status_hint="unpublished",
                    )
                else:
                    _record_race_status(
                        target,
                        fetch_status="failed",
                        used_cache=False,
                        missing_odds_cells=120,
                        failed_reason=reason,
                        url=url,
                        status_hint="fetch_failed",
                    )
            if request_interval_local > 0 and index < len(target_subset) - 1:
                time.sleep(request_interval_local)

    _fetch_round(list(targets), request_interval_local=request_interval)

    retry_targets = [
        FetchTarget(
            race_id=str(row["race_id"]),
            date=str(row["date"]),
            jcd=str(row["jcd"]),
            stadium=str(row["stadium"]),
            race_no=int(row["race_no"]),
        )
        for row in target_status_map.values()
        if str(row.get("fetch_status", "")).lower() in {"failed", "partial_missing"}
    ]
    for round_index in range(int(max(0, settle_retry_rounds))):
        if not retry_targets:
            break
        if settle_retry_sleep > 0:
            time.sleep(settle_retry_sleep)
        _fetch_round(retry_targets, request_interval_local=request_interval)
        retry_targets = [
            FetchTarget(
                race_id=str(row["race_id"]),
                date=str(row["date"]),
                jcd=str(row["jcd"]),
                stadium=str(row["stadium"]),
                race_no=int(row["race_no"]),
            )
        for row in target_status_map.values()
        if str(row.get("fetch_status", "")).lower() in {"failed", "partial_missing"}
    ]

    def _pending_table_targets() -> list[FetchTarget]:
        selected: list[FetchTarget] = []
        for race_id, row in target_status_map.items():
            fetch_status = str(row.get("fetch_status", "")).lower()
            reason = str(row.get("failed_reason", "")).lower()
            if fetch_status == "failed" and (
                "odds table not found" in reason
                or "real_odds_pending_unpublished" in reason
            ):
                target = target_lookup.get(race_id)
                if target:
                    selected.append(target)
        return selected

    pending_targets = _pending_table_targets()
    for round_index in range(int(max(0, pending_retry_rounds))):
        if not pending_targets:
            break
        if pending_retry_sleep > 0:
            time.sleep(pending_retry_sleep)
        _fetch_round(pending_targets, request_interval_local=request_interval)
        pending_targets = _pending_table_targets()

    unpublished_targets = [
        FetchTarget(
            race_id=str(row["race_id"]),
            date=str(row["date"]),
            jcd=str(row["jcd"]),
            stadium=str(row["stadium"]),
            race_no=int(row["race_no"]),
        )
        for row in target_status_map.values()
        if str(row.get("fetch_status", "")).lower() == "pending_unpublished"
    ]
    for round_index in range(int(max(0, unpublished_retry_rounds))):
        if not unpublished_targets:
            break
        if unpublished_retry_sleep > 0:
            time.sleep(unpublished_retry_sleep)
        _fetch_round(unpublished_targets, request_interval_local=request_interval)
        unpublished_targets = [
            FetchTarget(
                race_id=str(row["race_id"]),
                date=str(row["date"]),
                jcd=str(row["jcd"]),
                stadium=str(row["stadium"]),
                race_no=int(row["race_no"]),
            )
            for row in target_status_map.values()
            if str(row.get("fetch_status", "")).lower() == "pending_unpublished"
        ]

    rows = list(row_map.values())
    race_targets_rows = list(target_status_map.values())
    failures = list(failure_map.values())
    status_counts = pd.Series([str(row.get("status", "")).lower() for row in race_targets_rows], dtype="object")
    available_races = int(status_counts.isin({"available"}).sum())
    finished_races = int(status_counts.isin({"finished"}).sum())
    unpublished_races = int(status_counts.eq("unpublished").sum())
    fetch_failed_races = int(status_counts.eq("fetch_failed").sum())
    cancelled_races = int(status_counts.eq("cancelled").sum())
    success_races = int(available_races + finished_races)
    complete_races = sum(
        1
        for row in race_targets_rows
        if str(row.get("fetch_status", "")) in {"success", "cached"} and int(row.get("missing_odds_cells", 0) or 0) == 0
    )
    total_missing = sum(int(row.get("missing_odds_cells", 0) or 0) for row in race_targets_rows if str(row.get("fetch_status", "")) in {"success", "partial_missing"})
    fail_reasons = pd.Series([f["error"] for f in failures], dtype="object")
    report = {
        "status": "ok",
        "target_date": target_date,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "target_source": target_source,
        "pending_only": bool(pending_only),
        "request_policy": {
            "timeout_seconds": timeout,
            "retries": retries,
            "retry_sleep_seconds": retry_sleep,
            "request_interval_seconds": request_interval,
            "settle_retry_sleep_seconds": settle_retry_sleep,
            "settle_retry_rounds": int(settle_retry_rounds),
            "pending_retry_sleep_seconds": pending_retry_sleep,
            "pending_retry_rounds": int(pending_retry_rounds),
            "user_agent": DEFAULT_USER_AGENT,
            "refresh": refresh,
        },
        "target_races": int(len(targets)),
        "success_races": int(success_races),
        "available_races": int(available_races),
        "finished_races": int(finished_races),
        "unpublished_races": int(unpublished_races),
        "fetch_failed_races": int(fetch_failed_races),
        "cancelled_races": int(cancelled_races),
        "failed_races": int(len(failures)),
        "pending_unpublished_races": int(unpublished_races),
        "complete_120_races": int(complete_races),
        "missing_odds_cells": int(total_missing),
        "cached_races": int(sum(1 for row in race_targets_rows if row["fetch_status"] == "cached")),
        "failed_race_ids": [f["race_id"] for f in failures if str(f.get("status", "")).lower() == "fetch_failed"],
        "pending_unpublished_race_ids": [
            str(row["race_id"]) for row in race_targets_rows if str(row.get("status", "")).lower() == "unpublished"
        ],
        "cancelled_race_ids": [
            str(row["race_id"]) for row in race_targets_rows if str(row.get("status", "")).lower() == "cancelled"
        ],
        "status_counts": status_counts.value_counts().to_dict() if not status_counts.empty else {},
        "failure_reason_counts": fail_reasons.value_counts().to_dict() if not fail_reasons.empty else {},
        "sample_failure_reasons": failures[:20],
    }
    output_paths = save_daily_odds(rows, target_date, report, race_targets_rows, failures)
    report["output"] = {
        "csv": str(output_paths["odds_path"]),
        "targets_csv": str(output_paths["targets_path"]),
        "race_status_csv": str(output_paths["race_status_path"]),
        "failures_csv": str(output_paths["failures_path"]),
        "failed_races_csv": str(output_paths["legacy_failures_path"]),
        "report_json": str(output_paths["report_path"]),
        "latest_csv": str(output_paths["latest_path"]),
    }
    output_paths["report_path"].write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch daily BOAT RACE trifecta odds and save them as CSV.")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="Target date in YYYY-MM-DD. Default: today.")
    parser.add_argument("--timeout", type=float, default=15.0, help="Per-request timeout seconds.")
    parser.add_argument("--retries", type=int, default=2, help="Retry count per race.")
    parser.add_argument("--retry-sleep", type=float, default=1.5, help="Base sleep seconds between retries.")
    parser.add_argument("--request-interval", type=float, default=0.6, help="Sleep seconds between race requests.")
    parser.add_argument("--settle-retry-rounds", type=int, default=0, help="Additional delayed retry rounds for pending races.")
    parser.add_argument("--settle-retry-sleep", type=float, default=0.0, help="Sleep seconds before delayed retry rounds.")
    parser.add_argument(
        "--pending-retry-rounds",
        type=int,
        default=0,
        help="Extra re-fetch rounds for races that raise 'odds table not found'.",
    )
    parser.add_argument(
        "--pending-retry-sleep",
        type=float,
        default=0.0,
        help="Sleep seconds before each pending-target retry round.",
    )
    parser.add_argument(
        "--unpublished-retry-rounds",
        type=int,
        default=1,
        help="Extra delayed re-fetch rounds for races that look unpublished.",
    )
    parser.add_argument(
        "--unpublished-retry-sleep",
        type=float,
        default=180.0,
        help="Sleep seconds before the unpublished-page retry round.",
    )
    parser.add_argument(
        "--pending-only",
        action="store_true",
        help="Only refetch races previously marked pending_unpublished for the target date.",
    )
    parser.add_argument("--refresh", action="store_true", help="Ignore cached daily odds and refetch all races.")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    report = run_for_date(
        target_date=str(args.date),
        timeout=float(args.timeout),
        retries=int(args.retries),
        retry_sleep=float(args.retry_sleep),
        request_interval=float(args.request_interval),
        settle_retry_sleep=float(args.settle_retry_sleep),
        settle_retry_rounds=int(args.settle_retry_rounds),
        pending_retry_sleep=float(args.pending_retry_sleep),
        pending_retry_rounds=int(args.pending_retry_rounds),
        unpublished_retry_rounds=int(args.unpublished_retry_rounds),
        unpublished_retry_sleep=float(args.unpublished_retry_sleep),
        pending_only=bool(args.pending_only),
        refresh=bool(args.refresh),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
