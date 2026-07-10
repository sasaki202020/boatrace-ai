from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

DEFAULT_SIM_DIR = Path("reports/simulator")
DEFAULT_MONITOR_DIR = Path("reports/monitoring")
DEFAULT_OUTPUT_DIR = Path("reports/weekly")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", required=True, help="YYYY-MM-DD or YYYYMMDD")
    parser.add_argument("--end-date", required=True, help="YYYY-MM-DD or YYYYMMDD")
    parser.add_argument("--sim-dir", type=Path, default=DEFAULT_SIM_DIR)
    parser.add_argument("--monitor-dir", type=Path, default=DEFAULT_MONITOR_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def normalize_date_str(value: str) -> str:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) != 8:
        raise ValueError(f"Invalid date: {value}")
    return digits


def load_simulation_range(sim_dir: Path, start_date: str, end_date: str) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []

    for path in sorted(sim_dir.glob("simulation_summary_*.csv")):
        df = pd.read_csv(path)
        if df.empty or "date" not in df.columns:
            continue

        tmp = df.copy()
        tmp["date"] = tmp["date"].astype(str).str.replace(r"\D", "", regex=True).str[:8]
        tmp = tmp[(tmp["date"] >= start_date) & (tmp["date"] <= end_date)]
        tmp = tmp[tmp["date"] != "TOTAL"].copy()
        if not tmp.empty:
            rows.append(tmp)

    if not rows:
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

    merged = pd.concat(rows, ignore_index=True)
    merged = merged.sort_values("date").reset_index(drop=True)
    return merged


def load_monitoring_range(monitor_dir: Path, start_date: str, end_date: str) -> pd.DataFrame:
    path = monitor_dir / "daily_monitoring_summary.csv"
    if not path.exists():
        return pd.DataFrame(
            columns=[
                "date",
                "total_count",
                "buy_count",
                "buy_hit_count",
                "buy_hit_rate",
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
    out = out[(out["date"] >= start_date) & (out["date"] <= end_date)]
    out = out[out["date"] != "TOTAL"].copy()
    out = out.sort_values("date").reset_index(drop=True)
    return out


def safe_num(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series([0] * len(df))
    return pd.to_numeric(df[col], errors="coerce").fillna(0)


def merge_weekly(sim_df: pd.DataFrame, mon_df: pd.DataFrame) -> pd.DataFrame:
    if sim_df.empty and mon_df.empty:
        return pd.DataFrame(
            columns=[
                "date",
                "buy_count",
                "hit_count",
                "hit_rate",
                "roi",
                "total_profit",
                "avg_odds",
                "pending_count",
                "skip_count",
                "real_odds_available",
                "pending_unpublished",
                "improvement_report_top1",
            ]
        )

    if sim_df.empty:
        sim_df = pd.DataFrame(columns=["date", "buy_count", "hit_count", "hit_rate", "roi", "total_profit"])
    if mon_df.empty:
        mon_df = pd.DataFrame(
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

    keep_sim = [c for c in ["date", "buy_count", "hit_count", "hit_rate", "roi", "total_profit", "total_stake", "total_payout"] if c in sim_df.columns]
    keep_mon = [c for c in ["date", "avg_odds", "pending_count", "skip_count", "real_odds_available", "pending_unpublished", "improvement_report_top1"] if c in mon_df.columns]

    merged = pd.merge(
        sim_df[keep_sim].copy(),
        mon_df[keep_mon].copy(),
        on="date",
        how="outer",
    )
    merged["date"] = merged["date"].astype(str).str.replace(r"\D", "", regex=True).str[:8]
    for col in [
        "buy_count",
        "hit_count",
        "hit_rate",
        "roi",
        "total_profit",
        "total_stake",
        "total_payout",
        "avg_odds",
        "pending_count",
        "skip_count",
        "real_odds_available",
        "pending_unpublished",
    ]:
        if col in merged.columns:
            merged[col] = pd.to_numeric(merged[col], errors="coerce").fillna(0)
    return merged.sort_values("date").reset_index(drop=True)


def build_total_row(df: pd.DataFrame) -> dict:
    buy_count = int(safe_num(df, "buy_count").sum())
    hit_count = int(safe_num(df, "hit_count").sum())
    total_profit = int(safe_num(df, "total_profit").sum())
    total_stake = int(safe_num(df, "total_stake").sum())
    total_payout = int(safe_num(df, "total_payout").sum())
    pending_count = int(safe_num(df, "pending_count").sum())
    skip_count = int(safe_num(df, "skip_count").sum())
    real_odds_available = int(safe_num(df, "real_odds_available").sum())
    pending_unpublished = int(safe_num(df, "pending_unpublished").sum())

    hit_rate = (hit_count / buy_count) if buy_count > 0 else 0.0
    roi = (total_payout / total_stake) if total_stake > 0 else 0.0
    avg_odds = float(safe_num(df, "avg_odds").mean()) if not df.empty else 0.0

    return {
        "date": "TOTAL",
        "buy_count": buy_count,
        "hit_count": hit_count,
        "hit_rate": round(hit_rate, 4),
        "roi": round(roi, 4),
        "total_profit": total_profit,
        "total_stake": total_stake,
        "total_payout": total_payout,
        "avg_odds": round(avg_odds, 4),
        "pending_count": pending_count,
        "skip_count": skip_count,
        "real_odds_available": real_odds_available,
        "pending_unpublished": pending_unpublished,
        "improvement_report_top1": pd.NA,
    }


def decide_recommendation(
    buy_count: int,
    hit_rate: float,
    roi: float,
    avg_real_odds_available: float,
    avg_pending_unpublished: float,
) -> str:
    if buy_count == 0:
        return "BUYが0件。閾値を触る前に、オッズ取得率と候補生成を優先点検。"
    if buy_count < 5:
        return "BUY母数が少ない。変更を急がず、固定運用を継続。"
    if avg_real_odds_available <= 0:
        return "real_odds_available が弱い。ロジックより先に取得運用を改善。"
    if avg_pending_unpublished > 10:
        return "pending_unpublished が多い。時間帯運用と再取得を優先改善。"
    if hit_rate >= 0.25 and roi >= 1.0:
        return "三連単継続。大きな gate 調整は避け、上流精度の改善を続行。"
    if hit_rate < 0.15:
        return "勝率が弱い。2着3着順位と approx_prob を優先再点検。"
    if roi < 1.0:
        return "回収率が弱い。BUY条件の厳格化候補を1件だけ試す。"
    return "中間状態。変更は1件だけ、2〜3日保持して再判定。"


def build_weekly_kpis(df: pd.DataFrame) -> dict:
    if df.empty:
        return {
            "period_days": 0,
            "days_with_buy": 0,
            "weekly_buy_count": 0,
            "weekly_hit_count": 0,
            "weekly_hit_rate": 0.0,
            "weekly_roi": 0.0,
            "weekly_profit": 0,
            "avg_buy_per_day": 0.0,
            "avg_real_odds_available": 0.0,
            "avg_pending_unpublished": 0.0,
            "best_day": None,
            "worst_day": None,
            "top_improvement_report_item": None,
            "recommendation": "データ不足。運用継続して母数確保。",
        }

    buy_count = int(safe_num(df, "buy_count").sum())
    hit_count = int(safe_num(df, "hit_count").sum())
    total_profit = int(safe_num(df, "total_profit").sum())
    total_stake = int(safe_num(df, "total_stake").sum())
    total_payout = int(safe_num(df, "total_payout").sum())

    hit_rate = (hit_count / buy_count) if buy_count > 0 else 0.0
    roi = (total_payout / total_stake) if total_stake > 0 else 0.0

    tmp = df.copy()
    tmp["profit_num"] = safe_num(tmp, "total_profit")
    tmp["buy_num"] = safe_num(tmp, "buy_count")

    best_day = str(tmp.loc[tmp["profit_num"].idxmax(), "date"]) if not tmp.empty else None
    worst_day = str(tmp.loc[tmp["profit_num"].idxmin(), "date"]) if not tmp.empty else None

    top_item = None
    if "improvement_report_top1" in tmp.columns:
        vc = tmp["improvement_report_top1"].dropna().astype(str).value_counts()
        if not vc.empty:
            top_item = str(vc.index[0])

    recommendation = decide_recommendation(
        buy_count=buy_count,
        hit_rate=hit_rate,
        roi=roi,
        avg_real_odds_available=float(safe_num(df, "real_odds_available").mean()) if not df.empty else 0.0,
        avg_pending_unpublished=float(safe_num(df, "pending_unpublished").mean()) if not df.empty else 0.0,
    )

    return {
        "period_days": int(len(df)),
        "days_with_buy": int((tmp["buy_num"] > 0).sum()),
        "weekly_buy_count": buy_count,
        "weekly_hit_count": hit_count,
        "weekly_hit_rate": round(hit_rate, 4),
        "weekly_roi": round(roi, 4),
        "weekly_profit": total_profit,
        "avg_buy_per_day": round(float(tmp["buy_num"].mean()), 4),
        "avg_real_odds_available": round(float(safe_num(df, "real_odds_available").mean()), 4),
        "avg_pending_unpublished": round(float(safe_num(df, "pending_unpublished").mean()), 4),
        "best_day": best_day,
        "worst_day": worst_day,
        "top_improvement_report_item": top_item,
        "recommendation": recommendation,
    }


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


def _jsonable(value: object) -> object:
    if value is pd.NA:
        return None
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def write_markdown_report(daily_df: pd.DataFrame, total_row: dict, kpis: dict, output_path: Path) -> None:
    lines: list[str] = []
    lines.append("# Weekly Integrated Report")
    lines.append("")
    lines.append("## Weekly KPI")
    lines.append("")
    for key, value in kpis.items():
        lines.append(f"- {key}: {value}")
    lines.append("")
    lines.append("## Total")
    lines.append("")
    for key, value in total_row.items():
        lines.append(f"- {key}: {value}")
    lines.append("")
    lines.append("## Daily Table")
    lines.append("")
    lines.append(df_to_markdown(daily_df))
    lines.append("")
    output_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = parse_args()
    start_date = normalize_date_str(args.start_date)
    end_date = normalize_date_str(args.end_date)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    sim_df = load_simulation_range(args.sim_dir, start_date, end_date)
    mon_df = load_monitoring_range(args.monitor_dir, start_date, end_date)
    merged_df = merge_weekly(sim_df, mon_df)

    total_row = build_total_row(merged_df)
    total_df = pd.DataFrame([{col: total_row.get(col, pd.NA) for col in merged_df.columns}])
    final_df = pd.concat([merged_df, total_df], ignore_index=True)
    kpis = build_weekly_kpis(merged_df)

    base_name = f"weekly_report_{start_date}_{end_date}"
    csv_path = args.output_dir / f"{base_name}.csv"
    md_path = args.output_dir / f"{base_name}.md"
    json_path = args.output_dir / f"{base_name}.json"

    final_df.to_csv(csv_path, index=False, encoding="utf-8")
    write_markdown_report(merged_df, total_row, kpis, md_path)

    payload = {
        "kpi": kpis,
        "total": total_row,
        "rows": final_df.to_dict(orient="records"),
    }
    json_path.write_text(json.dumps(_jsonable(payload), ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== Weekly Integrated Report ===")
    print(final_df.to_string(index=False))
    print("\n=== Weekly KPI ===")
    for k, v in kpis.items():
        print(f"{k}: {v}")
    print("\nSaved:")
    print(f"- {csv_path}")
    print(f"- {md_path}")
    print(f"- {json_path}")


if __name__ == "__main__":
    main()
