from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from src.demo.build_demo_dashboard import build_dashboard_html
from src.demo.demo_selector import (
    build_demo_diff_report,
    build_demo_buy_promotion_diagnostics,
    build_demo_selector_gate_ablation,
    build_demo_selector_diagnostics,
    build_demo_risk_suspicious_odds_cases,
    build_demo_summary,
    select_demo_predictions,
)
from src.features.build_features import FeatureBuilder
from src.models.win_baseline_common import augment_features_for_relative_comparison
from src.models.win_lgbm import predict_feature_set
from src.utils.race_id import canonical_race_id
from src.strategy.evaluate_ev_and_skip import StrategyEvaluator
from src.strategy.generate_trifecta_candidates import TrifectaGenerator


ROOT = Path(__file__).resolve().parents[2]
HISTORICAL_PATH = ROOT / "data" / "processed" / "historical_races.csv"
ODDS_ROOT = ROOT / "data" / "odds"
REPORTS_ROOT = ROOT / "reports" / "demo"
CORE_RELATIVE_MODEL_PATH = ROOT / "models" / "win_relative" / "win_baseline_core_relative.joblib"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a one-day replay demo.")
    parser.add_argument("--date", required=True, help="Target date in YYYY-MM-DD format.")
    parser.add_argument("--model-path", type=Path, default=CORE_RELATIVE_MODEL_PATH)
    return parser.parse_args()


def _parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"invalid --date: {value}") from exc


def _report_dir(target_date: date) -> Path:
    path = REPORTS_ROOT / target_date.isoformat()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _load_demo_day_source(target_date: date) -> pd.DataFrame:
    if not HISTORICAL_PATH.exists():
        raise FileNotFoundError(f"historical data not found: {HISTORICAL_PATH}")
    hist = pd.read_csv(HISTORICAL_PATH, low_memory=False)
    day_df = hist[hist["date"].astype(str) == target_date.isoformat()].copy()
    if day_df.empty:
        raise ValueError(f"指定日付の既存データがありません: {target_date.isoformat()}")
    if "union_key" not in day_df.columns:
        day_df["union_key"] = (
            day_df["date"].astype(str).str.replace("-", "", regex=False)
            + "_"
            + pd.to_numeric(day_df["jcd"], errors="coerce").fillna(0).astype(int).astype(str).str.zfill(2)
            + "_"
            + pd.to_numeric(day_df["race_no"], errors="coerce").fillna(0).astype(int).astype(str).str.zfill(2)
        )
    day_df["race_id"] = day_df.apply(
        lambda row: canonical_race_id(row["date"], row["jcd"], row["race_no"]),
        axis=1,
    )
    race_counts = day_df.groupby("union_key")["lane"].transform("count")
    day_df = day_df[race_counts == 6].copy()
    if day_df.empty:
        raise ValueError(f"6艇そろったレースがありません: {target_date.isoformat()}")
    return day_df


def _ensure_model_artifact(model_path: Path) -> Path:
    if model_path.exists():
        return model_path
    raise FileNotFoundError(f"demo model artifact not found: {model_path}")


def _find_odds_path(target_date: date) -> Path | None:
    dated_path = ODDS_ROOT / target_date.strftime("%Y%m%d") / "trifecta_odds.csv"
    if dated_path.exists():
        return dated_path
    fallback = ODDS_ROOT / "today_trifecta_odds.csv"
    if fallback.exists():
        return fallback
    return None


def _build_stale_warning(target_date: date, generated_at: datetime) -> str:
    age_days = (generated_at.date() - target_date).days
    if age_days >= 7:
        return f"このデモは {age_days} 日前の既存データを使っています。最新運用判断には使わないでください。"
    if age_days >= 1:
        return f"このデモは過去日のリプレイです。対象日は {target_date.isoformat()} です。"
    return ""


def _coerce_series_or_default(frame: pd.DataFrame, column_name: str, default: float = 0.0) -> pd.Series:
    if column_name in frame.columns:
        return pd.to_numeric(frame[column_name], errors="coerce")
    return pd.Series(default, index=frame.index, dtype="float64")


def _standardize_demo_feature_frame(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    if "boat_no" not in work.columns and "lane" in work.columns:
        work["boat_no"] = pd.to_numeric(work["lane"], errors="coerce")
    if "race_number" not in work.columns and "race_no" in work.columns:
        work["race_number"] = pd.to_numeric(work["race_no"], errors="coerce")
    if "boat_no" in work.columns:
        work["boat_no"] = pd.to_numeric(work["boat_no"], errors="coerce")
    if "race_number" in work.columns:
        work["race_number"] = pd.to_numeric(work["race_number"], errors="coerce")
    return work


def run_demo_day(target_date: date, *, model_path: Path) -> dict[str, Path]:
    report_dir = _report_dir(target_date)
    raw_day_path = report_dir / "_demo_day_source.csv"
    feature_path = report_dir / "_demo_features.csv"
    relative_feature_path = report_dir / "_demo_features_core_relative.csv"
    prediction_path = report_dir / "_demo_win_proba.csv"
    candidates_path = report_dir / "_demo_trifecta_candidates.csv"
    ev_path = report_dir / "_demo_ev_analysis.csv"
    summary_path = report_dir / "demo_summary.json"
    predictions_csv_path = report_dir / "demo_predictions.csv"
    dashboard_path = report_dir / "dashboard.html"
    diff_path = report_dir / "demo_diff_vs_previous.json"
    diagnostics_path = report_dir / "demo_selector_diagnostics.json"
    sensitivity_path = report_dir / "demo_selector_sensitivity.json"
    counterfactual_path = report_dir / "demo_selector_counterfactual.json"
    suspicious_cases_path = report_dir / "demo_risk_suspicious_odds_cases.csv"
    suspicious_summary_path = report_dir / "demo_risk_suspicious_odds_summary.json"
    buy_promotion_path = report_dir / "demo_buy_promotion_diagnostics.json"
    watch_cases_path = report_dir / "demo_watch_cases.csv"
    gate_ablation_path = report_dir / "demo_selector_gate_ablation.json"
    gate_ablation_csv_path = report_dir / "demo_selector_gate_ablation.csv"

    previous_summary: dict | None = None
    previous_predictions: pd.DataFrame | None = None
    if summary_path.exists():
        try:
            previous_summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            previous_summary = None
    if predictions_csv_path.exists():
        try:
            previous_predictions = pd.read_csv(predictions_csv_path, low_memory=False)
        except Exception:
            previous_predictions = None

    day_df = _load_demo_day_source(target_date)
    day_df.to_csv(raw_day_path, index=False, encoding="utf-8")

    FeatureBuilder().build(str(raw_day_path), str(feature_path), "today")
    feature_df = _standardize_demo_feature_frame(pd.read_csv(feature_path, low_memory=False))
    relative_feature_df = augment_features_for_relative_comparison(feature_df)
    relative_feature_df = _standardize_demo_feature_frame(relative_feature_df)
    relative_feature_df.to_csv(relative_feature_path, index=False, encoding="utf-8")

    artifact_path = _ensure_model_artifact(model_path)
    pred_df = predict_feature_set(artifact_path=artifact_path, feature_path=relative_feature_path)
    feature_df = pd.read_csv(relative_feature_path, low_memory=False)
    rich_pred_df = pred_df.merge(feature_df, on=["race_id", "lane", "date"], how="left")
    rich_pred_df.to_csv(prediction_path, index=False, encoding="utf-8")

    candidate_df = TrifectaGenerator().generate(str(prediction_path))
    if candidate_df.empty:
        raise RuntimeError("三連単候補が生成できませんでした。")
    candidate_df.to_csv(candidates_path, index=False, encoding="utf-8")

    evaluator = StrategyEvaluator()
    odds_path = _find_odds_path(target_date)
    ev_df = evaluator.build_ev_analysis(
        str(candidates_path),
        odds_path=str(odds_path) if odds_path is not None else None,
    )
    ev_df.to_csv(ev_path, index=False, encoding="utf-8")

    generated_at = datetime.now()
    stale_warning = _build_stale_warning(target_date, generated_at)
    ev_df = ev_df.copy()
    if "normalized_win_probability" in ev_df.columns:
        ev_df["normalized_win_probability"] = pd.to_numeric(ev_df["normalized_win_probability"], errors="coerce")
    elif "first_win_proba" in ev_df.columns:
        ev_df["normalized_win_probability"] = pd.to_numeric(ev_df["first_win_proba"], errors="coerce")
    elif "p_win_norm" in ev_df.columns:
        ev_df["normalized_win_probability"] = pd.to_numeric(ev_df["p_win_norm"], errors="coerce")
    else:
        ev_df["normalized_win_probability"] = pd.Series(0.0, index=ev_df.index, dtype="float64")
    ev_df["normalized_win_probability"] = ev_df["normalized_win_probability"].fillna(0.0)
    ev_df["model_name"] = "win_baseline_core_relative"
    ev_df["feature_set_name"] = "win_baseline_core_relative"
    ev_df["decision_source"] = "core_relative"
    localized_df = select_demo_predictions(ev_df, target_date=target_date)
    localized_df.to_csv(predictions_csv_path, index=False, encoding="utf-8")

    diagnostics = build_demo_selector_diagnostics(ev_df, target_date=target_date)
    diagnostics_path.write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")
    counterfactual_payload = diagnostics.get("counterfactual", {})
    counterfactual_path.write_text(json.dumps(counterfactual_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    suspicious_cases_df, suspicious_summary = build_demo_risk_suspicious_odds_cases(ev_df, target_date=target_date)
    suspicious_cases_df.to_csv(suspicious_cases_path, index=False, encoding="utf-8")
    suspicious_summary_path.write_text(json.dumps(suspicious_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    watch_cases_df, buy_promotion_summary = build_demo_buy_promotion_diagnostics(ev_df, target_date=target_date)
    watch_cases_df.to_csv(watch_cases_path, index=False, encoding="utf-8")
    buy_promotion_path.write_text(json.dumps(buy_promotion_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    gate_ablation_df, gate_ablation_summary = build_demo_selector_gate_ablation(ev_df, target_date=target_date)
    gate_ablation_df.to_csv(gate_ablation_csv_path, index=False, encoding="utf-8")
    gate_ablation_path.write_text(json.dumps(gate_ablation_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    sensitivity_payload = {
        "target_date": target_date.isoformat(),
        "model_name": diagnostics.get("model_name", ""),
        "feature_set_name": diagnostics.get("feature_set_name", ""),
        "decision_source": diagnostics.get("decision_source", ""),
        "thresholds_tested": diagnostics.get("sensitivity", {}).get("thresholds_tested", []),
        "results_all": diagnostics.get("sensitivity", {}).get("results_all", []),
        "results_with_odds_only": diagnostics.get("sensitivity", {}).get("results_with_odds_only", []),
        "buy_count_by_threshold": diagnostics.get("sensitivity", {}).get("buy_count_by_threshold", {}),
        "watch_count_by_threshold": diagnostics.get("sensitivity", {}).get("watch_count_by_threshold", {}),
        "skip_count_by_threshold": diagnostics.get("sensitivity", {}).get("skip_count_by_threshold", {}),
        "nearest_to_buy_examples_by_threshold": diagnostics.get("sensitivity", {}).get("nearest_to_buy_examples_by_threshold", {}),
        "notes": [
            "min_win_proba のみを変更し、min_ev / max_ev / risk_flag は固定しています。",
            "results_all は missing_odds を含む全候補、results_with_odds_only は実オッズありのみです。",
        ],
    }
    sensitivity_path.write_text(json.dumps(sensitivity_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = build_demo_summary(
        localized_df,
        target_date=target_date,
        generated_at=generated_at.isoformat(timespec="seconds"),
        stale_warning=stale_warning,
        model_name="win_baseline_core_relative",
        feature_set_name="win_baseline_core_relative",
        decision_source="core_relative",
    )
    summary["source_paths"] = {
        "historical": str(HISTORICAL_PATH),
        "odds": str(odds_path) if odds_path is not None else "",
        "model": str(artifact_path),
        "feature_path": str(relative_feature_path),
    }
    summary["artifact_paths"] = {
        "summary_json": str(summary_path),
        "predictions_csv": str(predictions_csv_path),
        "dashboard_html": str(dashboard_path),
        "diff_json": str(diff_path),
        "selector_diagnostics_json": str(diagnostics_path),
        "selector_sensitivity_json": str(sensitivity_path),
        "selector_counterfactual_json": str(counterfactual_path),
        "risk_suspicious_odds_cases_csv": str(suspicious_cases_path),
        "risk_suspicious_odds_summary_json": str(suspicious_summary_path),
        "buy_promotion_diagnostics_json": str(buy_promotion_path),
        "watch_cases_csv": str(watch_cases_path),
        "gate_ablation_json": str(gate_ablation_path),
        "gate_ablation_csv": str(gate_ablation_csv_path),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    diff_report = build_demo_diff_report(
        previous_summary=previous_summary,
        previous_predictions=previous_predictions,
        current_summary=summary,
        current_predictions=localized_df,
    )
    diff_path.write_text(json.dumps(diff_report, ensure_ascii=False, indent=2), encoding="utf-8")

    html = build_dashboard_html(summary, localized_df)
    dashboard_path.write_text(html, encoding="utf-8")

    return {
        "summary_json": summary_path,
        "predictions_csv": predictions_csv_path,
        "dashboard_html": dashboard_path,
        "diff_json": diff_path,
        "selector_diagnostics_json": diagnostics_path,
        "selector_sensitivity_json": sensitivity_path,
        "selector_counterfactual_json": counterfactual_path,
        "risk_suspicious_odds_cases_csv": suspicious_cases_path,
        "risk_suspicious_odds_summary_json": suspicious_summary_path,
        "buy_promotion_diagnostics_json": buy_promotion_path,
        "watch_cases_csv": watch_cases_path,
        "gate_ablation_json": gate_ablation_path,
        "gate_ablation_csv": gate_ablation_csv_path,
    }


def main() -> None:
    args = parse_args()
    target_date = _parse_date(args.date)
    outputs = run_demo_day(target_date, model_path=args.model_path)
    print(json.dumps({key: str(value) for key, value in outputs.items()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[demo:error] {exc}", file=sys.stderr)
        raise
