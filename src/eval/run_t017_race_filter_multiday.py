from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, median

import pandas as pd
from src.data.parse_fixed_width import BoatRaceParser
from src.eval.ablation_and_bottleneck import (
    build_actual_map,
    generate_trifecta_candidates_for_race,
    load_candidate_generation_config,
    select_top60_per_first_pattern,
)


ROOT = Path(__file__).resolve().parents[2]
DAILY_ROOT = ROOT / "reports" / "daily"
TMP_ROOT = ROOT / "data" / "tmp"
REPORT_TMP_ROOT = ROOT / "reports" / "tmp"
OFFICIAL_RESULTS_ROOT = ROOT / "data" / "raw" / "official" / "results"
OUT_MD = ROOT / "reports" / "t017_race_filter_multiday_validation.md"
OUT_JSON = ROOT / "reports" / "t017_race_filter_multiday_validation.json"
OUT_DIAG_MD = ROOT / "reports" / "t017_missing_snapshot_diagnostics.md"
OUT_DIAG_JSON = ROOT / "reports" / "t017_missing_snapshot_diagnostics.json"

DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
METHOD_ORDER = [
    "no_filter",
    "first_gap_filter",
    "top_score_gap_filter",
    "concentration_filter",
    "and_filter",
]


@dataclass
class SnapshotSource:
    date_label: str
    source_dir: Path
    snapshot_dir: Path
    source_type: str
    source_status: str
    missing_items: list[str]


def normalize_date_label(label: str) -> str:
    return label.replace("-", "")


def as_float(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fmt(value, digits: int = 4) -> str:
    num = as_float(value)
    if num is None:
        return "n/a"
    return f"{num:.{digits}f}"


def _snapshot_date_from_dir(path: Path) -> str | None:
    m = re.match(r"^(\d{8})_eval$", path.name)
    if not m:
        return None
    date8 = m.group(1)
    return f"{date8[:4]}-{date8[4:6]}-{date8[6:8]}"


def list_daily_sources() -> list[SnapshotSource]:
    sources_by_date: dict[str, SnapshotSource] = {}
    if not DAILY_ROOT.exists():
        return []

    for day_dir in sorted(DAILY_ROOT.iterdir()):
        if not day_dir.is_dir() or not DATE_DIR_RE.match(day_dir.name):
            continue
        date8 = normalize_date_label(day_dir.name)
        snapshot_dir = TMP_ROOT / f"{date8}_eval"
        required = [
            "today_features.csv",
            "today_win_proba.csv",
        ]
        missing_items = [name for name in required if not (day_dir / name).exists()]
        status = "available" if not missing_items else "incomplete"
        sources_by_date[day_dir.name] = SnapshotSource(
            date_label=day_dir.name,
            source_dir=day_dir,
            snapshot_dir=snapshot_dir,
            source_type="daily",
            source_status=status,
            missing_items=missing_items,
        )

    if TMP_ROOT.exists():
        for snapshot_dir in sorted(TMP_ROOT.iterdir()):
            if not snapshot_dir.is_dir():
                continue
            date_label = _snapshot_date_from_dir(snapshot_dir)
            if not date_label:
                continue
            required = [
                "today_features.csv",
                "today_win_proba.csv",
                "backtest_race_results.csv",
            ]
            missing_items = [name for name in required if not (snapshot_dir / name).exists()]
            status = "available" if not missing_items else "incomplete"
            existing = sources_by_date.get(date_label)
            if existing is not None and existing.source_type == "snapshot" and status != "available":
                continue
            if existing is not None and existing.source_type == "snapshot" and status == "available":
                sources_by_date[date_label] = SnapshotSource(
                    date_label=date_label,
                    source_dir=snapshot_dir,
                    snapshot_dir=snapshot_dir,
                    source_type="snapshot",
                    source_status=status,
                    missing_items=missing_items,
                )
                continue
            if existing is not None and existing.source_type == "daily" and status != "available":
                continue
            if existing is None or existing.source_type == "daily":
                sources_by_date[date_label] = SnapshotSource(
                    date_label=date_label,
                    source_dir=snapshot_dir,
                    snapshot_dir=snapshot_dir,
                    source_type="snapshot",
                    source_status=status,
                    missing_items=missing_items,
                )
    return sorted(sources_by_date.values(), key=lambda item: item.date_label)


def _copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _build_official_results_snapshot(date_label: str) -> tuple[pd.DataFrame, Path]:
    date8 = normalize_date_label(date_label)
    result_txt = OFFICIAL_RESULTS_ROOT / f"K{date8[2:]}.TXT"
    result_csv = OFFICIAL_RESULTS_ROOT.parent / "parsed" / f"K{date8[2:]}.csv"
    required = {"race_id", "date", "lane", "finish_position"}
    if result_csv.exists():
        result_df = pd.read_csv(result_csv)
        if required.issubset(set(result_df.columns)):
            return result_df, result_csv
    if not result_txt.exists():
        raise FileNotFoundError(f"missing official results file for {date_label}: {result_txt}")

    result_df = BoatRaceParser.parse_results_file(result_txt)
    return result_df.copy(), result_txt


def _build_backtest_from_official_results(features_df: pd.DataFrame, result_df: pd.DataFrame) -> pd.DataFrame:
    work = result_df.copy()
    required = {"race_id", "date", "lane", "finish_position"}
    missing = required - set(work.columns)
    if missing:
        raise ValueError(f"official results snapshot missing columns: {sorted(missing)}")

    work["finish_position"] = pd.to_numeric(work["finish_position"], errors="coerce")
    work["lane"] = pd.to_numeric(work["lane"], errors="coerce")
    work["odds_trifecta"] = pd.to_numeric(work.get("odds_trifecta"), errors="coerce")
    work = work.dropna(subset=["race_id", "finish_position", "lane"]).copy()
    work["lane"] = work["lane"].astype(int)
    work["finish_position"] = work["finish_position"].astype(int)

    rows: list[dict[str, object]] = []
    for _, grp in work.groupby(["date", "jcd", "race_no"], sort=False):
        grp = grp.sort_values("finish_position")
        top3_rows: list[pd.Series] = []
        seen_lanes: set[int] = set()
        for _, row in grp.iterrows():
            lane = int(row["lane"])
            if lane in seen_lanes:
                continue
            seen_lanes.add(lane)
            top3_rows.append(row)
            if len(top3_rows) >= 3:
                break
        if len(top3_rows) < 3:
            continue
        top3 = pd.DataFrame(top3_rows)
        actual_trifecta = "-".join(top3["lane"].astype(int).astype(str).tolist())
        odds_value = grp.loc[grp["odds_trifecta"].notna() & (grp["odds_trifecta"] > 0), "odds_trifecta"].head(1)
        odds = float(odds_value.iloc[0]) if len(odds_value) else 0.0
        date_value = str(grp["date"].iloc[0]).replace("-", "")
        jcd_value = int(float(pd.to_numeric(grp["jcd"].iloc[0], errors="coerce")))
        race_no_value = int(float(pd.to_numeric(grp["race_no"].iloc[0], errors="coerce")))
        rows.append(
            {
                "race_id": f"{date_value}-{jcd_value:02d}-{race_no_value:02d}",
                "date": str(grp["date"].iloc[0]),
                "actual_trifecta": actual_trifecta,
                "result_available": True,
                "official_odds": odds,
                "settled_odds": odds,
            }
        )

    backtest_df = pd.DataFrame(rows)
    if backtest_df.empty:
        raise ValueError("official results snapshot produced no result_available rows")

    race_meta = features_df[["race_id", "date"]].drop_duplicates("race_id").copy()
    race_meta["race_id"] = race_meta["race_id"].astype(str)
    backtest_df["race_id"] = backtest_df["race_id"].astype(str)
    merged = race_meta.merge(backtest_df, on="race_id", how="left")
    merged["result_available"] = merged["result_available"].fillna(False)
    merged["official_odds"] = pd.to_numeric(merged["official_odds"], errors="coerce").fillna(0.0)
    merged["settled_odds"] = pd.to_numeric(merged["settled_odds"], errors="coerce").fillna(0.0)
    return merged


def materialize_snapshot(source: SnapshotSource) -> dict[str, object]:
    snapshot_dir = source.snapshot_dir
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    if source.source_type == "snapshot":
        return {
            "date": source.date_label.replace("-", ""),
            "source_daily_dir": str(source.source_dir),
            "snapshot_dir": str(snapshot_dir),
            "source_status_before_materialization": "snapshot_existing",
            "missing_source_items": source.missing_items,
            "files": {
                "today_features.csv": {
                    "source": str(snapshot_dir / "today_features.csv"),
                    "rows": _count_csv_rows(snapshot_dir / "today_features.csv"),
                },
                "today_win_proba.csv": {
                    "source": str(snapshot_dir / "today_win_proba.csv"),
                    "rows": _count_csv_rows(snapshot_dir / "today_win_proba.csv"),
                },
                "backtest_race_results.csv": {
                    "source": str(snapshot_dir / "backtest_race_results.csv"),
                    "rows": _count_csv_rows(snapshot_dir / "backtest_race_results.csv"),
                },
            },
        }

    features_src = source.source_dir / "today_features.csv"
    proba_src = source.source_dir / "today_win_proba.csv"
    summary_src = source.source_dir / "daily_summary.json"
    daily_eval_src = source.source_dir / "daily_evaluation_race_results.csv"

    features_df = pd.read_csv(features_src)
    result_df, result_source = _build_official_results_snapshot(source.date_label)
    backtest_df = _build_backtest_from_official_results(features_df, result_df)
    if daily_eval_src.exists():
        daily_eval_df = pd.read_csv(daily_eval_src, low_memory=False)
        odds_cols = [c for c in ["race_id", "official_odds", "settled_odds"] if c in daily_eval_df.columns]
        if len(odds_cols) >= 2:
            odds_df = daily_eval_df[odds_cols].drop_duplicates(subset=["race_id"]).copy()
            backtest_df = backtest_df.merge(odds_df, on="race_id", how="left", suffixes=("", "_daily"))
            for col in ["official_odds", "settled_odds"]:
                daily_col = f"{col}_daily"
                if daily_col in backtest_df.columns:
                    base = pd.to_numeric(backtest_df[col], errors="coerce")
                    daily = pd.to_numeric(backtest_df[daily_col], errors="coerce")
                    base = base.where(base > 0)
                    backtest_df[col] = daily.where(daily > 0).fillna(base).fillna(0.0)
                    backtest_df.drop(columns=[daily_col], inplace=True)

    _copy_file(features_src, snapshot_dir / "today_features.csv")
    _copy_file(proba_src, snapshot_dir / "today_win_proba.csv")
    backtest_df.to_csv(snapshot_dir / "backtest_race_results.csv", index=False)
    if summary_src.exists():
        _copy_file(summary_src, snapshot_dir / "daily_summary.json")

    manifest = {
        "date": source.date_label.replace("-", ""),
        "source_daily_dir": str(source.source_dir),
        "snapshot_dir": str(snapshot_dir),
        "source_status_before_materialization": source.source_status,
        "missing_source_items": source.missing_items,
        "files": {
            "today_features.csv": {
                "source": str(features_src),
                "rows": _count_csv_rows(features_src),
            },
            "today_win_proba.csv": {
                "source": str(proba_src),
                "rows": _count_csv_rows(proba_src),
            },
            "backtest_race_results.csv": {
                "source": str(result_source),
                "rows": int(len(backtest_df)),
            },
        },
    }
    (snapshot_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest


def _count_csv_rows(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        with path.open("r", encoding="utf-8", newline="") as f:
            return max(sum(1 for _ in f) - 1, 0)
    except Exception:
        return 0


def _load_snapshot_frames(snapshot_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    proba_df = pd.read_csv(snapshot_dir / "today_win_proba.csv")
    feat_df = pd.read_csv(snapshot_dir / "today_features.csv")
    backtest_df = pd.read_csv(snapshot_dir / "backtest_race_results.csv")

    proba_df["lane"] = pd.to_numeric(proba_df["lane"], errors="coerce")
    feat_df["lane"] = pd.to_numeric(feat_df["lane"], errors="coerce")
    proba_df = proba_df.dropna(subset=["race_id", "lane"]).copy()
    feat_df = feat_df.dropna(subset=["race_id", "lane"]).copy()
    proba_df["lane"] = proba_df["lane"].astype(int)
    feat_df["lane"] = feat_df["lane"].astype(int)
    merged_df = proba_df.merge(feat_df, on=["race_id", "lane"], how="left")
    merged_df = merged_df.drop_duplicates(subset=["race_id", "lane"]).copy()
    return merged_df, feat_df, backtest_df


def _build_race_filter_report(snapshot_dir: Path) -> dict[str, object]:
    merged_df, feat_df, backtest_df = _load_snapshot_frames(snapshot_dir)
    actual_map = build_actual_map(backtest_df)
    race_ids = list(merged_df["race_id"].astype(str).unique())
    candidate_cfg = load_candidate_generation_config()
    top_n_win = int(candidate_cfg.get("top_n_win", 6))

    race_eval_rows: list[dict[str, object]] = []
    for race_id in race_ids:
        actual = actual_map.get(race_id)
        if actual is None:
            continue
        actual_trifecta = str(actual["actual_trifecta"]).strip()
        race_data = merged_df[merged_df["race_id"].astype(str) == race_id].copy()
        if race_data.empty:
            continue

        full_candidates = generate_trifecta_candidates_for_race(
            race_data,
            top_n_win=top_n_win,
            max_trifecta_combinations=100000,
        )
        if not full_candidates:
            continue
        picked = select_top60_per_first_pattern(
            full_candidates,
            min_per_first=12,
            fill_mode="global",
        )
        if len(picked) == 0:
            continue

        bt_row = backtest_df[backtest_df["race_id"].astype(str) == race_id].iloc[0]
        odds = pd.to_numeric(bt_row.get("official_odds"), errors="coerce")
        if pd.isna(odds) or float(odds) <= 0:
            odds = pd.to_numeric(bt_row.get("settled_odds"), errors="coerce")
        if pd.isna(odds) or float(odds) <= 0:
            odds = 0.0
        odds_value = float(odds)

        race_sorted = race_data.copy()
        race_sorted["win_proba_norm"] = pd.to_numeric(race_sorted["win_proba_norm"], errors="coerce").fillna(0.0)
        race_sorted = race_sorted.sort_values("win_proba_norm", ascending=False).reset_index(drop=True)
        first_gap = 0.0
        if len(race_sorted) >= 2:
            first_gap = float(race_sorted.iloc[0]["win_proba_norm"] - race_sorted.iloc[1]["win_proba_norm"])

        top_score_gap = 0.0
        if len(picked) >= 2:
            top_score_gap = float(picked[0]["approx_prob"]) - float(picked[1]["approx_prob"])

        picked_probs = [float(r.get("approx_prob", 0.0)) for r in picked]
        concentration_top5 = 0.0
        total_prob = float(sum(picked_probs))
        if total_prob > 0:
            concentration_top5 = float(sum(picked_probs[:5]) / total_prob)

        picked_count = len(picked)
        actual_in_set = any(str(row["trifecta"]).strip() == actual_trifecta for row in picked)
        stake_on_actual = (1.0 / picked_count) if actual_in_set and picked_count > 0 else 0.0
        payout = (stake_on_actual * odds_value) if odds_value > 0 else 0.0
        profit = payout - 1.0

        race_eval_rows.append(
            {
                "race_id": race_id,
                "date": str(bt_row.get("date", "")),
                "first_gap": first_gap,
                "top_score_gap": top_score_gap,
                "concentration_top5": concentration_top5,
                "hit": bool(actual_in_set),
                "missing_odds": bool(odds_value <= 0),
                "profit_if_bought": profit,
            }
        )

    race_eval_df = pd.DataFrame(race_eval_rows)
    if race_eval_df.empty:
        return {
            "target": "race_filter_comparison",
            "candidate_mode_fixed": "per_first_m12_global",
            "betting_mode_fixed": "flat_bet_race_total_stake_fixed_to_1.0",
            "thresholds": {"first_gap_q60": 0.0, "top_score_gap_q60": 0.0, "concentration_top5_q60": 0.0},
            "methods": {
                "no_filter": {
                    "result_available_rows": 0,
                    "bought_races": 0,
                    "skipped_races": 0,
                    "missing_odds_rows": 0,
                    "total_stake": 0.0,
                    "total_return": 0.0,
                    "profit": 0.0,
                    "hit_count": 0,
                    "hit_rate": None,
                    "roi": None,
                    "recovery_rate": None,
                    "max_drawdown": 0.0,
                    "longest_losing_streak": 0,
                    "score": None,
                }
            },
            "diff_vs_no_filter": {},
            "threshold_sweep": {},
            "threshold_sweep_best_by_roi": {},
            "threshold_sweep_best_by_profit": {},
            "threshold_sweep_best_by_score": {},
            "rolling_window_filter_comparison": [],
        }

    q_first_gap = float(race_eval_df["first_gap"].quantile(0.6))
    q_top_score_gap = float(race_eval_df["top_score_gap"].quantile(0.6))
    q_concentration = float(race_eval_df["concentration_top5"].quantile(0.6))

    def build_filter_metrics(
        source_df: pd.DataFrame,
        first_gap_threshold: float,
        top_score_gap_threshold: float,
        concentration_threshold: float,
        *,
        use_and_filter: bool = False,
    ) -> dict[str, dict[str, object]]:
        active_df = source_df.copy()
        if active_df.empty:
            return {
                "no_filter": {
                    "result_available_rows": 0,
                    "bought_races": 0,
                    "skipped_races": 0,
                    "missing_odds_rows": 0,
                    "total_stake": 0.0,
                    "total_return": 0.0,
                    "profit": 0.0,
                    "hit_count": 0,
                    "hit_rate": None,
                    "roi": None,
                    "recovery_rate": None,
                    "max_drawdown": 0.0,
                    "longest_losing_streak": 0,
                }
            }

        race_filters = {
            "no_filter": lambda row: True,
            "first_gap_filter": lambda row: float(row["first_gap"]) >= first_gap_threshold,
            "top_score_gap_filter": lambda row: float(row["top_score_gap"]) >= top_score_gap_threshold,
            "concentration_filter": lambda row: float(row["concentration_top5"]) >= concentration_threshold,
        }
        if use_and_filter:
            race_filters["and_filter"] = lambda row: (
                float(row["first_gap"]) >= first_gap_threshold
                and float(row["concentration_top5"]) >= concentration_threshold
            )

        metrics: dict[str, dict[str, object]] = {}
        for filter_name, filter_fn in race_filters.items():
            bought = active_df[active_df.apply(filter_fn, axis=1)].copy()
            bought_count = int(len(bought))
            skipped_count = int(len(active_df) - bought_count)
            total_stake = float(bought_count)
            total_return = float(bought["profit_if_bought"].sum() + total_stake) if bought_count > 0 else 0.0
            profit = float(total_return - total_stake)
            hit_count = int(bought["hit"].sum()) if bought_count > 0 else 0
            hit_rate = round(float(hit_count / bought_count), 4) if bought_count > 0 else None
            roi = round(float(profit / total_stake), 4) if total_stake > 0 else None
            recovery_rate = round(float(total_return / total_stake), 4) if total_stake > 0 else None
            max_drawdown = 0.0
            if bought_count > 0:
                values = bought["profit_if_bought"].tolist()
                equity = 0.0
                peak = 0.0
                for v in values:
                    equity += float(v)
                    peak = max(peak, equity)
                    max_drawdown = max(max_drawdown, peak - equity)
            missing_odds_rows = int(bought["missing_odds"].sum()) if bought_count > 0 else 0

            metrics[filter_name] = {
                "result_available_rows": int(len(race_eval_df)),
                "bought_races": bought_count,
                "skipped_races": skipped_count,
                "missing_odds_rows": missing_odds_rows,
                "total_stake": round(total_stake, 4),
                "total_return": round(total_return, 4),
                "profit": round(profit, 4),
                "hit_count": hit_count,
                "hit_rate": hit_rate,
                "roi": roi,
                "recovery_rate": recovery_rate,
                "max_drawdown": round(max_drawdown, 4),
                "longest_losing_streak": 0,
                "score": round(float(profit) - 0.5 * float(max_drawdown), 4),
            }

        return metrics

    race_filter_metrics = build_filter_metrics(
        race_eval_df,
        q_first_gap,
        q_top_score_gap,
        q_concentration,
        use_and_filter=True,
    )
    threshold_sweep: dict[str, dict[str, object]] = {}
    for level in [0.5, 0.6, 0.7, 0.8]:
        fg = float(race_eval_df["first_gap"].quantile(level))
        tg = float(race_eval_df["top_score_gap"].quantile(level))
        cg = float(race_eval_df["concentration_top5"].quantile(level))
        threshold_sweep[f"q{int(level * 100):02d}"] = {
            "thresholds": {
                "first_gap": round(fg, 8),
                "top_score_gap": round(tg, 8),
                "concentration_top5": round(cg, 8),
            },
            "methods": build_filter_metrics(race_eval_df, fg, tg, cg, use_and_filter=True),
        }

    def pick_best(method_name: str, metric_name: str) -> dict[str, object]:
        best_label = None
        best_metrics = None
        for label, payload in threshold_sweep.items():
            metrics = payload.get("methods", {}).get(method_name)
            if not metrics:
                continue
            value = metrics.get(metric_name)
            if value is None:
                continue
            if best_metrics is None or float(value) > float(best_metrics.get(metric_name, float("-inf"))):
                best_label = label
                best_metrics = metrics
        return {"threshold_label": best_label, "metrics": best_metrics}

    sweep_best_by_roi = {
        method: pick_best(method, "roi")
        for method in ["first_gap_filter", "top_score_gap_filter", "concentration_filter"]
    }
    sweep_best_by_profit = {
        method: pick_best(method, "profit")
        for method in ["first_gap_filter", "top_score_gap_filter", "concentration_filter"]
    }
    sweep_best_by_score = {
        method: pick_best(method, "score")
        for method in ["first_gap_filter", "top_score_gap_filter", "concentration_filter", "and_filter"]
    }

    baseline_filter = race_filter_metrics["no_filter"]
    race_filter_diff_vs_baseline = {}
    for filter_name, cur in race_filter_metrics.items():
        if filter_name == "no_filter":
            continue
        race_filter_diff_vs_baseline[filter_name] = {
            "roi_diff": round(float(cur["roi"]) - float(baseline_filter["roi"]), 4)
            if cur["roi"] is not None and baseline_filter["roi"] is not None
            else None,
            "profit_diff": round(float(cur["profit"]) - float(baseline_filter["profit"]), 4),
            "max_drawdown_diff": round(
                float(cur["max_drawdown"]) - float(baseline_filter["max_drawdown"]),
                4,
            ),
        }

    return {
        "target": "race_filter_comparison",
        "candidate_mode_fixed": "per_first_m12_global",
        "betting_mode_fixed": "flat_bet_race_total_stake_fixed_to_1.0",
        "thresholds": {
            "first_gap_q60": round(q_first_gap, 8),
            "top_score_gap_q60": round(q_top_score_gap, 8),
            "concentration_top5_q60": round(q_concentration, 8),
        },
        "methods": race_filter_metrics,
        "diff_vs_no_filter": race_filter_diff_vs_baseline,
        "threshold_sweep": threshold_sweep,
        "threshold_sweep_best_by_roi": sweep_best_by_roi,
        "threshold_sweep_best_by_profit": sweep_best_by_profit,
        "threshold_sweep_best_by_score": sweep_best_by_score,
        "rolling_window_filter_comparison": [],
    }


def _run_ablation(snapshot_dir: Path, date_label: str) -> tuple[dict[str, object], Path, int]:
    REPORT_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    out_json = REPORT_TMP_ROOT / f"t017_race_filter_comparison_{normalize_date_label(date_label)}.json"
    report = _build_race_filter_report(snapshot_dir)
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report, out_json, 0


def _build_day_row(date_label: str, report: dict[str, object]) -> dict[str, object]:
    methods = report.get("methods", {}) if isinstance(report, dict) else {}
    no_filter = methods.get("no_filter", {}) if isinstance(methods, dict) else {}
    row = {
        "date": date_label,
        "total_races": int(no_filter.get("result_available_rows", 0) or 0),
        "no_filter_roi": as_float(no_filter.get("roi")),
        "no_filter_max_drawdown": as_float(no_filter.get("max_drawdown")),
        "no_filter_bet_count": int(no_filter.get("bought_races", 0) or 0),
        "first_gap_filter_roi": as_float(methods.get("first_gap_filter", {}).get("roi")) if isinstance(methods, dict) else None,
        "first_gap_filter_max_drawdown": as_float(methods.get("first_gap_filter", {}).get("max_drawdown")) if isinstance(methods, dict) else None,
        "first_gap_filter_bet_count": int(methods.get("first_gap_filter", {}).get("bought_races", 0) or 0) if isinstance(methods, dict) else 0,
        "top_score_gap_filter_roi": as_float(methods.get("top_score_gap_filter", {}).get("roi")) if isinstance(methods, dict) else None,
        "top_score_gap_filter_max_drawdown": as_float(methods.get("top_score_gap_filter", {}).get("max_drawdown")) if isinstance(methods, dict) else None,
        "top_score_gap_filter_bet_count": int(methods.get("top_score_gap_filter", {}).get("bought_races", 0) or 0) if isinstance(methods, dict) else 0,
        "concentration_filter_roi": as_float(methods.get("concentration_filter", {}).get("roi")) if isinstance(methods, dict) else None,
        "concentration_filter_max_drawdown": as_float(methods.get("concentration_filter", {}).get("max_drawdown")) if isinstance(methods, dict) else None,
        "concentration_filter_bet_count": int(methods.get("concentration_filter", {}).get("bought_races", 0) or 0) if isinstance(methods, dict) else 0,
        "and_filter_roi": as_float(methods.get("and_filter", {}).get("roi")) if isinstance(methods, dict) else None,
        "and_filter_max_drawdown": as_float(methods.get("and_filter", {}).get("max_drawdown")) if isinstance(methods, dict) else None,
        "and_filter_bet_count": int(methods.get("and_filter", {}).get("bought_races", 0) or 0) if isinstance(methods, dict) else 0,
    }
    row["best_filter_by_roi"] = max(
        ((name, as_float(metrics.get("roi"))) for name, metrics in methods.items() if as_float(metrics.get("roi")) is not None),
        key=lambda item: item[1],
        default=(None, None),
    )[0]
    return row


def _aggregate_metric(values: list[float | None], fn) -> float | None:
    clean = [float(v) for v in values if v is not None]
    if not clean:
        return None
    return float(fn(clean))


def _build_filter_aggregation(day_rows: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    filters = [
        "no_filter",
        "first_gap_filter",
        "top_score_gap_filter",
        "concentration_filter",
        "and_filter",
    ]
    agg: dict[str, dict[str, object]] = {}
    for flt in filters:
        rois = [row.get(f"{flt}_roi") for row in day_rows]
        dds = [row.get(f"{flt}_max_drawdown") for row in day_rows]
        agg[flt] = {
            "mean_roi": _aggregate_metric(rois, mean),
            "median_roi": _aggregate_metric(rois, median),
            "worst_roi": _aggregate_metric(rois, min),
            "best_roi": _aggregate_metric(rois, max),
            "positive_day_count": int(sum(1 for v in rois if v is not None and float(v) > 0)),
            "positive_day_rate": (
                round(sum(1 for v in rois if v is not None and float(v) > 0) / len([v for v in rois if v is not None]), 4)
                if any(v is not None for v in rois)
                else None
            ),
            "mean_max_drawdown": _aggregate_metric(dds, mean),
        }
    return agg


def _build_no_filter_diffs(day_rows: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    for flt in ["first_gap_filter", "top_score_gap_filter", "concentration_filter", "and_filter"]:
        roi_diffs = []
        dd_diffs = []
        for row in day_rows:
            base = row.get("no_filter_roi")
            cur = row.get(f"{flt}_roi")
            base_dd = row.get("no_filter_max_drawdown")
            cur_dd = row.get(f"{flt}_max_drawdown")
            if base is not None and cur is not None:
                roi_diffs.append(float(cur) - float(base))
            if base_dd is not None and cur_dd is not None:
                dd_diffs.append(float(cur_dd) - float(base_dd))
        out[flt] = {
            "mean_roi_diff": _aggregate_metric(roi_diffs, mean),
            "median_roi_diff": _aggregate_metric(roi_diffs, median),
            "worst_roi_diff": _aggregate_metric(roi_diffs, min),
            "best_roi_diff": _aggregate_metric(roi_diffs, max),
            "mean_max_drawdown_diff": _aggregate_metric(dd_diffs, mean),
        }
    return out


def _classify_missing(source: SnapshotSource) -> dict[str, object]:
    date8 = normalize_date_label(source.date_label)
    pred_path = ROOT / "data" / "predictions" / date8
    ui_path = ROOT / "data" / "ui" / date8
    frozen_path = ROOT / "frozen_bets" / date8
    odds_path = ROOT / "data" / "odds" / date8
    raw_result_path = ROOT / "data" / "raw" / "official" / "results" / f"K{date8[2:]}.TXT"
    reports_daily_dir = source.source_dir

    if source.source_type == "snapshot":
        availability = {
            "prediction_dir_exists": pred_path.exists(),
            "ui_dir_exists": ui_path.exists(),
            "frozen_bets_dir_exists": frozen_path.exists(),
            "odds_dir_exists": odds_path.exists(),
            "results_file_exists": raw_result_path.exists(),
            "reports_daily_dir_exists": reports_daily_dir.exists(),
            "eval_snapshot_dir_exists_before": source.snapshot_dir.exists(),
            "required_daily_files_present": not source.missing_items,
        }
        reasons = []
        if not source.missing_items:
            reasons.append("ready_for_snapshot")
            classification = "ready_for_snapshot"
        else:
            classification = "invalid_snapshot_shape"
            reasons.append(f"missing snapshot files: {', '.join(source.missing_items)}")
        return {
            "date": date8,
            "date_label": source.date_label,
            "classification": classification,
            "availability": availability,
            "reconstructable_from_reports_daily": not source.missing_items,
            "reason_details": reasons,
            "snapshot_dir_after_materialization": str(source.snapshot_dir),
        }

    availability = {
        "prediction_dir_exists": pred_path.exists(),
        "ui_dir_exists": ui_path.exists(),
        "frozen_bets_dir_exists": frozen_path.exists(),
        "odds_dir_exists": odds_path.exists(),
        "results_file_exists": raw_result_path.exists(),
        "reports_daily_dir_exists": reports_daily_dir.exists(),
        "eval_snapshot_dir_exists_before": source.snapshot_dir.exists(),
        "required_daily_files_present": not source.missing_items,
    }

    reasons = []
    classification = "unknown"
    if not availability["prediction_dir_exists"]:
        reasons.append("missing canonical data/predictions snapshot")
        classification = "missing_prediction"
    if not availability["ui_dir_exists"]:
        reasons.append("missing canonical data/ui snapshot")
        if classification == "unknown":
            classification = "missing_prediction"
    if not availability["frozen_bets_dir_exists"]:
        reasons.append("missing frozen_bets snapshot")
        if classification == "unknown":
            classification = "missing_frozen_bets"
    if not availability["odds_dir_exists"]:
        reasons.append("missing odds snapshot")
        if classification == "unknown":
            classification = "missing_odds"
    if not availability["results_file_exists"]:
        reasons.append("missing raw results file")
        if classification == "unknown":
            classification = "missing_results"
    if source.missing_items:
        reasons.append(f"missing required daily files: {', '.join(source.missing_items)}")
        if classification == "unknown":
            classification = "missing_required_columns"

    reconstructable = (
        reports_daily_dir.exists()
        and (reports_daily_dir / "today_features.csv").exists()
        and (reports_daily_dir / "today_win_proba.csv").exists()
        and availability["results_file_exists"]
    )
    if reconstructable:
        reasons.append("reconstructable from reports/daily artifacts plus official results")

    return {
        "date": date8,
        "date_label": source.date_label,
        "classification": classification,
        "availability": availability,
        "reconstructable_from_reports_daily": reconstructable,
        "reason_details": reasons,
        "snapshot_dir_after_materialization": str(source.snapshot_dir),
    }


def _choose_primary_candidate(day_rows: list[dict[str, object]], filter_agg: dict[str, dict[str, object]]) -> str | None:
    valid_days = [row for row in day_rows if row.get("total_races", 0) > 0]
    if len(valid_days) < 3:
        return "provisional_concentration_filter"

    no_filter_mean = filter_agg["no_filter"]["mean_roi"]
    conc = filter_agg["concentration_filter"]
    first_gap = filter_agg["first_gap_filter"]

    if conc["mean_roi"] is None or no_filter_mean is None:
        return "needs_more_data"

    conc_beats = conc["mean_roi"] > no_filter_mean and (conc["positive_day_rate"] or 0.0) >= 0.5
    first_beats = first_gap["mean_roi"] is not None and first_gap["mean_roi"] > no_filter_mean and (first_gap["positive_day_rate"] or 0.0) >= 0.5

    if not conc_beats and not first_beats:
        return "needs_more_data"
    if first_beats and (
        (first_gap["mean_roi"] or -999.0) >= (conc["mean_roi"] or -999.0)
        and (first_gap["mean_max_drawdown"] or 999.0) <= (conc["mean_max_drawdown"] or 999.0)
    ):
        return "provisional_first_gap_filter"
    if conc_beats:
        return "provisional_concentration_filter"
    return "needs_more_data"


def build_markdown(summary: dict[str, object], diagnostics: dict[str, object]) -> str:
    day_rows = summary["day_rows"]
    filter_agg = summary["filter_aggregation"]
    diff_agg = summary["no_filter_diffs"]

    day_table_lines = []
    for row in day_rows:
        day_table_lines.append(
            "| {date} | {total_races} | {no_roi} | {conc_roi} | {first_roi} | {top_roi} | {and_roi} | {dd} | {bet} |".format(
                date=row["date"],
                total_races=row.get("total_races", 0),
                no_roi=fmt(row.get("no_filter_roi")),
                conc_roi=fmt(row.get("concentration_filter_roi")),
                first_roi=fmt(row.get("first_gap_filter_roi")),
                top_roi=fmt(row.get("top_score_gap_filter_roi")),
                and_roi=fmt(row.get("and_filter_roi")),
                dd=fmt(row.get("no_filter_max_drawdown")),
                bet=row.get("no_filter_bet_count", 0),
            )
        )

    agg_lines = []
    for name in ["no_filter", "first_gap_filter", "top_score_gap_filter", "concentration_filter", "and_filter"]:
        row = filter_agg[name]
        agg_lines.append(
            "| {name} | {mean_roi} | {median_roi} | {worst_roi} | {best_roi} | {pos_count} | {pos_rate} | {mean_dd} |".format(
                name=name,
                mean_roi=fmt(row.get("mean_roi")),
                median_roi=fmt(row.get("median_roi")),
                worst_roi=fmt(row.get("worst_roi")),
                best_roi=fmt(row.get("best_roi")),
                pos_count=row.get("positive_day_count", 0),
                pos_rate=fmt(row.get("positive_day_rate")),
                mean_dd=fmt(row.get("mean_max_drawdown")),
            )
        )

    diff_lines = []
    for name in ["first_gap_filter", "top_score_gap_filter", "concentration_filter", "and_filter"]:
        row = diff_agg[name]
        diff_lines.append(
            "| {name} | {mean_roi_diff} | {median_roi_diff} | {worst_roi_diff} | {best_roi_diff} | {mean_dd_diff} |".format(
                name=name,
                mean_roi_diff=fmt(row.get("mean_roi_diff")),
                median_roi_diff=fmt(row.get("median_roi_diff")),
                worst_roi_diff=fmt(row.get("worst_roi_diff")),
                best_roi_diff=fmt(row.get("best_roi_diff")),
                mean_dd_diff=fmt(row.get("mean_max_drawdown_diff")),
            )
        )

    validated_dates = summary["validated_dates"]
    skipped_dates = summary["skipped_dates"]
    skipped_lines = "\n".join(
        f"- {item['date']}: {item['reason']}" for item in skipped_dates
    ) or "- なし"

    diagnostics_lines = []
    for item in diagnostics["per_date"]:
        reasons = "; ".join(item.get("reason_details", [])) or "n/a"
        diagnostics_lines.append(f"- {item['date']}: {item['classification']} / {reasons}")

    md = f"""# TASK-017B Race Filter Multiday Validation

## 目的
`TASK-016` の単日好成績をそのまま信用せず、`2026-04-22` と `2026-04-23` の不足 snapshot を補完したうえで、race filter の横断安定性を再評価する。

## 検証できた日付
{chr(10).join(f"- {d}" for d in validated_dates) if validated_dates else "- なし"}

## 検証できなかった日付
{skipped_lines}

## 検証できなかった理由
{chr(10).join(diagnostics_lines) if diagnostics_lines else "- なし"}

## 日付別の結果
| date | total_races | no_filter roi | concentration_filter roi | first_gap_filter roi | top_score_gap_filter roi | and_filter roi | max_drawdown | bet_count |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(day_table_lines)}

## filter 別集計
| filter_name | mean_roi | median_roi | worst_roi | best_roi | positive_day_count | positive_day_rate | mean_max_drawdown |
|---|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(agg_lines)}

## no_filter との差分
| filter_name | mean_roi_diff | median_roi_diff | worst_roi_diff | best_roi_diff | mean_max_drawdown_diff |
|---|---:|---:|---:|---:|---:|
{chr(10).join(diff_lines)}

## 判断
- `primary_candidate_after`: `{summary["primary_candidate_after"]}`
- `production_adoption`: `false`
- `decision`: {summary["decision"]}
- `reason`: {summary["decision_reason"]}

## 注意点
- `and_filter` は参考値に留める。
- `q70` は固定しない。
- `20260311` の単日好成績は本番根拠にしない。
- 本レポートは shadow validation であり、BUY rule には未反映。

## 次にやること
1. 追加の dated snapshot を増やす。
2. 同じ runner で日数を増やして再評価する。
3. `concentration_filter` と `first_gap_filter` の安定性が出るまで production 化しない。
"""
    return md


def build_diagnostics_markdown(diagnostics: dict[str, object]) -> str:
    lines = []
    for item in diagnostics["per_date"]:
        lines.append(
            f"- {item['date']}: {item['classification']} / reconstructable={item['reconstructable_from_reports_daily']} / "
            f"{'; '.join(item.get('reason_details', []))}"
        )
    return "\n".join(
        [
            "# TASK-017B Missing Snapshot Diagnostics",
            "",
            "## Summary",
            f"- dates_checked: {len(diagnostics['per_date'])}",
            f"- reconstructed_snapshots: {len([i for i in diagnostics['per_date'] if i['reconstructable_from_reports_daily']])}",
            "",
            "## Per Date",
            *lines,
            "",
            "## Notes",
            "- canonical `data/predictions` and `data/ui` are missing for the affected dates",
            "- raw results under `data/raw/official/results` are present",
            "- snapshot materialization uses `reports/daily/<date>` artifacts, not live pipeline writes",
        ]
    )


def main() -> None:
    global DAILY_ROOT, TMP_ROOT, REPORT_TMP_ROOT
    parser = argparse.ArgumentParser(description="Run TASK-017B multiday race filter validation.")
    parser.add_argument("--daily-root", default=str(DAILY_ROOT))
    parser.add_argument("--tmp-root", default=str(TMP_ROOT))
    parser.add_argument("--report-tmp-root", default=str(REPORT_TMP_ROOT))
    parser.add_argument("--output-md", default=str(OUT_MD))
    parser.add_argument("--output-json", default=str(OUT_JSON))
    parser.add_argument("--diag-md", default=str(OUT_DIAG_MD))
    parser.add_argument("--diag-json", default=str(OUT_DIAG_JSON))
    parser.add_argument(
        "--include-day",
        action="append",
        default=[],
        help="Optional date filter in YYYY-MM-DD form. Repeat to limit validation set.",
    )
    args = parser.parse_args()

    DAILY_ROOT = Path(args.daily_root)
    TMP_ROOT = Path(args.tmp_root)
    REPORT_TMP_ROOT = Path(args.report_tmp_root)

    candidates = list_daily_sources()
    if args.include_day:
        include_set = set(args.include_day)
        candidates = [source for source in candidates if source.date_label in include_set]
    if not candidates:
        raise SystemExit("no daily snapshots found under reports/daily")

    diagnostics_records = []
    materialized_manifests = []
    day_reports = []
    skipped_dates = []

    for source in candidates:
        diagnostics_records.append(_classify_missing(source))
        if source.source_status != "available":
            skipped_dates.append({"date": source.date_label, "reason": f"incomplete daily source: {', '.join(source.missing_items)}"})
            continue
        try:
            manifest = materialize_snapshot(source)
            materialized_manifests.append(manifest)
            report, out_json_path, _ = _run_ablation(source.snapshot_dir, source.date_label)
        except FileNotFoundError as exc:
            skipped_dates.append({"date": source.date_label, "reason": str(exc)})
            continue
        except (ValueError, subprocess.CalledProcessError) as exc:
            skipped_dates.append({"date": source.date_label, "reason": f"validation failed: {exc}"})
            continue
        day_reports.append(
            {
                "date": source.date_label,
                "snapshot_dir": str(source.snapshot_dir),
                "comparison_json": str(out_json_path),
                "report": report,
                "manifest": manifest,
            }
        )

    if not day_reports:
        raise SystemExit("no complete daily snapshots were available for validation")

    day_rows = [_build_day_row(item["date"], item["report"]) for item in day_reports]
    filter_aggregation = _build_filter_aggregation(day_rows)
    no_filter_diffs = _build_no_filter_diffs(day_rows)

    validated_dates = [item["date"] for item in day_reports]
    primary_candidate_after = _choose_primary_candidate(day_rows, filter_aggregation)
    production_adoption = False
    if primary_candidate_after == "provisional_concentration_filter":
        decision = "shadow candidate retained as provisional_concentration_filter"
        decision_reason = "Validated days are still too few to promote beyond a shadow hypothesis."
    elif primary_candidate_after == "provisional_first_gap_filter":
        decision = "shadow candidate shifted to provisional_first_gap_filter"
        decision_reason = "first_gap_filter showed stronger cross-date stability than concentration_filter in this sample."
    elif primary_candidate_after == "needs_more_data":
        decision = "shadow candidate unresolved"
        decision_reason = "The validated sample does not show stable improvement over no_filter."
    else:
        decision = "shadow candidate retained"
        decision_reason = "Cross-date evidence was insufficient for production adoption."

    summary = {
        "task": "TASK-017B",
        "validated_dates": validated_dates,
        "skipped_dates": skipped_dates,
        "day_rows": day_rows,
        "filter_aggregation": filter_aggregation,
        "no_filter_diffs": no_filter_diffs,
        "primary_candidate_after": primary_candidate_after,
        "production_adoption": production_adoption,
        "decision": decision,
        "decision_reason": decision_reason,
        "materialized_snapshot_manifests": materialized_manifests,
    }
    diagnostics = {
        "task": "TASK-017B",
        "per_date": diagnostics_records,
        "materialized_snapshot_count": len(materialized_manifests),
    }

    md_text = build_markdown(summary, diagnostics)
    diag_md_text = build_diagnostics_markdown(diagnostics)

    out_md = Path(args.output_md)
    out_json = Path(args.output_json)
    diag_md = Path(args.diag_md)
    diag_json = Path(args.diag_json)
    for path in [out_md, out_json, diag_md, diag_json]:
        path.parent.mkdir(parents=True, exist_ok=True)

    out_md.write_text(md_text, encoding="utf-8")
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    diag_md.write_text(diag_md_text, encoding="utf-8")
    diag_json.write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[saved] {out_md}")
    print(f"[saved] {out_json}")
    print(f"[saved] {diag_md}")
    print(f"[saved] {diag_json}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
