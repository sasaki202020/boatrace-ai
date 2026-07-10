from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.simulator.results_loader import load_results_for_date

DEFAULT_SKIP_PATH = Path("data/strategy_outputs/skip_decisions.csv")
DEFAULT_RESULTS_DIR = Path("data/processed/simulator_inputs")
DEFAULT_OUTPUT_DIR = Path("reports/calibration")

APPROX_PROB_CANDIDATES = ["approx_prob", "pred_prob", "prob", "predicted_prob", "candidate_prob"]
RACE_KEY_CANDIDATES = ["race_key", "race_id", "normalized_race_key", "race_code", "race"]
TICKET_CANDIDATES = ["ticket", "trifecta", "candidate", "prediction", "bet_ticket", "recommended_trifecta", "buy_ticket"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Calibrate approx_prob using realized results")
    parser.add_argument("--dates", required=True, help="Comma separated dates, e.g. 2026-04-04,2026-04-05")
    parser.add_argument("--skip-path", type=Path, default=DEFAULT_SKIP_PATH)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--bins", type=int, default=10)
    parser.add_argument("--min-bin-size", type=int, default=20)
    return parser.parse_args()


def normalize_date_str(value: object) -> str:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) < 8:
        raise ValueError(f"Invalid date: {value}")
    return digits[:8]


def pick_first_existing_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    lowered = {str(c).lower(): c for c in df.columns}
    for candidate in candidates:
        if candidate.lower() in lowered:
            return lowered[candidate.lower()]
    return None


def normalize_ticket(value: object) -> str:
    text = str(value).strip()
    nums = [ch for ch in text if ch in "123456"]
    if len(nums) >= 3:
        return f"{nums[0]}-{nums[1]}-{nums[2]}"
    raise ValueError(f"Could not normalize ticket: {value}")


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
    raise ValueError(f"Could not normalize race key: {value}")


def parse_dates(text: str) -> list[str]:
    values = [normalize_date_str(x.strip()) for x in str(text).split(",") if x.strip()]
    return sorted(set(values))


def load_skip_rows(skip_path: Path, dates: list[str]) -> pd.DataFrame:
    if not skip_path.exists():
        raise FileNotFoundError(f"skip_decisions not found: {skip_path}")

    df = pd.read_csv(skip_path)
    if df.empty:
        return pd.DataFrame()
    if "date" not in df.columns:
        raise ValueError("skip_decisions.csv must contain date column")

    df["date_norm"] = df["date"].map(normalize_date_str)
    df = df[df["date_norm"].isin(dates)].copy()
    if df.empty:
        return pd.DataFrame()

    prob_col = pick_first_existing_column(df, APPROX_PROB_CANDIDATES)
    race_col = pick_first_existing_column(df, RACE_KEY_CANDIDATES)
    ticket_col = pick_first_existing_column(df, TICKET_CANDIDATES)
    if prob_col is None:
        raise ValueError(f"approx_prob column not found. candidates={APPROX_PROB_CANDIDATES}")
    if race_col is None:
        raise ValueError(f"race key column not found. candidates={RACE_KEY_CANDIDATES}")
    if ticket_col is None:
        raise ValueError(f"ticket column not found. candidates={TICKET_CANDIDATES}")

    out = df.copy()
    out["date"] = out["date_norm"]
    out["race_key"] = out.apply(lambda row: normalize_race_key(row[race_col], row["date"]), axis=1)
    out["ticket"] = out[ticket_col].map(normalize_ticket)
    out["approx_prob"] = pd.to_numeric(out[prob_col], errors="coerce")
    out = out.dropna(subset=["approx_prob"]).copy()

    keep_cols = ["date", "race_key", "ticket", "approx_prob"]
    if "decision" in out.columns:
        keep_cols.append("decision")

    return out[keep_cols].drop_duplicates(subset=["date", "race_key", "ticket"]).reset_index(drop=True)


def load_results_rows(results_dir: Path, dates: list[str]) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []

    for date_str in dates:
        path = results_dir / f"results_{date_str}.csv"
        if path.exists():
            df = pd.read_csv(path)
            required = {"date", "race_key", "winning_ticket"}
            missing = required - set(df.columns)
            if missing:
                raise ValueError(f"{path} missing columns: {sorted(missing)}")

            out = df.copy()
            out["date"] = out["date"].map(normalize_date_str)
            out["race_key"] = out["race_key"].astype(str).str.strip()
            out["winning_ticket"] = out["winning_ticket"].map(normalize_ticket)
            parts.append(out[["date", "race_key", "winning_ticket"]])
            continue

        raw_df, load_result = load_results_for_date(date_str)
        if raw_df.empty:
            print(f"[calibrate] skip results {date_str}: {load_result.status} ({load_result.warning or 'ok'})")
            continue

        if {"date", "race_key", "winning_ticket"} - set(raw_df.columns):
            raise ValueError(f"raw results for {date_str} missing required columns")

        out = raw_df.copy()
        out["date"] = out["date"].map(normalize_date_str)
        out["race_key"] = out["race_key"].astype(str).str.strip()
        out["winning_ticket"] = out["winning_ticket"].map(normalize_ticket)
        parts.append(out[["date", "race_key", "winning_ticket"]])

    if not parts:
        return pd.DataFrame(columns=["date", "race_key", "winning_ticket"])

    return pd.concat(parts, ignore_index=True).drop_duplicates().reset_index(drop=True)


def attach_hit_label(skip_df: pd.DataFrame, results_df: pd.DataFrame) -> pd.DataFrame:
    merged = skip_df.merge(results_df, on=["date", "race_key"], how="left")
    merged["is_hit"] = (merged["ticket"] == merged["winning_ticket"]).astype(int)
    return merged


def build_calibration_table(df: pd.DataFrame, bins: int, min_bin_size: int) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["bin_order", "bin", "count", "avg_prob", "empirical_hit_rate", "calibrated_prob"])

    work = df.copy().sort_values("approx_prob").reset_index(drop=True)
    unique_probs = work["approx_prob"].nunique()
    effective_bins = min(max(1, bins), max(1, unique_probs))

    if effective_bins == 1:
        work["bin"] = "all"
    else:
        work["bin"] = pd.qcut(work["approx_prob"], q=effective_bins, duplicates="drop")

    summary = (
        work.groupby("bin", dropna=False)
        .agg(count=("approx_prob", "size"), avg_prob=("approx_prob", "mean"), empirical_hit_rate=("is_hit", "mean"))
        .reset_index()
    )
    summary["bin"] = summary["bin"].astype(str)
    summary["avg_prob"] = pd.to_numeric(summary["avg_prob"], errors="coerce")
    summary["empirical_hit_rate"] = pd.to_numeric(summary["empirical_hit_rate"], errors="coerce")
    summary = summary.sort_values("avg_prob").reset_index(drop=True)
    summary["bin_order"] = range(1, len(summary) + 1)

    global_hit_rate = float(df["is_hit"].mean()) if len(df) > 0 else 0.0
    calibrated_values: list[float] = []
    for _, row in summary.iterrows():
        count = int(row["count"])
        empirical = float(row["empirical_hit_rate"])
        weight = min(1.0, count / max(1, min_bin_size))
        calibrated = empirical * weight + global_hit_rate * (1.0 - weight)
        calibrated_values.append(calibrated)

    summary["calibrated_prob_raw"] = calibrated_values

    monotonic_vals: list[float] = []
    current_max = 0.0
    for val in summary["calibrated_prob_raw"].tolist():
        current_max = max(current_max, float(val))
        monotonic_vals.append(current_max)

    summary["calibrated_prob"] = pd.Series(monotonic_vals).clip(lower=0.0, upper=1.0)
    summary["avg_prob"] = summary["avg_prob"].round(4)
    summary["empirical_hit_rate"] = summary["empirical_hit_rate"].round(4)
    summary["calibrated_prob"] = summary["calibrated_prob"].round(4)

    return summary[["bin_order", "bin", "count", "avg_prob", "empirical_hit_rate", "calibrated_prob"]].copy()


def apply_calibration(df: pd.DataFrame, calib_df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or calib_df.empty:
        out = df.copy()
        if "calibrated_prob" not in out.columns:
            out["calibrated_prob"] = pd.NA
        return out

    bins = calib_df.sort_values("avg_prob").reset_index(drop=True)
    avg_probs = bins["avg_prob"].tolist()
    calibrated_probs = bins["calibrated_prob"].tolist()

    def map_prob(p: float) -> float:
        if pd.isna(p):
            return float("nan")
        best_idx = min(range(len(avg_probs)), key=lambda i: abs(float(p) - float(avg_probs[i])))
        return float(calibrated_probs[best_idx])

    out = df.copy()
    out["calibrated_prob"] = out["approx_prob"].map(map_prob)
    out["calibrated_prob"] = pd.to_numeric(out["calibrated_prob"], errors="coerce")
    return out


def summarize_before_after(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {"rows": 0, "avg_approx_prob": 0.0, "avg_calibrated_prob": 0.0, "global_hit_rate": 0.0}

    return {
        "rows": int(len(df)),
        "avg_approx_prob": round(float(df["approx_prob"].mean()), 4),
        "avg_calibrated_prob": round(float(df["calibrated_prob"].mean()), 4),
        "global_hit_rate": round(float(df["is_hit"].mean()), 4),
    }


def decide_calibration_message(calib_df: pd.DataFrame) -> str:
    if calib_df.empty:
        return "校正不可。データ不足。"
    if len(calib_df) < 2:
        return "校正表は作成できたが、母数が少ない。追加観測優先。"

    avg_prob = calib_df["avg_prob"].tolist()
    empirical = calib_df["empirical_hit_rate"].tolist()
    calibrated = calib_df["calibrated_prob"].tolist()
    raw_gap = sum(abs(a - b) for a, b in zip(avg_prob, empirical)) / len(calib_df)
    cal_gap = sum(abs(c - b) for c, b in zip(calibrated, empirical)) / len(calib_df)

    if cal_gap < raw_gap:
        return "校正表は有効。閾値調整前に calibrated_prob を試す価値あり。"
    return "校正効果は限定的。approx_prob 本体か順位モデル側の改善が優先。"


def df_to_markdown(df: pd.DataFrame) -> str:
    if df.empty:
        return "_no data_"
    cols = list(df.columns)
    header = "| " + " | ".join(cols) + " |"
    sep = "| " + " | ".join(["---"] * len(cols)) + " |"
    rows = []
    for _, row in df.iterrows():
        values = []
        for c in cols:
            value = row[c]
            if isinstance(value, list):
                values.append(" / ".join(str(v) for v in value))
            elif isinstance(value, dict):
                values.append(json.dumps(value, ensure_ascii=False))
            elif pd.isna(value):
                values.append("")
            else:
                values.append(str(value))
        rows.append("| " + " | ".join(values) + " |")
    return "\n".join([header, sep, *rows])


def write_markdown_report(summary_obj: dict[str, Any], calib_df: pd.DataFrame, message: str, output_path: Path) -> None:
    lines: list[str] = []
    lines.append("# approx_prob Calibration Report")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    for k, v in summary_obj.items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## Calibration Table")
    lines.append("")
    lines.append(df_to_markdown(calib_df))
    lines.append("")
    lines.append("## Message")
    lines.append("")
    lines.append(message)
    lines.append("")
    output_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = parse_args()
    dates = parse_dates(args.dates)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    skip_df = load_skip_rows(args.skip_path, dates)
    results_df = load_results_rows(args.results_dir, dates)
    merged_df = attach_hit_label(skip_df, results_df)

    calib_df = build_calibration_table(merged_df, bins=args.bins, min_bin_size=args.min_bin_size)
    calibrated_df = apply_calibration(merged_df, calib_df)

    summary_obj = summarize_before_after(calibrated_df)
    summary_obj["dates"] = dates
    summary_obj["bins"] = args.bins
    summary_obj["min_bin_size"] = args.min_bin_size

    message = decide_calibration_message(calib_df)

    suffix = f"{dates[0]}_{dates[-1]}" if dates else "none"
    rows_path = args.output_dir / f"approx_prob_calibrated_rows_{suffix}.csv"
    table_path = args.output_dir / f"approx_prob_calibration_table_{suffix}.csv"
    md_path = args.output_dir / f"approx_prob_calibration_{suffix}.md"
    json_path = args.output_dir / f"approx_prob_calibration_{suffix}.json"
    latest_rows_path = args.output_dir / "approx_prob_calibrated_rows_latest.csv"
    latest_table_path = args.output_dir / "approx_prob_calibration_table_latest.csv"
    latest_md_path = args.output_dir / "approx_prob_calibration_latest.md"
    latest_json_path = args.output_dir / "approx_prob_calibration_latest.json"

    calibrated_df.to_csv(rows_path, index=False, encoding="utf-8")
    calib_df.to_csv(table_path, index=False, encoding="utf-8")
    write_markdown_report(summary_obj, calib_df, message, md_path)
    shutil.copyfile(rows_path, latest_rows_path)
    shutil.copyfile(table_path, latest_table_path)
    shutil.copyfile(md_path, latest_md_path)

    payload = {"summary": summary_obj, "message": message, "calibration_rows": calib_df.to_dict(orient="records")}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== approx_prob Calibration ===")
    print("Summary:")
    for k, v in summary_obj.items():
        print(f"{k}: {v}")
    print("\nCalibration Table:")
    print(calib_df.to_string(index=False) if not calib_df.empty else "no data")
    print("\nMessage:")
    print(message)
    print("\nSaved:")
    print(f"- {rows_path}")
    print(f"- {table_path}")
    print(f"- {md_path}")
    print(f"- {json_path}")
    print(f"- {latest_rows_path}")


if __name__ == "__main__":
    main()
