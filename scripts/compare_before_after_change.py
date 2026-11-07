from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

DEFAULT_CHANGE_LOG = Path("reports/monitoring/change_log.csv")
DEFAULT_SIM_DIR = Path("reports/simulator")
DEFAULT_MONITOR_PATH = Path("reports/monitoring/daily_monitoring_summary.csv")
DEFAULT_OUTPUT_DIR = Path("reports/analysis")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare before/after metrics around a logged parameter change"
    )
    parser.add_argument("--change-date", required=True, help="YYYY-MM-DD or YYYYMMDD")
    parser.add_argument("--change-key", required=True, help="e.g. max_buy_count")
    parser.add_argument("--window-days", type=int, default=3, help="Number of days before and after to compare")
    parser.add_argument("--change-log", type=Path, default=DEFAULT_CHANGE_LOG)
    parser.add_argument("--sim-dir", type=Path, default=DEFAULT_SIM_DIR)
    parser.add_argument("--monitor-path", type=Path, default=DEFAULT_MONITOR_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def normalize_date_str(value: str) -> str:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) != 8:
        raise ValueError(f"Invalid date: {value}")
    return digits


def ymd_to_ts(ymd: str) -> pd.Timestamp:
    return pd.to_datetime(ymd, format="%Y%m%d", errors="raise")


def safe_num(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series([0] * len(df), dtype="float64")
    return pd.to_numeric(df[col], errors="coerce").fillna(0)


def load_change_log(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"change_log not found: {path}")

    df = pd.read_csv(path)
    if df.empty:
        raise ValueError("change_log.csv is empty")

    required = {"change_date", "change_key"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"change_log missing columns: {sorted(missing)}")

    df["change_date_norm"] = df["change_date"].astype(str).str.replace(r"\D", "", regex=True).str[:8]
    df["change_key_norm"] = df["change_key"].astype(str).str.strip()
    return df


def select_change_row(change_df: pd.DataFrame, change_date: str, change_key: str) -> pd.Series:
    matched = change_df[
        (change_df["change_date_norm"] == change_date) & (change_df["change_key_norm"] == change_key)
    ].copy()
    if matched.empty:
        raise ValueError(
            f"No matching change found for change_date={change_date}, change_key={change_key}"
        )
    matched = matched.sort_values(["change_date_norm", "change_key_norm"]).reset_index(drop=True)
    return matched.iloc[-1]


def load_simulation_daily(sim_dir: Path) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    for path in sorted(sim_dir.glob("simulation_summary_*.csv")):
        df = pd.read_csv(path)
        if df.empty or "date" not in df.columns:
            continue
        tmp = df.copy()
        tmp["date"] = tmp["date"].astype(str).str.replace(r"\D", "", regex=True).str[:8]
        parts.append(tmp)

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
            ]
        )

    merged = pd.concat(parts, ignore_index=True)
    merged = merged.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
    return merged


def load_monitoring_daily(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(
            columns=[
                "date",
                "avg_odds",
                "pending_count",
                "skip_count",
                "real_odds_available",
                "pending_unpublished",
                "improvement_report_top1",
            ]
        )

    df = pd.read_csv(path)
    if df.empty or "date" not in df.columns:
        return pd.DataFrame()

    out = df.copy()
    out["date"] = out["date"].astype(str).str.replace(r"\D", "", regex=True).str[:8]
    out = out[out["date"] != "TOTAL"].copy()
    out = out.sort_values("date").reset_index(drop=True)
    return out


def merge_daily(sim_df: pd.DataFrame, mon_df: pd.DataFrame) -> pd.DataFrame:
    if sim_df.empty and mon_df.empty:
        return pd.DataFrame(columns=["date"])
    if sim_df.empty:
        sim_df = pd.DataFrame(columns=["date"])
    if mon_df.empty:
        mon_df = pd.DataFrame(columns=["date"])
    merged = pd.merge(sim_df, mon_df, on="date", how="outer")
    return merged.sort_values("date").reset_index(drop=True)


def split_before_after(df: pd.DataFrame, change_date: str, window_days: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = df.copy()
    df["date_ts"] = pd.to_datetime(df["date"], format="%Y%m%d", errors="coerce")
    change_ts = ymd_to_ts(change_date)

    before_start = change_ts - pd.Timedelta(days=window_days)
    before_end = change_ts - pd.Timedelta(days=1)
    after_start = change_ts
    after_end = change_ts + pd.Timedelta(days=window_days - 1)

    before_df = df[(df["date_ts"] >= before_start) & (df["date_ts"] <= before_end)].copy()
    after_df = df[(df["date_ts"] >= after_start) & (df["date_ts"] <= after_end)].copy()
    return before_df, after_df


def summarize_window(df: pd.DataFrame, label: str) -> dict:
    if df.empty:
        return {
            "label": label,
            "days": 0,
            "buy_count": 0,
            "hit_count": 0,
            "hit_rate": 0.0,
            "roi": 0.0,
            "total_profit": 0,
            "avg_buy_per_day": 0.0,
            "avg_odds": 0.0,
            "avg_real_odds_available": 0.0,
            "avg_pending_unpublished": 0.0,
            "avg_pending_count": 0.0,
            "avg_skip_count": 0.0,
        }

    buy_count = int(safe_num(df, "buy_count").sum())
    hit_count = int(safe_num(df, "hit_count").sum())
    total_stake = float(safe_num(df, "total_stake").sum())
    total_payout = float(safe_num(df, "total_payout").sum())
    total_profit = int(safe_num(df, "total_profit").sum())
    hit_rate = (hit_count / buy_count) if buy_count > 0 else 0.0
    roi = (total_payout / total_stake) if total_stake > 0 else 0.0

    return {
        "label": label,
        "days": int(len(df)),
        "buy_count": buy_count,
        "hit_count": hit_count,
        "hit_rate": round(hit_rate, 4),
        "roi": round(roi, 4),
        "total_profit": total_profit,
        "avg_buy_per_day": round(float(safe_num(df, "buy_count").mean()), 4),
        "avg_odds": round(float(safe_num(df, "avg_odds").mean()), 4),
        "avg_real_odds_available": round(float(safe_num(df, "real_odds_available").mean()), 4),
        "avg_pending_unpublished": round(float(safe_num(df, "pending_unpublished").mean()), 4),
        "avg_pending_count": round(float(safe_num(df, "pending_count").mean()), 4),
        "avg_skip_count": round(float(safe_num(df, "skip_count").mean()), 4),
    }


def build_comparison(before: dict, after: dict) -> pd.DataFrame:
    metric_keys = [
        "days",
        "buy_count",
        "hit_count",
        "hit_rate",
        "roi",
        "total_profit",
        "avg_buy_per_day",
        "avg_odds",
        "avg_real_odds_available",
        "avg_pending_unpublished",
        "avg_pending_count",
        "avg_skip_count",
    ]

    rows: list[dict] = []
    for key in metric_keys:
        before_val = before.get(key)
        after_val = after.get(key)
        diff = None
        if isinstance(before_val, (int, float)) and isinstance(after_val, (int, float)):
            diff = round(after_val - before_val, 4)
        rows.append(
            {
                "metric": key,
                "before": before_val,
                "after": after_val,
                "diff_after_minus_before": diff,
            }
        )
    return pd.DataFrame(rows)


def decide_effect(before: dict, after: dict) -> str:
    before_buy = float(before["buy_count"])
    after_buy = float(after["buy_count"])
    before_hit_rate = float(before["hit_rate"])
    after_hit_rate = float(after["hit_rate"])
    before_roi = float(before["roi"])
    after_roi = float(after["roi"])
    before_profit = float(before["total_profit"])
    after_profit = float(after["total_profit"])

    if after["days"] == 0:
        return "AFTER期間のデータがない。判定保留。"
    if before["days"] == 0:
        return "BEFORE期間のデータがない。基準不足で判定保留。"

    improved_roi = after_roi > before_roi
    improved_hit = after_hit_rate > before_hit_rate
    improved_buy = after_buy > before_buy
    improved_profit = after_profit > before_profit

    if improved_roi and improved_hit and improved_profit:
        return "有望。ROI・hit_rate・利益が改善。維持候補。"
    if improved_buy and not improved_hit and not improved_roi:
        return "BUY件数は増えたが質が悪化。広げすぎの可能性。"
    if improved_hit and not improved_buy:
        return "勝率は改善したが母数減。厳しすぎる可能性もある。"
    if not improved_roi and not improved_hit and not improved_profit:
        return "悪化。元に戻すか、別の1変更を検討。"
    return "混在。2〜3日追加観測して再判定。"


def write_markdown_report(
    change_row: pd.Series,
    before_df: pd.DataFrame,
    after_df: pd.DataFrame,
    comparison_df: pd.DataFrame,
    effect_judgement: str,
    output_path: Path,
) -> None:
    def df_to_markdown(df: pd.DataFrame) -> str:
        if df.empty:
            return "_no data_"

        cols = list(df.columns)
        header = "| " + " | ".join(cols) + " |"
        sep = "| " + " | ".join(["---"] * len(cols)) + " |"
        rows = []
        for _, row in df.iterrows():
            values = ["" if pd.isna(row[c]) else str(row[c]) for c in cols]
            rows.append("| " + " | ".join(values) + " |")
        return "\n".join([header, sep, *rows])

    lines: list[str] = []
    lines.append("# Before / After Change Comparison")
    lines.append("")
    lines.append("## Change")
    lines.append("")
    for col in ["change_date", "change_key", "before_value", "after_value", "reason", "applied_by", "ticket"]:
        if col in change_row.index:
            lines.append(f"- {col}: {change_row[col]}")
    lines.append("")
    lines.append("## Comparison")
    lines.append("")
    lines.append(df_to_markdown(comparison_df))
    lines.append("")
    lines.append("## Effect Judgement")
    lines.append("")
    lines.append(effect_judgement)
    lines.append("")
    lines.append("## BEFORE Daily")
    lines.append("")
    lines.append(df_to_markdown(before_df))
    lines.append("")
    lines.append("## AFTER Daily")
    lines.append("")
    lines.append(df_to_markdown(after_df))
    lines.append("")
    output_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = parse_args()
    change_date = normalize_date_str(args.change_date)
    change_key = str(args.change_key).strip()

    args.output_dir.mkdir(parents=True, exist_ok=True)

    change_df = load_change_log(args.change_log)
    change_row = select_change_row(change_df, change_date, change_key)

    sim_df = load_simulation_daily(args.sim_dir)
    mon_df = load_monitoring_daily(args.monitor_path)
    merged_df = merge_daily(sim_df, mon_df)

    before_df, after_df = split_before_after(merged_df, change_date, args.window_days)

    before_summary = summarize_window(before_df, "before")
    after_summary = summarize_window(after_df, "after")
    comparison_df = build_comparison(before_summary, after_summary)
    effect_judgement = decide_effect(before_summary, after_summary)

    base_name = f"compare_{change_key}_{change_date}_w{args.window_days}"
    csv_path = args.output_dir / f"{base_name}.csv"
    md_path = args.output_dir / f"{base_name}.md"
    json_path = args.output_dir / f"{base_name}.json"

    comparison_df.to_csv(csv_path, index=False, encoding="utf-8")
    write_markdown_report(
        change_row=change_row,
        before_df=before_df.drop(columns=["date_ts"], errors="ignore"),
        after_df=after_df.drop(columns=["date_ts"], errors="ignore"),
        comparison_df=comparison_df,
        effect_judgement=effect_judgement,
        output_path=md_path,
    )

    payload = {
        "change": change_row.to_dict(),
        "before_summary": before_summary,
        "after_summary": after_summary,
        "effect_judgement": effect_judgement,
        "comparison_rows": comparison_df.to_dict(orient="records"),
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== Before / After Comparison ===")
    print(comparison_df.to_string(index=False))
    print("\n=== Effect Judgement ===")
    print(effect_judgement)
    print("\nSaved:")
    print(f"- {csv_path}")
    print(f"- {md_path}")
    print(f"- {json_path}")


if __name__ == "__main__":
    main()
