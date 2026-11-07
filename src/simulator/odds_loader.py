from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from src.pipeline.pipeline_utils import ROOT


ODDS_DIR = ROOT / "data" / "odds"


def _normalize_date(value: str | date | datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    try:
        return pd.to_datetime(text).date().isoformat()
    except Exception:
        return text


def _normalize_race_key(race_id: str | None) -> str | None:
    if race_id is None or pd.isna(race_id):
        return None
    text = str(race_id).strip()
    if not text:
        return None

    m = re.match(r"^(\d{8})-(\d{2})-(\d{2})$", text)
    if m:
        date8, venue, race_no = m.groups()
        return f"d{date8}-c{int(venue):02d}-r{int(race_no):02d}"

    return text


def odds_csv_path(target_date: str | date | datetime, odds_root: Path | None = None) -> Path:
    date_str = _normalize_date(target_date)
    if not date_str:
        raise ValueError("target_date is required")
    root = odds_root or ODDS_DIR
    dated_dir = root / date_str.replace("-", "")
    candidates = [
        dated_dir / "trifecta_odds.csv",
        dated_dir / "today_trifecta_odds.csv",
        root / "today_trifecta_odds.csv",
        root / "trifecta_odds.csv",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def load_odds_for_date(target_date: str | date | datetime, odds_root: Path | None = None) -> pd.DataFrame:
    path = odds_csv_path(target_date, odds_root)
    if not path.exists():
        return pd.DataFrame(
            columns=[
                "date",
                "race_id",
                "combo",
                "odds",
                "odds_status",
                "odds_fetch_status",
                "source_path",
            ]
        )

    df = pd.read_csv(path, low_memory=False)
    if "race_id" not in df.columns:
        raise ValueError(f"odds file missing race_id: {path}")
    if "combo" not in df.columns and "trifecta" in df.columns:
        df = df.rename(columns={"trifecta": "combo"})
    if "odds" not in df.columns:
        raise ValueError(f"odds file missing odds column: {path}")

    out = df.copy()
    out["date"] = out.get("date", pd.Series([pd.NA] * len(out)))
    out["race_id"] = out["race_id"].astype(str).str.strip()
    out["race_key"] = out["race_id"].apply(_normalize_race_key)
    if "combo" in out.columns:
        out["combo"] = out["combo"].astype(str).str.strip()
    out["odds"] = pd.to_numeric(out["odds"], errors="coerce")
    out["odds_status"] = out.get("odds_status", pd.Series([pd.NA] * len(out)))
    out["odds_fetch_status"] = out.get("odds_fetch_status", pd.Series([pd.NA] * len(out)))
    out["source_path"] = str(path)
    return out
