"""日次運用(朝予想・夜答え合わせ)の共通処理。"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd
import yaml

from src.data_processing.database import BoatRaceDatabase
from src.data_processing.preprocessor import Preprocessor
from src.data_processing.real_data_fetcher import (
    PLACE_NAMES, RealDataFetcher, resolve_place,
)
from src.features.feature_engineer import FeatureEngineer
from src.models.prediction_system import PredictionSystem

PREDICTION_COLUMNS = [
    "race_id", "race_date", "course_id", "place_name", "race_number",
    "lane", "racer_id", "name", "grade", "win_odds",
    "pred_proba", "pred_rank", "expected_value",
    "prediction_status", "unavailable_reason",
]

RESULT_COLUMNS = [
    "race_id", "race_date", "course_id", "race_number", "lane",
    "finish_position", "win", "start_time",
    "result_status", "unavailable_reason",
]

SETTLEMENT_COLUMNS = PREDICTION_COLUMNS + [
    "finish_position", "win", "result_status", "result_unavailable_reason",
    "top1_bet", "top2_bet", "value_filter_bet",
    "top1_return", "top2_return", "value_filter_return",
]


def load_config(base_dir: Path) -> dict:
    with open(base_dir / "config" / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def daily_output_dir(base_dir: Path, date: str) -> Path:
    return base_dir / "output" / "daily" / date


def _now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _jsonable(value):
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def _records(df: pd.DataFrame) -> list[dict]:
    return [
        {key: _jsonable(value) for key, value in row.items()}
        for row in df.to_dict(orient="records")
    ]


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_csv(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def make_fetcher(base_dir: Path, config: dict) -> RealDataFetcher:
    f = config["fetcher"]
    return RealDataFetcher(
        base_url=f["base_url"],
        cache_dir=str(base_dir / config["data"]["raw_cache_dir"]),
        min_interval_seconds=f["min_interval_seconds"],
        max_retries=f["max_retries"],
        backoff_base_seconds=f["backoff_base_seconds"],
        user_agent=f["user_agent"],
    )


def resolve_course_ids(places: Iterable[str | int] | None) -> list[int] | None:
    if places is None:
        return None
    course_ids = [resolve_place(place) for place in places]
    return sorted(dict.fromkeys(course_ids))


def build_features_with_history(today_df: pd.DataFrame, history_df: pd.DataFrame,
                                config: dict) -> pd.DataFrame:
    """履歴と当日出走表を連結し、当日行だけの特徴量を返す。"""
    pre = Preprocessor()
    combined = pd.concat([history_df, today_df], ignore_index=True)
    combined = pre.fit_transform(combined)
    fe = FeatureEngineer(rolling_windows=config["features"]["rolling_windows"])
    combined = fe.create_features(combined)
    today_ids = set(today_df["race_id"])
    return combined[combined["race_id"].isin(today_ids)].copy()


def _empty_predictions() -> pd.DataFrame:
    return pd.DataFrame(columns=PREDICTION_COLUMNS)


def _empty_results() -> pd.DataFrame:
    return pd.DataFrame(columns=RESULT_COLUMNS)


def _empty_settlement() -> pd.DataFrame:
    return pd.DataFrame(columns=SETTLEMENT_COLUMNS)


def _quality_counts(course_ids: list[int], entries: pd.DataFrame,
                    errors: list[dict]) -> dict:
    expected_races = len(course_ids) * 12
    observed_races = int(entries["race_id"].nunique()) if len(entries) else 0
    complete_races = int(
        (entries.groupby("race_id")["lane"].nunique() == 6).sum()
    ) if len(entries) else 0
    errors_by_reason = Counter(e.get("reason", "unknown") for e in errors)
    return {
        "courses_requested": course_ids,
        "courses_observed": sorted(entries["course_id"].dropna().astype(int).unique().tolist())
        if len(entries) else [],
        "races_observed": observed_races,
        "expected_races": expected_races,
        "complete_races": complete_races,
        "missing_entry_rows": max(0, expected_races * 6 - len(entries)),
        "missing_name_rows": int(entries["name"].isna().sum()) if len(entries) else 0,
        "missing_grade_rows": int(entries["grade"].isna().sum()) if len(entries) else 0,
        "missing_win_odds_rows": int(entries["win_odds"].isna().sum()) if len(entries) else 0,
        "errors_by_reason": dict(sorted(errors_by_reason.items())),
        "errors": errors,
    }


def run_daily_prediction(date: str, base_dir: Path, places: Iterable[str | int] | None = None,
                         overwrite: bool = False, model_path: str | None = None,
                         fetcher: RealDataFetcher | None = None) -> dict:
    """朝予想を生成し、output/daily/YYYY-MM-DD に保存する。"""
    config = load_config(base_dir)
    out_dir = daily_output_dir(base_dir, date)
    pred_csv = out_dir / "predictions.csv"
    pred_json = out_dir / "predictions.json"
    run_json = out_dir / "morning_run.json"
    coverage_json = out_dir / "coverage.json"

    if pred_csv.exists() and not overwrite:
        existing = pd.read_csv(pred_csv, dtype={"race_id": str})
        if not pred_json.exists():
            write_json(pred_json, _records(existing))
        payload = {
            "date": date,
            "status": "exists",
            "generated_at": _now_iso(),
            "predictions_csv": str(pred_csv),
            "message": "既存 predictions.csv を使用しました。--overwrite 指定時のみ再生成します。",
            "prediction_rows": int(len(existing)),
            "prediction_races": int(existing["race_id"].nunique()) if len(existing) else 0,
        }
        write_json(run_json, payload)
        return payload

    fetcher = fetcher or make_fetcher(base_dir, config)
    course_ids = resolve_course_ids(places)
    if course_ids is None:
        course_ids = fetcher.discover_course_ids(date)

    errors: list[dict] = []
    if not course_ids:
        pred = _empty_predictions()
        write_csv(pred_csv, pred)
        write_json(pred_json, [])
        coverage = _quality_counts([], pred, [{
            "reason": "no_race_day",
            "message": "開催場を検出できませんでした。",
        }])
        coverage["date"] = date
        coverage["status"] = "no_courses"
        write_json(coverage_json, coverage)
        payload = {"date": date, "status": "no_courses",
                   "generated_at": _now_iso(), "prediction_rows": 0}
        write_json(run_json, payload)
        return payload

    model_file = Path(model_path) if model_path else base_dir / config["output"]["model_path"]
    if not model_file.exists():
        coverage = _quality_counts(course_ids, pd.DataFrame(), [{
            "reason": "model_missing",
            "message": f"モデルが見つかりません: {model_file}",
        }])
        coverage["date"] = date
        coverage["status"] = "error"
        write_json(coverage_json, coverage)
        payload = {"date": date, "status": "error", "generated_at": _now_iso(),
                   "message": f"モデルが見つかりません: {model_file}"}
        write_json(run_json, payload)
        raise FileNotFoundError(payload["message"])

    frames: list[pd.DataFrame] = []
    for jcd in course_ids:
        try:
            day = fetcher.fetch_day(date, jcd, include_results=False)
            if day.empty:
                errors.append({
                    "course_id": jcd,
                    "place_name": PLACE_NAMES.get(jcd, str(jcd)),
                    "reason": "no_race_day",
                    "message": "出走表を取得できませんでした。",
                })
            else:
                frames.append(day)
        except Exception as e:  # noqa: BLE001 - 日次運用では場単位で継続する
            errors.append({
                "course_id": jcd,
                "place_name": PLACE_NAMES.get(jcd, str(jcd)),
                "reason": "parse_error",
                "message": str(e),
            })

    entries = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    coverage = _quality_counts(course_ids, entries, errors)
    coverage["date"] = date

    if entries.empty:
        pred = _empty_predictions()
        write_csv(pred_csv, pred)
        write_json(pred_json, [])
        coverage["status"] = "no_predictions"
        write_json(coverage_json, coverage)
        payload = {"date": date, "status": "no_predictions",
                   "generated_at": _now_iso(), "prediction_rows": 0}
        write_json(run_json, payload)
        return payload

    db = BoatRaceDatabase(str(base_dir / config["data"]["database_path"]))
    history = db.load_dataframe(end_date=date, with_results_only=True)
    history = history[history["race_date"] < date] if len(history) else history
    features = build_features_with_history(entries, history, config)
    system = PredictionSystem.load(str(model_file))
    pred = system.predict_races(features)
    pred["expected_value"] = pred["pred_proba"] * pred["win_odds"]
    pred["place_name"] = pred["course_id"].map(PLACE_NAMES)
    pred["prediction_status"] = "predicted"
    pred["unavailable_reason"] = ""
    pred_out = pred[PREDICTION_COLUMNS].sort_values(
        ["course_id", "race_number", "lane"]
    ).reset_index(drop=True)

    write_csv(pred_csv, pred_out)
    write_json(pred_json, _records(pred_out))
    coverage["status"] = "complete" if (
        coverage["complete_races"] == coverage["expected_races"]
        and coverage["missing_name_rows"] == 0
        and coverage["missing_grade_rows"] == 0
        and coverage["missing_win_odds_rows"] == 0
    ) else "partial"
    coverage["prediction_rows"] = int(len(pred_out))
    coverage["prediction_races"] = int(pred_out["race_id"].nunique()) if len(pred_out) else 0
    write_json(coverage_json, coverage)
    payload = {
        "date": date,
        "status": coverage["status"],
        "generated_at": _now_iso(),
        "prediction_rows": int(len(pred_out)),
        "prediction_races": int(pred_out["race_id"].nunique()) if len(pred_out) else 0,
        "predictions_csv": str(pred_csv),
    }
    write_json(run_json, payload)
    return payload


def load_daily_predictions(base_dir: Path, date: str) -> pd.DataFrame:
    path = daily_output_dir(base_dir, date) / "predictions.csv"
    if not path.exists():
        raise FileNotFoundError(f"予想ファイルが見つかりません: {path}")
    return pd.read_csv(path, dtype={"race_id": str})


def settle_predictions(predictions: pd.DataFrame, results: pd.DataFrame,
                       unit_stake: int, ev_threshold: float) -> pd.DataFrame:
    if predictions.empty:
        return _empty_settlement()
    if results.empty:
        merged = predictions.copy()
        merged["finish_position"] = None
        merged["win"] = None
        merged["result_status"] = "missing"
        merged["result_unavailable_reason"] = "missing"
    else:
        merged = predictions.merge(
            results[["race_id", "lane", "finish_position", "win",
                     "result_status", "unavailable_reason"]],
            on=["race_id", "lane"],
            how="left",
        )
        merged = merged.rename(columns={"unavailable_reason_y": "result_unavailable_reason"})
        if "unavailable_reason_x" in merged.columns:
            merged = merged.rename(columns={"unavailable_reason_x": "unavailable_reason"})
        merged["result_unavailable_reason"] = merged["result_unavailable_reason"].fillna("missing")
        merged["result_status"] = merged["result_status"].fillna("missing")

    merged["top1_bet"] = merged["pred_rank"] == 1
    merged["top2_bet"] = merged["pred_rank"] <= 2
    merged["value_filter_bet"] = (merged["pred_rank"] == 1) & (
        pd.to_numeric(merged["expected_value"], errors="coerce") >= ev_threshold
    )
    is_win = pd.to_numeric(merged["win"], errors="coerce") == 1
    odds = pd.to_numeric(merged["win_odds"], errors="coerce").fillna(0.0)
    merged["top1_return"] = (merged["top1_bet"] & is_win) * odds * unit_stake
    merged["top2_return"] = (merged["top2_bet"] & is_win) * odds * unit_stake
    merged["value_filter_return"] = (
        (merged["value_filter_bet"] & is_win) * odds * unit_stake
    )
    return merged[SETTLEMENT_COLUMNS].sort_values(
        ["course_id", "race_number", "lane"]
    ).reset_index(drop=True)


def _strategy_metrics(settlement: pd.DataFrame, bet_col: str,
                      return_col: str, unit_stake: int) -> dict:
    final = settlement[settlement["result_status"] == "final"].copy()
    n_races = int(final["race_id"].nunique()) if len(final) else 0
    bets = final[final[bet_col]].copy()
    n_bets = int(len(bets))
    hits = bets[pd.to_numeric(bets["win"], errors="coerce") == 1]
    total_stake = n_bets * unit_stake
    total_return = float(bets[return_col].sum()) if n_bets else 0.0
    return {
        "n_races": n_races,
        "n_bets": n_bets,
        "n_hits": int(len(hits)),
        "hit_rate": float(len(hits) / n_bets) if n_bets else 0.0,
        "race_hit_rate": float(hits["race_id"].nunique() / n_races) if n_races else 0.0,
        "total_stake": int(total_stake),
        "total_return": total_return,
        "roi": float(total_return / total_stake) if total_stake else 0.0,
    }


def evaluate_settlement(settlement: pd.DataFrame, unit_stake: int) -> dict:
    if settlement.empty:
        return {
            "top1_win": _strategy_metrics(settlement, "top1_bet", "top1_return", unit_stake),
            "top2_win": _strategy_metrics(settlement, "top2_bet", "top2_return", unit_stake),
            "value_filter": _strategy_metrics(
                settlement, "value_filter_bet", "value_filter_return", unit_stake),
        }
    return {
        "top1_win": _strategy_metrics(settlement, "top1_bet", "top1_return", unit_stake),
        "top2_win": _strategy_metrics(settlement, "top2_bet", "top2_return", unit_stake),
        "value_filter": _strategy_metrics(
            settlement, "value_filter_bet", "value_filter_return", unit_stake),
    }


def build_daily_report(date: str, predictions: pd.DataFrame, results: pd.DataFrame,
                       settlement: pd.DataFrame, result_errors: list[dict],
                       unit_stake: int) -> dict:
    race_results = results.drop_duplicates("race_id") if len(results) else pd.DataFrame()
    unavailable = race_results[race_results["result_status"] != "final"] \
        if len(race_results) else pd.DataFrame()
    metrics = evaluate_settlement(settlement, unit_stake)
    errors_by_reason = Counter()
    if len(unavailable):
        errors_by_reason.update(unavailable["unavailable_reason"].fillna("missing").tolist())
    errors_by_reason.update(e.get("reason", "unknown") for e in result_errors)

    by_course = []
    for course_id, group in predictions.groupby("course_id") if len(predictions) else []:
        course_settlement = settlement[settlement["course_id"] == course_id] if len(settlement) else settlement
        course_results = results[results["course_id"] == course_id] if len(results) else results
        c_metrics = evaluate_settlement(course_settlement, unit_stake)
        by_course.append({
            "course_id": int(course_id),
            "place_name": PLACE_NAMES.get(int(course_id), str(course_id)),
            "predicted_races": int(group["race_id"].nunique()),
            "settled_races": int(course_results[course_results["result_status"] == "final"]["race_id"].nunique())
            if len(course_results) else 0,
            "unavailable_races": int(course_results[course_results["result_status"] != "final"]["race_id"].nunique())
            if len(course_results) else 0,
            "top1_hit_rate": c_metrics["top1_win"]["hit_rate"],
            "top1_roi": c_metrics["top1_win"]["roi"],
        })

    final_races = int(race_results[race_results["result_status"] == "final"]["race_id"].nunique()) \
        if len(race_results) else 0
    return {
        "date": date,
        "generated_at": _now_iso(),
        "status": "settled" if final_races else "no_final_results",
        "sample_notice": "検証中: 決済済みレース数が少ないためROIだけで良否判定しません。"
        if final_races < 30 else "",
        "predicted_races": int(predictions["race_id"].nunique()) if len(predictions) else 0,
        "prediction_rows": int(len(predictions)),
        "settled_races": final_races,
        "unavailable_races_count": int(len(unavailable)) if len(unavailable) else 0,
        "unavailable_races": _records(unavailable[[
            "race_id", "course_id", "race_number", "result_status", "unavailable_reason"
        ]]) if len(unavailable) else [],
        "errors_by_reason": dict(sorted(errors_by_reason.items())),
        "errors": result_errors,
        "metrics": metrics,
        "by_course": by_course,
        "value_filter_note": "value_filter は検証用のshadow評価です。本番推奨買い目ではありません。",
    }


def write_daily_report_md(path: Path, report: dict) -> None:
    metrics = report["metrics"]
    lines = [
        f"# 競艇AI 日次レポート {report['date']}",
        "",
        f"- 状態: {report['status']}",
        f"- 予想レース数: {report['predicted_races']}",
        f"- 決済済みレース数: {report['settled_races']}",
        f"- 未取得レース数: {report['unavailable_races_count']}",
    ]
    if report["sample_notice"]:
        lines.append(f"- 注意: {report['sample_notice']}")
    lines.extend([
        "",
        "## Shadow評価",
        "| 戦略 | 買い目数 | 的中数 | 的中率 | ROI |",
        "|---|---:|---:|---:|---:|",
    ])
    for key in ("top1_win", "top2_win", "value_filter"):
        m = metrics[key]
        lines.append(
            f"| {key} | {m['n_bets']} | {m['n_hits']} | "
            f"{m['hit_rate']:.1%} | {m['roi']:.1%} |"
        )
    lines.extend([
        "",
        f"> {report['value_filter_note']}",
        "",
        "## 開催場別",
        "| 場 | 予想R | 決済R | 未取得R | Top1的中率 | Top1 ROI |",
        "|---|---:|---:|---:|---:|---:|",
    ])
    for c in report["by_course"]:
        lines.append(
            f"| {c['place_name']} | {c['predicted_races']} | {c['settled_races']} | "
            f"{c['unavailable_races']} | {c['top1_hit_rate']:.1%} | {c['top1_roi']:.1%} |"
        )
    lines.extend(["", "## 未取得理由"])
    if report["errors_by_reason"]:
        for reason, count in report["errors_by_reason"].items():
            lines.append(f"- {reason}: {count}")
    else:
        lines.append("- なし")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def update_rolling_summary(base_dir: Path, report: dict) -> None:
    path = base_dir / "output" / "daily" / "rolling_summary.csv"
    top1 = report["metrics"]["top1_win"]
    top2 = report["metrics"]["top2_win"]
    value = report["metrics"]["value_filter"]
    row = {
        "date": report["date"],
        "status": report["status"],
        "predicted_races": report["predicted_races"],
        "settled_races": report["settled_races"],
        "unavailable_races_count": report["unavailable_races_count"],
        "top1_hit_rate": top1["hit_rate"],
        "top1_roi": top1["roi"],
        "top2_hit_rate": top2["hit_rate"],
        "top2_roi": top2["roi"],
        "value_filter_bets": value["n_bets"],
        "value_filter_roi": value["roi"],
        "generated_at": report["generated_at"],
    }
    if path.exists():
        existing = pd.read_csv(path, dtype={"date": str})
        existing = existing[existing["date"] != report["date"]]
        out = pd.concat([existing, pd.DataFrame([row])], ignore_index=True)
    else:
        out = pd.DataFrame([row])
    out = out.sort_values("date").reset_index(drop=True)
    write_csv(path, out)


def run_daily_settlement(date: str, base_dir: Path,
                         fetcher: RealDataFetcher | None = None) -> dict:
    """保存済み予想を壊さず、結果取得と答え合わせだけを行う。"""
    config = load_config(base_dir)
    out_dir = daily_output_dir(base_dir, date)
    predictions = load_daily_predictions(base_dir, date)
    fetcher = fetcher or make_fetcher(base_dir, config)
    results, result_errors = fetcher.fetch_results_for_predictions(date, predictions)
    if results.empty:
        results = _empty_results()
    write_csv(out_dir / "results.csv", results)

    bt_cfg = config["backtest"]
    settlement = settle_predictions(
        predictions,
        results,
        unit_stake=bt_cfg["unit_stake"],
        ev_threshold=bt_cfg["ev_threshold"],
    )
    write_csv(out_dir / "settlement.csv", settlement)
    report = build_daily_report(
        date,
        predictions,
        results,
        settlement,
        result_errors,
        unit_stake=bt_cfg["unit_stake"],
    )
    write_json(out_dir / "daily_report.json", report)
    write_daily_report_md(out_dir / "daily_report.md", report)
    update_rolling_summary(base_dir, report)
    return report
