from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

import pandas as pd


DEFAULT_INPUT_PATH = Path("data/strategy_outputs/skip_decisions_with_calibrated_prob.csv")
DEFAULT_OUTPUT_DIR = Path("reports/comparison")
DEFAULT_TEMP_DIR = Path("reports/comparison/tmp")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare raw vs calibrated probability in BUY judgement + simulation"
    )
    parser.add_argument("--date", required=True, help="YYYY-MM-DD or YYYYMMDD")
    parser.add_argument("--input-path", type=Path, default=DEFAULT_INPUT_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--temp-dir", type=Path, default=DEFAULT_TEMP_DIR)
    parser.add_argument("--stake", type=int, default=100)
    parser.add_argument("--buy-min-ev", type=float, default=0.1)
    parser.add_argument("--buy-min-prob", type=float, default=0.0)
    parser.add_argument("--max-buy-count", type=int, default=3)
    return parser.parse_args()


def normalize_date_str(value: str) -> str:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) != 8:
        raise ValueError(f"Invalid date: {value}")
    return digits


def run_cmd(cmd: list[str]) -> int:
    print(f"\n[RUN] {' '.join(cmd)}")
    result = subprocess.run(cmd, check=False)
    print(f"[EXIT] code={result.returncode}")
    return result.returncode


def load_summary(path: Path) -> dict[str, Any]:
    empty = {
        "buy_count": 0,
        "hit_count": 0,
        "hit_rate": 0.0,
        "total_stake": 0,
        "total_payout": 0,
        "total_profit": 0,
        "roi": 0.0,
    }

    if not path.exists():
        return empty

    df = pd.read_csv(path)
    if df.empty:
        return empty

    row = df.iloc[0].to_dict()
    return {
        "buy_count": int(pd.to_numeric(row.get("buy_count", 0), errors="coerce") or 0),
        "hit_count": int(pd.to_numeric(row.get("hit_count", 0), errors="coerce") or 0),
        "hit_rate": float(pd.to_numeric(row.get("hit_rate", 0), errors="coerce") or 0),
        "total_stake": int(pd.to_numeric(row.get("total_stake", 0), errors="coerce") or 0),
        "total_payout": int(pd.to_numeric(row.get("total_payout", 0), errors="coerce") or 0),
        "total_profit": int(pd.to_numeric(row.get("total_profit", 0), errors="coerce") or 0),
        "roi": float(pd.to_numeric(row.get("roi", 0), errors="coerce") or 0),
    }


def df_to_markdown(df: pd.DataFrame) -> str:
    if df.empty:
        return "_no data_"

    cols = list(df.columns)
    lines = [
        "| " + " | ".join(cols) + " |",
        "| " + " | ".join(["---"] * len(cols)) + " |",
    ]
    for _, row in df.iterrows():
        values = []
        for col in cols:
            value = row[col]
            if pd.isna(value):
                values.append("")
            else:
                values.append(str(value))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def build_comparison_df(raw_summary: dict[str, Any], cal_summary: dict[str, Any]) -> pd.DataFrame:
    metric_keys = [
        "buy_count",
        "hit_count",
        "hit_rate",
        "total_stake",
        "total_payout",
        "total_profit",
        "roi",
    ]

    rows: list[dict[str, Any]] = []
    for key in metric_keys:
        raw_val = raw_summary.get(key, 0)
        cal_val = cal_summary.get(key, 0)
        diff = None
        if isinstance(raw_val, (int, float)) and isinstance(cal_val, (int, float)):
            diff = round(float(cal_val) - float(raw_val), 4)
        rows.append(
            {
                "metric": key,
                "raw": raw_val,
                "calibrated": cal_val,
                "diff_calibrated_minus_raw": diff,
            }
        )

    return pd.DataFrame(rows)


def decide_winner(raw_summary: dict[str, Any], cal_summary: dict[str, Any]) -> str:
    raw_roi = float(raw_summary.get("roi", 0.0))
    cal_roi = float(cal_summary.get("roi", 0.0))
    raw_profit = float(raw_summary.get("total_profit", 0.0))
    cal_profit = float(cal_summary.get("total_profit", 0.0))
    raw_hit_rate = float(raw_summary.get("hit_rate", 0.0))
    cal_hit_rate = float(cal_summary.get("hit_rate", 0.0))
    raw_buy = int(raw_summary.get("buy_count", 0))
    cal_buy = int(cal_summary.get("buy_count", 0))

    if cal_buy == 0 and raw_buy > 0:
        return "raw優勢。calibrated はBUYが消えている。"
    if raw_buy == 0 and cal_buy > 0:
        return "calibrated優勢。raw ではBUYが出ず、calibrated で母数確保できている。"
    if cal_roi > raw_roi and cal_profit > raw_profit:
        return "calibrated優勢。ROIと利益がともに改善。"
    if cal_hit_rate > raw_hit_rate and cal_roi >= raw_roi:
        return "calibratedやや優勢。勝率改善かつROI維持以上。"
    if cal_buy > raw_buy and cal_hit_rate < raw_hit_rate and cal_roi < raw_roi:
        return "raw優勢。calibrated はBUYを増やしたが質が悪化。"
    if raw_roi > cal_roi and raw_profit > cal_profit:
        return "raw優勢。校正の効果は現状限定的。"
    return "優劣は拮抗。追加日数で比較継続。"


def write_markdown_report(
    date_str: str,
    raw_summary: dict[str, Any],
    cal_summary: dict[str, Any],
    comp_df: pd.DataFrame,
    judgement: str,
    notes: list[str],
    output_path: Path,
) -> None:
    lines: list[str] = []
    lines.append("# Raw vs Calibrated Comparison")
    lines.append("")
    lines.append(f"- date: {date_str}")
    lines.append("")
    lines.append("## Raw Summary")
    lines.append("")
    for k, v in raw_summary.items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## Calibrated Summary")
    lines.append("")
    for k, v in cal_summary.items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## Comparison")
    lines.append("")
    lines.append(df_to_markdown(comp_df))
    if notes:
        lines.append("")
        lines.append("## Notes")
        lines.append("")
        for note in notes:
            lines.append(f"- {note}")
    lines.append("")
    lines.append("## Judgement")
    lines.append("")
    lines.append(judgement)
    lines.append("")
    output_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = parse_args()
    date_str = normalize_date_str(args.date)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.temp_dir.mkdir(parents=True, exist_ok=True)

    raw_judged = args.temp_dir / f"skip_decisions_rejudged_raw_{date_str}.csv"
    cal_judged = args.temp_dir / f"skip_decisions_rejudged_calibrated_{date_str}.csv"
    raw_buy = args.temp_dir / f"buy_tickets_raw_{date_str}.csv"
    cal_buy = args.temp_dir / f"buy_tickets_calibrated_{date_str}.csv"
    raw_sim_dir = args.temp_dir / f"sim_raw_{date_str}"
    cal_sim_dir = args.temp_dir / f"sim_calibrated_{date_str}"

    run_cmd(
        [
            "python",
            "scripts/run_buy_judgement_calibrated.py",
            "--input-path",
            str(args.input_path),
            "--output-path",
            str(raw_judged),
            "--buy-min-ev",
            str(args.buy_min_ev),
            "--buy-min-prob",
            str(args.buy_min_prob),
            "--max-buy-count",
            str(args.max_buy_count),
            "--prob-source",
            "raw",
        ]
    )

    run_cmd(
        [
            "python",
            "scripts/run_buy_judgement_calibrated.py",
            "--input-path",
            str(args.input_path),
            "--output-path",
            str(cal_judged),
            "--buy-min-ev",
            str(args.buy_min_ev),
            "--buy-min-prob",
            str(args.buy_min_prob),
            "--max-buy-count",
            str(args.max_buy_count),
            "--prob-source",
            "calibrated",
        ]
    )

    run_cmd(
        [
            "python",
            "scripts/build_buy_tickets.py",
            "--date",
            date_str,
            "--skip-path",
            str(raw_judged),
            "--output-path",
            str(raw_buy),
        ]
    )

    run_cmd(
        [
            "python",
            "scripts/build_buy_tickets.py",
            "--date",
            date_str,
            "--skip-path",
            str(cal_judged),
            "--output-path",
            str(cal_buy),
        ]
    )

    raw_sim_exit = run_cmd(
        [
            "python",
            "scripts/run_simulator_for_date.py",
            "--date",
            date_str,
            "--buy-path",
            str(raw_buy),
            "--output-dir",
            str(raw_sim_dir),
            "--stake",
            str(args.stake),
        ]
    )

    cal_sim_exit = run_cmd(
        [
            "python",
            "scripts/run_simulator_for_date.py",
            "--date",
            date_str,
            "--buy-path",
            str(cal_buy),
            "--output-dir",
            str(cal_sim_dir),
            "--stake",
            str(args.stake),
        ]
    )

    raw_summary = load_summary(raw_sim_dir / f"simulation_summary_{date_str}.csv")
    cal_summary = load_summary(cal_sim_dir / f"simulation_summary_{date_str}.csv")
    comp_df = build_comparison_df(raw_summary, cal_summary)
    judgement = decide_winner(raw_summary, cal_summary)
    notes: list[str] = []
    if raw_sim_exit != 0 or cal_sim_exit != 0:
        notes.append("simulation subprocess returned non-zero; summaries may be unavailable for this date")
        judgement = "比較保留。シミュレーション結果が不完全なため、日を変えて再実行してください。"

    csv_path = args.output_dir / f"raw_vs_calibrated_{date_str}.csv"
    md_path = args.output_dir / f"raw_vs_calibrated_{date_str}.md"
    json_path = args.output_dir / f"raw_vs_calibrated_{date_str}.json"

    comp_df.to_csv(csv_path, index=False, encoding="utf-8")
    write_markdown_report(date_str, raw_summary, cal_summary, comp_df, judgement, notes, md_path)

    payload = {
        "date": date_str,
        "raw_summary": raw_summary,
        "calibrated_summary": cal_summary,
        "judgement": judgement,
        "notes": notes,
        "raw_sim_exit": raw_sim_exit,
        "cal_sim_exit": cal_sim_exit,
        "comparison_rows": comp_df.to_dict(orient="records"),
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== Raw vs Calibrated Comparison ===")
    print(comp_df.to_string(index=False))
    print("\nJudgement:")
    print(judgement)
    print("\nSaved:")
    print(f"- {csv_path}")
    print(f"- {md_path}")
    print(f"- {json_path}")


if __name__ == "__main__":
    main()
