from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from src.core.ids import (
    canonical_race_id,
    canonical_race_key,
    canonical_snapshot_ts,
    canonical_ticket_id,
    normalize_race_id,
    race_key_from_race_id,
)


HISTORICAL_REQUIRED_COLUMNS = {"date", "jcd", "venue", "race_no", "finish_position", "lane", "racer_id"}

ODDS_TICKET_CANDIDATES = ("combo", "trifecta", "ticket")
ODDS_DATE_CANDIDATES = ("date", "race_date")
ODDS_RACE_COLS = ("race_id", "date", "jcd", "race_no")


def _empty_tables() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    return (
        pd.DataFrame(
            columns=[
                "race_id",
                "race_key",
                "race_date",
                "date",
                "jcd",
                "venue",
                "race_no",
                "source_kind",
                "source_path",
                "source_row_count",
                "created_at",
            ]
        ),
        pd.DataFrame(
            columns=[
                "entry_id",
                "race_id",
                "race_key",
                "race_date",
                "date",
                "jcd",
                "venue",
                "race_no",
                "lane",
                "racer_id",
                "racer_class",
                "st",
                "exhibition_time",
                "national_win_rate",
                "national_2ren_rate",
                "local_win_rate",
                "local_2ren_rate",
                "motor_2ren_rate",
                "boat_2ren_rate",
                "source_path",
                "created_at",
            ]
        ),
        pd.DataFrame(
            columns=[
                "race_id",
                "race_key",
                "race_date",
                "date",
                "jcd",
                "venue",
                "race_no",
                "winning_ticket_id",
                "status",
                "raw_rows",
                "source_path",
                "created_at",
            ]
        ),
    )


def _empty_odds() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "race_id",
            "race_key",
            "race_date",
            "date",
            "jcd",
            "stadium",
            "race_no",
            "ticket_id",
            "snapshot_ts",
            "odds",
            "odds_status",
            "odds_fetch_status",
            "odds_fetch_used_cache",
            "odds_missing_odds_cells",
            "odds_source",
            "source_url",
            "source_path",
            "created_at",
        ]
    )


def _normalize_date_text(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if not text:
        return None

    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"

    try:
        return pd.to_datetime(text, errors="raise").date().isoformat()
    except Exception:
        return text


def _normalize_created_at() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _split_race_id_to_components(race_id: object) -> tuple[str, int, int]:
    race_id_text = normalize_race_id(race_id)
    date8, jcd, race_no = race_id_text.split("-")
    return date8, int(jcd), int(race_no)


def _safe_series(frame: pd.DataFrame, column: str, default: object = pd.NA) -> pd.Series:
    if column in frame.columns:
        return frame[column]
    return pd.Series([default] * len(frame), index=frame.index)


def _coalesce_series(frame: pd.DataFrame, *columns: str, default: object = pd.NA) -> pd.Series:
    for column in columns:
        if column in frame.columns:
            return frame[column]
    return pd.Series([default] * len(frame), index=frame.index)


def _parse_bool_series(series: pd.Series) -> pd.Series:
    def _parse(value: object) -> object:
        if value is None or pd.isna(value):
            return pd.NA
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "t", "yes", "y"}:
            return True
        if text in {"0", "false", "f", "no", "n"}:
            return False
        return pd.NA

    return series.map(_parse).astype("boolean")


def load_historical_tables(historical_path: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if not historical_path.exists():
        return _empty_tables()

    df = pd.read_csv(historical_path, low_memory=False)
    if df.empty:
        return _empty_tables()

    missing = HISTORICAL_REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"historical_races.csv missing columns: {sorted(missing)}")

    work = df.copy()
    work["date"] = work["date"].map(_normalize_date_text)
    work["jcd"] = pd.to_numeric(work["jcd"], errors="coerce").astype("Int64")
    work["race_no"] = pd.to_numeric(work["race_no"], errors="coerce").astype("Int64")
    work["lane"] = pd.to_numeric(work["lane"], errors="coerce").astype("Int64")
    work["finish_position"] = pd.to_numeric(work["finish_position"], errors="coerce").astype("Int64")
    work["racer_id"] = pd.to_numeric(work["racer_id"], errors="coerce").astype("Int64")
    work = work.dropna(subset=["date", "jcd", "race_no", "lane", "finish_position", "racer_id"]).copy()

    if work.empty:
        return _empty_tables()

    work["race_id"] = work.apply(lambda row: canonical_race_id(row["date"], row["jcd"], row["race_no"]), axis=1)
    work["race_key"] = work.apply(lambda row: canonical_race_key(row["date"], row["jcd"], row["race_no"]), axis=1)
    work["entry_id"] = work.apply(lambda row: f"{row['race_id']}-L{int(row['lane']):02d}", axis=1)
    work["race_date"] = work["date"]
    work["source_path"] = str(historical_path)
    work["created_at"] = _normalize_created_at()

    entries = pd.DataFrame(
        {
            "entry_id": work["entry_id"],
            "race_id": work["race_id"],
            "race_key": work["race_key"],
            "race_date": work["race_date"],
            "date": work["date"],
            "jcd": work["jcd"],
            "venue": work["venue"].astype(str),
            "race_no": work["race_no"],
            "lane": work["lane"],
            "racer_id": work["racer_id"],
            "racer_class": _safe_series(work, "racer_class"),
            "st": pd.to_numeric(_safe_series(work, "st"), errors="coerce"),
            "exhibition_time": pd.to_numeric(_safe_series(work, "exhibition_time"), errors="coerce"),
            "national_win_rate": pd.to_numeric(_safe_series(work, "national_win_rate"), errors="coerce"),
            "national_2ren_rate": pd.to_numeric(_safe_series(work, "national_2ren_rate"), errors="coerce"),
            "local_win_rate": pd.to_numeric(_safe_series(work, "local_win_rate"), errors="coerce"),
            "local_2ren_rate": pd.to_numeric(_safe_series(work, "local_2ren_rate"), errors="coerce"),
            "motor_2ren_rate": pd.to_numeric(_safe_series(work, "motor_2ren_rate"), errors="coerce"),
            "boat_2ren_rate": pd.to_numeric(_safe_series(work, "boat_2ren_rate"), errors="coerce"),
            "source_path": work["source_path"],
            "created_at": work["created_at"],
        }
    ).drop_duplicates(subset=["entry_id"]).reset_index(drop=True)

    races = entries[
        [
            "race_id",
            "race_key",
            "race_date",
            "date",
            "jcd",
            "venue",
            "race_no",
            "source_path",
        ]
    ].copy()
    races["source_kind"] = "historical_races.csv"
    races["source_row_count"] = races.groupby("race_id")["race_id"].transform("size")
    races["created_at"] = _normalize_created_at()
    races = (
        races.drop_duplicates(subset=["race_id"])
        .sort_values(["date", "jcd", "race_no"])
        .reset_index(drop=True)
    )

    results_rows: list[dict[str, Any]] = []
    created_at = _normalize_created_at()
    for race_id, group in work.groupby("race_id", dropna=False):
        # historical_races.csv は同一 race_id の結果行が重複して入ることがあるため、
        # finish_position ごとに 1 行へ畳んでから winning_ticket を組み立てる。
        ordered = (
            group.sort_values(["finish_position", "lane", "racer_id"], kind="stable")
            .drop_duplicates(subset=["finish_position"], keep="first")
            .copy()
        )
        top3 = (
            ordered.dropna(subset=["finish_position", "lane"])
            .sort_values("finish_position")
            .loc[lambda frame: frame["finish_position"].isin([1, 2, 3])]
        )

        winning_ticket = "-".join(str(int(v)) for v in top3["lane"].tolist()) if len(top3) == 3 else pd.NA
        row0 = group.iloc[0]
        results_rows.append(
            {
                "race_id": race_id,
                "race_key": canonical_race_key(row0["date"], row0["jcd"], row0["race_no"]),
                "race_date": row0["date"],
                "date": row0["date"],
                "jcd": row0["jcd"],
                "venue": str(row0["venue"]),
                "race_no": row0["race_no"],
                "winning_ticket_id": winning_ticket,
                "status": "available" if pd.notna(winning_ticket) else "incomplete",
                "raw_rows": int(len(group)),
                "source_path": str(historical_path),
                "created_at": created_at,
            }
        )

    results = pd.DataFrame(results_rows).reset_index(drop=True)
    return races, entries, results


def _load_odds_file(path: Path) -> tuple[pd.DataFrame, list[str]]:
    warnings: list[str] = []
    if not path.exists():
        return _empty_odds(), [f"missing odds file: {path}"]

    df = pd.read_csv(path, low_memory=False)
    if df.empty:
        return _empty_odds(), [f"empty odds file: {path}"]

    ticket_col = next((candidate for candidate in ODDS_TICKET_CANDIDATES if candidate in df.columns), None)
    if ticket_col is None:
        return _empty_odds(), [f"odds file missing ticket column: {path}"]

    work = df.copy()
    if "race_id" in work.columns:
        work["race_id"] = work["race_id"].map(normalize_race_id)
    elif {"date", "jcd", "race_no"}.issubset(work.columns):
        work["race_id"] = work.apply(lambda row: canonical_race_id(row["date"], row["jcd"], row["race_no"]), axis=1)
    else:
        return _empty_odds(), [f"odds file missing race columns: {path}"]

    if "date" in work.columns:
        work["date"] = work["date"].map(_normalize_date_text)
    else:
        work["date"] = work["race_id"].map(
            lambda value: _normalize_date_text(_split_race_id_to_components(value)[0]) if pd.notna(value) else None
        )

    if "jcd" in work.columns:
        work["jcd"] = pd.to_numeric(work["jcd"], errors="coerce").astype("Int64")
    else:
        work["jcd"] = work["race_id"].map(lambda value: _split_race_id_to_components(value)[1] if pd.notna(value) else pd.NA)

    if "race_no" in work.columns:
        work["race_no"] = pd.to_numeric(work["race_no"], errors="coerce").astype("Int64")
    else:
        work["race_no"] = work["race_id"].map(lambda value: _split_race_id_to_components(value)[2] if pd.notna(value) else pd.NA)

    work["race_key"] = work["race_id"].map(race_key_from_race_id)
    work["ticket_id"] = work[ticket_col].map(canonical_ticket_id)
    snapshot_source = _coalesce_series(work, "fetched_at", "snapshot_ts", default=pd.NA)
    work["snapshot_ts"] = snapshot_source.map(canonical_snapshot_ts)
    work["odds"] = pd.to_numeric(_safe_series(work, "odds"), errors="coerce")
    work["odds_status"] = _safe_series(work, "odds_status")
    work["odds_fetch_status"] = _safe_series(work, "odds_fetch_status")
    work["odds_fetch_used_cache"] = _parse_bool_series(_safe_series(work, "odds_fetch_used_cache"))
    work["odds_missing_odds_cells"] = pd.to_numeric(_safe_series(work, "odds_missing_odds_cells"), errors="coerce")
    work["odds_source"] = _coalesce_series(work, "odds_source", "source")
    work["source_url"] = _safe_series(work, "source_url")
    work["stadium"] = _coalesce_series(work, "stadium", "venue")
    work["source_path"] = str(path)
    work["created_at"] = _normalize_created_at()

    snapshots = pd.DataFrame(
        {
            "race_id": work["race_id"],
            "race_key": work["race_key"],
            "race_date": work["date"],
            "date": work["date"],
            "jcd": work["jcd"],
            "stadium": work["stadium"],
            "race_no": work["race_no"],
            "ticket_id": work["ticket_id"],
            "snapshot_ts": work["snapshot_ts"],
            "odds": work["odds"],
            "odds_status": work["odds_status"],
            "odds_fetch_status": work["odds_fetch_status"],
            "odds_fetch_used_cache": work["odds_fetch_used_cache"],
            "odds_missing_odds_cells": work["odds_missing_odds_cells"],
            "odds_source": work["odds_source"],
            "source_url": work["source_url"],
            "source_path": work["source_path"],
            "created_at": work["created_at"],
        }
    )
    snapshots = snapshots.drop_duplicates(subset=["race_id", "ticket_id", "snapshot_ts"]).reset_index(drop=True)
    return snapshots, warnings


def load_odds_snapshots(odds_root: Path) -> tuple[pd.DataFrame, list[str]]:
    warnings: list[str] = []
    paths: list[Path] = []

    if odds_root.exists():
        paths.extend(sorted(odds_root.glob("**/trifecta_odds.csv")))
        today_path = odds_root / "today_trifecta_odds.csv"
        if today_path.exists():
            paths.append(today_path)

    strategy_live_path = odds_root.parent / "strategy_outputs" / "live_odds.csv"
    if strategy_live_path.exists():
        paths.append(strategy_live_path)

    frames: list[pd.DataFrame] = []
    for path in paths:
        frame, frame_warnings = _load_odds_file(path)
        warnings.extend(frame_warnings)
        if not frame.empty:
            frames.append(frame)

    if not frames:
        return _empty_odds(), warnings

    out = pd.concat(frames, ignore_index=True)
    out = out.drop_duplicates(subset=["race_id", "ticket_id", "snapshot_ts"]).reset_index(drop=True)
    return out, warnings


def load_v2_sources(historical_path: Path, odds_root: Path) -> tuple[dict[str, pd.DataFrame], list[str]]:
    races, entries, results = load_historical_tables(historical_path)
    odds_snapshots, warnings = load_odds_snapshots(odds_root)
    return {
        "races": races,
        "entries": entries,
        "results": results,
        "odds_snapshots": odds_snapshots,
    }, warnings
