from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.eval.backtest_buy_skip import build_race_outcomes, normalize_predictions, run_backtest
from src.strategy.evaluate_ev_and_skip import StrategyEvaluator


DEFAULT_START = "2026-01-04"
DEFAULT_END = "2026-04-04"
DEFAULT_PERIOD_DIR = ROOT / "reports" / "yearly_backtest" / f"{DEFAULT_START}_{DEFAULT_END}"
DEFAULT_ARTIFACTS_DIR = DEFAULT_PERIOD_DIR / "artifacts"
DEFAULT_FEATURES = DEFAULT_ARTIFACTS_DIR / "train_features_target.csv"
DEFAULT_CANDIDATES = DEFAULT_ARTIFACTS_DIR / "train_trifecta_candidates_target.csv"
DEFAULT_ODDS = DEFAULT_ARTIFACTS_DIR / "combined_odds_3m.csv"
DEFAULT_HISTORICAL = ROOT / "data" / "processed" / "historical_races.csv"
DEFAULT_CONFIG = ROOT / "config" / "strategy_config.json"

JCD_TO_VENUE = {
    "01": "桐生",
    "02": "戸田",
    "03": "江戸川",
    "04": "平和島",
    "05": "多摩川",
    "06": "浜名湖",
    "07": "蒲郡",
    "08": "常滑",
    "09": "津",
    "10": "三国",
    "11": "びわこ",
    "12": "住之江",
    "13": "尼崎",
    "14": "鳴門",
    "15": "丸亀",
    "16": "児島",
    "17": "宮島",
    "18": "徳山",
    "19": "下関",
    "20": "若松",
    "21": "芦屋",
    "22": "福岡",
    "23": "唐津",
    "24": "大村",
}


def _safe_float(value: object) -> float | None:
    num = pd.to_numeric(value, errors="coerce")
    if pd.isna(num):
        return None
    return float(num)


def _load_features(feature_path: Path, start: str, end: str) -> pd.DataFrame:
    df = pd.read_csv(feature_path, low_memory=False)
    if "date" not in df.columns:
        raise ValueError(f"feature file missing date column: {feature_path}")
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.loc[df["date"].between(pd.Timestamp(start), pd.Timestamp(end))].copy()
    if df.empty:
        raise ValueError(f"no feature rows found in {feature_path} for {start}..{end}")
    if "race_id" in df.columns:
        df["race_id"] = df["race_id"].astype(str).str.strip()
    return df


def _load_historical(historical_path: Path, start: str, end: str) -> pd.DataFrame:
    df = pd.read_csv(historical_path, low_memory=False)
    if "date" not in df.columns:
        raise ValueError(f"historical file missing date column: {historical_path}")
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.loc[df["date"].between(pd.Timestamp(start), pd.Timestamp(end))].copy()
    if df.empty:
        raise ValueError(f"no historical rows found in {historical_path} for {start}..{end}")
    if "race_id" in df.columns:
        df["race_id"] = df["race_id"].astype(str).str.strip()
    return df


def _prepare_feature_lookup(features_df: pd.DataFrame) -> pd.DataFrame:
    if features_df.empty:
        return pd.DataFrame(columns=["race_id", "lane"])
    feat = features_df.copy()
    feat["race_id"] = feat["race_id"].astype(str).str.strip()
    feat["lane"] = pd.to_numeric(feat["lane"], errors="coerce")
    feat = feat.dropna(subset=["race_id", "lane"]).copy()
    feat["lane"] = feat["lane"].astype(int)
    return feat.drop_duplicates(subset=["race_id", "lane"], keep="last").reset_index(drop=True)


def _race_boat_counts(feature_path: Path) -> dict[str, int]:
    evaluator = StrategyEvaluator(config_path=str(DEFAULT_CONFIG))
    return evaluator._load_race_boat_counts(feature_path)


def _map_odds_to_candidate_race_ids(odds_path: Path, out_dir: Path) -> Path:
    odds_df = pd.read_csv(odds_path, low_memory=False)
    if odds_df.empty:
        raise ValueError(f"odds file is empty: {odds_path}")
    if "trifecta" not in odds_df.columns and "combo" in odds_df.columns:
        odds_df["trifecta"] = odds_df["combo"]
    required = {"date", "stadium", "race_no", "trifecta", "odds"}
    missing = required - set(odds_df.columns)
    if missing:
        raise ValueError(f"odds file missing required columns: {sorted(missing)}")

    odds_df = odds_df.copy()
    odds_df["date"] = pd.to_datetime(odds_df["date"], errors="coerce").dt.strftime("%Y%m%d")
    odds_df["race_no"] = pd.to_numeric(odds_df["race_no"], errors="coerce")
    odds_df = odds_df.dropna(subset=["date", "stadium", "race_no", "trifecta", "odds"]).copy()
    odds_df["race_no"] = odds_df["race_no"].astype(int)
    odds_df["race_id"] = odds_df.apply(lambda row: f"{row['date']}_{row['stadium']}_{int(row['race_no'])}", axis=1)
    odds_df = odds_df.drop_duplicates(subset=["race_id", "trifecta"], keep="last").reset_index(drop=True)

    mapped_path = out_dir / "combined_odds_3m_candidate_race_ids.csv"
    odds_df.to_csv(mapped_path, index=False)
    return mapped_path


def _run_backtest(
    *,
    start: str,
    end: str,
    feature_path: Path,
    candidates_path: Path,
    odds_path: Path,
    historical_path: Path,
    config_path: Path,
    output_dir: Path,
) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    features_df = _load_features(feature_path, start, end)
    historical_df = _load_historical(historical_path, start, end)
    mapped_odds_path = _map_odds_to_candidate_race_ids(odds_path, output_dir)

    evaluator = StrategyEvaluator(config_path=str(config_path))
    feature_lookup = _prepare_feature_lookup(features_df)
    evaluator._load_pre_race_features = lambda: feature_lookup.copy()

    ev_df = evaluator.build_ev_analysis(str(candidates_path), odds_path=str(mapped_odds_path))
    if ev_df.empty:
        raise RuntimeError("EV analysis returned no rows")

    race_boat_counts = _race_boat_counts(feature_path)
    skip_df = evaluator.build_skip_decisions(ev_df, race_boat_counts=race_boat_counts)
    if skip_df.empty:
        raise RuntimeError("skip decision generation returned no rows")

    skip_path = output_dir / "skip_decisions_real_odds_3m.csv"
    skip_df.to_csv(skip_path, index=False)

    pred_df = normalize_predictions(skip_path)
    outcomes_df = build_race_outcomes(historical_path)
    outcomes_df["date"] = pd.to_datetime(outcomes_df.get("date"), errors="coerce")
    outcomes_df = outcomes_df.loc[outcomes_df["date"].between(pd.Timestamp(start), pd.Timestamp(end))].copy()

    race_results, summary = run_backtest(pred_df, outcomes_df)

    settled_buy = race_results.loc[
        (pd.to_numeric(race_results.get("stake_amount"), errors="coerce").fillna(0.0) > 0)
        & race_results.get("result_available", pd.Series(dtype=bool)).fillna(False)
    ].copy()
    avg_odds = (
        _safe_float(pd.to_numeric(settled_buy.get("settled_odds"), errors="coerce").mean())
        if not settled_buy.empty
        else None
    )

    decision_counts = (
        skip_df.get("decision", pd.Series(dtype=object)).fillna("unknown").astype(str).value_counts().to_dict()
        if not skip_df.empty
        else {}
    )
    stop_reason_counts = (
        skip_df.get("stop_reason", pd.Series(dtype=object)).fillna("unknown").astype(str).value_counts().to_dict()
        if not skip_df.empty
        else {}
    )

    report = {
        "period": {"start": start, "end": end},
        "paths": {
            "feature_path": str(feature_path),
            "candidates_path": str(candidates_path),
            "odds_path": str(odds_path),
            "mapped_odds_path": str(mapped_odds_path),
            "historical_path": str(historical_path),
            "config_path": str(config_path),
            "skip_path": str(skip_path),
        },
        "rows": {
            "features": int(len(features_df)),
            "historical": int(len(historical_df)),
            "ev": int(len(ev_df)),
            "skip": int(len(skip_df)),
            "prediction": int(len(pred_df)),
            "race_results": int(len(race_results)),
            "result_available": int(race_results["result_available"].sum()) if not race_results.empty else 0,
        },
        "decision_counts": {str(k): int(v) for k, v in decision_counts.items()},
        "stop_reason_counts_top10": [
            {"stop_reason": str(k), "count": int(v)}
            for k, v in pd.Series(stop_reason_counts).sort_values(ascending=False).head(10).items()
        ],
        "summary": {
            "buy_count": int(summary.get("buy_count", 0) or 0),
            "hit_count": int(summary.get("hit_count", 0) or 0),
            "hit_rate": _safe_float(summary.get("hit_rate")),
            "roi": _safe_float(summary.get("roi")),
            "total_stake": _safe_float(race_results.loc[
                pd.to_numeric(race_results.get("stake_amount"), errors="coerce").fillna(0.0) > 0,
                "stake_amount",
            ].sum()) if not race_results.empty else 0.0,
            "total_return": _safe_float(race_results.loc[
                pd.to_numeric(race_results.get("stake_amount"), errors="coerce").fillna(0.0) > 0,
                "payout_amount",
            ].sum()) if not race_results.empty else 0.0,
            "avg_odds": avg_odds,
            "available_hit_rate": _safe_float(summary.get("hit_rate")),
        },
        "files": {
            "skip_decisions": str(skip_path),
        },
    }

    (output_dir / "real_odds_3m_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    race_results.to_csv(output_dir / "real_odds_3m_race_results.csv", index=False)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a 3-month real-odds backtest using current logic.")
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--feature-path", default=str(DEFAULT_FEATURES))
    parser.add_argument("--candidates-path", default=str(DEFAULT_CANDIDATES))
    parser.add_argument("--odds-path", default=str(DEFAULT_ODDS))
    parser.add_argument("--historical-path", default=str(DEFAULT_HISTORICAL))
    parser.add_argument("--config-path", default=str(DEFAULT_CONFIG))
    parser.add_argument("--output-dir", default=str(DEFAULT_PERIOD_DIR / "real_odds_3m_current_logic"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = _run_backtest(
        start=args.start,
        end=args.end,
        feature_path=Path(args.feature_path),
        candidates_path=Path(args.candidates_path),
        odds_path=Path(args.odds_path),
        historical_path=Path(args.historical_path),
        config_path=Path(args.config_path),
        output_dir=Path(args.output_dir),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
