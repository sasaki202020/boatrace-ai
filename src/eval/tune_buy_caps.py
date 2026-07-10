import json
import sys
from pathlib import Path

import pandas as pd

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[2]))

from src.eval.evaluate_experiments import load_inputs

PRED_PATH = Path("data/strategy_outputs/skip_decisions.csv")
HIST_PATH = Path("data/processed/historical_races.csv")
OUT_PATH = Path("reports/buy_caps_tuning.json")


def evaluate_slice(df: pd.DataFrame) -> dict:
    n = len(df)
    if n == 0:
        return {"buy_count": 0, "hit_count": 0, "hit_rate": 0.0, "roi": 0.0}
    hit = df["hit"].sum()
    ret = df.loc[df["hit"], "settled_odds"].fillna(0.0).sum()
    return {
        "buy_count": int(n),
        "hit_count": int(hit),
        "hit_rate": round(float(hit / n), 4),
        "roi": round(float(ret / n), 4),
    }


def main() -> None:
    merged = load_inputs(PRED_PATH, HIST_PATH).copy()
    merged["settled_odds"] = pd.to_numeric(merged["official_odds"], errors="coerce").fillna(
        pd.to_numeric(merged["odds"], errors="coerce")
    )
    merged["hit"] = merged["predicted_trifecta"].astype(str).str.strip() == merged["actual_trifecta"].astype(str).str.strip()

    latest = merged["date"].dt.normalize().max()
    eval_start = latest - pd.Timedelta(days=29)
    tune_df = merged[merged["date"].dt.normalize() < eval_start].copy()
    eval_df = merged[merged["date"].dt.normalize() >= eval_start].copy()

    rows = []
    for max_odds in [400, 500, 600, 700, 800, 1000]:
        for max_ev in [60, 80, 100, 120, 150]:
            for max_prob in [0.18, 0.20, 0.22, 0.24]:
                filt = (
                    (tune_df["odds"] <= max_odds)
                    & (tune_df["ev"] <= max_ev)
                    & (tune_df["first_win_proba"] <= max_prob)
                    & tune_df["is_buy"].astype(bool)
                )
                filt_eval = (
                    (eval_df["odds"] <= max_odds)
                    & (eval_df["ev"] <= max_ev)
                    & (eval_df["first_win_proba"] <= max_prob)
                    & eval_df["is_buy"].astype(bool)
                )
                tune_metrics = evaluate_slice(tune_df[filt])
                eval_metrics = evaluate_slice(eval_df[filt_eval])
                rows.append(
                    {
                        "max_odds_for_buy": max_odds,
                        "max_ev_for_buy": max_ev,
                        "max_first_win_proba_for_buy": max_prob,
                        "tune_period": tune_metrics,
                        "eval_period_recent30": eval_metrics,
                    }
                )

    # まずは十分サンプル条件（tune>=20, eval>=10）で評価し、
    # 候補が無い場合は緩和条件（tune>=10, eval>=5）へフォールバックする。
    strict_rows = [
        r for r in rows
        if r["tune_period"]["buy_count"] >= 20 and r["eval_period_recent30"]["buy_count"] >= 10
    ]
    if strict_rows:
        candidate_rows = strict_rows
        selection_mode = "strict"
    else:
        relaxed_rows = [
            r for r in rows
            if r["tune_period"]["buy_count"] >= 10 and r["eval_period_recent30"]["buy_count"] >= 5
        ]
        if relaxed_rows:
            candidate_rows = relaxed_rows
            selection_mode = "fallback_relaxed"
        else:
            candidate_rows = [r for r in rows if r["eval_period_recent30"]["buy_count"] > 0]
            selection_mode = "fallback_any_eval_buy"

    rows_sorted = sorted(
        candidate_rows,
        key=lambda r: (
            r["tune_period"]["roi"],
            r["tune_period"]["hit_rate"],
            r["eval_period_recent30"]["roi"],
        ),
        reverse=True,
    )
    result = {
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "split": {
            "tune_end_exclusive": eval_start.strftime("%Y-%m-%d"),
            "eval_start_inclusive": eval_start.strftime("%Y-%m-%d"),
            "eval_end_inclusive": latest.strftime("%Y-%m-%d"),
        },
        "selection_mode": selection_mode,
        "candidates_evaluated": len(rows),
        "candidates_after_filter": len(rows_sorted),
        "recommended": rows_sorted[0] if rows_sorted else None,
        "top10": rows_sorted[:10],
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["recommended"], ensure_ascii=False, indent=2))
    print(f"[saved] {OUT_PATH}")


if __name__ == "__main__":
    main()
