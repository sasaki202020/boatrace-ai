from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.pipeline.boatrace_official_pipeline import JCD_TO_VENUE
from src.strategy.evaluate_ev_and_skip import StrategyEvaluator

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PREDICTIONS = ROOT / "data" / "strategy_outputs" / "skip_decisions.csv"
DEFAULT_TRUTH = ROOT / "data" / "processed" / "historical_races.csv"
DEFAULT_OUTPUT_ROOT = ROOT / "reports" / "daily"

REASON_ORDER = [
    "buy",
    "odds_unavailable",
    "compare_impossible",
    "ev_below_threshold",
    "probability_below_threshold",
    "calibration_suppressed",
    "hard_guard_reject",
    "feature_missing",
    "invalid_race_input",
    "no_candidate_generated",
    "other",
]


def _safe_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def _safe_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    text = _safe_text(value).lower()
    return text in {"true", "1", "yes", "y"}


def _safe_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        if isinstance(value, float) and pd.isna(value):
            return None
        num = pd.to_numeric(value, errors="coerce")
        if pd.isna(num):
            return None
        return float(num)
    except Exception:
        return None


def _safe_int(value: object) -> int | None:
    try:
        num = _safe_float(value)
        if num is None:
            return None
        return int(num)
    except Exception:
        return None


def _markdown_table(df: pd.DataFrame, columns: list[str] | None = None) -> str:
    if df.empty:
        return "- なし"
    work = df.copy()
    if columns is not None:
        work = work[columns]
    work = work.fillna("")
    headers = list(work.columns)
    rows = work.astype(str).values.tolist()
    widths = [len(str(h)) for h in headers]
    for row in rows:
        for idx, value in enumerate(row):
            widths[idx] = max(widths[idx], len(str(value)))
    header_line = "| " + " | ".join(str(h).ljust(widths[idx]) for idx, h in enumerate(headers)) + " |"
    sep_line = "| " + " | ".join("-" * widths[idx] for idx in range(len(headers))) + " |"
    body_lines = []
    for row in rows:
        body_lines.append("| " + " | ".join(str(value).ljust(widths[idx]) for idx, value in enumerate(row)) + " |")
    return "\n".join([header_line, sep_line, *body_lines])


def _race_jcd_from_race_id(race_id: object) -> str:
    text = _safe_text(race_id)
    parts = text.split("-")
    if len(parts) >= 3 and parts[1]:
        return parts[1].zfill(2)
    return ""


def _venue_from_race_id(race_id: object) -> str:
    return JCD_TO_VENUE.get(_race_jcd_from_race_id(race_id), "")


def _venue_from_jcd(jcd: object) -> str:
    text = _safe_text(jcd)
    if not text:
        return ""
    return JCD_TO_VENUE.get(text.zfill(2), "")


def _normalized_race_key(date_value: object, jcd_value: object, race_no_value: object) -> str:
    date_text = _safe_text(date_value)
    if date_text:
        date_text = pd.to_datetime(date_text, errors="coerce").strftime("%Y%m%d") if not pd.isna(pd.to_datetime(date_text, errors="coerce")) else date_text.replace("-", "")
    jcd_text = _safe_text(jcd_value).zfill(2)
    race_no = _safe_int(race_no_value)
    if race_no is None:
        race_no_text = _safe_text(race_no_value)
    else:
        race_no_text = f"{race_no:02d}"
    if date_text and jcd_text and race_no_text:
        return f"{date_text}-{jcd_text}-{race_no_text}"
    return _safe_text(race_no_value)


def _race_key_from_row(row: pd.Series) -> str:
    race_id = _safe_text(row.get("race_id"))
    if race_id and "-" in race_id:
        return race_id
    return _normalized_race_key(row.get("date"), row.get("jcd"), row.get("race_no"))


def _candidate_id_from_row(row: pd.Series) -> str:
    for key in ("candidate_id", "recommended_trifecta", "trifecta"):
        value = _safe_text(row.get(key))
        if value:
            return value
    return _safe_text(row.get("race_id"))


def _reason_from_notes(row: pd.Series) -> str:
    for key in ("reason", "stop_reason", "race_note", "rank_rescue_reason", "near_cap_rescue_reason", "payout_outlier_rescue_reason"):
        value = _safe_text(row.get(key))
        if value:
            return value
    return ""


def _has_any_text(row: pd.Series, patterns: list[str]) -> bool:
    haystack = " | ".join(_safe_text(row.get(col)) for col in row.index)
    if not haystack:
        return False
    lowered = haystack.lower()
    return any(p.lower() in lowered for p in patterns)


def _classify_reason(row: pd.Series, evaluator: StrategyEvaluator, compare_possible: bool) -> tuple[str, str]:
    decision_raw = _safe_text(row.get("decision")).upper()
    if decision_raw == "BUY":
        return "buy", _reason_from_notes(row) or "BUY"

    race_id = _safe_text(row.get("race_id"))
    if not race_id or "-" not in race_id:
        return "invalid_race_input", "race_id missing or malformed"

    if _has_any_text(row, ["6艇未満", "actual_boats=5", "actual_boats=4", "actual_boats=3", "actual_boats=2", "actual_boats=1", "actual_boats=0"]):
        return "invalid_race_input", _reason_from_notes(row) or "actual_boats < 6"

    has_real_odds = _safe_bool(row.get("has_real_odds"))
    odds_status = _safe_text(row.get("odds_status")).lower()
    odds_fetch_status = _safe_text(row.get("odds_fetch_status")).lower()
    if (not has_real_odds) or odds_status in {"missing", "pending", "unknown"} or odds_fetch_status in {"pending_unpublished", "failed"}:
        return "odds_unavailable", f"has_real_odds={has_real_odds}, odds_status={odds_status or 'missing'}, odds_fetch_status={odds_fetch_status or 'missing'}"

    if not compare_possible:
        return "compare_impossible", "compare row missing for this race"

    reason_text = _reason_from_notes(row)
    if "hard_guard" in reason_text.lower() or "guard" in reason_text.lower() and "reject" in reason_text.lower():
        return "hard_guard_reject", reason_text or "hard guard reject"

    if pd.isna(row.get("ev")) or pd.isna(row.get("approx_prob")) or pd.isna(row.get("first_win_proba")):
        return "feature_missing", "important indicators missing"

    ev = _safe_float(row.get("ev"))
    approx_prob = _safe_float(row.get("approx_prob"))
    calibrated_prob = _safe_float(row.get("calibrated_hit_prob"))
    if ev is None or approx_prob is None:
        return "feature_missing", "ev / approx_prob missing"

    if ev < evaluator.buy_min_ev:
        return "ev_below_threshold", f"ev={ev:.3f} < threshold={evaluator.buy_min_ev:.3f}"

    if approx_prob < evaluator.buy_min_approx_prob:
        return "probability_below_threshold", f"approx_prob={approx_prob:.4f} < threshold={evaluator.buy_min_approx_prob:.4f}"

    if calibrated_prob is not None and calibrated_prob < evaluator.rank_rescue_min_calibrated_hit_prob:
        return "calibration_suppressed", (
            f"calibrated_prob={calibrated_prob:.4f} < threshold={evaluator.rank_rescue_min_calibrated_hit_prob:.4f}"
        )

    if _has_any_text(row, ["missing", "欠損", "not found", "na ", "nan"]):
        return "feature_missing", reason_text or "feature missing"

    if _safe_text(row.get("stop_reason")).startswith("real_odds_missing") or "pending_unpublished" in odds_fetch_status:
        return "odds_unavailable", reason_text or "real odds unavailable"

    if _safe_text(row.get("stop_reason")).startswith("hard_guard"):
        return "hard_guard_reject", reason_text or _safe_text(row.get("stop_reason"))

    return "other", reason_text or _safe_text(row.get("stop_reason")) or "unclassified"


def _build_diagnostic_rows(
    pred_df: pd.DataFrame,
    *,
    evaluator: StrategyEvaluator,
    target_date: str,
    truth_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    work = pred_df.copy()
    if work.empty:
        work = pd.DataFrame(columns=["race_id", "date", "decision"])

    if "date" in work.columns:
        work["date"] = pd.to_datetime(work["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    else:
        work["date"] = target_date

    truth_race_ids: set[str] = set()
    truth_race_meta: dict[str, dict[str, object]] = {}
    if truth_df is not None and not truth_df.empty and "race_id" in truth_df.columns:
        truth = truth_df.copy()
        if "date" in truth.columns:
            truth["date"] = pd.to_datetime(truth["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        if "date" in truth.columns:
            truth = truth[truth["date"].astype(str) == target_date].copy()
        for _, truth_row in truth.iterrows():
            key = _race_key_from_row(truth_row)
            if not key:
                continue
            truth_race_ids.add(key)
            truth_race_meta[key] = {
                "venue": _venue_from_jcd(truth_row.get("jcd")) or _safe_text(truth_row.get("venue")),
                "race_no": _safe_int(truth_row.get("race_no")),
                "race_id": key,
            }
        expected_race_ids = set(truth_race_ids)
    else:
        expected_race_ids = set(_race_key_from_row(row) for _, row in work.iterrows())

    work_race_ids = set(_race_key_from_row(row) for _, row in work.iterrows())
    missing_race_ids = sorted(rid for rid in expected_race_ids if rid and rid not in work_race_ids)

    rows = []
    for _, row in work.iterrows():
        decision_raw = _safe_text(row.get("decision")).upper() or "SKIP"
        compare_possible = _race_key_from_row(row) in truth_race_ids
        reason_code, reason_detail = _classify_reason(row, evaluator, compare_possible=compare_possible)
        if decision_raw == "BUY":
            final_decision = "BUY"
            reason_code = "buy"
        else:
            final_decision = "SKIP" if decision_raw != "BUY" else "BUY"

        ev = _safe_float(row.get("ev"))
        pred_prob = _safe_float(row.get("approx_prob"))
        calibrated_prob = _safe_float(row.get("calibrated_hit_prob"))
        odds = _safe_float(row.get("odds"))
        stale_age_days = None
        fetched_at = _safe_text(row.get("odds_last_fetched_at"))
        if fetched_at:
            try:
                fetched_dt = pd.to_datetime(fetched_at, errors="coerce")
                if not pd.isna(fetched_dt):
                    stale_age_days = max((pd.Timestamp(target_date) - fetched_dt.normalize()).days, 0)
            except Exception:
                stale_age_days = None

        data_fields = [
            row.get("ev"),
            row.get("approx_prob"),
            row.get("calibrated_hit_prob"),
            row.get("odds"),
            row.get("confidence_score"),
            row.get("race_score"),
        ]
        data_completeness = round(sum(not pd.isna(v) for v in data_fields) / float(len(data_fields)), 4)
        odds_available = 1.0 if _safe_bool(row.get("has_real_odds")) else 0.0

        rows.append(
            {
                "date": target_date,
                "venue": _venue_from_race_id(row.get("race_id")),
                "race_no": _safe_int(_safe_text(row.get("race_id")).split("-")[-1]) if "-" in _safe_text(row.get("race_id")) else _safe_int(row.get("race_no")),
                "race_id": _safe_text(row.get("race_id")) or _race_key_from_row(row),
                "candidate_id": _candidate_id_from_row(row),
                "decision_raw": decision_raw,
                "final_decision": final_decision,
                "reason_code": reason_code,
                "reason_detail": reason_detail,
                "ev": ev,
                "pred_prob": pred_prob,
                "calibrated_prob": calibrated_prob,
                "odds": odds,
                "threshold_ev": evaluator.buy_min_ev,
                "threshold_prob": evaluator.buy_min_approx_prob,
                "threshold_calibrated_prob": evaluator.rank_rescue_min_calibrated_hit_prob,
                "threshold_odds": evaluator.max_odds_for_buy,
                "threshold_data_completeness": evaluator.buy_hard_guard_min_data_completeness,
                "threshold_odds_availability": evaluator.buy_hard_guard_min_odds_availability,
                "threshold_max_stale_age_days": evaluator.buy_hard_guard_max_stale_age_days,
                "threshold_model_confidence": evaluator.buy_hard_guard_min_model_confidence,
                "data_completeness": data_completeness,
                "odds_availability": odds_available,
                "stale_age_days": stale_age_days,
                "diag_has_candidate": bool(_safe_text(row.get("candidate_rank_by_sort")) or _safe_text(row.get("recommended_trifecta"))),
                "diag_has_odds": bool(_safe_bool(row.get("has_real_odds"))),
                "diag_compare_possible": bool(compare_possible),
                "diag_ev_pass": bool(ev is not None and ev >= evaluator.buy_min_ev),
                "diag_prob_pass": bool(pred_prob is not None and pred_prob >= evaluator.buy_min_approx_prob),
                "diag_calibration_pass": bool(
                    calibrated_prob is None or calibrated_prob >= evaluator.rank_rescue_min_calibrated_hit_prob
                ),
                "diag_guard_pass": not ("hard_guard" in reason_code or "hard_guard" in reason_detail.lower()),
                "diag_final_reason": reason_code,
                "skip_reason": _safe_text(row.get("skip_reason")) or _safe_text(row.get("stop_reason")),
                "reason": _reason_from_notes(row),
            }
        )

    for race_id in missing_race_ids:
        meta = truth_race_meta.get(race_id, {})
        rows.append(
            {
                "date": target_date,
                "venue": _safe_text(meta.get("venue")) or _venue_from_race_id(race_id),
                "race_no": meta.get("race_no") if meta.get("race_no") is not None else _safe_int(race_id.split("-")[-1]),
                "race_id": race_id,
                "candidate_id": "",
                "decision_raw": "",
                "final_decision": "SKIP",
                "reason_code": "no_candidate_generated",
                "reason_detail": "skip_decisions に候補行なし",
                "ev": None,
                "pred_prob": None,
                "calibrated_prob": None,
                "odds": None,
                "threshold_ev": evaluator.buy_min_ev,
                "threshold_prob": evaluator.buy_min_approx_prob,
                "threshold_calibrated_prob": evaluator.rank_rescue_min_calibrated_hit_prob,
                "threshold_odds": evaluator.max_odds_for_buy,
                "threshold_data_completeness": evaluator.buy_hard_guard_min_data_completeness,
                "threshold_odds_availability": evaluator.buy_hard_guard_min_odds_availability,
                "threshold_max_stale_age_days": evaluator.buy_hard_guard_max_stale_age_days,
                "threshold_model_confidence": evaluator.buy_hard_guard_min_model_confidence,
                "data_completeness": 0.0,
                "odds_availability": 0.0,
                "stale_age_days": None,
                "diag_has_candidate": False,
                "diag_has_odds": False,
                "diag_compare_possible": False,
                "diag_ev_pass": False,
                "diag_prob_pass": False,
                "diag_calibration_pass": False,
                "diag_guard_pass": False,
                "diag_final_reason": "no_candidate_generated",
                "skip_reason": "",
                "reason": "no candidate row generated",
            }
        )

    diag_df = pd.DataFrame(rows)
    if diag_df.empty:
        return diag_df

    diag_df = diag_df.sort_values(["venue", "race_no", "race_id", "final_decision"], ascending=[True, True, True, True]).reset_index(drop=True)
    return diag_df


def build_buy_zero_diagnosis_report(
    pred_df: pd.DataFrame,
    *,
    target_date: str,
    output_dir: Path,
    truth_df: pd.DataFrame | None = None,
    evaluator: StrategyEvaluator | None = None,
) -> tuple[pd.DataFrame, dict[str, object]]:
    evaluator = evaluator or StrategyEvaluator(config_path=str(ROOT / "config" / "strategy_config.json"))
    diag_df = _build_diagnostic_rows(pred_df, evaluator=evaluator, target_date=target_date, truth_df=truth_df)
    output_dir.mkdir(parents=True, exist_ok=True)

    total_races = int(diag_df["race_id"].astype(str).nunique()) if not diag_df.empty else 0
    candidate_total = int(len(diag_df))
    buy_count = int(diag_df["final_decision"].astype(str).str.upper().eq("BUY").sum()) if not diag_df.empty else 0
    skip_count = int(diag_df["final_decision"].astype(str).str.upper().eq("SKIP").sum()) if not diag_df.empty else 0

    reason_counts = (
        diag_df["reason_code"].fillna("other").astype(str).value_counts().reindex(REASON_ORDER, fill_value=0).astype(int).to_dict()
        if not diag_df.empty
        else {code: 0 for code in REASON_ORDER}
    )
    venue_counts = (
        diag_df.groupby(["venue", "reason_code"], dropna=False).size().reset_index(name="count")
        if not diag_df.empty
        else pd.DataFrame(columns=["venue", "reason_code", "count"])
    )
    stage_summary = {
        "diag_has_candidate": int(diag_df.get("diag_has_candidate", pd.Series(dtype=bool)).fillna(False).astype(bool).sum()) if not diag_df.empty else 0,
        "diag_has_odds": int(diag_df.get("diag_has_odds", pd.Series(dtype=bool)).fillna(False).astype(bool).sum()) if not diag_df.empty else 0,
        "diag_compare_possible": int(diag_df.get("diag_compare_possible", pd.Series(dtype=bool)).fillna(False).astype(bool).sum()) if not diag_df.empty else 0,
        "diag_ev_pass": int(diag_df.get("diag_ev_pass", pd.Series(dtype=bool)).fillna(False).astype(bool).sum()) if not diag_df.empty else 0,
        "diag_prob_pass": int(diag_df.get("diag_prob_pass", pd.Series(dtype=bool)).fillna(False).astype(bool).sum()) if not diag_df.empty else 0,
        "diag_calibration_pass": int(diag_df.get("diag_calibration_pass", pd.Series(dtype=bool)).fillna(False).astype(bool).sum()) if not diag_df.empty else 0,
        "diag_guard_pass": int(diag_df.get("diag_guard_pass", pd.Series(dtype=bool)).fillna(False).astype(bool).sum()) if not diag_df.empty else 0,
    }

    detail_cols = [
        "date",
        "venue",
        "race_no",
        "race_id",
        "candidate_id",
        "decision_raw",
        "final_decision",
        "reason_code",
        "reason_detail",
        "ev",
        "pred_prob",
        "calibrated_prob",
        "odds",
        "threshold_ev",
        "threshold_prob",
        "threshold_calibrated_prob",
        "threshold_odds",
        "threshold_data_completeness",
        "threshold_odds_availability",
        "threshold_max_stale_age_days",
        "threshold_model_confidence",
        "data_completeness",
        "odds_availability",
        "stale_age_days",
        "diag_has_candidate",
        "diag_has_odds",
        "diag_compare_possible",
        "diag_ev_pass",
        "diag_prob_pass",
        "diag_calibration_pass",
        "diag_guard_pass",
    ]
    for col in detail_cols:
        if col not in diag_df.columns:
            diag_df[col] = None

    diag_df = diag_df[detail_cols].copy()
    csv_path = output_dir / f"buy_zero_diagnosis_{target_date}.csv"
    md_path = output_dir / f"buy_zero_diagnosis_{target_date}.md"
    json_path = output_dir / f"buy_zero_diagnosis_{target_date}.json"
    diag_df.to_csv(csv_path, index=False)

    top_reasons = (
        diag_df["reason_code"].fillna("other").astype(str).value_counts().head(8).to_dict()
        if not diag_df.empty
        else {}
    )
    venue_summary = (
        diag_df.groupby("venue", dropna=False)["final_decision"].value_counts().unstack(fill_value=0)
        if not diag_df.empty
        else pd.DataFrame()
    )
    rejects = diag_df[diag_df["final_decision"].astype(str).str.upper() != "BUY"].copy() if not diag_df.empty else pd.DataFrame()
    representative = rejects.sort_values(
        ["reason_code", "venue", "race_no", "ev"],
        ascending=[True, True, True, True],
    ).head(10)

    md_lines = [
        f"# BUY Zero Diagnosis {target_date}",
        "",
        f"- 対象日: `{target_date}`",
        f"- 総レース数: `{total_races}`",
        f"- 候補総数: `{candidate_total}`",
        f"- BUY 件数: `{buy_count}`",
        f"- SKIP 件数: `{skip_count}`",
        "",
        "## Stage Summary",
    ]
    for key, value in stage_summary.items():
        md_lines.append(f"- {key}: `{value}`")
    md_lines.extend(["", "## Reason Summary"])
    for reason in REASON_ORDER:
        md_lines.append(f"- {reason}: `{reason_counts.get(reason, 0)}`")
    md_lines.extend(["", "## Venue Summary"])
    if venue_summary.empty:
        md_lines.append("- なし")
    else:
        md_lines.append(_markdown_table(venue_summary.reset_index()))
    md_lines.extend(["", "## Representative Rejects"])
    if representative.empty:
        md_lines.append("- 見送り候補なし")
    else:
        show_cols = ["venue", "race_no", "race_id", "candidate_id", "final_decision", "reason_code", "reason_detail", "ev", "pred_prob", "calibrated_prob", "odds"]
        md_lines.append(_markdown_table(representative, show_cols))
    md_lines.extend(["", "## Top Reasons", ""])
    for reason, count in top_reasons.items():
        md_lines.append(f"- {reason}: `{count}`")
    md_lines.extend(["", "## Thresholds", ""])
    md_lines.append(
        f"- ev >= {evaluator.buy_min_ev:.3f} / prob >= {evaluator.buy_min_approx_prob:.3f} / calibrated >= {evaluator.rank_rescue_min_calibrated_hit_prob:.3f}"
    )
    md_lines.append(
        f"- hard_guard: data_completeness >= {evaluator.buy_hard_guard_min_data_completeness:.3f} / odds_availability >= {evaluator.buy_hard_guard_min_odds_availability:.3f} / max_stale_age_days <= {evaluator.buy_hard_guard_max_stale_age_days:.1f} / model_confidence >= {evaluator.buy_hard_guard_min_model_confidence:.3f}"
    )

    md_path.write_text("\n".join(md_lines), encoding="utf-8")

    summary = {
        "date": target_date,
        "total_races": total_races,
        "candidate_total": candidate_total,
        "buy_count": buy_count,
        "skip_count": skip_count,
        "reason_counts": reason_counts,
        "venue_counts": venue_counts.to_dict(orient="records") if not venue_counts.empty else [],
        "stage_summary": stage_summary,
        "top_reasons": top_reasons,
        "outputs": {
            "csv": str(csv_path),
            "md": str(md_path),
            "json": str(json_path),
        },
        "thresholds": {
            "ev": evaluator.buy_min_ev,
            "prob": evaluator.buy_min_approx_prob,
            "calibrated_prob": evaluator.rank_rescue_min_calibrated_hit_prob,
            "odds": evaluator.max_odds_for_buy,
            "data_completeness": evaluator.buy_hard_guard_min_data_completeness,
            "odds_availability": evaluator.buy_hard_guard_min_odds_availability,
            "max_stale_age_days": evaluator.buy_hard_guard_max_stale_age_days,
            "model_confidence": evaluator.buy_hard_guard_min_model_confidence,
        },
    }
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return diag_df, summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a daily buy-zero diagnosis report.")
    parser.add_argument("--date", required=True, help="Target date (YYYY-MM-DD).")
    parser.add_argument("--predictions", default=str(DEFAULT_PREDICTIONS))
    parser.add_argument("--truth", default=str(DEFAULT_TRUTH))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--output-dir", default="")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    target_date = str(args.date)
    pred_path = Path(args.predictions)
    truth_path = Path(args.truth)
    output_root = Path(args.output_root)
    output_dir = Path(args.output_dir) if args.output_dir else output_root / target_date
    evaluator = StrategyEvaluator(config_path=str(ROOT / "config" / "strategy_config.json"))

    pred_df = pd.read_csv(pred_path, low_memory=False) if pred_path.exists() else pd.DataFrame()
    truth_df = pd.read_csv(truth_path, low_memory=False) if truth_path.exists() else pd.DataFrame()
    if not pred_df.empty and "date" in pred_df.columns:
        pred_df["date"] = pd.to_datetime(pred_df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        pred_df = pred_df[pred_df["date"].astype(str) == target_date].copy()
    if not truth_df.empty and "date" in truth_df.columns:
        truth_df["date"] = pd.to_datetime(truth_df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    diag_df, summary = build_buy_zero_diagnosis_report(
        pred_df,
        target_date=target_date,
        output_dir=output_dir,
        truth_df=truth_df,
        evaluator=evaluator,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not diag_df.empty:
        print(diag_df.head(10).to_string(index=False))


if __name__ == "__main__":
    main()
