import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[2]))

from src.eval.evaluate_experiments import load_inputs


PRED_PATH = Path("data/strategy_outputs/skip_decisions.csv")
HIST_PATH = Path("data/processed/historical_races.csv")
CONFIG_PATH = Path("config/strategy_config.json")
OUT_JSON = Path("reports/finalize_buy_strategy.json")


@dataclass
class Params:
    operational_min_win_proba: float
    min_ev: float
    min_approx_prob: float
    max_odds_for_buy: float
    max_ev_for_buy: float
    max_first_win_proba_for_buy: float

    def to_config(self) -> dict[str, float]:
        return {
            "operational_min_win_proba": self.operational_min_win_proba,
            "min_ev": self.min_ev,
            "min_approx_prob": self.min_approx_prob,
            "max_odds_for_buy": self.max_odds_for_buy,
            "max_ev_for_buy": self.max_ev_for_buy,
            "max_first_win_proba_for_buy": self.max_first_win_proba_for_buy,
        }


def calc_metrics(df: pd.DataFrame, params: Params) -> dict[str, Any]:
    work = df.copy()
    work["buy"] = (
        (work["first_win_proba"] >= params.operational_min_win_proba)
        & (work["ev"] >= params.min_ev)
        & (work["approx_prob"] >= params.min_approx_prob)
        & (work["odds"] <= params.max_odds_for_buy)
        & (work["ev"] <= params.max_ev_for_buy)
        & (work["first_win_proba"] <= params.max_first_win_proba_for_buy)
    )
    buy = work[work["buy"]].copy()
    if len(buy) == 0:
        return {
            "buy_count": 0,
            "hit_count": 0,
            "hit_rate": 0.0,
            "roi": 0.0,
            "avg_settled_odds": 0.0,
        }
    hit = buy["predicted_trifecta"].astype(str).eq(buy["actual_trifecta"].astype(str))
    hit_count = int(hit.sum())
    total_return = float(buy.loc[hit, "settled_odds"].fillna(0.0).sum())
    buy_count = int(len(buy))
    return {
        "buy_count": buy_count,
        "hit_count": hit_count,
        "hit_rate": round(hit_count / buy_count, 4),
        "roi": round(total_return / buy_count, 4),
        "avg_settled_odds": round(float(buy["settled_odds"].mean()), 4),
    }


def build_folds(df: pd.DataFrame) -> list[dict[str, Any]]:
    months = sorted(df["date"].dt.to_period("M").dropna().unique())
    folds = []
    for i in range(1, len(months)):
        eval_month = months[i]
        tune_mask = df["date"].dt.to_period("M") < eval_month
        eval_mask = df["date"].dt.to_period("M") == eval_month
        tune_df = df[tune_mask].copy()
        eval_df = df[eval_mask].copy()
        if len(tune_df) == 0 or len(eval_df) == 0:
            continue
        folds.append(
            {
                "eval_month": str(eval_month),
                "tune_count": int(len(tune_df)),
                "eval_count": int(len(eval_df)),
                "tune_df": tune_df,
                "eval_df": eval_df,
            }
        )
    return folds


def choose_best(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None

    strict = [
        r
        for r in rows
        if r["min_eval_buy_count"] >= 8 and r["max_eval_buy_count"] <= 40 and r["total_eval_hit_count"] >= 1
    ]
    target = strict if strict else rows
    # 安定性重視: 各foldの最悪ROI -> 平均ROI -> 最悪hit_rate -> BUY件数の適正
    return sorted(
        target,
        key=lambda r: (
            r["min_eval_roi"],
            r["avg_eval_roi"],
            r["min_eval_hit_rate"],
            -abs(r["avg_eval_buy_count"] - 20),
        ),
        reverse=True,
    )[0]


def main() -> None:
    merged = load_inputs(PRED_PATH, HIST_PATH).copy()
    merged["settled_odds"] = pd.to_numeric(merged["official_odds"], errors="coerce").fillna(
        pd.to_numeric(merged["odds"], errors="coerce")
    )
    merged = merged.dropna(subset=["date", "first_win_proba", "approx_prob", "ev", "odds"]).copy()

    folds = build_folds(merged)
    if not folds:
        raise RuntimeError("not enough monthly folds for time-split tuning")

    grid = [
        Params(op, mev, mprob, modds, maxev, maxfwp)
        for op in [0.14, 0.16, 0.18, 0.20]
        for mev in [1.1, 2.0, 5.0, 10.0]
        for mprob in [0.03, 0.05, 0.08, 0.10]
        for modds in [300.0, 400.0, 500.0, 600.0, 800.0]
        for maxev in [60.0, 80.0, 100.0, 120.0]
        for maxfwp in [0.18, 0.20, 0.22]
    ]

    evaluated = []
    for p in grid:
        fold_metrics = []
        for f in folds:
            tm = calc_metrics(f["tune_df"], p)
            em = calc_metrics(f["eval_df"], p)
            fold_metrics.append(
                {
                    "eval_month": f["eval_month"],
                    "tune": tm,
                    "eval": em,
                }
            )
        eval_rois = [x["eval"]["roi"] for x in fold_metrics]
        eval_hits = [x["eval"]["hit_count"] for x in fold_metrics]
        eval_buys = [x["eval"]["buy_count"] for x in fold_metrics]
        eval_hit_rates = [x["eval"]["hit_rate"] for x in fold_metrics]
        row = {
            "params": p.to_config(),
            "folds": fold_metrics,
            "min_eval_roi": round(min(eval_rois), 4),
            "avg_eval_roi": round(sum(eval_rois) / len(eval_rois), 4),
            "min_eval_hit_rate": round(min(eval_hit_rates), 4),
            "avg_eval_hit_rate": round(sum(eval_hit_rates) / len(eval_hit_rates), 4),
            "min_eval_buy_count": int(min(eval_buys)),
            "max_eval_buy_count": int(max(eval_buys)),
            "avg_eval_buy_count": round(sum(eval_buys) / len(eval_buys), 2),
            "total_eval_hit_count": int(sum(eval_hits)),
        }
        evaluated.append(row)

    best = choose_best(evaluated)
    ranked = sorted(
        evaluated,
        key=lambda r: (r["min_eval_roi"], r["avg_eval_roi"], r["min_eval_hit_rate"]),
        reverse=True,
    )

    out = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "folds": [{"eval_month": f["eval_month"], "tune_count": f["tune_count"], "eval_count": f["eval_count"]} for f in folds],
        "grid_size": len(grid),
        "best": best,
        "top10": ranked[:10],
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(best, ensure_ascii=False, indent=2))
    print(f"[saved] {OUT_JSON}")

    # 推奨値を config へ反映
    if best is not None:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        cfg.setdefault("buy_conditions", {}).update(best["params"])
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=4), encoding="utf-8")
        print(f"[updated] {CONFIG_PATH}")


if __name__ == "__main__":
    main()
