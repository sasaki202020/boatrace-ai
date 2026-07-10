from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd


ProbSource = Literal["raw", "calibrated"]


@dataclass
class BuyJudgementConfig:
    buy_min_ev: float = 0.1
    buy_min_prob: float = 0.0
    max_buy_count: int = 3
    prob_source: ProbSource = "raw"


REQUIRED_COLUMNS = {"date", "race_key", "ticket"}
EV_CANDIDATES = ["ev", "expected_value", "calculated_ev"]
RAW_PROB_CANDIDATES = ["approx_prob", "pred_prob", "prob"]
CALIBRATED_PROB_CANDIDATES = ["calibrated_prob", "calibrated_hit_prob", "calibrated_hit_prob_adjusted"]


def _pick_first_existing_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    lowered = {str(c).lower(): c for c in df.columns}
    for candidate in candidates:
        if candidate.lower() in lowered:
            return lowered[candidate.lower()]
    return None


def _resolve_ev_column(df: pd.DataFrame) -> str:
    col = _pick_first_existing_column(df, EV_CANDIDATES)
    if col is None:
        raise ValueError(f"EV column not found. candidates={EV_CANDIDATES}")
    return col


def _resolve_prob_column(df: pd.DataFrame, prob_source: ProbSource) -> str:
    candidates = CALIBRATED_PROB_CANDIDATES if prob_source == "calibrated" else RAW_PROB_CANDIDATES
    col = _pick_first_existing_column(df, candidates)
    if col is None:
        raise ValueError(f"{prob_source} prob column not found. candidates={candidates}")
    return col


def judge_buys(candidate_df: pd.DataFrame, config: BuyJudgementConfig) -> pd.DataFrame:
    missing = REQUIRED_COLUMNS - set(candidate_df.columns)
    if missing:
        raise ValueError(f"candidate_df missing columns: {sorted(missing)}")

    df = candidate_df.copy()
    ev_col = _resolve_ev_column(df)
    prob_col = _resolve_prob_column(df, config.prob_source)

    df["ev_value"] = pd.to_numeric(df[ev_col], errors="coerce")
    df["prob_value"] = pd.to_numeric(df[prob_col], errors="coerce")
    df = df.dropna(subset=["ev_value", "prob_value"]).copy()

    if df.empty:
        out = candidate_df.copy()
        out["decision"] = "SKIP"
        out["prob_source_used"] = config.prob_source
        return out

    df["passes_ev"] = (df["ev_value"] >= config.buy_min_ev).astype(int)
    df["passes_prob"] = (df["prob_value"] >= config.buy_min_prob).astype(int)
    df["eligible"] = ((df["passes_ev"] == 1) & (df["passes_prob"] == 1)).astype(int)

    eligible_df = df[df["eligible"] == 1].copy()
    eligible_df = eligible_df.sort_values(["date", "ev_value", "prob_value"], ascending=[True, False, False]).reset_index(drop=True)
    eligible_df["buy_rank_daily"] = eligible_df.groupby("date").cumcount() + 1
    eligible_df["decision"] = eligible_df["buy_rank_daily"].map(lambda x: "BUY" if x <= config.max_buy_count else "SKIP")

    df["decision"] = "SKIP"
    if not eligible_df.empty:
        df = df.merge(
            eligible_df[["date", "race_key", "ticket", "decision"]],
            on=["date", "race_key", "ticket"],
            how="left",
            suffixes=("", "_eligible"),
        )
        if "decision_eligible" in df.columns:
            df["decision"] = df["decision_eligible"].fillna(df["decision"])
            df = df.drop(columns=["decision_eligible"])
    df["prob_source_used"] = config.prob_source
    return df


def summarize_judgement(df: pd.DataFrame) -> dict:
    if df.empty or "decision" not in df.columns:
        return {"rows": 0, "buy_count": 0, "skip_count": 0, "avg_ev_buy": 0.0, "avg_prob_buy": 0.0}

    buy_df = df[df["decision"] == "BUY"].copy()
    return {
        "rows": int(len(df)),
        "buy_count": int((df["decision"] == "BUY").sum()),
        "skip_count": int((df["decision"] == "SKIP").sum()),
        "avg_ev_buy": round(float(buy_df["ev_value"].mean()), 4) if not buy_df.empty else 0.0,
        "avg_prob_buy": round(float(buy_df["prob_value"].mean()), 4) if not buy_df.empty else 0.0,
    }
