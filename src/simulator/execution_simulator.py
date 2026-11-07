from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass
class SimulationConfig:
    stake_per_ticket: int = 100


REQUIRED_BUY_COLUMNS = {
    "date",
    "race_key",
    "ticket",
    "decision",
    "odds",
}

REQUIRED_RESULT_COLUMNS = {
    "date",
    "race_key",
    "winning_ticket",
}


def validate_columns(df: pd.DataFrame, required: set[str], name: str) -> None:
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{name} missing columns: {sorted(missing)}")


def normalize_text_columns(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    out = df.copy()
    for col in cols:
        if col in out.columns:
            out[col] = out[col].astype(str).str.strip()
    return out


def simulate_bets(
    buy_df: pd.DataFrame,
    result_df: pd.DataFrame,
    config: SimulationConfig,
) -> pd.DataFrame:
    validate_columns(buy_df, REQUIRED_BUY_COLUMNS, "buy_df")
    validate_columns(result_df, REQUIRED_RESULT_COLUMNS, "result_df")

    buy_df = normalize_text_columns(buy_df, ["date", "race_key", "ticket", "decision"])
    result_df = normalize_text_columns(result_df, ["date", "race_key", "winning_ticket"])

    target_df = buy_df[buy_df["decision"] == "BUY"].copy()
    if target_df.empty:
        return pd.DataFrame(
            columns=[
                "date",
                "race_key",
                "ticket",
                "odds",
                "stake",
                "hit",
                "payout",
                "profit",
            ]
        )

    target_df["odds"] = pd.to_numeric(target_df["odds"], errors="coerce")
    target_df["stake"] = config.stake_per_ticket

    merged = target_df.merge(
        result_df[["date", "race_key", "winning_ticket"]],
        on=["date", "race_key"],
        how="left",
    )

    merged["hit"] = (merged["ticket"] == merged["winning_ticket"]).astype(int)
    merged["payout"] = merged.apply(
        lambda row: int(row["stake"] * row["odds"]) if row["hit"] == 1 and pd.notna(row["odds"]) else 0,
        axis=1,
    )
    merged["profit"] = merged["payout"] - merged["stake"]

    return merged[
        [
            "date",
            "race_key",
            "ticket",
            "odds",
            "stake",
            "hit",
            "payout",
            "profit",
        ]
    ].copy()


def summarize_simulation(sim_df: pd.DataFrame) -> dict:
    if sim_df.empty:
        return {
            "buy_count": 0,
            "hit_count": 0,
            "hit_rate": 0.0,
            "total_stake": 0,
            "total_payout": 0,
            "total_profit": 0,
            "roi": 0.0,
        }

    buy_count = int(len(sim_df))
    hit_count = int(sim_df["hit"].sum())
    total_stake = int(sim_df["stake"].sum())
    total_payout = int(sim_df["payout"].sum())
    total_profit = int(sim_df["profit"].sum())
    roi = (total_payout / total_stake) if total_stake > 0 else 0.0

    return {
        "buy_count": buy_count,
        "hit_count": hit_count,
        "hit_rate": round(hit_count / buy_count, 4) if buy_count > 0 else 0.0,
        "total_stake": total_stake,
        "total_payout": total_payout,
        "total_profit": total_profit,
        "roi": round(roi, 4),
    }


def save_simulation_results(sim_df: pd.DataFrame, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sim_df.to_csv(output_path, index=False, encoding="utf-8")
