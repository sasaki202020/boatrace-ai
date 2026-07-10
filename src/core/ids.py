from __future__ import annotations

from datetime import datetime

import pandas as pd

from src.utils.race_id import (
    canonical_race_id,
    canonical_race_key,
    normalize_race_id,
    race_id_from_race_key,
    race_key_from_race_id,
    split_race_id,
)

def normalize_ticket_id(value: object) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError("ticket_id is empty")

    parts = re.findall(r"[1-6]", text)
    if len(parts) >= 3:
        return f"{parts[0]}-{parts[1]}-{parts[2]}"

    text = re.sub(r"[^1-6]", "", text)
    if len(text) >= 3:
        return f"{text[0]}-{text[1]}-{text[2]}"

    raise ValueError(f"could not normalize ticket_id: {value}")


def canonical_ticket_id(value: object) -> str:
    return normalize_ticket_id(value)


def normalize_snapshot_ts(value: object | None) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return datetime.now().isoformat(timespec="seconds")
    text = str(value).strip()
    if not text:
        return datetime.now().isoformat(timespec="seconds")
    try:
        return pd.to_datetime(text).to_pydatetime().isoformat(timespec="seconds")
    except Exception:
        return text


def canonical_snapshot_ts(value: object | None) -> str:
    return normalize_snapshot_ts(value)
