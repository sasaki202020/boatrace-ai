import argparse
import json
from pathlib import Path

import pandas as pd

from src.eval.backtest_buy_skip import build_race_outcomes, normalize_predictions, run_backtest
from src.eval.diagnose_upstream_pool import attach_truth, build_rank_rows, build_truth, rank_stats
from src.models.predict_win_proba import WinProbabilityPredictor
from src.strategy.evaluate_ev_and_skip import StrategyEvaluator
from src.strategy.generate_trifecta_candidates import TrifectaGenerator


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_START = "2025-01-01"
DEFAULT_END = "2025-12-31"
REPORT_ROOT = ROOT / "reports" / "yearly_backtest"

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


def _safe_int(value: object) -> int | None:
    num = pd.to_numeric(value, errors="coerce")
    if pd.isna(num):
        return None
    return int(num)


def _normalize_jcd(value: object) -> str:
    num = pd.to_numeric(value, errors="coerce")
    if pd.isna(num):
        return ""
    return f"{int(num):02d}"


def _make_output_dir(args: argparse.Namespace) -> Path:
    if args.output_dir:
        out_dir = Path(args.output_dir)
    else:
        label = f"{args.start}_{args.end}"
        out_dir = REPORT_ROOT / label
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _load_target_frames(features_path: Path, historical_path: Path, start: str, end: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)

    def read_filtered(path: Path, chunksize: int = 100_000) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        for chunk in pd.read_csv(path, low_memory=False, chunksize=chunksize):
            if "date" not in chunk.columns:
                continue
            chunk = chunk.copy()
            chunk["date"] = pd.to_datetime(chunk["date"], errors="coerce")
            chunk = chunk.loc[chunk["date"].between(start_ts, end_ts)].copy()
            if chunk.empty:
                continue
            if "race_id" in chunk.columns:
                chunk["race_id"] = chunk["race_id"].astype(str).str.strip()
            frames.append(chunk)
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True, sort=False)

    return read_filtered(features_path), read_filtered(historical_path)


def _write_temp_config(base_config_path: Path, feature_path: Path, out_path: Path) -> Path:
    config = json.loads(base_config_path.read_text(encoding="utf-8"))
    for key in ("pre_race_filter", "first_place_filter", "place_role_filter", "race_selection_filter"):
        section = dict(config.get(key, {}))
        section["feature_path"] = str(feature_path)
        config[key] = section
    out_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def _prepare_feature_lookup(features_df: pd.DataFrame) -> pd.DataFrame:
    if features_df.empty:
        return pd.DataFrame(columns=["race_id", "lane"])
    feat = features_df.copy()
    feat["race_id"] = feat["race_id"].astype(str).str.strip()
    feat["lane"] = pd.to_numeric(feat["lane"], errors="coerce")
    feat = feat.dropna(subset=["race_id", "lane"]).copy()
    feat["lane"] = feat["lane"].astype(int)
    return feat.drop_duplicates(subset=["race_id", "lane"], keep="last").reset_index(drop=True)


def _build_predictions(
    features_df: pd.DataFrame,
    out_dir: Path,
    strategy_config_path: Path,
    precomputed_win_path: Path | None = None,
) -> dict[str, Path]:
    tmp_dir = out_dir / "artifacts"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    feature_path = tmp_dir / "train_features_target.csv"
    win_path = tmp_dir / "train_win_proba_target.csv"
    candidates_path = tmp_dir / "train_trifecta_candidates_target.csv"
    ev_path = tmp_dir / "ev_analysis_year.csv"
    skip_path = tmp_dir / "skip_decisions_year.csv"
    config_path = tmp_dir / "strategy_config_year.json"

    features_df.to_csv(feature_path, index=False)
    _write_temp_config(strategy_config_path, feature_path, config_path)

    pred_df = pd.DataFrame()
    if precomputed_win_path and precomputed_win_path.exists():
        pred_df = pd.read_csv(precomputed_win_path, low_memory=False)
        if "date" in pred_df.columns:
            pred_df["date"] = pd.to_datetime(pred_df["date"], errors="coerce")
            feature_dates = pd.to_datetime(features_df.get("date"), errors="coerce")
            min_date = feature_dates.min()
            max_date = feature_dates.max()
            pred_df = pred_df.loc[pred_df["date"].between(min_date, max_date)].copy()
    if pred_df.empty:
        predictor = WinProbabilityPredictor()
        pred_df = predictor.predict(feature_path)
    if pred_df is None or pred_df.empty:
        raise RuntimeError("win probability prediction returned no rows")
    pred_df.to_csv(win_path, index=False)

    generator = TrifectaGenerator(config_path=str(strategy_config_path))
    cand_df = generator.generate(win_path)
    if cand_df.empty:
        raise RuntimeError("trifecta candidate generation returned no rows")
    cand_df.to_csv(candidates_path, index=False)

    evaluator = StrategyEvaluator(config_path=str(config_path))
    feature_lookup = _prepare_feature_lookup(features_df)
    evaluator._load_pre_race_features = lambda: feature_lookup.copy()
    ev_df = evaluator.build_ev_analysis(candidates_path, odds_path=None)
    if ev_df.empty:
        raise RuntimeError("EV analysis returned no rows")
    ev_df.to_csv(ev_path, index=False)

    race_boat_counts = evaluator._load_race_boat_counts(feature_path)
    skip_df = evaluator.build_skip_decisions(ev_df, race_boat_counts=race_boat_counts)
    if skip_df.empty:
        raise RuntimeError("skip decision generation returned no rows")
    skip_df.to_csv(skip_path, index=False)

    return {
        "feature_path": feature_path,
        "win_path": win_path,
        "candidates_path": candidates_path,
        "ev_path": ev_path,
        "skip_path": skip_path,
        "config_path": config_path,
    }


def _prepare_race_meta(features_df: pd.DataFrame, hist_df: pd.DataFrame) -> pd.DataFrame:
    feat_meta_cols = [c for c in ["race_id", "date", "jcd"] if c in features_df.columns]
    feat_meta = features_df[feat_meta_cols].drop_duplicates("race_id").copy()
    if "jcd" not in feat_meta.columns:
        feat_meta["jcd"] = ""
    hist_meta_cols = [c for c in ["race_id", "date", "jcd"] if c in hist_df.columns]
    hist_meta = hist_df[hist_meta_cols].drop_duplicates("race_id").copy()
    if "jcd" not in hist_meta.columns:
        hist_meta["jcd"] = ""
    meta = feat_meta.merge(hist_meta, on="race_id", how="outer", suffixes=("_feat", "_hist"))
    if "date_feat" in meta.columns:
        meta["date"] = pd.to_datetime(meta["date_feat"], errors="coerce")
    if "date_hist" in meta.columns:
        meta["date"] = pd.to_datetime(meta.get("date"), errors="coerce").fillna(pd.to_datetime(meta["date_hist"], errors="coerce"))
    meta["jcd"] = meta.get("jcd_feat", "").map(_normalize_jcd)
    hist_jcd = meta.get("jcd_hist", "").map(_normalize_jcd) if "jcd_hist" in meta.columns else ""
    if isinstance(hist_jcd, pd.Series):
        meta["jcd"] = meta["jcd"].where(meta["jcd"].astype(str).str.len() > 0, hist_jcd)
    meta["venue_name"] = meta["jcd"].map(JCD_TO_VENUE).fillna("")
    meta["month"] = pd.to_datetime(meta["date"], errors="coerce").dt.strftime("%Y-%m")
    return meta[["race_id", "date", "month", "jcd", "venue_name"]].drop_duplicates("race_id")


def _enrich_rank_rows(candidates_path: Path, skip_df: pd.DataFrame, truth_df: pd.DataFrame, race_meta: pd.DataFrame) -> pd.DataFrame:
    cand_df = pd.read_csv(candidates_path, low_memory=False)
    cand_df = attach_truth(cand_df, truth_df)
    rank_df = build_rank_rows(cand_df, skip_df)
    return rank_df.merge(race_meta, on="race_id", how="left")


def _count_stop_reasons(df: pd.DataFrame, limit: int = 5) -> list[dict[str, object]]:
    if "stop_reason" not in df.columns or df.empty:
        return []
    counts = (
        df["stop_reason"]
        .fillna("unknown")
        .astype(str)
        .value_counts()
        .head(limit)
    )
    return [{"stop_reason": str(idx), "count": int(val)} for idx, val in counts.items()]


def _max_losing_streak_from_pnl(pnl: pd.Series) -> int:
    longest = 0
    current = 0
    for value in pd.to_numeric(pnl, errors="coerce").fillna(0.0):
        if float(value) < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _group_backtest_metrics(race_results: pd.DataFrame, rank_df: pd.DataFrame, skip_df: pd.DataFrame) -> dict[str, object]:
    stats = rank_stats(rank_df)
    ordered = race_results.copy()
    if "date" in ordered.columns:
        ordered = ordered.sort_values(["date", "normalized_race_key", "race_id"], kind="mergesort")
    else:
        ordered = ordered.sort_values(["race_id"], kind="mergesort")
    pnl = pd.to_numeric(ordered.get("pnl"), errors="coerce").fillna(0.0)
    equity = pnl.cumsum()
    peak = equity.cummax()
    drawdown = (equity / peak.replace(0, pd.NA) - 1.0).fillna(0.0)
    settled_buy = ordered.loc[
        (pd.to_numeric(ordered.get("stake_amount"), errors="coerce").fillna(0.0) > 0)
        & ordered.get("result_available", pd.Series([False] * len(ordered), index=ordered.index)).fillna(False)
    ].copy()
    buy_count = int(len(settled_buy))
    hit_count = int(pd.to_numeric(settled_buy.get("hit"), errors="coerce").fillna(0).astype(int).sum()) if not settled_buy.empty else 0
    total_stake = float(pd.to_numeric(settled_buy.get("stake_amount"), errors="coerce").fillna(0.0).sum()) if not settled_buy.empty else 0.0
    total_return = float(pd.to_numeric(settled_buy.get("payout_amount"), errors="coerce").fillna(0.0).sum()) if not settled_buy.empty else 0.0
    drawdown_raw = float(drawdown.min()) if not drawdown.empty else 0.0
    metrics = {
        "races": int(race_results["race_id"].nunique()) if not race_results.empty else 0,
        "buy_count": buy_count,
        "hit_count": hit_count,
        "hit_rate": (hit_count / buy_count) if buy_count > 0 else None,
        "roi": (total_return / total_stake) if total_stake > 0 else None,
        "exact": _safe_float(stats.get("exact_rate")),
        "top5": _safe_float(stats.get("top5_rate")),
        "top10": _safe_float(stats.get("top10_rate")),
        "avg_rank": _safe_float(stats.get("avg_rank")),
        "max_drawdown": abs(drawdown_raw),
        "max_drawdown_raw": drawdown_raw,
        "max_losing_streak": _max_losing_streak_from_pnl(pnl),
        "stop_reason_top": _count_stop_reasons(skip_df),
    }
    return metrics


def _evaluate_thresholds(metrics: dict[str, object], thresholds: dict[str, float | int | None]) -> dict[str, object]:
    checks = [
        ("buy_count", "min", thresholds.get("min_buy_count")),
        ("hit_rate", "min", thresholds.get("min_hit_rate")),
        ("roi", "min", thresholds.get("min_roi")),
        ("exact", "min", thresholds.get("min_exact")),
        ("top5", "min", thresholds.get("min_top5")),
        ("top10", "min", thresholds.get("min_top10")),
        ("avg_rank", "max", thresholds.get("max_avg_rank")),
        ("max_drawdown", "max", thresholds.get("max_drawdown")),
    ]
    results: list[dict[str, object]] = []
    missing_thresholds = False
    failed = False
    unmet: list[str] = []
    for metric_name, direction, threshold in checks:
        actual = metrics.get(metric_name)
        if threshold is None:
            missing_thresholds = True
            status = "not_set"
        elif actual is None:
            failed = True
            status = "fail"
            unmet.append(metric_name)
        elif direction == "min":
            status = "pass" if float(actual) >= float(threshold) else "fail"
            if status == "fail":
                failed = True
                unmet.append(metric_name)
        else:
            status = "pass" if float(actual) <= float(threshold) else "fail"
            if status == "fail":
                failed = True
                unmet.append(metric_name)
        results.append(
            {
                "metric": metric_name,
                "direction": direction,
                "threshold": threshold,
                "actual": actual,
                "status": status,
            }
        )
    overall_status = "not_set" if missing_thresholds and not failed else ("fail" if failed else "pass")
    return {"status": overall_status, "checks": results, "unmet_metrics": unmet}


def _summarize_group(
    group_name: str,
    group_value: str,
    race_results: pd.DataFrame,
    rank_df: pd.DataFrame,
    skip_df: pd.DataFrame,
    thresholds: dict[str, float | int | None],
) -> dict[str, object]:
    metrics = _group_backtest_metrics(race_results, rank_df, skip_df)
    judgement = _evaluate_thresholds(metrics, thresholds)
    return {
        "group_by": group_name,
        "group_value": group_value,
        **metrics,
        "pass_status": judgement["status"],
        "unmet_metrics": ",".join(judgement["unmet_metrics"]),
    }


def _build_group_summaries(
    race_results: pd.DataFrame,
    rank_df: pd.DataFrame,
    skip_df: pd.DataFrame,
    thresholds: dict[str, float | int | None],
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, object]]:
    race_results_with_meta = race_results.copy()
    skip_with_meta = skip_df.copy()
    overall_metrics = _group_backtest_metrics(race_results_with_meta, rank_df, skip_df)
    overall_judgement = _evaluate_thresholds(overall_metrics, thresholds)

    venue_rows: list[dict[str, object]] = []
    for jcd, grp in race_results_with_meta.groupby("jcd", dropna=False, sort=True):
        jcd_s = str(jcd or "")
        venue_races = set(grp["race_id"].astype(str))
        venue_rank = rank_df[rank_df["race_id"].astype(str).isin(venue_races)].copy()
        venue_skip = skip_with_meta[skip_with_meta["race_id"].astype(str).isin(venue_races)].copy()
        venue_name = JCD_TO_VENUE.get(jcd_s, jcd_s or "unknown")
        row = _summarize_group("venue", venue_name, grp.copy(), venue_rank, venue_skip, thresholds)
        row["jcd"] = jcd_s
        venue_rows.append(row)

    month_rows: list[dict[str, object]] = []
    for month, grp in race_results_with_meta.groupby("month", dropna=False, sort=True):
        month_s = str(month or "")
        month_races = set(grp["race_id"].astype(str))
        month_rank = rank_df[rank_df["race_id"].astype(str).isin(month_races)].copy()
        month_skip = skip_with_meta[skip_with_meta["race_id"].astype(str).isin(month_races)].copy()
        month_rows.append(_summarize_group("month", month_s, grp.copy(), month_rank, month_skip, thresholds))

    venue_df = pd.DataFrame(venue_rows).sort_values(["jcd", "group_value"]).reset_index(drop=True)
    month_df = pd.DataFrame(month_rows).sort_values("group_value").reset_index(drop=True)
    overall_row = {
        "group_by": "overall",
        "group_value": "all",
        **overall_metrics,
        "pass_status": overall_judgement["status"],
        "unmet_metrics": ",".join(overall_judgement["unmet_metrics"]),
    }
    return venue_df, month_df, {"summary": overall_row, "checks": overall_judgement["checks"]}


def _data_quality(skip_df: pd.DataFrame, race_results: pd.DataFrame, target_race_count: int, generated_race_count: int) -> dict[str, object]:
    odds_status_counts = (
        skip_df.get("odds_status", pd.Series(dtype=object))
        .fillna("unknown")
        .astype(str)
        .value_counts()
        .to_dict()
    )
    stop_reason_counts = (
        skip_df.get("stop_reason", pd.Series(dtype=object))
        .fillna("unknown")
        .astype(str)
        .value_counts()
        .to_dict()
    )
    missing_results = int((~race_results.get("result_available", pd.Series(dtype=bool)).fillna(False)).sum()) if not race_results.empty else 0
    stop_reason_series = skip_df.get("stop_reason", pd.Series(dtype=object)).fillna("").astype(str)
    return {
        "target_races": int(target_race_count),
        "generated_races": int(generated_race_count),
        "excluded_races": int(max(target_race_count - generated_race_count, 0)),
        "real_odds_missing_count": int(stop_reason_series.str.startswith("real_odds_missing").sum()),
        "real_odds_pending_before_deadline_count": int(stop_reason_series.eq("real_odds_pending_before_deadline").sum()),
        "pending_count": int((skip_df.get("decision", pd.Series(dtype=object)).astype(str) == "PENDING").sum()) if not skip_df.empty else 0,
        "result_missing_count": missing_results,
        "odds_status_counts": {str(k): int(v) for k, v in odds_status_counts.items()},
        "stop_reason_counts": {str(k): int(v) for k, v in stop_reason_counts.items()},
    }


def _build_odds_availability_comparison(
    base_pred_df: pd.DataFrame,
    outcomes_df: pd.DataFrame,
    skip_df: pd.DataFrame,
    rank_df: pd.DataFrame,
    race_meta: pd.DataFrame,
    out_dir: Path,
) -> dict[str, object]:
    if skip_df.empty or base_pred_df.empty or outcomes_df.empty:
        summary_path = out_dir / "odds_availability_summary.csv"
        grouped_path = out_dir / "odds_availability_grouped.csv"
        empty_summary = pd.DataFrame(columns=[
            "slice_name",
            "races",
            "buy_count",
            "hit_count",
            "hit_rate",
            "roi",
            "exact",
            "top5",
            "top10",
            "avg_rank",
            "max_drawdown",
            "real_odds_available_races",
            "real_odds_missing_races",
            "pending_races",
        ])
        empty_summary.to_csv(summary_path, index=False)
        pd.DataFrame(columns=["slice_name", "group_by", "group_value"]).to_csv(grouped_path, index=False)
        return {"summary_csv": str(summary_path), "grouped_csv": str(grouped_path), "rows": []}

    slice_defs = [
        ("all_races", None),
        (
            "real_odds_available_only",
            set(
                skip_df.loc[
                    skip_df.get("odds_status", pd.Series(dtype=object)).astype(str).eq("real_odds_available"),
                    "race_id",
                ]
                .dropna()
                .astype(str)
            ),
        ),
    ]
    summary_rows: list[dict[str, object]] = []
    grouped_rows: list[dict[str, object]] = []

    for slice_name, race_ids in slice_defs:
        if race_ids is None:
            pred_slice = base_pred_df.copy()
            outcomes_slice = outcomes_df.copy()
            skip_slice = skip_df.copy()
            rank_slice = rank_df.copy()
            slice_race_meta = race_meta.copy()
        else:
            if not race_ids:
                summary_rows.append(
                    {
                        "slice_name": slice_name,
                        "races": 0,
                        "buy_count": 0,
                        "hit_count": 0,
                        "hit_rate": None,
                        "roi": None,
                        "exact": None,
                        "top5": None,
                        "top10": None,
                        "avg_rank": None,
                        "max_drawdown": None,
                        "real_odds_available_races": 0,
                        "real_odds_missing_races": 0,
                        "pending_races": 0,
                    }
                )
                continue
            pred_slice = base_pred_df[base_pred_df["race_id"].astype(str).isin(race_ids)].copy()
            outcomes_slice = outcomes_df[outcomes_df["race_id"].astype(str).isin(race_ids)].copy()
            skip_slice = skip_df[skip_df["race_id"].astype(str).isin(race_ids)].copy()
            rank_slice = rank_df[rank_df["race_id"].astype(str).isin(race_ids)].copy()
            slice_race_meta = race_meta[race_meta["race_id"].astype(str).isin(race_ids)].copy()

        if pred_slice.empty or outcomes_slice.empty:
            if race_ids is not None:
                summary_rows.append(
                    {
                        "slice_name": slice_name,
                        "races": int(len(race_ids)),
                        "buy_count": 0,
                        "hit_count": 0,
                        "hit_rate": None,
                        "roi": None,
                        "exact": None,
                        "top5": None,
                        "top10": None,
                        "avg_rank": None,
                        "max_drawdown": None,
                        "real_odds_available_races": 0,
                        "real_odds_missing_races": 0,
                        "pending_races": 0,
                    }
                )
            continue

        race_results, _ = run_backtest(pred_slice, outcomes_slice)
        race_results = race_results.merge(
            slice_race_meta[[c for c in ["race_id", "month", "jcd", "venue_name"] if c in slice_race_meta.columns]],
            on="race_id",
            how="left",
        )
        overall_metrics = _group_backtest_metrics(race_results, rank_slice, skip_slice)
        stop_series = skip_slice.get("stop_reason", pd.Series(dtype=object)).fillna("").astype(str)
        summary_rows.append(
            {
                "slice_name": slice_name,
                "races": int(slice_race_meta["race_id"].nunique()) if not slice_race_meta.empty else int(skip_slice["race_id"].nunique()),
                "buy_count": int(overall_metrics.get("buy_count", 0) or 0),
                "hit_count": int(overall_metrics.get("hit_count", 0) or 0),
                "hit_rate": overall_metrics.get("hit_rate"),
                "roi": overall_metrics.get("roi"),
                "exact": overall_metrics.get("exact"),
                "top5": overall_metrics.get("top5"),
                "top10": overall_metrics.get("top10"),
                "avg_rank": overall_metrics.get("avg_rank"),
                "max_drawdown": overall_metrics.get("max_drawdown"),
                "real_odds_available_races": int(skip_slice.loc[skip_slice.get("odds_status", pd.Series(dtype=object)).astype(str).eq("real_odds_available"), "race_id"].astype(str).nunique()) if not skip_slice.empty else 0,
                "real_odds_missing_races": int(skip_slice.loc[stop_series.str.startswith("real_odds_missing"), "race_id"].astype(str).nunique()) if not skip_slice.empty else 0,
                "pending_races": int(skip_slice.loc[skip_slice.get("decision", pd.Series(dtype=object)).astype(str).str.upper().eq("PENDING"), "race_id"].astype(str).nunique()) if not skip_slice.empty else 0,
            }
        )

        for group_by in ("month", "venue_name"):
            if group_by not in race_results.columns:
                continue
            grouped = race_results.groupby(group_by, dropna=False, sort=True)
            for group_value, grp in grouped:
                grp_pred = pred_slice[pred_slice["race_id"].astype(str).isin(set(grp["race_id"].astype(str)))].copy()
                grp_outcomes = outcomes_slice[outcomes_slice["race_id"].astype(str).isin(set(grp["race_id"].astype(str)))].copy()
                grp_skip = skip_slice[skip_slice["race_id"].astype(str).isin(set(grp["race_id"].astype(str)))].copy()
                grp_rank = rank_slice[rank_slice["race_id"].astype(str).isin(set(grp["race_id"].astype(str)))].copy()
                grp_results, _ = run_backtest(grp_pred, grp_outcomes)
                grp_metrics = _group_backtest_metrics(grp_results, grp_rank, grp_skip)
                grouped_rows.append(
                    {
                        "slice_name": slice_name,
                        "group_by": group_by,
                        "group_value": str(group_value or ""),
                        "races": int(grp["race_id"].nunique()),
                        "buy_count": int(grp_metrics.get("buy_count", 0) or 0),
                        "hit_count": int(grp_metrics.get("hit_count", 0) or 0),
                        "hit_rate": grp_metrics.get("hit_rate"),
                        "roi": grp_metrics.get("roi"),
                        "exact": grp_metrics.get("exact"),
                        "top5": grp_metrics.get("top5"),
                        "top10": grp_metrics.get("top10"),
                        "avg_rank": grp_metrics.get("avg_rank"),
                        "max_drawdown": grp_metrics.get("max_drawdown"),
                    }
                )

    summary_df = pd.DataFrame(summary_rows)
    grouped_df = pd.DataFrame(grouped_rows)
    summary_path = out_dir / "odds_availability_summary.csv"
    grouped_path = out_dir / "odds_availability_grouped.csv"
    summary_df.to_csv(summary_path, index=False)
    grouped_df.to_csv(grouped_path, index=False)
    return {
        "summary_csv": str(summary_path),
        "grouped_csv": str(grouped_path),
        "rows": summary_rows,
        "grouped_rows": grouped_rows,
    }


def _top_failure_causes(overall_summary: dict[str, object], venue_df: pd.DataFrame, month_df: pd.DataFrame, data_quality: dict[str, object]) -> list[str]:
    causes: list[str] = []
    unmet = [m for m in str(overall_summary.get("unmet_metrics", "")).split(",") if m]
    metric_labels = {
        "buy_count": "BUY件数不足",
        "hit_rate": "hit_rate不足",
        "roi": "ROI不足",
        "exact": "exact不足",
        "top5": "top5不足",
        "top10": "top10不足",
        "avg_rank": "avg_rank悪化",
        "max_drawdown": "drawdown超過",
    }
    for metric in unmet:
        label = metric_labels.get(metric, f"{metric}未達")
        if label not in causes:
            causes.append(label)
    if data_quality.get("real_odds_missing_count", 0):
        causes.append(f"real_odds_missing多発({data_quality['real_odds_missing_count']})")
    if not venue_df.empty and (venue_df["pass_status"] == "fail").any():
        causes.append(f"場別不合格 {int((venue_df['pass_status'] == 'fail').sum())}場")
    if not month_df.empty and (month_df["pass_status"] == "fail").any():
        causes.append(f"月別不合格 {int((month_df['pass_status'] == 'fail').sum())}か月")
    if data_quality.get("excluded_races", 0):
        causes.append(f"対象外レース発生({data_quality['excluded_races']})")
    deduped = []
    for cause in causes:
        if cause not in deduped:
            deduped.append(cause)
    return deduped[:3]


def _to_jsonable_records(df: pd.DataFrame) -> list[dict[str, object]]:
    if df.empty:
        return []
    out = df.copy()
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = out[col].dt.strftime("%Y-%m-%d")
    return json.loads(out.to_json(orient="records", force_ascii=False))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run yearly backtest with current production logic.")
    parser.add_argument("--year", type=int, default=None)
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--features", default=str(ROOT / "data" / "features" / "train_features.csv"))
    parser.add_argument("--historical", default=str(ROOT / "data" / "processed" / "historical_races.csv"))
    parser.add_argument("--strategy-config", default=str(ROOT / "config" / "strategy_config.json"))
    parser.add_argument("--win-proba", default=str(ROOT / "data" / "model_outputs" / "train_win_proba.csv"))
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--min-buy-count", type=int, default=None)
    parser.add_argument("--min-hit-rate", type=float, default=None)
    parser.add_argument("--min-roi", type=float, default=None)
    parser.add_argument("--min-exact", type=float, default=None)
    parser.add_argument("--min-top5", type=float, default=None)
    parser.add_argument("--min-top10", type=float, default=None)
    parser.add_argument("--max-avg-rank", type=float, default=None)
    parser.add_argument("--max-drawdown", type=float, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.year is not None:
        args.start = f"{args.year}-01-01"
        args.end = f"{args.year}-12-31"

    out_dir = _make_output_dir(args)
    features_path = Path(args.features)
    historical_path = Path(args.historical)
    strategy_config_path = Path(args.strategy_config)
    win_proba_path = Path(args.win_proba) if args.win_proba else None

    features_df, hist_df = _load_target_frames(features_path, historical_path, args.start, args.end)
    if features_df.empty:
        raise ValueError(f"no feature rows found in range {args.start} to {args.end}")
    if hist_df.empty:
        raise ValueError(f"no historical rows found in range {args.start} to {args.end}")

    artifacts = _build_predictions(features_df, out_dir, strategy_config_path, precomputed_win_path=win_proba_path)
    skip_df = pd.read_csv(artifacts["skip_path"], low_memory=False)
    pred_df = normalize_predictions(artifacts["skip_path"])
    outcomes_df = build_race_outcomes(historical_path)
    outcomes_df["date"] = pd.to_datetime(outcomes_df.get("date"), errors="coerce")
    outcomes_df = outcomes_df.loc[outcomes_df["date"].between(pd.Timestamp(args.start), pd.Timestamp(args.end))].copy()

    race_meta = _prepare_race_meta(features_df, hist_df)
    pred_df = pred_df.merge(race_meta, on="race_id", how="left")
    base_pred_df = pred_df.drop(columns=[c for c in ["date", "month", "jcd", "venue_name"] if c in pred_df.columns])

    truth_df = build_truth(historical_path)
    truth_df = truth_df[truth_df["race_id"].astype(str).isin(set(hist_df["race_id"].astype(str)))].copy()
    rank_df = _enrich_rank_rows(artifacts["candidates_path"], skip_df, truth_df, race_meta)

    thresholds = {
        "min_buy_count": args.min_buy_count,
        "min_hit_rate": args.min_hit_rate,
        "min_roi": args.min_roi,
        "min_exact": args.min_exact,
        "min_top5": args.min_top5,
        "min_top10": args.min_top10,
        "max_avg_rank": args.max_avg_rank,
        "max_drawdown": args.max_drawdown,
    }

    race_results, backtest_summary = run_backtest(base_pred_df, outcomes_df)
    race_meta_for_results = race_meta[[c for c in ["race_id", "month", "jcd", "venue_name"] if c in race_meta.columns]].copy()
    race_results = race_results.merge(race_meta_for_results, on="race_id", how="left")
    venue_df, month_df, overall_block = _build_group_summaries(
        race_results,
        rank_df,
        skip_df.merge(race_meta_for_results, on="race_id", how="left"),
        thresholds,
    )
    race_results.to_csv(out_dir / "race_results.csv", index=False)
    skip_df.merge(race_meta, on="race_id", how="left").to_csv(out_dir / "skip_decisions_year.csv", index=False)
    rank_df.to_csv(out_dir / "rank_rows.csv", index=False)
    venue_df.to_csv(out_dir / "venue_summary.csv", index=False)
    month_df.to_csv(out_dir / "month_summary.csv", index=False)
    pd.DataFrame(overall_block["checks"]).to_csv(out_dir / "threshold_judgement_overall.csv", index=False)

    data_quality = _data_quality(skip_df, race_results, target_race_count=race_meta["race_id"].nunique(), generated_race_count=skip_df["race_id"].nunique())
    failure_causes = _top_failure_causes(overall_block["summary"], venue_df, month_df, data_quality)
    odds_availability_report = _build_odds_availability_comparison(
        base_pred_df,
        outcomes_df,
        skip_df,
        rank_df,
        race_meta,
        out_dir,
    )

    report = {
        "period": {"start": args.start, "end": args.end},
        "thresholds": thresholds,
        "overall_summary": overall_block["summary"],
        "overall_threshold_checks": overall_block["checks"],
        "venue_summary_path": str(out_dir / "venue_summary.csv"),
        "month_summary_path": str(out_dir / "month_summary.csv"),
        "data_quality": data_quality,
        "failure_causes_top3": failure_causes,
        "odds_availability_comparison": odds_availability_report,
        "artifacts": {k: str(v) for k, v in artifacts.items()},
        "backtest_summary": backtest_summary,
    }
    safe_report_text = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    (out_dir / "yearly_backtest_report.json").write_text(safe_report_text, encoding="utf-8")
    (out_dir / "threshold_judgement_by_venue.json").write_text(
        json.dumps(_to_jsonable_records(venue_df[["group_value", "jcd", "pass_status", "unmet_metrics"]]), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "threshold_judgement_by_month.json").write_text(
        json.dumps(_to_jsonable_records(month_df[["group_value", "pass_status", "unmet_metrics"]]), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(safe_report_text)
    print(f"[saved] {out_dir}")


if __name__ == "__main__":
    main()
