from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

import pandas as pd

DEFAULT_CHANGE_LOG_PATH = Path("reports/monitoring/change_log.csv")

CHANGE_LOG_COLUMNS = [
    "change_date",
    "change_key",
    "before_value",
    "after_value",
    "reason",
    "applied_by",
    "ticket",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Append one parameter change record to change_log.csv"
    )
    parser.add_argument("--change-date", default=None, help="YYYY-MM-DD or YYYYMMDD. Default: today")
    parser.add_argument("--change-key", required=True, help="Parameter or logic name, e.g. max_buy_count")
    parser.add_argument("--before", required=True, help="Before value")
    parser.add_argument("--after", required=True, help="After value")
    parser.add_argument("--reason", required=True, help="Short reason for the change")
    parser.add_argument("--applied-by", default="manual", help="Who applied the change")
    parser.add_argument("--ticket", default="", help="Optional experiment ticket or issue id")
    parser.add_argument("--path", type=Path, default=DEFAULT_CHANGE_LOG_PATH, help="Path to change_log.csv")
    return parser.parse_args()


def normalize_date_str(value: str | None) -> str:
    if value is None or str(value).strip() == "":
        return datetime.now().strftime("%Y-%m-%d")

    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) != 8:
        raise ValueError(f"Invalid date: {value}")
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"


def load_existing_change_log(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=CHANGE_LOG_COLUMNS)

    df = pd.read_csv(path)
    for col in CHANGE_LOG_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    return df[CHANGE_LOG_COLUMNS].copy()


def build_new_row(args: argparse.Namespace) -> dict:
    return {
        "change_date": normalize_date_str(args.change_date),
        "change_key": str(args.change_key).strip(),
        "before_value": str(args.before).strip(),
        "after_value": str(args.after).strip(),
        "reason": str(args.reason).strip(),
        "applied_by": str(args.applied_by).strip(),
        "ticket": str(args.ticket).strip(),
    }


def append_change_log(path: Path, row: dict) -> pd.DataFrame:
    df = load_existing_change_log(path)
    new_df = pd.DataFrame([row], columns=CHANGE_LOG_COLUMNS)
    out = pd.concat([df, new_df], ignore_index=True)
    out["change_date"] = out["change_date"].astype(str).str.strip()
    out = out.sort_values(["change_date", "change_key"]).reset_index(drop=True)

    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False, encoding="utf-8")
    return out


def main() -> None:
    args = parse_args()
    row = build_new_row(args)
    out = append_change_log(args.path, row)

    print("[change_log] appended 1 row")
    print(f"[change_log] path={args.path}")
    print("[change_log] latest row:")
    print(pd.DataFrame([row]).to_string(index=False))
    print(f"[change_log] total_rows={len(out)}")


if __name__ == "__main__":
    main()
