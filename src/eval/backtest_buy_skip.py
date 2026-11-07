import argparse
import json
import re
from pathlib import Path

import pandas as pd


def normalize_race_key(race_id: str) -> str | None:
    if pd.isna(race_id):
        return None
    rid = str(race_id).strip()
    if not rid:
        return None

    # ex) 20260312-B260312-149 -> d20260312-n149
    m = re.match(r"^(\d{8})-[A-Z]\d{6}-(\d{1,3})$", rid)
    if m:
        date8, serial = m.groups()
        return f"d{date8}-n{int(serial):03d}"

    # ex) 20260201-K260201_s02-01 -> d20260201-n013
    m = re.match(r"^(\d{8})-[A-Z]\d{6}_s(\d{2})-(\d{2})$", rid)
    if m:
        date8, sec, race_no = m.groups()
        serial = (int(sec) - 1) * 12 + int(race_no)
        return f"d{date8}-n{serial:03d}"

    # ex) 20260303-05-05 -> d20260303-v05-r05
    m = re.match(r"^(\d{8})-(\d{2})-(\d{2})$", rid)
    if m:
        date8, venue, race_no = m.groups()
        return f"d{date8}-v{int(venue):02d}-r{int(race_no):02d}"

    return rid


def prediction_match_key(race_id: str) -> str | None:
    """
    予測側 race_id から、日付+開催(圧縮連番)+R の突合キーを作る。
    ex) 20260312-B260312-149 -> d20260312-c13-r05
    """
    if pd.isna(race_id):
        return None
    rid = str(race_id).strip()
    m = re.match(r"^(\d{8})-[A-Z]\d{6}-(\d{1,3})$", rid)
    if not m:
        return None
    date8, serial = m.groups()
    serial_i = int(serial)
    section_compact = (serial_i - 1) // 12 + 1
    race_no = (serial_i - 1) % 12 + 1
    return f"d{date8}-c{section_compact:02d}-r{race_no:02d}"


def _extract_outcome_section(race_id: str) -> tuple[str, int, int] | None:
    """
    結果側 race_id から (date8, section_raw, race_no) を取り出す。
    ex) 20260312-K260312_s02-01 -> ('20260312', 2, 1)
    """
    if pd.isna(race_id):
        return None
    rid = str(race_id).strip()
    m = re.match(r"^(\d{8})-[A-Z]\d{6}_s(\d{2})-(\d{2})$", rid)
    if not m:
        return None
    date8, section_raw, race_no = m.groups()
    return date8, int(section_raw), int(race_no)


def build_outcome_match_keys(race_ids: pd.Series) -> pd.Series:
    """
    結果側 race_id を、日付内の section_raw を圧縮した開催キーへ変換する。
    section_raw は欠番があるため、そのままだと予測側 serial と一致しない。
    """
    parsed = race_ids.apply(_extract_outcome_section)
    df = pd.DataFrame(
        {
            "race_id": race_ids.astype(str),
            "date8": [x[0] if x else None for x in parsed],
            "section_raw": [x[1] if x else None for x in parsed],
            "race_no": [x[2] if x else None for x in parsed],
        }
    )
    has_parts = df["date8"].notna() & df["section_raw"].notna() & df["race_no"].notna()
    keys = pd.Series([None] * len(df), index=df.index, dtype=object)
    if has_parts.any():
        tmp = df.loc[has_parts, ["date8", "section_raw", "race_no"]].copy()
        tmp["section_raw"] = pd.to_numeric(tmp["section_raw"], errors="coerce")
        tmp["race_no"] = pd.to_numeric(tmp["race_no"], errors="coerce")
        tmp["section_compact"] = tmp.groupby("date8")["section_raw"].rank(method="dense").astype(int)
        keys.loc[has_parts] = tmp.apply(
            lambda r: f"d{r['date8']}-c{int(r['section_compact']):02d}-r{int(r['race_no']):02d}",
            axis=1,
        )
    return keys


def build_race_outcomes(historical_path: Path) -> pd.DataFrame:
    hist = pd.read_csv(historical_path, low_memory=False)
    required = {"race_id", "lane", "finish_position"}
    missing = required - set(hist.columns)
    if missing:
        raise ValueError(f"historical file missing required columns: {sorted(missing)}")

    hist_race_id_key = hist["race_id"].astype(str)
    if {"date", "jcd", "race_no"}.issubset(hist.columns):
        hist_date8 = pd.to_datetime(hist["date"], errors="coerce").dt.strftime("%Y%m%d")
        hist_jcd = pd.to_numeric(hist["jcd"], errors="coerce")
        hist_race_no = pd.to_numeric(hist["race_no"], errors="coerce")
        hist_has_canonical = hist_date8.notna() & hist_jcd.notna() & hist_race_no.notna()
        hist_race_id_key.loc[hist_has_canonical] = [
            f"{d}-{int(v):02d}-{int(r):02d}"
            for d, v, r in zip(hist_date8[hist_has_canonical], hist_jcd[hist_has_canonical], hist_race_no[hist_has_canonical])
        ]

    work = hist.copy()
    work["finish_position"] = pd.to_numeric(work["finish_position"], errors="coerce")
    work["lane"] = pd.to_numeric(work["lane"], errors="coerce")
    work = work.dropna(subset=["race_id", "lane", "finish_position"])
    work["race_id_key"] = work["race_id"].astype(str)
    if {"date", "jcd", "race_no"}.issubset(work.columns):
        date8 = pd.to_datetime(work["date"], errors="coerce").dt.strftime("%Y%m%d")
        jcd = pd.to_numeric(work["jcd"], errors="coerce")
        race_no = pd.to_numeric(work["race_no"], errors="coerce")
        has_canonical = date8.notna() & jcd.notna() & race_no.notna()
        work.loc[has_canonical, "race_id_key"] = [
            f"{d}-{int(v):02d}-{int(r):02d}"
            for d, v, r in zip(date8[has_canonical], jcd[has_canonical], race_no[has_canonical])
        ]

    top3 = work[work["finish_position"].isin([1, 2, 3])].copy()
    top3 = top3.sort_values(["race_id_key", "finish_position"])
    trifecta = top3.groupby("race_id_key")["lane"].apply(
        lambda s: "-".join(str(int(v)) for v in s.tolist()) if len(s) == 3 else None
    )
    trifecta = trifecta.reset_index().rename(columns={"race_id_key": "race_id", "lane": "actual_trifecta"})

    odds = pd.Series(dtype=float)
    if "odds_trifecta" in hist.columns:
        odds = pd.to_numeric(hist["odds_trifecta"], errors="coerce")
        odds = (
            pd.DataFrame({"race_id": hist_race_id_key, "official_odds": odds})
            .dropna(subset=["official_odds"])
            .groupby("race_id", as_index=False)["official_odds"]
            .first()
        )

    dates = (
        pd.DataFrame({"race_id": hist_race_id_key, "date": hist["date"]})
        .dropna(subset=["race_id"])
        .groupby("race_id", as_index=False)["date"]
        .first()
        if "date" in hist.columns
        else pd.DataFrame(columns=["race_id", "date"])
    )

    outcomes = trifecta.merge(dates, on="race_id", how="left")
    if isinstance(odds, pd.DataFrame):
        outcomes = outcomes.merge(odds, on="race_id", how="left")
    else:
        outcomes["official_odds"] = pd.NA
    outcomes["normalized_race_key_legacy"] = outcomes["race_id"].apply(normalize_race_key)
    outcomes["normalized_race_key"] = build_outcome_match_keys(outcomes["race_id"])
    outcomes["normalized_race_key"] = outcomes["normalized_race_key"].fillna(outcomes["normalized_race_key_legacy"])
    return outcomes


def normalize_predictions(pred_path: Path) -> pd.DataFrame:
    pred = pd.read_csv(pred_path, low_memory=False).copy()
    if "race_id" not in pred.columns:
        raise ValueError("prediction file must contain race_id")

    if "recommended_trifecta" in pred.columns:
        out = pred.rename(columns={"recommended_trifecta": "predicted_trifecta"}).copy()
        if "decision" not in out.columns:
            out["decision"] = "BUY"
        out["normalized_race_key_legacy"] = out["race_id"].apply(normalize_race_key)
        out["normalized_race_key"] = out["race_id"].apply(prediction_match_key)
        out["normalized_race_key"] = out["normalized_race_key"].fillna(out["normalized_race_key_legacy"])
        return out

    if "trifecta" not in pred.columns:
        raise ValueError("prediction file must contain trifecta or recommended_trifecta")

    out = pred.rename(columns={"trifecta": "predicted_trifecta"}).copy()
    if "decision" not in out.columns:
        out["decision"] = "BUY"

    # race単位に1行へ（EV優先、なければ確率優先）
    sort_cols = []
    if "ev" in out.columns:
        sort_cols.append("ev")
    if "approx_prob" in out.columns:
        sort_cols.append("approx_prob")
    if sort_cols:
        out = out.sort_values(sort_cols, ascending=False)
    out = out.groupby("race_id", as_index=False).first()
    out["normalized_race_key_legacy"] = out["race_id"].apply(normalize_race_key)
    out["normalized_race_key"] = out["race_id"].apply(prediction_match_key)
    out["normalized_race_key"] = out["normalized_race_key"].fillna(out["normalized_race_key_legacy"])
    return out


def _max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    running_max = equity.cummax()
    drawdown = equity / running_max - 1.0
    return float(drawdown.min()) if not drawdown.empty else 0.0


def _max_consecutive_losses(pnl: pd.Series) -> int:
    longest = 0
    current = 0
    for v in pnl.fillna(0.0):
        if float(v) < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _choose_stake_series(df: pd.DataFrame, mode: str, flat_stake: float) -> pd.Series:
    mode_key = str(mode or "auto").lower()
    buy_mask = df["decision"].astype(str).str.upper().eq("BUY") if "decision" in df.columns else pd.Series([False] * len(df), index=df.index)
    if mode_key == "flat":
        return pd.Series([flat_stake if bool(v) else 0.0 for v in buy_mask], index=df.index)

    if "bet_amount" in df.columns:
        bet_amount = pd.to_numeric(df["bet_amount"], errors="coerce").fillna(0.0)
        stake = bet_amount.where((bet_amount > 0) & buy_mask, 0.0)
        if mode_key == "auto":
            fallback = pd.Series([flat_stake if bool(v) else 0.0 for v in buy_mask], index=df.index)
            stake = stake.where(stake > 0, fallback)
        return stake

    return pd.Series([flat_stake if bool(v) else 0.0 for v in buy_mask], index=df.index)


def run_backtest(
    pred_df: pd.DataFrame,
    outcomes_df: pd.DataFrame,
    *,
    stake_mode: str = "auto",
    flat_stake: float = 1.0,
    initial_bankroll: float = 100000.0,
) -> tuple[pd.DataFrame, dict]:
    outcomes_lookup = outcomes_df.copy()
    if "normalized_race_key" in outcomes_lookup.columns:
        outcomes_lookup = outcomes_lookup.sort_values("race_id").drop_duplicates(subset=["normalized_race_key"])
    merged = pred_df.merge(
        outcomes_lookup,
        on="normalized_race_key",
        how="left",
        suffixes=("", "_result"),
    )
    if "race_id_result" in merged.columns:
        merged["matched_race_id"] = merged["race_id_result"]
    else:
        merged["matched_race_id"] = pd.NA
    merged["result_available"] = merged["actual_trifecta"].notna()
    merged["hit"] = (
        merged["result_available"]
        & merged["predicted_trifecta"].astype(str).eq(merged["actual_trifecta"].astype(str))
    )

    pred_odds = pd.to_numeric(merged["odds"], errors="coerce") if "odds" in merged.columns else pd.Series([pd.NA] * len(merged))
    off_odds = pd.to_numeric(merged["official_odds"], errors="coerce")
    merged["settled_odds"] = off_odds.fillna(pred_odds)
    merged["stake_amount"] = _choose_stake_series(merged, stake_mode, flat_stake)
    merged["payout_amount"] = merged["stake_amount"] * merged["hit"].astype(int) * pd.to_numeric(merged["settled_odds"], errors="coerce").fillna(0.0)
    merged["pnl"] = merged["payout_amount"] - merged["stake_amount"]
    merged["payout_unit"] = merged["payout_amount"]

    buy_mask = merged["stake_amount"] > 0
    settled_buy = merged[buy_mask & merged["result_available"]].copy()

    buy_count = int(len(settled_buy))
    hit_count = int(settled_buy["hit"].sum())
    hit_rate = (hit_count / buy_count) if buy_count > 0 else None
    avg_odds = settled_buy["settled_odds"].mean()
    avg_odds = float(avg_odds) if pd.notna(avg_odds) else None
    total_stake = float(settled_buy["stake_amount"].sum()) if buy_count > 0 else 0.0
    total_return = float(settled_buy["payout_amount"].sum()) if buy_count > 0 else 0.0
    profit = total_return - total_stake
    roi = (total_return / total_stake) if total_stake > 0 else None

    ordered = merged.sort_values(
        ["date", "normalized_race_key", "race_id"],
        kind="mergesort",
    ).copy()
    ordered["equity"] = initial_bankroll + ordered["pnl"].cumsum()
    ordered["peak_equity"] = ordered["equity"].cummax()
    ordered["drawdown"] = ordered["equity"] / ordered["peak_equity"] - 1.0
    max_drawdown = _max_drawdown(ordered["equity"])
    max_consecutive_loss = _max_consecutive_losses(ordered["pnl"])

    for col in ["equity", "peak_equity", "drawdown"]:
        merged[col] = pd.NA
    merged.loc[ordered.index, ["equity", "peak_equity", "drawdown"]] = ordered[
        ["equity", "peak_equity", "drawdown"]
    ].to_numpy()

    daily = (
        ordered.groupby(pd.to_datetime(ordered["date"], errors="coerce").dt.date, dropna=False)
        .agg(
            bets=("stake_amount", "sum"),
            returns=("payout_amount", "sum"),
            pnl=("pnl", "sum"),
            buys=("stake_amount", lambda s: int((s > 0).sum())),
            hits=("hit", "sum"),
        )
        .reset_index()
        .rename(columns={"date": "trade_date"})
    )
    daily["trade_date"] = daily["trade_date"].astype(str)
    daily["equity"] = initial_bankroll + daily["pnl"].cumsum()
    daily["peak_equity"] = daily["equity"].cummax()
    daily["drawdown"] = daily["equity"] / daily["peak_equity"] - 1.0

    summary = {
        "total_prediction_rows": int(len(merged)),
        "result_available_rows": int(merged["result_available"].sum()),
        "buy_count": buy_count,
        "hit_count": hit_count,
        "hit_rate": hit_rate,
        "roi": roi,
        "avg_odds": avg_odds,
        "total_stake": total_stake,
        "total_return": total_return,
        "profit": profit,
        "initial_bankroll": float(initial_bankroll),
        "final_equity": float(ordered["equity"].iloc[-1]) if not ordered.empty else float(initial_bankroll),
        "peak_equity": float(ordered["peak_equity"].max()) if not ordered.empty else float(initial_bankroll),
        "max_drawdown": max_drawdown,
        "max_consecutive_loss": int(max_consecutive_loss),
        "stake_mode": str(stake_mode),
        "flat_stake": float(flat_stake),
        "assumption": "ROI is return multiple with per-race stake determined by stake_mode",
    }
    summary["daily_summary"] = daily.to_dict(orient="records")
    summary["equity_curve"] = ordered[[
        c for c in [
            "race_id",
            "matched_race_id",
            "normalized_race_key",
            "date",
            "decision",
            "stake_amount",
            "payout_amount",
            "pnl",
            "equity",
            "peak_equity",
            "drawdown",
            "hit",
            "settled_odds",
        ]
        if c in ordered.columns
    ]].to_dict(orient="records")
    return merged, summary


def main():
    parser = argparse.ArgumentParser(description="Backtest BUY/SKIP decisions with historical race results")
    parser.add_argument("--predictions", default="data/strategy_outputs/skip_decisions.csv")
    parser.add_argument("--historical", default="data/processed/historical_races.csv")
    parser.add_argument("--output-summary", default="reports/backtest_summary.json")
    parser.add_argument("--output-races", default="reports/backtest_race_results.csv")
    parser.add_argument("--output-daily", default="reports/backtest_daily_summary.csv")
    parser.add_argument("--output-equity", default="reports/backtest_equity_curve.csv")
    parser.add_argument("--stake-mode", choices=["auto", "flat", "bet_amount"], default="auto")
    parser.add_argument("--flat-stake", type=float, default=1.0)
    parser.add_argument("--initial-bankroll", type=float, default=100000.0)
    args = parser.parse_args()

    pred_path = Path(args.predictions)
    hist_path = Path(args.historical)
    if not pred_path.exists():
        raise FileNotFoundError(f"predictions file not found: {pred_path}")
    if not hist_path.exists():
        raise FileNotFoundError(f"historical file not found: {hist_path}")

    pred_df = normalize_predictions(pred_path)
    outcomes_df = build_race_outcomes(hist_path)
    race_results, summary = run_backtest(
        pred_df,
        outcomes_df,
        stake_mode=args.stake_mode,
        flat_stake=args.flat_stake,
        initial_bankroll=args.initial_bankroll,
    )

    race_cols = [
        c
        for c in [
            "race_id",
            "matched_race_id",
            "normalized_race_key",
            "date",
            "decision",
            "predicted_trifecta",
            "actual_trifecta",
            "hit",
            "risk_flag",
            "official_odds",
            "odds",
            "settled_odds",
            "stake_amount",
            "payout_amount",
            "pnl",
            "payout_unit",
            "ev",
            "reason",
            "result_available",
            "equity",
            "peak_equity",
            "drawdown",
        ]
        if c in race_results.columns
    ]
    race_results = race_results[race_cols].sort_values(["race_id"]).reset_index(drop=True)

    out_summary = Path(args.output_summary)
    out_races = Path(args.output_races)
    out_daily = Path(args.output_daily)
    out_equity = Path(args.output_equity)
    out_summary.parent.mkdir(parents=True, exist_ok=True)
    out_races.parent.mkdir(parents=True, exist_ok=True)
    out_daily.parent.mkdir(parents=True, exist_ok=True)
    out_equity.parent.mkdir(parents=True, exist_ok=True)
    out_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    race_results.to_csv(out_races, index=False)
    pd.DataFrame(summary.get("daily_summary", [])).to_csv(out_daily, index=False)
    pd.DataFrame(summary.get("equity_curve", [])).to_csv(out_equity, index=False)

    print(f"Backtest summary saved: {out_summary}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Race-level results saved: {out_races}")
    print(f"Daily summary saved: {out_daily}")
    print(f"Equity curve saved: {out_equity}")


if __name__ == "__main__":
    main()
