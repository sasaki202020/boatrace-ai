from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import pandas as pd


REQUIRED_BASE_COLUMNS = {"date", "decision"}

RACE_COLUMN_CANDIDATES = [
    "race_key",
    "race_id",
    "race_code",
    "race",
    "race_no",
    "normalized_race_key",
    "race_key_new",
]

JCD_COLUMN_CANDIDATES = [
    "jcd",
    "stadium_code",
    "place_code",
    "venue_code",
]

RNO_COLUMN_CANDIDATES = [
    "rno",
    "race_number",
    "race_no_num",
    "round",
]

TICKET_COLUMN_CANDIDATES = [
    "ticket",
    "trifecta",
    "bet_ticket",
    "candidate",
    "prediction",
    "predicted_ticket",
    "buy_ticket",
    "combo",
    "recommended_trifecta",
    "predicted_trifecta",
]

ODDS_COLUMN_CANDIDATES = [
    "odds",
    "real_odds",
    "trifecta_odds",
    "final_odds",
]


def _pick_first_existing_column(df: pd.DataFrame, candidates: list[str]) -> Optional[str]:
    lowered = {str(col).lower(): col for col in df.columns}
    for candidate in candidates:
        if candidate.lower() in lowered:
            return lowered[candidate.lower()]
    return None


def _normalize_date_str(value: object) -> str:
    text = str(value).strip()
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 8:
        return digits[:8]
    raise ValueError(f"Could not normalize date: {value}")


def _normalize_ticket(value: object) -> str:
    text = str(value).strip()
    nums = re.findall(r"[1-6]", text)
    if len(nums) >= 3:
        return f"{nums[0]}-{nums[1]}-{nums[2]}"
    raise ValueError(f"Could not normalize ticket: {value}")


def _normalize_race_key_from_single_value(value: object, date_str: str) -> str:
    text = str(value).strip()

    if re.fullmatch(r"d\d{8}-c\d{2}-r\d{2}", text):
        return text

    m = re.fullmatch(r"(\d{8})-(\d{2})-(\d{2})", text)
    if m:
        return f"d{m.group(1)}-c{m.group(2)}-r{m.group(3)}"

    m = re.fullmatch(r"(\d{2})[-_/](\d{2})", text)
    if m:
        return f"d{date_str}-c{m.group(1)}-r{m.group(2)}"

    m = re.fullmatch(r"c(\d{2})-r(\d{2})", text)
    if m:
        return f"d{date_str}-c{m.group(1)}-r{m.group(2)}"

    m = re.search(r"(\d{8})[-_/](\d{2})[-_/](\d{2})", text)
    if m:
        return f"d{m.group(1)}-c{m.group(2)}-r{m.group(3)}"

    m = re.search(r"(\d{2}).*?(\d{1,2})", text)
    if m:
        jcd = m.group(1).zfill(2)
        rno = m.group(2).zfill(2)
        return f"d{date_str}-c{jcd}-r{rno}"

    raise ValueError(f"Could not normalize race key from value: {value}")


def _normalize_race_key_from_columns(row: pd.Series, date_str: str) -> str:
    race_col = _pick_first_existing_column(pd.DataFrame([row]), RACE_COLUMN_CANDIDATES)
    if race_col:
        return _normalize_race_key_from_single_value(row[race_col], date_str)

    jcd_col = _pick_first_existing_column(pd.DataFrame([row]), JCD_COLUMN_CANDIDATES)
    rno_col = _pick_first_existing_column(pd.DataFrame([row]), RNO_COLUMN_CANDIDATES)
    if jcd_col and rno_col:
        jcd = re.sub(r"\D", "", str(row[jcd_col])).zfill(2)[:2]
        rno = re.sub(r"\D", "", str(row[rno_col])).zfill(2)[:2]
        if len(jcd) == 2 and len(rno) == 2:
            return f"d{date_str}-c{jcd}-r{rno}"

    raise ValueError("Could not resolve race_key from row")


def _resolve_ticket_column(df: pd.DataFrame) -> str:
    ticket_col = _pick_first_existing_column(df, TICKET_COLUMN_CANDIDATES)
    if ticket_col:
        return ticket_col

    lowered = {str(c).lower(): c for c in df.columns}
    possible_first = next((lowered[c] for c in lowered if c in {"first", "first_boat", "rank1", "pred1"}), None)
    possible_second = next((lowered[c] for c in lowered if c in {"second", "second_boat", "rank2", "pred2"}), None)
    possible_third = next((lowered[c] for c in lowered if c in {"third", "third_boat", "rank3", "pred3"}), None)

    if possible_first and possible_second and possible_third:
        tmp_col = "__synthetic_ticket__"
        df[tmp_col] = (
            df[possible_first].astype(str).str.strip()
            + "-"
            + df[possible_second].astype(str).str.strip()
            + "-"
            + df[possible_third].astype(str).str.strip()
        )
        return tmp_col

    raise ValueError(f"skip_decisions needs one of ticket columns: {TICKET_COLUMN_CANDIDATES}")


def build_buy_tickets_from_skip_decisions(skip_df: pd.DataFrame, target_date: str) -> pd.DataFrame:
    missing = REQUIRED_BASE_COLUMNS - set(skip_df.columns)
    if missing:
        raise ValueError(f"skip_decisions missing columns: {sorted(missing)}")

    date_str = _normalize_date_str(target_date)

    df = skip_df.copy()
    df["date_norm"] = df["date"].map(_normalize_date_str)
    df = df[df["date_norm"] == date_str].copy()
    if df.empty:
        return pd.DataFrame(columns=["date", "race_key", "ticket", "decision", "odds"])

    df["decision"] = df["decision"].astype(str).str.upper().str.strip()
    df = df[df["decision"] == "BUY"].copy()
    if df.empty:
        return pd.DataFrame(columns=["date", "race_key", "ticket", "decision", "odds"])

    ticket_col = _resolve_ticket_column(df)
    odds_col = _pick_first_existing_column(df, ODDS_COLUMN_CANDIDATES)

    df["date"] = pd.to_datetime(date_str, format="%Y%m%d").strftime("%Y-%m-%d")
    df["race_key"] = df.apply(lambda row: _normalize_race_key_from_columns(row, date_str), axis=1)
    df["ticket"] = df[ticket_col].map(_normalize_ticket)
    df["decision"] = "BUY"

    if odds_col:
        df["odds"] = pd.to_numeric(df[odds_col], errors="coerce")
    else:
        df["odds"] = pd.NA

    out = df[["date", "race_key", "ticket", "decision", "odds"]].copy()
    out = out.drop_duplicates(subset=["date", "race_key", "ticket"]).reset_index(drop=True)
    return out


def attach_actual_odds(buy_df: pd.DataFrame, odds_df: pd.DataFrame) -> pd.DataFrame:
    required_buy = {"date", "race_key", "ticket", "decision"}

    missing_buy = required_buy - set(buy_df.columns)
    if missing_buy:
        raise ValueError(f"buy_df missing columns: {sorted(missing_buy)}")

    left = buy_df.copy()
    right = odds_df.copy()

    for col in ["date", "race_key", "ticket"]:
        left[col] = left[col].astype(str).str.strip()
    if "race_key" not in right.columns and "race_id" in right.columns:
        right["race_key"] = right["race_id"].astype(str).str.strip()
    if "ticket" not in right.columns and "combo" in right.columns:
        right["ticket"] = right["combo"]
    for col in ["date", "race_key", "ticket"]:
        if col not in right.columns:
            raise ValueError(f"odds_df missing columns: {[col]}")
        right[col] = right[col].astype(str).str.strip()

    right["odds"] = pd.to_numeric(right["odds"], errors="coerce")
    merged = left.drop(columns=["odds"], errors="ignore").merge(
        right[["date", "race_key", "ticket", "odds"]],
        on=["date", "race_key", "ticket"],
        how="left",
    )
    return merged


def save_buy_tickets(df: pd.DataFrame, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False, encoding="utf-8")
