from __future__ import annotations

import argparse
import json
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
DEFAULT_OUTPUT_DIR = Path("reports/diagnostics")

APPROX_PROB_CANDIDATES = ["approx_prob", "pred_prob", "prob", "predicted_prob", "candidate_prob"]
RACE_KEY_CANDIDATES = ["race_key", "race_id", "normalized_race_key", "race_code", "race"]
TICKET_CANDIDATES = [
    "ticket",
    "trifecta",
    "candidate",
    "prediction",
    "bet_ticket",
    "recommended_trifecta",
    "buy_ticket",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diagnose approx_prob quality against realized results")
    parser.add_argument("--date", required=True, help="YYYY-MM-DD or YYYYMMDD")
    parser.add_argument("--skip-path", type=Path, default=DEFAULT_SKIP_PATH)
    parser.add_argument("--results-path", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--bins", type=int, default=5)
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
    digits = digits.replace("--", "-")
    parts = [p for p in digits.split("-") if p]
    if len(parts) >= 3 and len(parts[0]) == 8:
        return f"d{parts[0]}-c{parts[1].zfill(2)}-r{parts[2].zfill(2)}"
    if len(parts) >= 2:
        return f"d{date_str}-c{parts[0].zfill(2)}-r{parts[1].zfill(2)}"
    raise ValueError(f"Could not normalize race key: {value}")


def load_skip_df(path: Path, target_date: str) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"skip_decisions not found: {path}")

    df = pd.read_csv(path)
    if df.empty:
        return pd.DataFrame()
    if "date" not in df.columns:
        raise ValueError("skip_decisions.csv must contain date column")

    df["date_norm"] = df["date"].map(normalize_date_str)
    df = df[df["date_norm"] == target_date].copy()
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
    out["date"] = target_date
    out["race_key"] = out[race_col].map(lambda x: normalize_race_key(x, target_date))
    out["ticket"] = out[ticket_col].map(normalize_ticket)
    out["approx_prob"] = pd.to_numeric(out[prob_col], errors="coerce")
    out = out.dropna(subset=["approx_prob"]).copy()

    keep_cols = ["date", "race_key", "ticket", "approx_prob"]
    if "decision" in out.columns:
        keep_cols.append("decision")
    return out[keep_cols].drop_duplicates(subset=["date", "race_key", "ticket"]).reset_index(drop=True)


def load_results_df(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"results not found: {path}")

    df = pd.read_csv(path)
    required = {"date", "race_key", "winning_ticket"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"results missing columns: {sorted(missing)}")

    out = df.copy()
    out["date"] = out["date"].map(normalize_date_str)
    out["race_key"] = out["race_key"].astype(str).str.strip()
    out["winning_ticket"] = out["winning_ticket"].map(normalize_ticket)
    return out[["date", "race_key", "winning_ticket"]].drop_duplicates().reset_index(drop=True)


def load_results_df_for_date(target_date: str, explicit_path: Path | None = None) -> pd.DataFrame:
    if explicit_path is not None and explicit_path.exists():
        return load_results_df(explicit_path)

    result_df, result_status = load_results_for_date(target_date)
    if result_df.empty:
        return pd.DataFrame(columns=["date", "race_key", "winning_ticket"])

    return result_df[["date", "race_key", "winning_ticket"]].drop_duplicates().reset_index(drop=True)


def attach_hit_label(skip_df: pd.DataFrame, results_df: pd.DataFrame) -> pd.DataFrame:
    merged = skip_df.merge(results_df, on=["date", "race_key"], how="left")
    merged["is_hit"] = (merged["ticket"] == merged["winning_ticket"]).astype(int)
    return merged


def build_bin_summary(df: pd.DataFrame, bins: int) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["bin", "count", "avg_prob", "hit_rate", "hit_count"])

    work = df.copy().sort_values("approx_prob").reset_index(drop=True)
    unique_probs = work["approx_prob"].nunique()
    effective_bins = min(bins, max(1, unique_probs))

    if effective_bins <= 1:
        work["bin"] = "all"
    else:
        work["bin"] = pd.qcut(work["approx_prob"], q=effective_bins, duplicates="drop")

    summary = (
        work.groupby("bin", dropna=False)
        .agg(
            count=("approx_prob", "size"),
            avg_prob=("approx_prob", "mean"),
            hit_rate=("is_hit", "mean"),
            hit_count=("is_hit", "sum"),
        )
        .reset_index()
    )
    summary["avg_prob"] = summary["avg_prob"].round(4)
    summary["hit_rate"] = summary["hit_rate"].round(4)
    summary["bin"] = summary["bin"].astype(str)
    return summary


def build_topk_summary(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["k", "race_count", "hit_races", "race_hit_rate"])

    rows: list[dict[str, Any]] = []
    for k in [1, 3, 5, 10]:
        per_race = (
            df.sort_values(["race_key", "approx_prob"], ascending=[True, False])
            .groupby("race_key", as_index=False)
            .head(k)
            .groupby("race_key")
            .agg(hit_any=("is_hit", "max"))
            .reset_index()
        )

        race_count = len(per_race)
        hit_races = int(per_race["hit_any"].sum()) if not per_race.empty else 0
        race_hit_rate = (hit_races / race_count) if race_count > 0 else 0.0
        rows.append(
            {
                "k": k,
                "race_count": race_count,
                "hit_races": hit_races,
                "race_hit_rate": round(race_hit_rate, 4),
            }
        )

    return pd.DataFrame(rows)


def build_global_summary(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {
            "rows": 0,
            "race_count": 0,
            "avg_prob": 0.0,
            "global_hit_rate": 0.0,
            "correlation_prob_hit": 0.0,
        }

    corr = df[["approx_prob", "is_hit"]].corr().iloc[0, 1]
    corr = 0.0 if pd.isna(corr) else float(corr)
    return {
        "rows": int(len(df)),
        "race_count": int(df["race_key"].nunique()),
        "avg_prob": round(float(df["approx_prob"].mean()), 4),
        "global_hit_rate": round(float(df["is_hit"].mean()), 4),
        "correlation_prob_hit": round(corr, 4),
    }


def decide_diagnosis(global_summary: dict[str, Any], bin_df: pd.DataFrame, topk_df: pd.DataFrame) -> str:
    rows = int(global_summary["rows"])
    corr = float(global_summary["correlation_prob_hit"])
    if rows == 0:
        return "データなし。診断不可。"
    if topk_df.empty:
        return "top-k 診断不可。"

    top1 = topk_df[topk_df["k"] == 1]
    top5 = topk_df[topk_df["k"] == 5]
    top1_rate = float(top1["race_hit_rate"].iloc[0]) if not top1.empty else 0.0
    top5_rate = float(top5["race_hit_rate"].iloc[0]) if not top5.empty else 0.0

    monotonic = True
    if not bin_df.empty and len(bin_df) >= 2:
        hit_rates = bin_df["hit_rate"].tolist()
        monotonic = all(hit_rates[i] <= hit_rates[i + 1] for i in range(len(hit_rates) - 1))

    if corr <= 0:
        return "approx_prob が的中と正相関していない。確率推定か順位付けの見直し優先。"
    if not monotonic:
        return "高確率帯ほど当たりやすい構造が崩れている。校正または候補順の見直し優先。"
    if top1_rate < 0.1 and top5_rate < 0.3:
        return "上位候補の命中力が弱い。2着3着順位と approx_prob の両方を点検。"
    if top1_rate >= 0.15 and corr > 0.05:
        return "approx_prob は一定の序列性あり。大改修より微修正・校正向き。"
    return "中間状態。閾値調整より前に、追加観測か軽い校正を推奨。"


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


def write_markdown_report(
    global_summary: dict[str, Any],
    bin_df: pd.DataFrame,
    topk_df: pd.DataFrame,
    diagnosis: str,
    output_path: Path,
) -> None:
    lines: list[str] = []
    lines.append("# approx_prob Diagnostic Report")
    lines.append("")
    lines.append("## Global Summary")
    lines.append("")
    for k, v in global_summary.items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## Bin Summary")
    lines.append("")
    lines.append(df_to_markdown(bin_df))
    lines.append("")
    lines.append("## Top-K Summary")
    lines.append("")
    lines.append(df_to_markdown(topk_df))
    lines.append("")
    lines.append("## Diagnosis")
    lines.append("")
    lines.append(diagnosis)
    lines.append("")
    output_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = parse_args()
    target_date = normalize_date_str(args.date)

    args.output_dir.mkdir(parents=True, exist_ok=True)

    skip_df = load_skip_df(args.skip_path, target_date)
    results_path = args.results_path or (DEFAULT_RESULTS_DIR / f"results_{target_date}.csv")
    results_df = load_results_df_for_date(target_date, explicit_path=results_path)
    merged_df = attach_hit_label(skip_df, results_df)

    bin_df = build_bin_summary(merged_df, bins=args.bins)
    topk_df = build_topk_summary(merged_df)
    global_summary = build_global_summary(merged_df)
    diagnosis = decide_diagnosis(global_summary, bin_df, topk_df)

    base_name = f"approx_prob_diagnostic_{target_date}"
    merged_path = args.output_dir / f"{base_name}_rows.csv"
    bin_path = args.output_dir / f"{base_name}_bins.csv"
    topk_path = args.output_dir / f"{base_name}_topk.csv"
    md_path = args.output_dir / f"{base_name}.md"
    json_path = args.output_dir / f"{base_name}.json"

    merged_df.to_csv(merged_path, index=False, encoding="utf-8")
    bin_df.to_csv(bin_path, index=False, encoding="utf-8")
    topk_df.to_csv(topk_path, index=False, encoding="utf-8")
    write_markdown_report(global_summary, bin_df, topk_df, diagnosis, md_path)

    payload = {
        "global_summary": global_summary,
        "diagnosis": diagnosis,
        "bin_rows": bin_df.to_dict(orient="records"),
        "topk_rows": topk_df.to_dict(orient="records"),
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== approx_prob Diagnostic ===")
    print("Global Summary:")
    for k, v in global_summary.items():
        print(f"{k}: {v}")

    print("\nBin Summary:")
    print(bin_df.to_string(index=False) if not bin_df.empty else "no data")
    print("\nTop-K Summary:")
    print(topk_df.to_string(index=False) if not topk_df.empty else "no data")
    print("\nDiagnosis:")
    print(diagnosis)
    print("\nSaved:")
    print(f"- {merged_path}")
    print(f"- {bin_path}")
    print(f"- {topk_path}")
    print(f"- {md_path}")
    print(f"- {json_path}")


if __name__ == "__main__":
    main()
