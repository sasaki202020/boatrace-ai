from __future__ import annotations

import re
from datetime import date, datetime

import pandas as pd


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

    m = re.match(r"^(\d{8})-[A-Z]\d{6}-(\d{1,3})$", text)
    if m:
        date8, _, serial = m.groups()
        serial_i = int(serial)
        section_compact = (serial_i - 1) // 12 + 1
        race_no = (serial_i - 1) % 12 + 1
        return f"d{date8}-c{section_compact:02d}-r{race_no:02d}"

    return text


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


def build_buy_tickets(
    skip_decisions: pd.DataFrame,
    odds_df: pd.DataFrame | None = None,
    *,
    target_date: str | date | datetime | None = None,
) -> pd.DataFrame:
    if skip_decisions.empty:
        return pd.DataFrame(
            columns=["date", "race_id", "race_key", "ticket", "decision", "odds", "reason"]
        )

    df = skip_decisions.copy()
    if "date" not in df.columns or "decision" not in df.columns:
        raise ValueError("skip_decisions must contain date and decision columns")

    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    if target_date is not None:
        date_str = _normalize_date(target_date)
        df = df[df["date"] == date_str].copy()

    df["decision"] = df["decision"].astype(str).str.upper()
    buy_df = df[df["decision"] == "BUY"].copy()
    if buy_df.empty:
        return pd.DataFrame(
            columns=["date", "race_id", "race_key", "ticket", "decision", "odds", "reason"]
        )

    ticket_col = None
    for candidate in ("recommended_trifecta", "predicted_trifecta", "trifecta"):
        if candidate in buy_df.columns:
            ticket_col = candidate
            break
    if ticket_col is None:
        raise ValueError("skip_decisions must contain recommended_trifecta/predicted_trifecta/trifecta")

    out = buy_df[["date", "race_id", ticket_col, "decision"]].copy()
    out = out.rename(columns={ticket_col: "ticket"})
    out["race_key"] = out["race_id"].apply(_normalize_race_key)
    if "reason" in buy_df.columns:
        out["reason"] = buy_df["reason"].astype(str).values
    else:
        out["reason"] = ""

    if odds_df is not None and not odds_df.empty:
        odds = odds_df.copy()
        if "combo" not in odds.columns:
            raise ValueError("odds_df must contain combo column")
        odds = odds.rename(columns={"combo": "ticket"})
        if "race_key" not in odds.columns:
            odds["race_key"] = odds["race_id"].apply(_normalize_race_key) if "race_id" in odds.columns else pd.NA
        keep_cols = ["race_key", "ticket", "odds"]
        if "odds_status" in odds.columns:
            keep_cols.append("odds_status")
        odds = odds[keep_cols].drop_duplicates(subset=["race_key", "ticket"])
        out = out.merge(odds, on=["race_key", "ticket"], how="left")
    else:
        out["odds"] = pd.NA

    return out
