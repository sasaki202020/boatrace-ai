from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


DEFAULT_SOURCE_PATH = Path("data/strategy_outputs/skip_decisions.csv")
DEFAULT_CALIBRATED_ROWS_PATH = Path("reports/calibration/approx_prob_calibrated_rows_latest.csv")
DEFAULT_OUTPUT_PATH = Path("data/strategy_outputs/skip_decisions_with_calibrated_prob.csv")

KEY_COLS = ["date", "race_key", "ticket"]


def normalize_date_str(value: object) -> str:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return digits[:8]


def normalize_ticket(value: object) -> str:
    text = str(value).strip()
    nums = [ch for ch in text if ch in "123456"]
    if len(nums) >= 3:
        return f"{nums[0]}-{nums[1]}-{nums[2]}"
    return text


def normalize_race_key(value: object, date_str: str) -> str:
    text = str(value).strip()
    if text.startswith("d") and "-c" in text and "-r" in text:
        return text

    digits = "".join(ch if ch.isdigit() else "-" for ch in text)
    parts = [p for p in digits.split("-") if p]
    if len(parts) >= 3 and len(parts[0]) == 8:
        return f"d{parts[0]}-c{parts[1].zfill(2)}-r{parts[2].zfill(2)}"
    if len(parts) >= 2:
        return f"d{date_str}-c{parts[0].zfill(2)}-r{parts[1].zfill(2)}"
    return text


def _pick_first_existing_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    lowered = {str(c).lower(): c for c in df.columns}
    for candidate in candidates:
        if candidate.lower() in lowered:
            return lowered[candidate.lower()]
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Attach calibrated_prob to original candidate file")
    parser.add_argument("--source-path", type=Path, default=DEFAULT_SOURCE_PATH)
    parser.add_argument("--calibrated-rows-path", type=Path, default=DEFAULT_CALIBRATED_ROWS_PATH)
    parser.add_argument("--output-path", type=Path, default=DEFAULT_OUTPUT_PATH)
    return parser.parse_args()


def _resolve_calibrated_rows_path(path: Path) -> Path:
    if path.exists():
        return path
    candidates = sorted(path.parent.glob("approx_prob_calibrated_rows_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    if candidates:
        return candidates[0]
    raise FileNotFoundError(f"calibrated rows not found: {path}")


def _ensure_race_key(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "race_key" in out.columns:
        return out
    race_col = _pick_first_existing_column(out, ["race_key", "race_id", "race_code", "race"])
    if race_col is None:
        raise ValueError("source file needs race_key or race_id-like column")
    if "date" not in out.columns:
        raise ValueError("source file needs date column")
    out["race_key"] = out.apply(lambda row: normalize_race_key(row[race_col], row["date"]), axis=1)
    return out


def _ensure_ticket(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "ticket" in out.columns:
        return out

    ticket_col = _pick_first_existing_column(
        out,
        [
            "ticket",
            "trifecta",
            "recommended_trifecta",
            "candidate",
            "prediction",
            "buy_ticket",
            "predicted_ticket",
        ],
    )
    if ticket_col is not None:
        out["ticket"] = out[ticket_col].map(normalize_ticket)
        return out

    first_col = _pick_first_existing_column(out, ["first_lane", "first", "lane1"])
    second_col = _pick_first_existing_column(out, ["second_lane", "second", "lane2"])
    third_col = _pick_first_existing_column(out, ["third_lane", "third", "lane3"])
    if first_col and second_col and third_col:
        out["ticket"] = out.apply(
            lambda row: normalize_ticket(f"{row[first_col]}-{row[second_col]}-{row[third_col]}"),
            axis=1,
        )
        return out

    raise ValueError("source file needs ticket-like or lane columns")


def main() -> None:
    args = parse_args()

    if not args.source_path.exists():
        raise FileNotFoundError(f"source not found: {args.source_path}")

    calibrated_rows_path = _resolve_calibrated_rows_path(args.calibrated_rows_path)
    source_df = pd.read_csv(args.source_path)
    cal_df = pd.read_csv(calibrated_rows_path)

    for df in [source_df, cal_df]:
        if "date" in df.columns:
            df["date"] = df["date"].map(normalize_date_str)
        if "ticket" in df.columns:
            df["ticket"] = df["ticket"].map(normalize_ticket)
        if "race_key" in df.columns:
            df["race_key"] = df["race_key"].astype(str).str.strip()

    source_df = _ensure_race_key(source_df)
    source_df = _ensure_ticket(source_df)

    required_cal_cols = {"date", "race_key", "ticket", "calibrated_prob"}
    missing = required_cal_cols - set(cal_df.columns)
    if missing:
        raise ValueError(f"calibrated rows missing columns: {sorted(missing)}")

    merged = source_df.merge(cal_df[["date", "race_key", "ticket", "calibrated_prob"]], on=KEY_COLS, how="left")

    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(args.output_path, index=False, encoding="utf-8")

    print(f"Saved: {args.output_path}")
    print(f"rows: {len(merged)}")
    print(f"calibrated_prob attached rows: {merged['calibrated_prob'].notna().sum() if 'calibrated_prob' in merged.columns else 0}")


if __name__ == "__main__":
    main()
