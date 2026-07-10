from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from src.eval.backtest_buy_skip import build_race_outcomes, normalize_predictions, run_backtest
from src.eval.buy_zero_diagnosis import build_buy_zero_diagnosis_report
from src.eval.diagnose_upstream_pool import attach_truth, build_rank_rows, build_truth, rank_stats
from src.data.parse_fixed_width import BoatRaceParser
from src.pipeline.pipeline_utils import (
    ROOT,
    actual_trifecta_by_race,
    append_log,
    copy_artifact,
    existing_report_dir_for,
    iso_now,
    log_file_for,
    parse_date,
    read_json,
    report_dir_for,
    run_step,
    summarize_reason_keywords,
    update_rolling_summary,
    build_results_status_diagnostic,
    write_json,
)


def _load_pre_race_artifact(report_dir: Path, name: str, fallback: Path) -> Path:
    candidate = report_dir / name
    return candidate if candidate.exists() else fallback


def _missing_predictions_result(target_date: date) -> tuple[str, str, list[str]]:
    warning = f"predictions_unavailable_for_date:{target_date.isoformat()}"
    return "missing_data", "predictions_unavailable_for_date", [warning]


def _load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, low_memory=False)


def _rolling_window_note(sample_count: int) -> str:
    return "件数が少ないため改善断定しない" if sample_count < 30 else "比較可能"


def _decision_counts(df: pd.DataFrame) -> dict[str, int]:
    if df.empty or "decision" not in df.columns:
        return {"BUY": 0, "PENDING": 0, "SKIP": 0}
    counts = df["decision"].astype(str).str.upper().value_counts().to_dict()
    return {
        "BUY": int(counts.get("BUY", 0)),
        "PENDING": int(counts.get("PENDING", 0)),
        "SKIP": int(counts.get("SKIP", 0)),
    }


def _format_decision_rows(df: pd.DataFrame, decision: str, limit: int = 20) -> list[str]:
    if df.empty or "decision" not in df.columns:
        return [f"- {decision}候補なし"]

    rows = df[df["decision"].astype(str).str.upper() == decision].copy()
    if rows.empty:
        return [f"- {decision}候補なし"]

    sort_cols = [col for col in ["ev", "odds", "approx_prob"] if col in rows.columns]
    if sort_cols:
        rows = rows.sort_values(sort_cols, ascending=False)

    rows = rows.head(limit).reset_index(drop=True)
    lines = [f"- 件数: {len(rows)}"]
    lines.append("| race_id | predicted_trifecta | odds | ev | result | reason |")
    lines.append("| --- | --- | ---: | ---: | --- | --- |")
    for _, row in rows.iterrows():
        lines.append(
            "| {race_id} | {predicted_trifecta} | {odds} | {ev} | {result} | {reason} |".format(
                race_id=row.get("race_id", "-"),
                predicted_trifecta=row.get("predicted_trifecta", row.get("trifecta", "-")),
                odds="-" if pd.isna(row.get("odds")) else f"{float(row.get('odds')):.2f}",
                ev="-" if pd.isna(row.get("ev")) else f"{float(row.get('ev')):.3f}",
                result="的中" if bool(row.get("hit", False)) else ("不的中" if decision == "BUY" else ("未確定" if decision == "PENDING" else "見送り")),
                reason=str(row.get("reason", "-")),
            )
        )
    return lines


def _gate_drop_summary(skip_df: pd.DataFrame, high_signal: pd.DataFrame) -> dict:
    gate_counts = {}
    for col in ["race_gate", "first_place_gate", "pre_race_gate"]:
        if col not in high_signal.columns:
            continue
        counts = high_signal[col].fillna("NA").astype(str).value_counts().to_dict()
        gate_counts[col] = counts
    rejection_counts = {}
    if not skip_df.empty:
        non_buy = skip_df[skip_df["decision"].astype(str).str.upper() != "BUY"].copy()
        rejection_counts["reason_keywords"] = summarize_reason_keywords(non_buy, limit=12)
        for col in ["race_gate", "first_place_gate", "pre_race_gate"]:
            if col in non_buy.columns:
                rejection_counts[col] = non_buy[col].fillna("NA").astype(str).value_counts().to_dict()
    return {"high_signal_gate_counts": gate_counts, "rejection_counts": rejection_counts}


def _gate_visibility_summary(skip_df: pd.DataFrame, rank_df: pd.DataFrame) -> dict:
    if skip_df.empty:
        return {}
    stop_series = skip_df.get("stop_reason", pd.Series(dtype=object)).fillna("").astype(str)
    stop_counts = stop_series.value_counts().to_dict()
    summary = {
        "real_odds_missing": int(stop_series.str.startswith("real_odds_missing").sum()),
        "real_odds_pending_before_deadline": int(stop_series.eq("real_odds_pending_before_deadline").sum()),
        "pending": int(skip_df.get("decision", pd.Series(dtype=object)).astype(str).str.upper().eq("PENDING").sum()),
        "odds_status_counts": skip_df.get("odds_status", pd.Series(dtype=object)).fillna("unknown").astype(str).value_counts().to_dict(),
        "race_gate_counts": skip_df.get("race_gate", pd.Series(dtype=object)).fillna("NA").astype(str).value_counts().to_dict(),
        "buy_eligible": int(skip_df.get("buy_eligible", pd.Series(dtype=bool)).astype(str).str.lower().eq("true").sum()),
        "risk_flag": int(skip_df.get("risk_flag", pd.Series(dtype=bool)).astype(str).str.lower().eq("true").sum()),
        "max_buy_count": int(skip_df.get("stop_reason", pd.Series(dtype=object)).astype(str).eq("max_buy_count").sum()),
        "stop_reason_counts": stop_counts,
        "candidate_top3_stop_reason_counts": {},
        "actual_rank_top5_stop_reason_counts": {},
    }
    if "candidate_rank_by_sort" in skip_df.columns:
        top3 = skip_df[pd.to_numeric(skip_df["candidate_rank_by_sort"], errors="coerce").le(3)].copy()
        if not top3.empty:
            summary["candidate_top3_stop_reason_counts"] = top3.get("stop_reason", pd.Series(dtype=object)).fillna("").astype(str).value_counts().to_dict()
    if not rank_df.empty and "actual_rank" in rank_df.columns:
        top5 = rank_df[rank_df["actual_rank"].between(1, 5, inclusive="both")].copy()
        if not top5.empty:
            summary["actual_rank_top5_stop_reason_counts"] = top5.get("stop_reason", pd.Series(dtype=object)).fillna("").astype(str).value_counts().to_dict()
    return summary


def _results_source_path(target_date: date) -> Path:
    return ROOT / "data" / "raw" / "official" / "results" / f"K{target_date.strftime('%y%m%d')}.TXT"


def _run_monitoring_summary() -> int:
    script_path = ROOT / "scripts" / "monitor_improvement_loop.py"
    if not script_path.exists():
        print(f"[monitor] script not found: {script_path}")
        return 0

    cmd = [
        sys.executable,
        str(script_path),
        "--lookback-days",
        "7",
        "--min-hold-days",
        "2",
    ]
    print(f"[monitor] running: {' '.join(cmd)}")
    result = subprocess.run(cmd, check=False, cwd=str(ROOT))
    print(f"[monitor] exit_code={result.returncode}")
    return int(result.returncode)


def _run_write_daily_metrics(
    target_date: date,
    daily_summary: dict,
    improvement_report: dict,
) -> int:
    script_path = ROOT / "scripts" / "write_daily_metrics.py"
    if not script_path.exists():
        print(f"[daily_metrics] script not found: {script_path}")
        return 0

    gate_visibility = daily_summary.get("gate_visibility_summary", {}) if isinstance(daily_summary, dict) else {}
    odds_status_counts = gate_visibility.get("odds_status_counts", {}) if isinstance(gate_visibility, dict) else {}
    stop_reason_counts = gate_visibility.get("stop_reason_counts", {}) if isinstance(gate_visibility, dict) else {}

    real_odds_available = int(odds_status_counts.get("real_odds_available", 0) or 0)
    pending_unpublished = int(stop_reason_counts.get("real_odds_pending_unpublished", 0) or 0)

    top_candidates = improvement_report.get("top_candidates", []) if isinstance(improvement_report, dict) else []
    top1 = None
    if isinstance(top_candidates, list) and top_candidates:
        first = top_candidates[0]
        if isinstance(first, dict):
            top1 = str(first.get("candidate") or first.get("item") or "").strip() or None

    cmd = [
        sys.executable,
        str(script_path),
        "--date",
        target_date.isoformat(),
        "--real-odds-available",
        str(real_odds_available),
        "--pending-unpublished",
        str(pending_unpublished),
    ]
    if top1:
        cmd.extend(["--improvement-report-top1", top1])

    print(f"[daily_metrics] running: {' '.join(cmd)}")
    result = subprocess.run(cmd, check=False, cwd=str(ROOT))
    print(f"[daily_metrics] exit_code={result.returncode}")
    return int(result.returncode)


def _classify_results_state(
    target_date: date,
    historical_path: Path,
    hist_day: pd.DataFrame,
    truth_day: pd.DataFrame,
    outcomes_day: pd.DataFrame,
) -> dict:
    raw_path = _results_source_path(target_date)
    raw_exists = raw_path.exists()
    raw_size = int(raw_path.stat().st_size) if raw_exists else None
    raw_rows = None
    raw_error = None
    if raw_exists:
        try:
            raw_rows = int(len(BoatRaceParser.parse_results_file(raw_path)))
        except Exception as exc:
            raw_error = str(exc)
    if not raw_exists:
        status = "raw_missing"
        warning = f"results_unavailable_for_date:{target_date.isoformat()}:raw_missing"
    elif raw_rows == 0:
        status = "raw_incomplete"
        warning = f"results_unavailable_for_date:{target_date.isoformat()}:raw_incomplete"
    elif hist_day.empty:
        status = "processed_not_reflected"
        warning = f"results_unavailable_for_date:{target_date.isoformat()}:historical_not_reflected"
    elif truth_day.empty or outcomes_day.empty:
        status = "read_mismatch"
        warning = f"results_unavailable_for_date:{target_date.isoformat()}:read_mismatch"
    else:
        status = "available"
        warning = None
    source = str(raw_path) if raw_exists else "missing"
    return {
        "results_status": status,
        "results_source": source,
        "results_rows": int(len(hist_day)) if status == "available" else 0,
        "results_raw_rows": raw_rows,
        "results_raw_size": raw_size,
        "results_warning": warning,
        "results_raw_parse_error": raw_error,
    }


def _build_improvement_candidates(skip_df: pd.DataFrame, rank_df: pd.DataFrame, max_buy_count: int) -> list[dict]:
    items: list[dict] = []

    high_signal = rank_df[rank_df["actual_rank"].between(1, 5, inclusive="both")] if not rank_df.empty and "actual_rank" in rank_df.columns else pd.DataFrame()
    if not high_signal.empty:
        blocked = high_signal[high_signal["decision"].astype(str).str.upper() != "BUY"] if "decision" in high_signal.columns else pd.DataFrame()
        items.append(
            {
                "rank": 1,
                "candidate": "高signal候補の落選救済",
                "count": int(len(blocked)),
                "reason": "actual_rank<=5 なのに BUY されていない候補が多い",
            }
        )

    payout_block = pd.DataFrame()
    if not skip_df.empty:
        payout_mask = skip_df["reason"].astype(str).str.contains("payout_outlier|オッズ上限|odds cap", case=False, na=False)
        payout_block = skip_df[payout_mask & (pd.to_numeric(skip_df.get("calibrated_hit_prob"), errors="coerce") >= 0.05)]
        items.append(
            {
                "rank": 2,
                "candidate": "payout_outlier / odds cap 境界再点検",
                "count": int(len(payout_block)),
                "reason": "校正後確率が高いのに payout / odds 条件で落ちている候補がある",
            }
        )

        pushed_out = pd.DataFrame()
        if "buy_eligible" in skip_df.columns and "candidate_rank_by_sort" in skip_df.columns:
            pushed_out = skip_df[
                (skip_df["buy_eligible"].astype(bool))
                & (skip_df["decision"].astype(str).str.upper() != "BUY")
                & (pd.to_numeric(skip_df["candidate_rank_by_sort"], errors="coerce") > max_buy_count)
            ]
        items.append(
            {
                "rank": 3,
                "candidate": "max_buy_count 採用順の再点検",
                "count": int(len(pushed_out)),
                "reason": "BUY上限に押し出された候補があるなら採用順の見直し余地がある",
            }
        )

    items = sorted(items, key=lambda x: x["count"], reverse=True)
    for idx, item in enumerate(items, start=1):
        item["rank"] = idx
    return items[:3]


def _write_markdown(report_dir: Path, daily_summary: dict, improvement_report: dict, race_results: pd.DataFrame) -> None:
    decision_counts = daily_summary.get("decision_counts", {})
    lines = [
        f"# Daily Evaluation: {daily_summary['date']}",
        "",
        "## Summary",
        f"- results_status: {daily_summary.get('results_status')}",
        f"- results_source: {daily_summary.get('results_source')}",
        f"- results_rows: {daily_summary.get('results_rows')}",
        f"- results_warning: {daily_summary.get('results_warning')}",
        f"- races: {daily_summary['races']}",
        f"- BUY件数: {daily_summary['buy_count']}",
        f"- hit件数: {daily_summary['hit_count']}",
        f"- hit_rate: {daily_summary['hit_rate']}",
        f"- ROI: {daily_summary['roi']}",
        f"- exact/top5/top10: {daily_summary['exact_rate']} / {daily_summary['top5_rate']} / {daily_summary['top10_rate']}",
        f"- avg_rank: {daily_summary['avg_rank']}",
        f"- max_drawdown: {daily_summary['max_drawdown']}",
        f"- max_losing_streak: {daily_summary['max_losing_streak']}",
        f"- 判定内訳: BUY:{decision_counts.get('BUY', 0)} / PENDING:{decision_counts.get('PENDING', 0)} / SKIP:{decision_counts.get('SKIP', 0)}",
        "",
        "## 判定別一覧",
    ]

    if race_results.empty:
        lines.extend(["- 判定別データなし", ""])
    else:
        for decision in ["BUY", "PENDING", "SKIP"]:
            lines.extend([f"### {decision}", *_format_decision_rows(race_results, decision), ""])

    lines.extend([
        "## 主な落選理由",
    ])
    for row in daily_summary.get("top_reasons", [])[:10]:
        lines.append(f"- {row['reason']}: {row['count']}")
    lines.extend(["", "## 改善候補トップ3"])
    for row in improvement_report.get("top_candidates", []):
        lines.append(f"- {row['rank']}. {row['candidate']} ({row['count']}件): {row['reason']}")
    (report_dir / "daily_evaluation.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the daily post-race evaluation pipeline.")
    parser.add_argument("--date", help="Target race date (YYYY-MM-DD). Defaults to yesterday.")
    parser.add_argument("--delay", type=float, default=1.0)
    args = parser.parse_args()

    target_date = parse_date(args.date, default=date.today() - timedelta(days=1))
    report_dir = report_dir_for(target_date)
    existing_report_dir = existing_report_dir_for(target_date)
    log_path = log_file_for("post_race", target_date)
    append_log(log_path, f"[run:start] pipeline=post_race date={target_date.isoformat()}")
    py_cmd = sys.executable

    steps = []
    run_status = "ok"
    failure_step = None

    step_specs = [
        (
            "fetch_results",
            [
                py_cmd,
                "src/data_fetch/fetch_official.py",
                "--type",
                "results",
                "--date",
                target_date.isoformat(),
                "--delay",
                str(args.delay),
                "--force",
            ],
            True,
        ),
        ("parse_fixed_width", [py_cmd, "src/data/parse_fixed_width.py", "--target-date", target_date.isoformat()], False),
    ]
    total_steps = len(step_specs)
    for index, (label, cmd, allow_failure) in enumerate(step_specs, start=1):
        print(f"[STEP {index}/{total_steps}] {label} started at {iso_now()}")
        step = run_step(label, cmd, allow_failure=allow_failure, log_path=log_path)
        steps.append(step)
        print(
            f"[STEP {index}/{total_steps}] {label} "
            f"{'OK' if step['returncode'] == 0 else step['status'].upper()} "
            f"({step['duration_sec']}s)"
        )
        if step["returncode"] != 0 and not allow_failure:
            run_status = "failed"
            failure_step = label
            break

    predictions_path = _load_pre_race_artifact(
        report_dir,
        "skip_decisions.csv",
        _load_pre_race_artifact(existing_report_dir, "skip_decisions.csv", ROOT / "data/strategy_outputs/skip_decisions.csv"),
    )
    exacta_predictions_path = _load_pre_race_artifact(
        report_dir,
        "skip_decisions_exacta_mode.csv",
        _load_pre_race_artifact(existing_report_dir, "skip_decisions_exacta_mode.csv", ROOT / "data/strategy_outputs/skip_decisions_exacta_mode.csv"),
    )
    candidates_path = _load_pre_race_artifact(
        report_dir,
        "trifecta_candidates.csv",
        _load_pre_race_artifact(existing_report_dir, "trifecta_candidates.csv", ROOT / "data/strategy_outputs/trifecta_candidates.csv"),
    )
    historical_path = ROOT / "data/processed/historical_races.csv"

    daily_summary = {}
    improvement_report = {}
    rolling_summary = {}
    results_status_diagnostic = {}
    run_warnings: list[str] = []
    daily_metrics_exit_code: int | None = None
    monitor_exit_code: int | None = None

    if run_status == "ok":
        pred_df = _load_csv(predictions_path)
        cand_df = _load_csv(candidates_path)
        hist_df = _load_csv(historical_path)

        pred_day = pred_df[pd.to_datetime(pred_df.get("date"), errors="coerce").dt.date == target_date].copy() if not pred_df.empty and "date" in pred_df.columns else pd.DataFrame()
        cand_day = cand_df[pd.to_datetime(cand_df.get("date"), errors="coerce").dt.date == target_date].copy() if not cand_df.empty and "date" in cand_df.columns else pd.DataFrame()
        hist_day = hist_df[pd.to_datetime(hist_df.get("date"), errors="coerce").dt.date == target_date].copy() if not hist_df.empty and "date" in hist_df.columns else pd.DataFrame()

        truth_df = build_truth(historical_path)
        truth_day = truth_df[truth_df["race_id"].astype(str).str.startswith(target_date.strftime("%Y%m%d"))].copy()

        warnings: list[str] = []

        if pred_day.empty:
            run_status, failure_step, warnings = _missing_predictions_result(target_date)
            run_warnings = warnings
        else:
            temp_pred_path = report_dir / "_tmp_pred_day.csv"
            pred_day.to_csv(temp_pred_path, index=False)
            normalized_pred = normalize_predictions(temp_pred_path)
            outcomes = build_race_outcomes(historical_path)
            outcomes_day = outcomes[pd.to_datetime(outcomes.get("date"), errors="coerce").dt.date == target_date].copy()
            results_state = _classify_results_state(target_date, historical_path, hist_day, truth_day, outcomes_day)
            results_available = results_state["results_status"] == "available"
            if results_available:
                race_results, bt_summary = run_backtest(
                    normalized_pred,
                    outcomes_day,
                    stake_mode="auto",
                    flat_stake=100.0,
                    initial_bankroll=100000.0,
                )
                if int(bt_summary.get("buy_count", 0) or 0) == 0:
                    bt_summary["hit_rate"] = 0.0
                    bt_summary["roi"] = 0.0
            else:
                race_results = pd.DataFrame()
                buy_mask = pred_day["decision"].astype(str).str.upper() == "BUY"
                if results_state.get("results_warning"):
                    warnings.append(results_state["results_warning"])
                bt_summary = {
                    "buy_count": 0,
                    "hit_count": 0,
                    "hit_rate": None,
                    "roi": None,
                    "total_stake": 0.0,
                    "total_return": 0.0,
                    "profit": 0.0,
                    "max_drawdown": None,
                    "max_consecutive_loss": 0,
                }

            exact_count = 0
            top5_count = 0
            top10_count = 0
            avg_rank = None
            ranked_race_count = 0
            rank_df = pd.DataFrame()
            if not cand_day.empty and not truth_day.empty:
                cand_truth = attach_truth(cand_day, truth_day)
                rank_df = build_rank_rows(cand_truth, pred_day)
                stats = rank_stats(rank_df)
                exact_count = int(round((stats.get("exact_rate") or 0.0) * stats.get("race_count", 0)))
                top5_count = int(round((stats.get("top5_rate") or 0.0) * stats.get("race_count", 0)))
                top10_count = int(round((stats.get("top10_rate") or 0.0) * stats.get("race_count", 0)))
                avg_rank = stats.get("avg_rank")
                ranked_race_count = int(rank_df["actual_rank"].notna().sum()) if "actual_rank" in rank_df.columns else 0

            top_reasons = summarize_reason_keywords(pred_day[pred_day["decision"].astype(str).str.upper() != "BUY"].copy(), limit=10)
            gate_summary = _gate_drop_summary(pred_day, rank_df[rank_df["actual_rank"].between(1, 5, inclusive="both")].copy() if not rank_df.empty and "actual_rank" in rank_df.columns else pd.DataFrame())
            gate_visibility = _gate_visibility_summary(pred_day, rank_df)
            max_buy_count = 5
            try:
                strategy_cfg = json.loads((ROOT / "config/strategy_config.json").read_text(encoding="utf-8"))
                max_buy_count = int(strategy_cfg.get("buy_conditions", {}).get("max_buy_count", 5))
            except Exception:
                pass
            top_candidates = _build_improvement_candidates(pred_day, rank_df, max_buy_count)

            decision_counts = _decision_counts(pred_day)

            daily_summary = {
                "date": target_date.isoformat(),
                "races": int(len(actual_trifecta_by_race(hist_day))) if results_available else 0,
                "buy_count": int(bt_summary.get("buy_count", 0) or 0),
                "hit_count": int(bt_summary.get("hit_count", 0) or 0),
                "hit_rate": bt_summary.get("hit_rate"),
                "roi": bt_summary.get("roi"),
                "total_stake": round(float(bt_summary.get("total_stake", 0.0) or 0.0), 2),
                "total_return": round(float(bt_summary.get("total_return", 0.0) or 0.0), 2),
                "profit": round(float(bt_summary.get("profit", 0.0) or 0.0), 2),
                "max_drawdown": bt_summary.get("max_drawdown"),
                "max_losing_streak": int(bt_summary.get("max_consecutive_loss", 0) or 0),
                "exact_count": exact_count,
                "exact_rate": round(exact_count / max(int(len(truth_day)), 1), 4) if len(truth_day) else None,
                "top5_count": top5_count,
                "top5_rate": round(top5_count / max(int(len(truth_day)), 1), 4) if len(truth_day) else None,
                "top10_count": top10_count,
                "top10_rate": round(top10_count / max(int(len(truth_day)), 1), 4) if len(truth_day) else None,
                "avg_rank": avg_rank,
                "ranked_race_count": ranked_race_count,
                "main_rejection_reason": top_reasons[0]["reason"] if top_reasons else None,
                "top_reasons": top_reasons,
                "gate_drop_summary": gate_summary,
                "gate_visibility_summary": gate_visibility,
                "pushed_out_candidates": int(
                    (
                        (pred_day.get("buy_eligible", False).astype(bool))
                        & (pred_day["decision"].astype(str).str.upper() != "BUY")
                        & (pd.to_numeric(pred_day.get("candidate_rank_by_sort"), errors="coerce") > max_buy_count)
                    ).sum()
                ) if "buy_eligible" in pred_day.columns and "candidate_rank_by_sort" in pred_day.columns else 0,
                "improvement_candidates_top3": top_candidates,
                "sample_note": _rolling_window_note(int(len(truth_day))),
                "results_available": bool(results_available),
                "decision_counts": decision_counts,
                **results_state,
                "warnings": warnings,
                "planned_buy_count": int(buy_mask.sum()) if not results_available else int(bt_summary.get("buy_count", 0) or 0),
            }

            improvement_report = {
                "date": target_date.isoformat(),
                "top_candidates": top_candidates,
                "hit_but_not_bought": int(
                    len(rank_df[(rank_df["actual_rank"] == 1) & (rank_df["decision"].astype(str).str.upper() != "BUY")])
                ) if not rank_df.empty and "decision" in rank_df.columns else 0,
                "rank_high_but_gate_dropped": int(
                    len(rank_df[(rank_df["actual_rank"].between(1, 5, inclusive="both")) & (rank_df["decision"].astype(str).str.upper() != "BUY")])
                ) if not rank_df.empty and "decision" in rank_df.columns else 0,
                "high_calibrated_prob_but_outlier_drop": int(
                    len(
                        pred_day[
                            pd.to_numeric(pred_day.get("calibrated_hit_prob"), errors="coerce").fillna(0.0).ge(0.05)
                            & pred_day["reason"].astype(str).str.contains("payout_outlier|オッズ上限|odds cap", case=False, na=False)
                        ]
                    )
                ) if not pred_day.empty and "reason" in pred_day.columns else 0,
                "max_buy_count_pushed_out": daily_summary["pushed_out_candidates"],
                "gate_visibility_summary": gate_visibility,
                "warnings": warnings,
                "results_status": results_state["results_status"],
                "results_source": results_state["results_source"],
                "results_rows": results_state["results_rows"],
                "results_warning": results_state["results_warning"],
            }

            exacta_summary = None
            exacta_run_id = f"daily_exacta_{target_date.strftime('%Y%m%d')}_recent30"
            if exacta_predictions_path.exists():
                exacta_step = run_step(
                    "evaluate_exacta_proxy",
                    [
                        py_cmd,
                        "-m",
                        "src.eval.evaluate_exacta_proxy",
                        "--predictions",
                        str(exacta_predictions_path),
                        "--results",
                        str(historical_path),
                        "--run-id",
                        exacta_run_id,
                        "--window",
                        "recent30",
                    ],
                    allow_failure=True,
                    log_path=log_path,
                )
                steps.append(exacta_step)
                exacta_summary_path = ROOT / "reports" / "experiments" / exacta_run_id / "exacta_proxy_summary.json"
                exacta_race_level_path = ROOT / "reports" / "experiments" / exacta_run_id / "exacta_proxy_race_level.csv"
                if exacta_summary_path.exists():
                    exacta_summary = read_json(exacta_summary_path)
                    copy_artifact(exacta_summary_path, report_dir)
                if exacta_race_level_path.exists():
                    copy_artifact(exacta_race_level_path, report_dir)
                if exacta_summary:
                    daily_summary["exacta_recent30"] = exacta_summary.get("exacta", {})
                    improvement_report["exacta_recent30"] = exacta_summary.get("exacta", {})

            buy_zero_diag_dir = report_dir
            buy_zero_diag_df, buy_zero_diag_summary = build_buy_zero_diagnosis_report(
                pred_day,
                target_date=target_date.isoformat(),
                output_dir=buy_zero_diag_dir,
                truth_df=truth_day,
            )
            daily_summary["buy_zero_diagnosis"] = {
                "outputs": buy_zero_diag_summary.get("outputs", {}),
                "buy_count": buy_zero_diag_summary.get("buy_count", 0),
                "skip_count": buy_zero_diag_summary.get("skip_count", 0),
                "reason_counts_top": dict(list(buy_zero_diag_summary.get("reason_counts", {}).items())[:5]),
                "other_count": int(buy_zero_diag_summary.get("reason_counts", {}).get("other", 0) or 0),
            }
            improvement_report["buy_zero_diagnosis"] = {
                "outputs": buy_zero_diag_summary.get("outputs", {}),
                "stage_summary": buy_zero_diag_summary.get("stage_summary", {}),
                "reason_counts": buy_zero_diag_summary.get("reason_counts", {}),
            }

            write_json(report_dir / "daily_evaluation.json", daily_summary)
            write_json(report_dir / "daily_summary.json", daily_summary)
            write_json(report_dir / "daily_gate_summary.json", gate_visibility)
            if not race_results.empty:
                race_results.to_csv(report_dir / "daily_evaluation_race_results.csv", index=False)
            if not rank_df.empty:
                rank_df.to_csv(report_dir / "daily_rank_diagnostics.csv", index=False)
            if not buy_zero_diag_df.empty:
                buy_zero_diag_df.to_csv(report_dir / f"buy_zero_diagnosis_{target_date.isoformat()}.csv", index=False)
            write_json(report_dir / "improvement_report.json", improvement_report)
            _write_markdown(report_dir, daily_summary, improvement_report, race_results)
            rolling_summary = update_rolling_summary()
            results_status_diagnostic = build_results_status_diagnostic()
            copy_artifact(ROOT / "reports/daily/rolling_summary.json", report_dir)
            copy_artifact(ROOT / "reports/daily/results_status_diagnostic.json", report_dir)
            run_warnings = warnings

    if run_status == "ok":
        daily_metrics_exit_code = _run_write_daily_metrics(
            target_date=target_date,
            daily_summary=daily_summary,
            improvement_report=improvement_report,
        )
        monitor_exit_code = _run_monitoring_summary()

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "pipeline": "post_race",
        "date": target_date.isoformat(),
        "status": run_status,
        "failure_step": failure_step,
        "warnings": run_warnings,
        "log_path": str(log_path),
        "steps": steps,
        "inputs": {
            "predictions": str(predictions_path),
            "exacta_predictions": str(exacta_predictions_path),
            "candidates": str(candidates_path),
            "historical": str(historical_path),
        },
        "outputs": {
            "daily_summary": str(report_dir / "daily_evaluation.json"),
            "improvement_report": str(report_dir / "improvement_report.json"),
            "daily_gate_summary": str(report_dir / "daily_gate_summary.json"),
            "rolling_summary": str(report_dir / "rolling_summary.json"),
            "results_status_diagnostic": str(report_dir / "results_status_diagnostic.json"),
        },
        "daily_summary_preview": daily_summary,
        "rolling_summary_preview": rolling_summary,
        "results_status_diagnostic_preview": results_status_diagnostic,
        "monitoring": {
            "daily_metrics_exit_code": daily_metrics_exit_code,
            "monitor_exit_code": monitor_exit_code,
        },
        "failure_handling": {
            "fetch_results": "結果取得失敗でも既存 historical_races.csv があれば継続可能",
            "odds_fetch": "pre-race でオッズ取得に失敗しても暫定値で skip_decisions を生成する",
            "model_safety": "pre-race 側で学習失敗時は backup から旧モデルを復元する",
        },
    }
    write_json(report_dir / "post_race_run.json", report)
    append_log(
        log_path,
        f"[run:end] pipeline=post_race status={run_status} failure_step={failure_step}",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
