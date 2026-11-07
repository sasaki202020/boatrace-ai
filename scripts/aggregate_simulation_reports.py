from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(REPO_ROOT))

DEFAULT_SIM_DIR = Path("reports/simulator")
DEFAULT_OUTPUT_DIR = Path("reports/simulator")

SUMMARY_FILE_PATTERN = re.compile(r"simulation_summary_(\d{8})\.csv$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sim-dir",
        type=Path,
        default=DEFAULT_SIM_DIR,
        help="Directory containing simulation_summary_YYYYMMDD.csv",
    )
    parser.add_argument(
        "--start-date",
        default=None,
        help="YYYY-MM-DD or YYYYMMDD",
    )
    parser.add_argument(
        "--end-date",
        default=None,
        help="YYYY-MM-DD or YYYYMMDD",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory to save aggregated outputs",
    )
    return parser.parse_args()


def normalize_date_str(value: str | None) -> str | None:
    if value is None:
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) != 8:
        raise ValueError(f"Invalid date: {value}")
    return digits


def extract_date_from_filename(path: Path) -> str | None:
    m = SUMMARY_FILE_PATTERN.search(path.name)
    if not m:
        return None
    return m.group(1)


def _to_number(series: pd.Series | None) -> pd.Series:
    if series is None:
        return pd.Series(dtype="float64")
    return pd.to_numeric(series, errors="coerce").fillna(0)


def load_summary_files(sim_dir: Path) -> pd.DataFrame:
    if not sim_dir.exists():
        raise FileNotFoundError(f"sim dir not found: {sim_dir}")

    parts: list[pd.DataFrame] = []
    for path in sorted(sim_dir.glob("simulation_summary_*.csv")):
        file_date = extract_date_from_filename(path)
        if file_date is None:
            continue

        df = pd.read_csv(path)
        if df.empty:
            continue

        if "date" not in df.columns:
            df["date"] = file_date
        else:
            df["date"] = df["date"].astype(str).str.strip()

        df["file_date"] = file_date
        df["source_file"] = str(path)
        parts.append(df)

    if not parts:
        return pd.DataFrame(
            columns=[
                "date",
                "buy_count",
                "hit_count",
                "hit_rate",
                "total_stake",
                "total_payout",
                "total_profit",
                "roi",
                "file_date",
                "source_file",
            ]
        )

    merged = pd.concat(parts, ignore_index=True)
    merged["date"] = merged["date"].astype(str).str.replace(r"\D", "", regex=True).str[:8]
    return merged


def filter_by_date_range(df: pd.DataFrame, start_date: str | None, end_date: str | None) -> pd.DataFrame:
    out = df.copy()
    if start_date is not None:
        out = out[out["date"] >= start_date]
    if end_date is not None:
        out = out[out["date"] <= end_date]
    return out.sort_values("date").reset_index(drop=True)


def build_total_row(df: pd.DataFrame) -> dict:
    if df.empty:
        return {
            "date": "TOTAL",
            "buy_count": 0,
            "hit_count": 0,
            "hit_rate": 0.0,
            "total_stake": 0,
            "total_payout": 0,
            "total_profit": 0,
            "roi": 0.0,
        }

    buy_count = int(_to_number(df.get("buy_count")).sum())
    hit_count = int(_to_number(df.get("hit_count")).sum())
    total_stake = int(_to_number(df.get("total_stake")).sum())
    total_payout = int(_to_number(df.get("total_payout")).sum())
    total_profit = int(_to_number(df.get("total_profit")).sum())

    hit_rate = (hit_count / buy_count) if buy_count > 0 else 0.0
    roi = (total_payout / total_stake) if total_stake > 0 else 0.0

    return {
        "date": "TOTAL",
        "buy_count": buy_count,
        "hit_count": hit_count,
        "hit_rate": round(hit_rate, 4),
        "total_stake": total_stake,
        "total_payout": total_payout,
        "total_profit": total_profit,
        "roi": round(roi, 4),
    }


def build_kpi_row(df: pd.DataFrame) -> dict:
    if df.empty:
        return {
            "period_days": 0,
            "days_with_buy": 0,
            "avg_buy_per_day": 0.0,
            "avg_profit_per_day": 0.0,
            "best_day": None,
            "worst_day": None,
        }

    profit_series = _to_number(df.get("total_profit"))
    buy_series = _to_number(df.get("buy_count"))

    tmp = df.copy()
    tmp["total_profit_num"] = profit_series
    tmp["buy_count_num"] = buy_series

    best_day = None
    worst_day = None
    if not tmp.empty:
        best_idx = tmp["total_profit_num"].idxmax()
        worst_idx = tmp["total_profit_num"].idxmin()
        best_day = str(tmp.loc[best_idx, "date"])
        worst_day = str(tmp.loc[worst_idx, "date"])

    period_days = int(len(tmp))
    days_with_buy = int((tmp["buy_count_num"] > 0).sum())
    avg_buy_per_day = float(tmp["buy_count_num"].mean()) if period_days > 0 else 0.0
    avg_profit_per_day = float(tmp["total_profit_num"].mean()) if period_days > 0 else 0.0

    return {
        "period_days": period_days,
        "days_with_buy": days_with_buy,
        "avg_buy_per_day": round(avg_buy_per_day, 4),
        "avg_profit_per_day": round(avg_profit_per_day, 4),
        "best_day": best_day,
        "worst_day": worst_day,
    }


def write_markdown_report(daily_df: pd.DataFrame, total_row: dict, kpi_row: dict, output_path: Path) -> None:
    def df_to_markdown(df: pd.DataFrame) -> str:
        if df.empty:
            return "_No rows_"

        cols = list(df.columns)
        header = "| " + " | ".join(cols) + " |"
        sep = "| " + " | ".join(["---"] * len(cols)) + " |"
        rows = []
        for _, row in df.iterrows():
            values = ["" if pd.isna(row[c]) else str(row[c]) for c in cols]
            rows.append("| " + " | ".join(values) + " |")
        return "\n".join([header, sep, *rows])

    md_lines: list[str] = []
    md_lines.append("# Aggregated Simulation Report")
    md_lines.append("")
    md_lines.append("## KPI")
    md_lines.append("")
    for key, value in kpi_row.items():
        md_lines.append(f"- {key}: {value}")
    md_lines.append("")
    md_lines.append("## Total")
    md_lines.append("")
    for key, value in total_row.items():
        md_lines.append(f"- {key}: {value}")
    md_lines.append("")
    md_lines.append("## Daily Summary")
    md_lines.append("")
    md_lines.append(df_to_markdown(daily_df))
    md_lines.append("")
    output_path.write_text("\n".join(md_lines), encoding="utf-8")


def main() -> None:
    args = parse_args()
    start_date = normalize_date_str(args.start_date)
    end_date = normalize_date_str(args.end_date)

    args.output_dir.mkdir(parents=True, exist_ok=True)

    all_df = load_summary_files(args.sim_dir)
    filtered_df = filter_by_date_range(all_df, start_date=start_date, end_date=end_date)

    daily_cols = [
        "date",
        "buy_count",
        "hit_count",
        "hit_rate",
        "total_stake",
        "total_payout",
        "total_profit",
        "roi",
    ]
    daily_df = filtered_df[daily_cols].copy() if not filtered_df.empty else pd.DataFrame(columns=daily_cols)

    total_row = build_total_row(daily_df)
    kpi_row = build_kpi_row(daily_df)
    final_df = pd.concat([daily_df, pd.DataFrame([total_row])], ignore_index=True)

    suffix = ""
    if start_date or end_date:
        suffix = f"_{start_date or 'begin'}_{end_date or 'end'}"

    csv_path = args.output_dir / f"aggregated_simulation_summary{suffix}.csv"
    md_path = args.output_dir / f"aggregated_simulation_summary{suffix}.md"
    json_path = args.output_dir / f"aggregated_simulation_summary{suffix}.json"

    final_df.to_csv(csv_path, index=False, encoding="utf-8")
    write_markdown_report(daily_df, total_row, kpi_row, md_path)

    payload = {
        "kpi": kpi_row,
        "total": total_row,
        "rows": final_df.to_dict(orient="records"),
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== Aggregated Simulation Report ===")
    print(final_df.to_string(index=False))
    print("\n=== KPI ===")
    for k, v in kpi_row.items():
        print(f"{k}: {v}")
    print("\nSaved:")
    print(f"- {csv_path}")
    print(f"- {md_path}")
    print(f"- {json_path}")


if __name__ == "__main__":
    main()
