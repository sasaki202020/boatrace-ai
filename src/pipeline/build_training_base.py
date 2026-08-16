from __future__ import annotations

import argparse
import json
import logging
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from src.data.parse_official_entries_html import parse_official_entries_html
from src.ingest.official_txt_parser import OfficialTxtParser
from src.core.ids import canonical_race_id, canonical_race_key, canonical_snapshot_ts


logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_DIR = ROOT / "data"
DEFAULT_REPORT_DIR = ROOT / "reports" / "data_base"
DEFAULT_FEATURE_AVAILABILITY_PATH = ROOT / "data" / "metadata" / "feature_availability.csv"
DEFAULT_PROGRAM_CSV = ROOT / "data" / "csv" / "program" / "program.csv"
DEFAULT_RESULT_CSV = ROOT / "data" / "csv" / "result" / "result.csv"

ENTRY_KEY_COLS = ["race_date", "jcd", "race_no", "lane"]
RESULT_KEY_COLS = ["race_date", "jcd", "race_no", "lane"]

ENTRY_COLUMNS = [
    "race_date",
    "jcd",
    "race_no",
    "lane",
    "race_id",
    "race_key",
    "boat_key",
    "player_id",
    "player_name",
    "class",
    "branch",
    "age",
    "weight",
    "avg_st",
    "nat_win_rate",
    "local_win_rate",
    "motor_no",
    "motor_rate",
    "boat_no",
    "boat_rate",
    "fl",
    "source_file",
    "ingest_ts",
]

PRE_RACE_COLUMNS = [
    "race_date",
    "jcd",
    "race_no",
    "lane",
    "race_id",
    "race_key",
    "boat_key",
    "player_id",
    "player_name",
    "class",
    "branch",
    "age",
    "weight",
    "avg_st",
    "nat_win_rate",
    "local_win_rate",
    "motor_no",
    "motor_rate",
    "boat_no",
    "boat_rate",
    "fl",
    "snapshot_time",
    "exhibition_time",
    "exhibition_type",
    "body_weight",
    "weather",
    "wind_speed",
    "wave_height",
    "source_file",
    "ingest_ts",
]

RESULT_COLUMNS = [
    "race_date",
    "jcd",
    "race_no",
    "lane",
    "race_id",
    "race_key",
    "boat_key",
    "finish_position",
    "is_win",
    "is_top2",
    "is_top3",
    "winning_trifecta",
    "payout_trifecta",
    "source_file",
    "ingest_ts",
]

PRE_RACE_FEATURE_COLUMNS = [
    "race_date",
    "jcd",
    "race_no",
    "lane",
    "race_id",
    "race_key",
    "boat_key",
    "player_id",
    "player_name",
    "class",
    "branch",
    "age",
    "weight",
    "avg_st",
    "nat_win_rate",
    "local_win_rate",
    "motor_no",
    "motor_rate",
    "boat_no",
    "boat_rate",
    "fl",
    "snapshot_time",
    "exhibition_time",
    "exhibition_type",
    "body_weight",
    "weather",
    "wind_speed",
    "wave_height",
]

TRAINING_LABEL_COLUMNS = [
    "finish_position",
    "is_win",
    "is_top2",
    "is_top3",
    "winning_trifecta",
    "payout_trifecta",
]

RESULT_PHASE_COLUMNS = set(TRAINING_LABEL_COLUMNS)


def _read_csv_any_encoding(path: Path) -> pd.DataFrame:
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "cp932"):
        try:
            return pd.read_csv(path, encoding=encoding, low_memory=False)
        except UnicodeDecodeError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    return pd.read_csv(path, low_memory=False)


def _normalize_date_text(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return pd.to_datetime(text, errors="raise").date().isoformat()
    except Exception:
        digits = "".join(ch for ch in text if ch.isdigit())
        if len(digits) >= 8:
            return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
        if len(digits) == 6:
            return f"20{digits[:2]}-{digits[2:4]}-{digits[4:6]}"
        return None


def _normalize_int(value: object) -> int | None:
    if value is None or pd.isna(value):
        return None
    try:
        parsed = pd.to_numeric(value, errors="coerce")
        if pd.isna(parsed):
            return None
        return int(parsed)
    except Exception:
        return None


def _file_mtime_iso(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
    except Exception:
        return datetime.now().isoformat(timespec="seconds")


def _relative_path(path: Path) -> str:
    # Keep provenance stable when a test or temporary workspace is nested under the repo.
    parts = path.parts
    for anchor in ("data", "raw"):
        if anchor in parts:
            idx = parts.index(anchor)
            return Path(*parts[idx:]).as_posix()
    try:
        return str(path.relative_to(ROOT))
    except Exception:
        return path.as_posix()


def _infer_date_from_path(path: Path) -> str | None:
    candidates = [path.stem, path.parent.name, path.parent.parent.name if path.parent.parent != path.parent else ""]
    for token in candidates:
        if not token:
            continue
        digits = "".join(ch for ch in token if ch.isdigit())
        if len(digits) >= 8:
            return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
        if len(digits) == 6:
            return f"20{digits[:2]}-{digits[2:4]}-{digits[4:6]}"
    return None


def _date_in_range(date_text: str | None, start_date: str | None, end_date: str | None) -> bool:
    if date_text is None:
        return True
    if start_date and date_text < start_date:
        return False
    if end_date and date_text > end_date:
        return False
    return True


def _path_date_matches(path: Path, start_date: str | None, end_date: str | None) -> bool:
    inferred = _infer_date_from_path(path)
    return _date_in_range(inferred, start_date, end_date)


def _make_keys(race_date: str | None, jcd: object, race_no: object, lane: object) -> dict[str, object]:
    if race_date is None:
        return {
            "race_date": None,
            "race_id": None,
            "race_key": None,
            "boat_key": None,
        }
    jcd_int = _normalize_int(jcd)
    race_no_int = _normalize_int(race_no)
    lane_int = _normalize_int(lane)
    if jcd_int is None or race_no_int is None:
        return {
            "race_date": race_date,
            "race_id": None,
            "race_key": None,
            "boat_key": None,
        }
    race_id = canonical_race_id(race_date, jcd_int, race_no_int)
    race_key = canonical_race_key(race_date, jcd_int, race_no_int)
    boat_key = f"{race_id}-L{lane_int:02d}" if lane_int is not None else None
    return {
        "race_date": race_date,
        "race_id": race_id,
        "race_key": race_key,
        "boat_key": boat_key,
    }


def _build_fl_value(f_count: object, l_count: object) -> object:
    f_val = _normalize_int(f_count)
    l_val = _normalize_int(l_count)
    if f_val is None and l_val is None:
        return pd.NA
    return f"F{f_val if f_val is not None else 0}L{l_val if l_val is not None else 0}"


def _coerce_numeric(series: pd.Series | None) -> pd.Series:
    if series is None:
        return pd.Series(dtype="float64")
    return pd.to_numeric(series, errors="coerce")


def _source_frame_priority(path: Path) -> int:
    text = str(path).lower()
    if "web_entries" in text:
        return 0
    if "program" in text:
        return 1
    if text.endswith(".txt") and ("/b" in text.replace("\\", "/") or "\\b" in text.lower()):
        return 2
    if "entries" in text:
        return 2
    if "results" in text:
        return 0
    return 3


def _load_program_entries(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=ENTRY_COLUMNS + PRE_RACE_COLUMNS)
    df = _read_csv_any_encoding(path)
    if df.empty:
        return pd.DataFrame(columns=ENTRY_COLUMNS + PRE_RACE_COLUMNS)

    records: list[dict[str, object]] = []
    ingest_ts = _file_mtime_iso(path)
    source_file = _relative_path(path)
    for _, row in df.iterrows():
        race_date = _normalize_date_text(row.get("date"))
        jcd = _normalize_int(row.get("jcd"))
        race_no = _normalize_int(row.get("race_no"))
        lane = _normalize_int(row.get("lane"))
        if race_date is None or jcd is None or race_no is None or lane is None:
            logger.warning(
                "program row skipped due to missing key fields: file=%s date=%s jcd=%s race_no=%s lane=%s",
                source_file,
                row.get("date"),
                row.get("jcd"),
                row.get("race_no"),
                row.get("lane"),
            )
            continue
        keys = _make_keys(race_date, jcd, race_no, lane)
        records.append(
            {
                **keys,
                "jcd": jcd,
                "race_no": race_no,
                "lane": lane,
                "player_id": _normalize_int(row.get("toban")),
                "player_name": row.get("name"),
                "class": row.get("grade"),
                "branch": pd.NA,
                "age": _normalize_int(row.get("age")),
                "weight": pd.to_numeric(row.get("weight"), errors="coerce"),
                "avg_st": pd.to_numeric(row.get("avg_st"), errors="coerce"),
                "nat_win_rate": pd.to_numeric(row.get("win_rate_all"), errors="coerce"),
                "local_win_rate": pd.to_numeric(row.get("win_rate_venue"), errors="coerce"),
                "motor_no": _normalize_int(row.get("motor_no")),
                "motor_rate": pd.to_numeric(row.get("motor_win_rate"), errors="coerce"),
                "boat_no": _normalize_int(row.get("boat_no")),
                "boat_rate": pd.to_numeric(row.get("boat_win_rate"), errors="coerce"),
                "fl": _build_fl_value(row.get("f_count"), row.get("l_count")),
                "snapshot_time": ingest_ts,
                "exhibition_time": pd.to_numeric(row.get("tenji_time"), errors="coerce"),
                "exhibition_type": "tenji_time" if pd.notna(row.get("tenji_time")) else pd.NA,
                "body_weight": pd.to_numeric(row.get("weight"), errors="coerce"),
                "weather": pd.NA,
                "wind_speed": pd.NA,
                "wave_height": pd.NA,
                "source_file": source_file,
                "ingest_ts": ingest_ts,
            }
        )
    return pd.DataFrame.from_records(records)


def _load_html_entries(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=ENTRY_COLUMNS + PRE_RACE_COLUMNS)
    html = path.read_text(encoding="utf-8", errors="ignore")
    try:
        date_from_path = _infer_date_from_path(path)
        if date_from_path is None:
            return pd.DataFrame(columns=ENTRY_COLUMNS + PRE_RACE_COLUMNS)
        file_name = path.stem
        match = re.search(r"(\d{8})-(\d{2})-(\d{2})$", file_name)
        if not match:
            return pd.DataFrame(columns=ENTRY_COLUMNS + PRE_RACE_COLUMNS)
        target_date, jcd_text, race_no_text = match.groups()
        parsed = parse_official_entries_html(html, target_date=f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:8]}", jcd=jcd_text, race_no=int(race_no_text))
    except Exception as exc:
        logger.warning("html parse failed for %s: %s", path, exc)
        return pd.DataFrame(columns=ENTRY_COLUMNS + PRE_RACE_COLUMNS)

    records: list[dict[str, object]] = []
    ingest_ts = _file_mtime_iso(path)
    source_file = _relative_path(path)
    for _, row in parsed.iterrows():
        race_date = _normalize_date_text(row.get("date"))
        jcd = _normalize_int(row.get("jcd"))
        race_no = _normalize_int(row.get("race_no"))
        lane = _normalize_int(row.get("lane"))
        if race_date is None or jcd is None or race_no is None or lane is None:
            logger.warning(
                "html entry row skipped due to missing key fields: file=%s date=%s jcd=%s race_no=%s lane=%s",
                source_file,
                row.get("date"),
                row.get("jcd"),
                row.get("race_no"),
                row.get("lane"),
            )
            continue
        keys = _make_keys(race_date, jcd, race_no, lane)
        records.append(
            {
                **keys,
                "jcd": jcd,
                "race_no": race_no,
                "lane": lane,
                "player_id": _normalize_int(row.get("racer_id")),
                "player_name": row.get("racer_name"),
                "class": row.get("racer_class"),
                "branch": row.get("branch"),
                "age": _normalize_int(row.get("age")),
                "weight": pd.to_numeric(row.get("weight"), errors="coerce"),
                "avg_st": pd.to_numeric(row.get("avg_st"), errors="coerce"),
                "nat_win_rate": pd.to_numeric(row.get("national_win_rate"), errors="coerce"),
                "local_win_rate": pd.to_numeric(row.get("local_win_rate"), errors="coerce"),
                "motor_no": _normalize_int(row.get("motor_no")),
                "motor_rate": pd.to_numeric(row.get("motor_2ren_rate"), errors="coerce"),
                "boat_no": _normalize_int(row.get("boat_no")),
                "boat_rate": pd.to_numeric(row.get("boat_2ren_rate"), errors="coerce"),
                "fl": _build_fl_value(row.get("f_count"), row.get("l_count")),
                "snapshot_time": ingest_ts,
                "exhibition_time": pd.NA,
                "exhibition_type": "web_entries",
                "body_weight": pd.to_numeric(row.get("weight"), errors="coerce"),
                "weather": pd.NA,
                "wind_speed": pd.NA,
                "wave_height": pd.NA,
                "source_file": source_file,
                "ingest_ts": ingest_ts,
            }
        )
    return pd.DataFrame.from_records(records)


def _load_txt_entries(path: Path) -> pd.DataFrame:
    parser = OfficialTxtParser()
    try:
        parsed = parser.parse(str(path), raw_kind="kbn_txt")
        df = parsed["dataframe"].copy()
    except Exception as exc:
        logger.warning("entry txt parse failed for %s: %s", path, exc)
        return pd.DataFrame(columns=ENTRY_COLUMNS + PRE_RACE_COLUMNS)

    if df.empty:
        return pd.DataFrame(columns=ENTRY_COLUMNS + PRE_RACE_COLUMNS)

    records: list[dict[str, object]] = []
    ingest_ts = _file_mtime_iso(path)
    source_file = _relative_path(path)
    for _, row in df.iterrows():
        race_date = _normalize_date_text(row.get("date"))
        jcd = _normalize_int(row.get("jcd"))
        race_no = _normalize_int(row.get("race_no"))
        lane = _normalize_int(row.get("lane"))
        if race_date is None or jcd is None or race_no is None or lane is None:
            logger.warning(
                "txt entry row skipped due to missing key fields: file=%s date=%s jcd=%s race_no=%s lane=%s",
                source_file,
                row.get("date"),
                row.get("jcd"),
                row.get("race_no"),
                row.get("lane"),
            )
            continue
        keys = _make_keys(race_date, jcd, race_no, lane)
        records.append(
            {
                **keys,
                "jcd": jcd,
                "race_no": race_no,
                "lane": lane,
                "player_id": _normalize_int(row.get("racer_id")),
                "player_name": row.get("racer_name"),
                "class": row.get("racer_class"),
                "branch": row.get("branch"),
                "age": _normalize_int(row.get("age")),
                "weight": pd.to_numeric(row.get("weight"), errors="coerce"),
                "avg_st": pd.to_numeric(row.get("avg_st"), errors="coerce"),
                "nat_win_rate": pd.to_numeric(row.get("national_win_rate"), errors="coerce"),
                "local_win_rate": pd.to_numeric(row.get("local_win_rate"), errors="coerce"),
                "motor_no": _normalize_int(row.get("motor_no")),
                "motor_rate": pd.to_numeric(row.get("motor_2ren_rate"), errors="coerce"),
                "boat_no": _normalize_int(row.get("boat_no")),
                "boat_rate": pd.to_numeric(row.get("boat_2ren_rate"), errors="coerce"),
                "fl": _build_fl_value(row.get("f_count"), row.get("l_count")),
                "snapshot_time": ingest_ts,
                "exhibition_time": pd.to_numeric(row.get("exhibition_time"), errors="coerce"),
                "exhibition_type": "official_txt",
                "body_weight": pd.to_numeric(row.get("weight"), errors="coerce"),
                "weather": pd.NA,
                "wind_speed": pd.NA,
                "wave_height": pd.NA,
                "source_file": source_file,
                "ingest_ts": ingest_ts,
            }
        )
    return pd.DataFrame.from_records(records)


def _load_txt_results(path: Path) -> pd.DataFrame:
    parser = OfficialTxtParser()
    try:
        parsed = parser.parse(str(path), raw_kind="kse_txt")
        df = parsed["dataframe"].copy()
    except Exception as exc:
        logger.warning("result txt parse failed for %s: %s", path, exc)
        return pd.DataFrame(columns=RESULT_COLUMNS)

    if df.empty:
        return pd.DataFrame(columns=RESULT_COLUMNS)

    records: list[dict[str, object]] = []
    ingest_ts = _file_mtime_iso(path)
    source_file = _relative_path(path)
    for _, row in df.iterrows():
        race_date = _normalize_date_text(row.get("date"))
        jcd = _normalize_int(row.get("jcd"))
        race_no = _normalize_int(row.get("race_no"))
        lane = _normalize_int(row.get("lane"))
        finish_position = _normalize_int(row.get("finish_position"))
        if race_date is None or jcd is None or race_no is None or lane is None or finish_position is None:
            logger.warning(
                "txt result row skipped due to missing key fields: file=%s date=%s jcd=%s race_no=%s lane=%s finish_position=%s",
                source_file,
                row.get("date"),
                row.get("jcd"),
                row.get("race_no"),
                row.get("lane"),
                row.get("finish_position"),
            )
            continue
        keys = _make_keys(race_date, jcd, race_no, lane)
        records.append(
            {
                **keys,
                "jcd": jcd,
                "race_no": race_no,
                "lane": lane,
                "finish_position": finish_position,
                "is_win": int(finish_position == 1),
                "is_top2": int(finish_position <= 2),
                "is_top3": int(finish_position <= 3),
                "winning_trifecta": pd.NA,
                "payout_trifecta": pd.to_numeric(row.get("odds_trifecta"), errors="coerce"),
                "source_file": source_file,
                "ingest_ts": ingest_ts,
            }
        )
    out = pd.DataFrame.from_records(records)
    if out.empty:
        return out.reindex(columns=RESULT_COLUMNS)
    out["winning_trifecta"] = (
        out.sort_values(["race_id", "finish_position"], kind="mergesort")
        .groupby("race_id")["lane"]
        .transform(lambda s: "-".join(str(int(v)) for v in s.head(3).tolist()) if len(s) >= 3 else pd.NA)
    )
    return out


def _load_program_result_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=RESULT_COLUMNS)
    df = _read_csv_any_encoding(path)
    if df.empty:
        return pd.DataFrame(columns=RESULT_COLUMNS)
    if not {"date", "jcd", "race_no", "lane"}.issubset(df.columns):
        return pd.DataFrame(columns=RESULT_COLUMNS)
    records: list[dict[str, object]] = []
    ingest_ts = _file_mtime_iso(path)
    source_file = _relative_path(path)
    for _, row in df.iterrows():
        race_date = _normalize_date_text(row.get("date"))
        jcd = _normalize_int(row.get("jcd"))
        race_no = _normalize_int(row.get("race_no"))
        lane = _normalize_int(row.get("lane"))
        if race_date is None or jcd is None or race_no is None or lane is None:
            logger.warning(
                "program result row skipped due to missing key fields: file=%s date=%s jcd=%s race_no=%s lane=%s",
                source_file,
                row.get("date"),
                row.get("jcd"),
                row.get("race_no"),
                row.get("lane"),
            )
            continue
        keys = _make_keys(race_date, jcd, race_no, lane)
        finish_position = _normalize_int(row.get("rank")) or _normalize_int(row.get("finish_position"))
        if finish_position is None:
            logger.warning(
                "program result row skipped due to missing finish_position: file=%s date=%s jcd=%s race_no=%s lane=%s",
                source_file,
                row.get("date"),
                row.get("jcd"),
                row.get("race_no"),
                row.get("lane"),
            )
            continue
        records.append(
            {
                **keys,
                "jcd": jcd,
                "race_no": race_no,
                "lane": lane,
                "finish_position": finish_position,
                "is_win": int(finish_position == 1),
                "is_top2": int(finish_position <= 2),
                "is_top3": int(finish_position <= 3),
                "winning_trifecta": row.get("combo"),
                "payout_trifecta": pd.to_numeric(row.get("payout"), errors="coerce"),
                "source_file": source_file,
                "ingest_ts": ingest_ts,
            }
        )
    out = pd.DataFrame.from_records(records)
    if out.empty:
        return out.reindex(columns=RESULT_COLUMNS)
    return out


def _merge_frames(frames: list[pd.DataFrame], key_cols: list[str]) -> pd.DataFrame:
    prepared = [frame.copy() for frame in frames if frame is not None and not frame.empty]
    if not prepared:
        return pd.DataFrame(columns=key_cols)

    for frame in prepared:
        frame.dropna(subset=key_cols, inplace=True)
        frame.sort_values(key_cols, inplace=True, kind="mergesort")
        frame.drop_duplicates(subset=key_cols, keep="last", inplace=True)

    merged = prepared[0].copy()
    for frame in prepared[1:]:
        overlap = [col for col in frame.columns if col not in key_cols and col in merged.columns]
        merged = merged.merge(frame, on=key_cols, how="outer", suffixes=("", "__src"))
        for col in overlap:
            src_col = f"{col}__src"
            if src_col in merged.columns:
                merged[col] = merged[col].combine_first(merged[src_col])
                merged.drop(columns=[src_col], inplace=True)
    return merged


def _normalize_entry_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=PRE_RACE_COLUMNS)
    work = frame.copy()
    for col in ["race_date", "jcd", "race_no", "lane"]:
        if col in work.columns:
            continue
        if col == "race_date" and "date" in work.columns:
            work["race_date"] = work["date"].map(_normalize_date_text)
        else:
            work[col] = pd.NA
    work["race_date"] = work["race_date"].map(_normalize_date_text)
    work["jcd"] = pd.to_numeric(work["jcd"], errors="coerce").astype("Int64")
    work["race_no"] = pd.to_numeric(work["race_no"], errors="coerce").astype("Int64")
    work["lane"] = pd.to_numeric(work["lane"], errors="coerce").astype("Int64")
    work = work.dropna(subset=["race_date", "jcd", "race_no", "lane"]).copy()
    work["jcd"] = work["jcd"].astype(int)
    work["race_no"] = work["race_no"].astype(int)
    work["lane"] = work["lane"].astype(int)
    work["race_id"] = work.apply(lambda row: canonical_race_id(row["race_date"], row["jcd"], row["race_no"]), axis=1)
    work["race_key"] = work.apply(lambda row: canonical_race_key(row["race_date"], row["jcd"], row["race_no"]), axis=1)
    work["boat_key"] = work.apply(lambda row: f"{row['race_id']}-L{int(row['lane']):02d}", axis=1)
    for col in PRE_RACE_COLUMNS:
        if col not in work.columns:
            work[col] = pd.NA
    work = work[PRE_RACE_COLUMNS].copy()
    return work


def _normalize_result_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=RESULT_COLUMNS)
    work = frame.copy()
    work["race_date"] = work["race_date"].map(_normalize_date_text)
    work["jcd"] = pd.to_numeric(work["jcd"], errors="coerce").astype("Int64")
    work["race_no"] = pd.to_numeric(work["race_no"], errors="coerce").astype("Int64")
    work["lane"] = pd.to_numeric(work["lane"], errors="coerce").astype("Int64")
    work["finish_position"] = pd.to_numeric(work["finish_position"], errors="coerce").astype("Int64")
    work = work.dropna(subset=["race_date", "jcd", "race_no", "lane", "finish_position"]).copy()
    work["jcd"] = work["jcd"].astype(int)
    work["race_no"] = work["race_no"].astype(int)
    work["lane"] = work["lane"].astype(int)
    work["finish_position"] = work["finish_position"].astype(int)
    work["race_id"] = work.apply(lambda row: canonical_race_id(row["race_date"], row["jcd"], row["race_no"]), axis=1)
    work["race_key"] = work.apply(lambda row: canonical_race_key(row["race_date"], row["jcd"], row["race_no"]), axis=1)
    work["boat_key"] = work.apply(lambda row: f"{row['race_id']}-L{int(row['lane']):02d}", axis=1)
    if "winning_trifecta" not in work.columns:
        work["winning_trifecta"] = pd.NA
    if "payout_trifecta" not in work.columns:
        work["payout_trifecta"] = pd.NA
    work["is_win"] = pd.to_numeric(work["is_win"], errors="coerce") if "is_win" in work.columns else pd.Series(pd.NA, index=work.index)
    work["is_top2"] = pd.to_numeric(work["is_top2"], errors="coerce") if "is_top2" in work.columns else pd.Series(pd.NA, index=work.index)
    work["is_top3"] = pd.to_numeric(work["is_top3"], errors="coerce") if "is_top3" in work.columns else pd.Series(pd.NA, index=work.index)
    work["is_win"] = work["is_win"].fillna(work["finish_position"].eq(1).astype(int)).astype(int)
    work["is_top2"] = work["is_top2"].fillna(work["finish_position"].le(2).astype(int)).astype(int)
    work["is_top3"] = work["is_top3"].fillna(work["finish_position"].le(3).astype(int)).astype(int)
    for col in RESULT_COLUMNS:
        if col not in work.columns:
            work[col] = pd.NA
    work = work[RESULT_COLUMNS].copy()
    return work


def _load_feature_availability(path: Path) -> pd.DataFrame:
    if path.exists():
        return pd.read_csv(path, low_memory=False)
    return pd.DataFrame(
        columns=[
            "feature_name",
            "source_table",
            "available_phase",
            "allowed_for_training",
            "allowed_for_live",
            "description",
        ]
    )


def _build_default_feature_availability() -> pd.DataFrame:
    rows = [
        ("race_date", "normalized_entries", "entry", True, True, "開催日"),
        ("jcd", "normalized_entries", "entry", True, True, "開催場コード"),
        ("race_no", "normalized_entries", "entry", True, True, "レース番号"),
        ("lane", "normalized_entries", "entry", True, True, "枠番"),
        ("race_id", "normalized_entries", "entry", True, True, "レース主キー"),
        ("race_key", "normalized_entries", "entry", True, True, "標準 race_key"),
        ("boat_key", "normalized_entries", "entry", True, True, "艇単位キー"),
        ("player_id", "normalized_entries", "entry", True, True, "選手ID"),
        ("player_name", "normalized_entries", "entry", True, True, "選手名"),
        ("class", "normalized_entries", "entry", True, True, "級別"),
        ("branch", "normalized_entries", "entry", True, True, "支部"),
        ("age", "normalized_entries", "entry", True, True, "年齢"),
        ("weight", "normalized_entries", "entry", True, True, "体重"),
        ("avg_st", "normalized_entries", "entry", True, True, "平均ST"),
        ("nat_win_rate", "normalized_entries", "entry", True, True, "全国勝率"),
        ("local_win_rate", "normalized_entries", "entry", True, True, "当地勝率"),
        ("motor_no", "normalized_entries", "entry", True, True, "モーター番号"),
        ("motor_rate", "normalized_entries", "entry", True, True, "モーター率"),
        ("boat_no", "normalized_entries", "entry", True, True, "ボート番号"),
        ("boat_rate", "normalized_entries", "entry", True, True, "ボート率"),
        ("fl", "normalized_entries", "entry", True, True, "F/L"),
        ("snapshot_time", "normalized_pre_race", "pre_race", True, True, "観測時刻"),
        ("exhibition_time", "normalized_pre_race", "pre_race", True, True, "展示タイム"),
        ("exhibition_type", "normalized_pre_race", "pre_race", True, True, "展示情報ソース"),
        ("body_weight", "normalized_pre_race", "pre_race", True, True, "体重"),
        ("weather", "normalized_pre_race", "pre_race", True, True, "天候"),
        ("wind_speed", "normalized_pre_race", "pre_race", True, True, "風速"),
        ("wave_height", "normalized_pre_race", "pre_race", True, True, "波高"),
        ("finish_position", "normalized_results", "result", True, False, "着順"),
        ("is_win", "normalized_results", "result", True, False, "1着ラベル"),
        ("is_top2", "normalized_results", "result", True, False, "2着以内ラベル"),
        ("is_top3", "normalized_results", "result", True, False, "3着以内ラベル"),
        ("winning_trifecta", "normalized_results", "result", True, False, "三連単の実結果"),
        ("payout_trifecta", "normalized_results", "result", True, False, "三連単払戻"),
    ]
    return pd.DataFrame(
        rows,
        columns=[
            "feature_name",
            "source_table",
            "available_phase",
            "allowed_for_training",
            "allowed_for_live",
            "description",
        ],
    )


def _validate_null_keys(frame: pd.DataFrame, name: str) -> list[str]:
    problems: list[str] = []
    for col in ["race_date", "jcd", "race_no", "lane"]:
        if col not in frame.columns:
            problems.append(f"{name}:missing_{col}")
            continue
        if frame[col].isna().any():
            problems.append(f"{name}:null_{col}")
    return problems


def _validate_duplicates(frame: pd.DataFrame, name: str) -> list[str]:
    if frame.empty:
        return []
    dup_mask = frame.duplicated(subset=["race_key", "lane"], keep=False)
    if dup_mask.any():
        return [f"{name}:duplicate_race_key_lane"]
    return []


def _validate_six_boats(frame: pd.DataFrame, name: str) -> list[str]:
    if frame.empty:
        return [f"{name}:empty"]
    counts = frame.groupby("race_key")["lane"].nunique(dropna=True)
    bad = counts[counts != 6]
    if not bad.empty:
        return [f"{name}:non_six_boat_race:{len(bad)}"]
    return []


def _validate_feature_availability(frame: pd.DataFrame, availability: pd.DataFrame, *, live_only: bool) -> list[str]:
    if frame.empty or availability.empty:
        return []
    live_allowed = set(
        availability.loc[availability["allowed_for_live"].astype(str).str.lower().isin(["true", "1"]), "feature_name"].astype(str).tolist()
    )
    declared = set(availability["feature_name"].astype(str).tolist())
    problems: list[str] = []
    columns = [col for col in frame.columns if col in declared]
    if live_only:
        forbidden = [col for col in columns if col not in live_allowed]
        if forbidden:
            problems.append("live_forbidden_columns:" + ",".join(sorted(set(forbidden))))
    result_cols = sorted(set(frame.columns) & RESULT_PHASE_COLUMNS)
    if result_cols and live_only:
        problems.append("result_phase_columns_present:" + ",".join(result_cols))
    return problems


def _complete_race_ids(frame: pd.DataFrame) -> set[str]:
    if frame.empty or "race_key" not in frame.columns:
        return set()
    counts = frame.groupby("race_key")["lane"].nunique(dropna=True)
    return set(counts[counts == 6].index.astype(str).tolist())


def _filter_complete_races(frame: pd.DataFrame, valid_race_keys: set[str]) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    work = frame.copy()
    work = work[work["race_key"].astype(str).isin(valid_race_keys)].copy()
    work = work.sort_values(["race_date", "jcd", "race_no", "lane"], kind="mergesort").reset_index(drop=True)
    return work


def build_training_base(
    *,
    out_dir: Path = DEFAULT_OUT_DIR,
    report_dir: Path = DEFAULT_REPORT_DIR,
    feature_availability_path: Path = DEFAULT_FEATURE_AVAILABILITY_PATH,
    date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    out_dir = Path(out_dir)
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    if date and (start_date or end_date):
        raise ValueError("use either --date or --start-date/--end-date, not both")
    if date:
        start_date = start_date or date
        end_date = end_date or date

    feature_availability = _load_feature_availability(feature_availability_path)
    if feature_availability.empty:
        feature_availability = _build_default_feature_availability()
        feature_availability_path.parent.mkdir(parents=True, exist_ok=True)
        feature_availability.to_csv(feature_availability_path, index=False, encoding="utf-8")

    program_entries = _load_program_entries(DEFAULT_PROGRAM_CSV)

    entry_txt_dir = ROOT / "data" / "raw" / "official" / "entries"
    entry_txt_frames = []
    if entry_txt_dir.exists():
        for path in sorted(entry_txt_dir.glob("*.TXT")):
            if "_head" in path.stem.lower():
                continue
            if not _path_date_matches(path, start_date, end_date):
                continue
            entry_txt_frames.append(_load_txt_entries(path))
    legacy_entry_dir = ROOT / "raw" / "B"
    if legacy_entry_dir.exists():
        for path in sorted(legacy_entry_dir.glob("*.txt")):
            if "_head" in path.stem.lower():
                continue
            if not _path_date_matches(path, start_date, end_date):
                continue
            entry_txt_frames.append(_load_txt_entries(path))

    html_frames = []
    web_entries_root = ROOT / "data" / "raw" / "official" / "web_entries"
    if web_entries_root.exists():
        for path in sorted(web_entries_root.glob("**/syusso_pages/*.html")):
            if not _path_date_matches(path, start_date, end_date):
                continue
            html_frames.append(_load_html_entries(path))

    entry_frame = _merge_frames(
        [frame for frame in [program_entries, *html_frames, *entry_txt_frames] if not frame.empty],
        ENTRY_KEY_COLS,
    )
    entry_frame = _normalize_entry_frame(entry_frame)

    result_txt_frames = []
    result_dir = ROOT / "data" / "raw" / "official" / "results"
    if result_dir.exists():
        for path in sorted(result_dir.glob("*.TXT")):
            if "_head" in path.stem.lower():
                continue
            if not _path_date_matches(path, start_date, end_date):
                continue
            result_txt_frames.append(_load_txt_results(path))
    legacy_result_dir = ROOT / "raw" / "K"
    if legacy_result_dir.exists():
        for path in sorted(legacy_result_dir.glob("*.txt")):
            if "_head" in path.stem.lower():
                continue
            if not _path_date_matches(path, start_date, end_date):
                continue
            result_txt_frames.append(_load_txt_results(path))
    result_csv = _load_program_result_csv(DEFAULT_RESULT_CSV)

    result_frame = _merge_frames(
        [frame for frame in [result_csv, *result_txt_frames] if not frame.empty],
        RESULT_KEY_COLS,
    )
    result_frame = _normalize_result_frame(result_frame)

    if start_date:
        start_norm = _normalize_date_text(start_date)
        end_norm = _normalize_date_text(end_date or start_date)
        if start_norm is None or end_norm is None:
            raise ValueError("invalid date range")
        entry_frame = entry_frame[
            entry_frame["race_date"].between(start_norm, end_norm, inclusive="both")
        ].copy()
        result_frame = result_frame[
            result_frame["race_date"].between(start_norm, end_norm, inclusive="both")
        ].copy()

    entry_complete_race_keys = _complete_race_ids(entry_frame)
    result_complete_race_keys = _complete_race_ids(result_frame)
    training_race_keys = entry_complete_race_keys & result_complete_race_keys

    entry_complete = _filter_complete_races(entry_frame, entry_complete_race_keys)
    result_complete = _filter_complete_races(result_frame, result_complete_race_keys)

    pre_race_features = entry_complete[PRE_RACE_FEATURE_COLUMNS].copy() if not entry_complete.empty else pd.DataFrame(columns=PRE_RACE_FEATURE_COLUMNS)
    if not pre_race_features.empty:
        pre_race_features = pre_race_features.drop(columns=["source_file", "ingest_ts"], errors="ignore")

    training_dataset = pd.DataFrame(columns=[*PRE_RACE_FEATURE_COLUMNS, *TRAINING_LABEL_COLUMNS])
    if not pre_race_features.empty and not result_complete.empty:
        training_results = _filter_complete_races(result_frame, training_race_keys)
        training_dataset = pre_race_features.merge(
            training_results[[c for c in RESULT_COLUMNS if c in training_results.columns and c not in {"source_file", "ingest_ts"}]],
            on=["race_date", "jcd", "race_no", "lane", "race_id", "race_key", "boat_key"],
            how="inner",
            validate="one_to_one",
        )

    if not training_dataset.empty:
        training_dataset = training_dataset.sort_values(["race_date", "race_key", "lane"], kind="mergesort").reset_index(drop=True)

    issues: list[str] = []
    issues.extend(_validate_null_keys(entry_complete, "normalized_entries"))
    issues.extend(_validate_null_keys(result_complete, "normalized_results"))
    issues.extend(_validate_duplicates(entry_complete, "normalized_entries"))
    issues.extend(_validate_duplicates(result_complete, "normalized_results"))
    issues.extend(_validate_six_boats(entry_complete, "normalized_entries"))
    issues.extend(_validate_six_boats(result_complete, "normalized_results"))
    issues.extend(_validate_feature_availability(pre_race_features, feature_availability, live_only=True))

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "date_range": {"start": start_date, "end": end_date or start_date or end_date},
        "input_counts": {
            "program_entries_rows": int(len(program_entries)),
            "html_entry_rows": int(sum(len(frame) for frame in html_frames)),
            "txt_entry_rows": int(sum(len(frame) for frame in entry_txt_frames)),
            "result_csv_rows": int(len(result_csv)),
            "txt_result_rows": int(sum(len(frame) for frame in result_txt_frames)),
        },
        "output_counts": {
            "normalized_entries_rows": int(len(entry_complete)),
            "normalized_pre_race_rows": int(len(entry_complete)),
            "normalized_results_rows": int(len(result_complete)),
            "pre_race_features_rows": int(len(pre_race_features)),
            "training_dataset_rows": int(len(training_dataset)),
            "entry_complete_race_count": int(len(entry_complete_race_keys)),
            "result_complete_race_count": int(len(result_complete_race_keys)),
            "training_race_count": int(len(training_race_keys)),
        },
        "entry_complete_race_keys": sorted(entry_complete_race_keys),
        "result_complete_race_keys": sorted(result_complete_race_keys),
        "training_race_keys": sorted(training_race_keys),
        "issues": issues,
    }

    out_dir = Path(out_dir)
    processed_dir = out_dir / "processed"
    metadata_dir = out_dir / "metadata"
    processed_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)

    entry_out = processed_dir / "normalized_entries.csv"
    pre_race_out = processed_dir / "normalized_pre_race.csv"
    result_out = processed_dir / "normalized_results.csv"
    pre_race_features_out = processed_dir / "pre_race_features.csv"
    training_out = processed_dir / "training_dataset.csv"

    entry_complete.to_csv(entry_out, index=False, encoding="utf-8")
    entry_complete.to_csv(pre_race_out, index=False, encoding="utf-8")
    result_complete.to_csv(result_out, index=False, encoding="utf-8")
    pre_race_features.to_csv(pre_race_features_out, index=False, encoding="utf-8")
    training_dataset.to_csv(training_out, index=False, encoding="utf-8")
    feature_availability.to_csv(feature_availability_path, index=False, encoding="utf-8")

    summary_path = report_dir / "build_training_base_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "status": "ok" if not issues else "warning",
                "summary": summary,
                "outputs": {
                    "normalized_entries": str(entry_out),
                    "normalized_pre_race": str(pre_race_out),
                    "normalized_results": str(result_out),
                    "pre_race_features": str(pre_race_features_out),
                    "training_dataset": str(training_out),
                    "feature_availability": str(feature_availability_path),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build normalized training base tables for boatrace data.")
    parser.add_argument("--date", default=None, help="Target date in YYYY-MM-DD")
    parser.add_argument("--start-date", default=None, help="Start date in YYYY-MM-DD")
    parser.add_argument("--end-date", default=None, help="End date in YYYY-MM-DD")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="Output root directory")
    parser.add_argument(
        "--feature-availability-path",
        default=str(DEFAULT_FEATURE_AVAILABILITY_PATH),
        help="Path to feature_availability.csv",
    )
    args = parser.parse_args(argv)

    try:
        build_training_base(
            out_dir=Path(args.out_dir),
            report_dir=DEFAULT_REPORT_DIR,
            feature_availability_path=Path(args.feature_availability_path),
            date=args.date,
            start_date=args.start_date,
            end_date=args.end_date,
        )
        return 0
    except Exception as exc:
        logger.exception("build_training_base failed")
        print(f"[error] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
