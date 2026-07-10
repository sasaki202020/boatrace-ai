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

RACE_KEY_CANDIDATES = ["race_key", "race_id", "normalized_race_key", "race_code", "race"]
TICKET_CANDIDATES = ["ticket", "trifecta", "candidate", "prediction", "bet_ticket", "recommended_trifecta", "buy_ticket"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diagnose rank quality for trifecta candidate ordering")
    parser.add_argument("--date", required=True, help="YYYY-MM-DD or YYYYMMDD")
    parser.add_argument("--skip-path", type=Path, default=DEFAULT_SKIP_PATH)
    parser.add_argument("--results-path", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--topk-list", default="1,3,5,10", help="Comma separated top-k values")
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


def parse_ticket_parts(ticket: str) -> tuple[str, str, str]:
    a, b, c = ticket.split("-")
    return a, b, c


def parse_topk_list(text: str) -> list[int]:
    values: list[int] = []
    for part in str(text).split(","):
        part = part.strip()
        if not part:
            continue
        values.append(int(part))
    return sorted(set(v for v in values if v > 0))


def load_candidates_df(path: Path, target_date: str) -> pd.DataFrame:
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

    race_col = pick_first_existing_column(df, RACE_KEY_CANDIDATES)
    ticket_col = pick_first_existing_column(df, TICKET_CANDIDATES)
    if race_col is None:
        raise ValueError(f"race key column not found. candidates={RACE_KEY_CANDIDATES}")
    if ticket_col is None:
        raise ValueError(f"ticket column not found. candidates={TICKET_CANDIDATES}")

    out = df.copy()
    out["date"] = target_date
    out["race_key"] = out[race_col].map(lambda x: normalize_race_key(x, target_date))
    out["ticket"] = out[ticket_col].map(normalize_ticket)
    if "approx_prob" in out.columns:
        out["rank_score"] = pd.to_numeric(out["approx_prob"], errors="coerce")
    else:
        rank_col = pick_first_existing_column(out, ["rank", "candidate_rank", "pred_rank"])
        if rank_col:
            out["rank_score"] = -pd.to_numeric(out[rank_col], errors="coerce")
        else:
            out["rank_score"] = 0.0
    out["rank_score"] = pd.to_numeric(out["rank_score"], errors="coerce").fillna(0.0)

    firsts: list[str] = []
    seconds: list[str] = []
    thirds: list[str] = []
    for ticket in out["ticket"]:
        a, b, c = parse_ticket_parts(ticket)
        firsts.append(a)
        seconds.append(b)
        thirds.append(c)

    out["pred_first"] = firsts
    out["pred_second"] = seconds
    out["pred_third"] = thirds

    keep_cols = ["date", "race_key", "ticket", "rank_score", "pred_first", "pred_second", "pred_third"]
    if "decision" in out.columns:
        keep_cols.append("decision")

    return (
        out[keep_cols]
        .drop_duplicates(subset=["date", "race_key", "ticket"])
        .sort_values(["race_key", "rank_score"], ascending=[True, False])
        .reset_index(drop=True)
    )


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

    truths_first: list[str] = []
    truths_second: list[str] = []
    truths_third: list[str] = []
    for ticket in out["winning_ticket"]:
        a, b, c = parse_ticket_parts(ticket)
        truths_first.append(a)
        truths_second.append(b)
        truths_third.append(c)

    out["true_first"] = truths_first
    out["true_second"] = truths_second
    out["true_third"] = truths_third
    return out[["date", "race_key", "winning_ticket", "true_first", "true_second", "true_third"]].drop_duplicates().reset_index(drop=True)


def load_results_df_for_date(target_date: str, explicit_path: Path | None = None) -> pd.DataFrame:
    if explicit_path is not None and explicit_path.exists():
        return load_results_df(explicit_path)

    result_df, result_status = load_results_for_date(target_date)
    if result_df.empty:
        return pd.DataFrame(columns=["date", "race_key", "winning_ticket", "true_first", "true_second", "true_third"])

    out = result_df[["date", "race_key", "winning_ticket"]].copy()
    truths_first: list[str] = []
    truths_second: list[str] = []
    truths_third: list[str] = []
    for ticket in out["winning_ticket"]:
        a, b, c = parse_ticket_parts(ticket)
        truths_first.append(a)
        truths_second.append(b)
        truths_third.append(c)
    out["true_first"] = truths_first
    out["true_second"] = truths_second
    out["true_third"] = truths_third
    return out.drop_duplicates().reset_index(drop=True)


def attach_labels(candidates_df: pd.DataFrame, results_df: pd.DataFrame) -> pd.DataFrame:
    merged = candidates_df.merge(results_df, on=["date", "race_key"], how="left")
    merged["exact_hit"] = (merged["ticket"] == merged["winning_ticket"]).astype(int)
    merged["first_hit"] = (merged["pred_first"] == merged["true_first"]).astype(int)
    merged["second_hit"] = (merged["pred_second"] == merged["true_second"]).astype(int)
    merged["third_hit"] = (merged["pred_third"] == merged["true_third"]).astype(int)
    merged["first_second_hit"] = ((merged["pred_first"] == merged["true_first"]) & (merged["pred_second"] == merged["true_second"])).astype(int)
    merged["first_third_hit"] = ((merged["pred_first"] == merged["true_first"]) & (merged["pred_third"] == merged["true_third"])).astype(int)
    merged["second_third_hit"] = ((merged["pred_second"] == merged["true_second"]) & (merged["pred_third"] == merged["true_third"])).astype(int)
    merged["set_match_3"] = (
        merged.apply(lambda row: set([row["pred_first"], row["pred_second"], row["pred_third"]]) == set([row["true_first"], row["true_second"], row["true_third"]]), axis=1)
    ).astype(int)
    merged["position_match_count"] = merged["first_hit"] + merged["second_hit"] + merged["third_hit"]
    return merged


def build_topk_rank_summary(df: pd.DataFrame, topk_list: list[int]) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(
            columns=[
                "k",
                "race_count",
                "exact_hit_races",
                "exact_hit_rate",
                "first_hit_races",
                "first_hit_rate",
                "first_second_hit_races",
                "first_second_hit_rate",
                "set_match_3_races",
                "set_match_3_rate",
            ]
        )

    rows: list[dict[str, Any]] = []
    for k in topk_list:
        per_race = (
            df.sort_values(["race_key", "rank_score"], ascending=[True, False])
            .groupby("race_key", as_index=False)
            .head(k)
            .groupby("race_key")
            .agg(
                exact_hit_any=("exact_hit", "max"),
                first_hit_any=("first_hit", "max"),
                first_second_hit_any=("first_second_hit", "max"),
                set_match_3_any=("set_match_3", "max"),
            )
            .reset_index()
        )

        race_count = len(per_race)
        exact_hit_races = int(per_race["exact_hit_any"].sum()) if not per_race.empty else 0
        first_hit_races = int(per_race["first_hit_any"].sum()) if not per_race.empty else 0
        first_second_hit_races = int(per_race["first_second_hit_any"].sum()) if not per_race.empty else 0
        set_match_3_races = int(per_race["set_match_3_any"].sum()) if not per_race.empty else 0

        rows.append(
            {
                "k": k,
                "race_count": race_count,
                "exact_hit_races": exact_hit_races,
                "exact_hit_rate": round(exact_hit_races / race_count, 4) if race_count > 0 else 0.0,
                "first_hit_races": first_hit_races,
                "first_hit_rate": round(first_hit_races / race_count, 4) if race_count > 0 else 0.0,
                "first_second_hit_races": first_second_hit_races,
                "first_second_hit_rate": round(first_second_hit_races / race_count, 4) if race_count > 0 else 0.0,
                "set_match_3_races": set_match_3_races,
                "set_match_3_rate": round(set_match_3_races / race_count, 4) if race_count > 0 else 0.0,
            }
        )

    return pd.DataFrame(rows)


def build_top1_position_summary(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {
            "race_count": 0,
            "top1_exact_hit_rate": 0.0,
            "top1_first_hit_rate": 0.0,
            "top1_second_hit_rate": 0.0,
            "top1_third_hit_rate": 0.0,
            "top1_first_second_hit_rate": 0.0,
            "top1_set_match_3_rate": 0.0,
            "top1_avg_position_match_count": 0.0,
        }

    top1 = df.sort_values(["race_key", "rank_score"], ascending=[True, False]).groupby("race_key", as_index=False).head(1).reset_index(drop=True)
    race_count = len(top1)
    return {
        "race_count": race_count,
        "top1_exact_hit_rate": round(float(top1["exact_hit"].mean()), 4) if race_count > 0 else 0.0,
        "top1_first_hit_rate": round(float(top1["first_hit"].mean()), 4) if race_count > 0 else 0.0,
        "top1_second_hit_rate": round(float(top1["second_hit"].mean()), 4) if race_count > 0 else 0.0,
        "top1_third_hit_rate": round(float(top1["third_hit"].mean()), 4) if race_count > 0 else 0.0,
        "top1_first_second_hit_rate": round(float(top1["first_second_hit"].mean()), 4) if race_count > 0 else 0.0,
        "top1_set_match_3_rate": round(float(top1["set_match_3"].mean()), 4) if race_count > 0 else 0.0,
        "top1_avg_position_match_count": round(float(top1["position_match_count"].mean()), 4) if race_count > 0 else 0.0,
    }


def build_error_profile(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {
            "rows": 0,
            "avg_position_match_count": 0.0,
            "first_only_rate": 0.0,
            "first_ok_but_tail_ng_rate": 0.0,
            "all_boats_included_but_order_ng_rate": 0.0,
        }

    first_only = ((df["first_hit"] == 1) & (df["second_hit"] == 0) & (df["third_hit"] == 0)).mean()
    first_ok_but_tail_ng = ((df["first_hit"] == 1) & (df["exact_hit"] == 0)).mean()
    all_boats_included_but_order_ng = ((df["set_match_3"] == 1) & (df["exact_hit"] == 0)).mean()

    return {
        "rows": int(len(df)),
        "avg_position_match_count": round(float(df["position_match_count"].mean()), 4),
        "first_only_rate": round(float(first_only), 4),
        "first_ok_but_tail_ng_rate": round(float(first_ok_but_tail_ng), 4),
        "all_boats_included_but_order_ng_rate": round(float(all_boats_included_but_order_ng), 4),
    }


def decide_rank_diagnosis(top1_summary: dict[str, Any], topk_df: pd.DataFrame, error_profile: dict[str, Any]) -> str:
    race_count = int(top1_summary["race_count"])
    if race_count == 0:
        return "データなし。診断不可。"

    top1_exact = float(top1_summary["top1_exact_hit_rate"])
    top1_first = float(top1_summary["top1_first_hit_rate"])
    top1_second = float(top1_summary["top1_second_hit_rate"])
    top1_third = float(top1_summary["top1_third_hit_rate"])
    first_ok_tail_ng = float(error_profile["first_ok_but_tail_ng_rate"])
    all_included_order_ng = float(error_profile["all_boats_included_but_order_ng_rate"])

    top5 = topk_df[topk_df["k"] == 5]
    top5_exact = float(top5["exact_hit_rate"].iloc[0]) if not top5.empty else 0.0
    top5_set = float(top5["set_match_3_rate"].iloc[0]) if not top5.empty else 0.0

    if top1_first >= 0.35 and top1_exact < 0.10 and first_ok_tail_ng > 0.20:
        return "1着は比較的拾えているが、2着3着の並びが弱い。順位モデル改善を優先。"
    if top5_set > top5_exact and (top5_set - top5_exact) >= 0.15:
        return "艇の組合せは候補に入るが順番が弱い。並び替えロジックか2着3着推定を優先。"
    if top1_first < 0.20 and top1_second < 0.20 and top1_third < 0.20:
        return "全体の順位推定が弱い。1着だけでなく順位モデル全体の見直しが必要。"
    if all_included_order_ng > 0.10:
        return "3艇の集合は合っているのに順番で落としている。order校正の改善余地が大きい。"
    if top1_exact >= 0.10 and top5_exact >= 0.25:
        return "順位モデルは一定の土台あり。大改修より微修正向き。"
    return "中間状態。2着3着順位特徴量を1点だけ変えて追加観測を推奨。"


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
    top1_summary: dict[str, Any],
    topk_df: pd.DataFrame,
    error_profile: dict[str, Any],
    diagnosis: str,
    output_path: Path,
) -> None:
    lines: list[str] = []
    lines.append("# Rank Model Diagnostic Report")
    lines.append("")
    lines.append("## Top1 Position Summary")
    lines.append("")
    for k, v in top1_summary.items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## Top-K Rank Summary")
    lines.append("")
    lines.append(df_to_markdown(topk_df))
    lines.append("")
    lines.append("## Error Profile")
    lines.append("")
    for k, v in error_profile.items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## Diagnosis")
    lines.append("")
    lines.append(diagnosis)
    lines.append("")
    output_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = parse_args()
    target_date = normalize_date_str(args.date)
    topk_list = parse_topk_list(args.topk_list)

    results_path = args.results_path or (DEFAULT_RESULTS_DIR / f"results_{target_date}.csv")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    candidates_df = load_candidates_df(args.skip_path, target_date)
    results_df = load_results_df_for_date(target_date, explicit_path=results_path)
    merged_df = attach_labels(candidates_df, results_df)

    top1_summary = build_top1_position_summary(merged_df)
    topk_df = build_topk_rank_summary(merged_df, topk_list=topk_list)
    error_profile = build_error_profile(merged_df)
    diagnosis = decide_rank_diagnosis(top1_summary, topk_df, error_profile)

    base_name = f"rank_model_diagnostic_{target_date}"
    rows_path = args.output_dir / f"{base_name}_rows.csv"
    topk_path = args.output_dir / f"{base_name}_topk.csv"
    md_path = args.output_dir / f"{base_name}.md"
    json_path = args.output_dir / f"{base_name}.json"

    merged_df.to_csv(rows_path, index=False, encoding="utf-8")
    topk_df.to_csv(topk_path, index=False, encoding="utf-8")
    write_markdown_report(top1_summary, topk_df, error_profile, diagnosis, md_path)

    payload = {
        "top1_summary": top1_summary,
        "error_profile": error_profile,
        "diagnosis": diagnosis,
        "topk_rows": topk_df.to_dict(orient="records"),
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== Rank Model Diagnostic ===")
    print("Top1 Summary:")
    for k, v in top1_summary.items():
        print(f"{k}: {v}")
    print("\nTop-K Summary:")
    print(topk_df.to_string(index=False) if not topk_df.empty else "no data")
    print("\nError Profile:")
    for k, v in error_profile.items():
        print(f"{k}: {v}")
    print("\nDiagnosis:")
    print(diagnosis)
    print("\nSaved:")
    print(f"- {rows_path}")
    print(f"- {topk_path}")
    print(f"- {md_path}")
    print(f"- {json_path}")


if __name__ == "__main__":
    main()
