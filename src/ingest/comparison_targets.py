from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


DEFAULT_COMPARISON_TARGETS_CSV = Path("data/v2/comparison_target_days.csv")
DEFAULT_COMPARISON_TARGETS_MD = Path("COMPARISON_TARGET_DAYS.md")

VALID_STATUSES = {"TARGET", "HOLD", "EXCLUDE"}


def normalize_date_str(value: object) -> str:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _parse_bool_text(value: object) -> bool | pd.NA:
    if value is None or pd.isna(value):
        return pd.NA
    text = str(value).strip().lower()
    if text in {"yes", "y", "true", "1"}:
        return True
    if text in {"no", "n", "false", "0"}:
        return False
    return pd.NA


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "date",
            "result_txt_ready",
            "raw_incomplete",
            "real_odds_available",
            "pending_unpublished",
            "missing_fetch",
            "simulator_ok",
            "status",
            "reason",
            "action",
        ]
    )


def _load_csv(csv_path: Path) -> pd.DataFrame:
    if not csv_path.exists():
        return _empty_frame()

    df = pd.read_csv(csv_path)
    if df.empty or "date" not in df.columns or "status" not in df.columns:
        return _empty_frame()

    out = df.copy()
    out["date"] = out["date"].map(normalize_date_str)
    out["status"] = out["status"].astype(str).str.upper()
    out = out[out["status"].isin(VALID_STATUSES)].copy()
    if out.empty:
        return _empty_frame()

    for col in ["result_txt_ready", "raw_incomplete", "simulator_ok"]:
        if col in out.columns:
            out[col] = out[col].map(_parse_bool_text)
        else:
            out[col] = pd.NA

    for col in ["real_odds_available", "pending_unpublished", "missing_fetch", "reason", "action"]:
        if col not in out.columns:
            out[col] = ""

    return out[
        [
            "date",
            "result_txt_ready",
            "raw_incomplete",
            "real_odds_available",
            "pending_unpublished",
            "missing_fetch",
            "simulator_ok",
            "status",
            "reason",
            "action",
        ]
    ].drop_duplicates(subset=["date"], keep="last").sort_values("date").reset_index(drop=True)


def _clean_cell(value: str) -> str:
    return value.strip().strip("|").strip()


def _load_markdown(md_path: Path) -> pd.DataFrame:
    if not md_path.exists():
        return _empty_frame()

    rows: list[dict[str, Any]] = []
    headers: list[str] | None = None

    for raw_line in md_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line.startswith("|") or line.count("|") < 2:
            continue

        cells = [_clean_cell(cell) for cell in line.split("|")[1:-1]]
        if not any(cells):
            continue
        if all(set(cell) <= {"-", ":"} for cell in cells if cell):
            continue

        if headers is None:
            headers = cells
            continue

        if len(cells) != len(headers):
            continue

        row = dict(zip(headers, cells))
        date = normalize_date_str(row.get("date", ""))
        if not date:
            continue

        status = str(row.get("status", row.get("compare_status", ""))).strip().upper()
        if status not in VALID_STATUSES:
            continue

        rows.append(
            {
                "date": date,
                "result_txt_ready": _parse_bool_text(row.get("result_txt_ready")),
                "raw_incomplete": _parse_bool_text(row.get("raw_incomplete")),
                "real_odds_available": row.get("real_odds_available", ""),
                "pending_unpublished": row.get("pending_unpublished", ""),
                "missing_fetch": row.get("missing_fetch", ""),
                "simulator_ok": _parse_bool_text(row.get("simulator_ok")),
                "status": status,
                "reason": row.get("reason", ""),
                "action": row.get("action", ""),
            }
        )

    if not rows:
        return _empty_frame()

    df = pd.DataFrame(rows)
    return df.drop_duplicates(subset=["date"], keep="last").sort_values("date").reset_index(drop=True)


def load_comparison_target_days(source_path: Path = DEFAULT_COMPARISON_TARGETS_CSV) -> pd.DataFrame:
    if source_path.exists() and source_path.suffix.lower() == ".csv":
        return _load_csv(source_path)
    if source_path.exists() and source_path.suffix.lower() in {".md", ".markdown"}:
        return _load_markdown(source_path)
    if DEFAULT_COMPARISON_TARGETS_CSV.exists():
        return _load_csv(DEFAULT_COMPARISON_TARGETS_CSV)
    return _load_markdown(DEFAULT_COMPARISON_TARGETS_MD)


def load_comparison_target_status(date: object, source_path: Path = DEFAULT_COMPARISON_TARGETS_CSV) -> str:
    date8 = normalize_date_str(date)
    if not date8:
        return "HOLD"
    df = load_comparison_target_days(source_path)
    if df.empty:
        return "HOLD"
    match = df[df["date"] == date8]
    if match.empty:
        return "HOLD"
    return str(match.iloc[-1]["status"]).upper()
