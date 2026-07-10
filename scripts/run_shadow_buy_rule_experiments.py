from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REPORTS_DAILY_ROOT = ROOT / "reports" / "daily"
REPORTS_PREDICTIONS_ROOT = ROOT / "reports" / "predictions"
REPORTS_CONSENSUS_ROOT = ROOT / "reports" / "consensus"
REPORTS_BACKTEST_ROOT = ROOT / "reports" / "backtest"
REPORTS_SHADOW_ROOT = ROOT / "reports" / "shadow"
REPORTS_AUDIT_ROOT = ROOT / "reports" / "repo_audit"
SHADOW_CONFIG_PATH = ROOT / "configs" / "shadow_buy_rules.yaml"
STRATEGY_CONFIG_PATH = ROOT / "config" / "strategy_config.json"

MISSING_RESULT_STATUSES = {
    "result_data_missing",
    "raw_missing",
    "source_not_ready",
    "future_date_not_ready",
    "parse_error",
}
UNRESOLVED_RESULT_STATUSES = MISSING_RESULT_STATUSES | {"pending", "unavailable"}
READY_RESULT_STATUSES = {"ok", "available", "settled"}
GRADE_RANK = {"NONE": 0, "C": 1, "B": 2, "A": 3}
RESULT_STATUS_ALIASES = {
    "missing_data": "result_data_missing",
    "results_missing": "result_data_missing",
}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except Exception:
        return pd.DataFrame()


def _load_shadow_rules(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        payload = yaml.safe_load(text)
    except Exception:
        payload = json.loads(text)
    variants = payload.get("variants", []) if isinstance(payload, dict) else []
    return [item for item in variants if isinstance(item, dict) and item.get("name")]


def _load_strategy_config() -> dict[str, Any]:
    return _read_json(STRATEGY_CONFIG_PATH)


def _date_range(start_date: str, end_date: str) -> list[str]:
    start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
    end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()
    days: list[str] = []
    cur = start_dt
    while cur <= end_dt:
        days.append(cur.isoformat())
        cur += timedelta(days=1)
    return days


def _safe_float(value: Any) -> float | None:
    try:
        num = pd.to_numeric(value, errors="coerce")
    except Exception:
        return None
    if pd.isna(num):
        return None
    return float(num)


def _boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y"}


def _canonical_grade(value: Any) -> str:
    grade = str(value or "NONE").strip().upper()
    return grade if grade in GRADE_RANK else "NONE"


def _canonical_result_status_label(value: Any) -> str:
    text = str(value or "").strip()
    return RESULT_STATUS_ALIASES.get(text, text)


def _json_cell(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if value is None:
        return ""
    return str(value)


def _markdown_table(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> str:
    if not rows:
        return "_なし_"
    header = "| " + " | ".join(label for _, label in columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"
    body: list[str] = []
    for row in rows:
        cells = []
        for key, _ in columns:
            value = row.get(key)
            if isinstance(value, float):
                cells.append(f"{value:.4f}")
            else:
                cells.append(_json_cell(value))
        body.append("| " + " | ".join(cells) + " |")
    return "\n".join([header, sep, *body])


def _list_dates_with_artifacts(start_date: str, end_date: str) -> list[str]:
    return _date_range(start_date, end_date)


def _date_compact(date_text: str) -> str:
    return date_text.replace("-", "")


def _suffix(label: str) -> str:
    return f"_{label}" if label else ""


def _daily_paths(date_text: str) -> dict[str, Path]:
    compact = _date_compact(date_text)
    return {
        "skip_decisions": REPORTS_DAILY_ROOT / date_text / "skip_decisions.csv",
        "daily_summary": REPORTS_DAILY_ROOT / date_text / "daily_summary.json",
        "daily_report": REPORTS_DAILY_ROOT / date_text / "daily_report.json",
        "post_race": REPORTS_DAILY_ROOT / date_text / "post_race_run.json",
        "daily_eval": REPORTS_DAILY_ROOT / date_text / "daily_evaluation_race_results.csv",
        "prediction_sheet": REPORTS_PREDICTIONS_ROOT / date_text / "prediction_sheet.json",
        "prediction_review": REPORTS_PREDICTIONS_ROOT / date_text / "prediction_review.json",
        "frozen_bets_json": REPORTS_PREDICTIONS_ROOT / date_text / "frozen_bets.json",
        "frozen_bets_csv": REPORTS_PREDICTIONS_ROOT / date_text / "frozen_bets.csv",
        "consensus_sheet": REPORTS_CONSENSUS_ROOT / date_text / "consensus_sheet.json",
        "ui_prediction_sheet": ROOT / "data" / "ui" / compact / "prediction_sheet.json",
        "settlement_file": REPORTS_DAILY_ROOT / f"{compact}_settlement.json",
    }


def _read_skip_decisions_buy_count(path: Path) -> int:
    df = _read_csv(path)
    if df.empty or "final_decision" not in df.columns:
        return 0
    return int(df["final_decision"].astype(str).str.upper().eq("BUY").sum())


def _read_prediction_decision_counts(prediction_sheet: dict[str, Any]) -> dict[str, int]:
    rows = prediction_sheet.get("candidates", [])
    if not isinstance(rows, list):
        return {"buy_count": 0, "watch_count": 0, "paper_count": 0}
    buy_count = 0
    watch_count = 0
    paper_count = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("finalDecision") or "").upper() == "BUY":
            buy_count += 1
        if str(row.get("paperDecision") or "").upper() == "WATCH":
            watch_count += 1
        if str(row.get("paperDecision") or "").upper() == "PAPER":
            paper_count += 1
    return {"buy_count": buy_count, "watch_count": watch_count, "paper_count": paper_count}


def _read_frozen_buy_count(frozen_bets: dict[str, Any]) -> int:
    races = frozen_bets.get("races", [])
    if not isinstance(races, list):
        return 0
    count = 0
    for race in races:
        if not isinstance(race, dict):
            continue
        for bet in race.get("bets", []):
            if isinstance(bet, dict) and str(bet.get("final_decision") or "").upper() == "BUY":
                count += 1
    return count


def _monetary_settlement_facts(date_text: str, paths: dict[str, Path]) -> dict[str, Any]:
    settlement_payload = _read_json(paths["settlement_file"])
    daily_summary = _read_json(paths["daily_summary"])
    daily_report = _read_json(paths["daily_report"])
    post_race = _read_json(paths["post_race"])
    daily_eval = _read_csv(paths["daily_eval"])

    stake_available = False
    return_available = False
    payout_available = False
    roi_available = False
    settled_bet_count = 0
    note_parts: list[str] = []

    if settlement_payload:
        settled_bet_count = int(settlement_payload.get("settledBetCount") or 0)
        stake_available = settlement_payload.get("stakeAmount") is not None
        payout_available = settlement_payload.get("payoutAmount") is not None
        return_available = payout_available or settlement_payload.get("profit") is not None
        roi_available = settlement_payload.get("roi") is not None or settlement_payload.get("settledRoi") is not None
        note_parts.append("settlement_json")
    elif daily_report:
        settled_bet_count = int(daily_report.get("settledBetCount") or 0)
        stake_available = daily_report.get("stakeAmount") is not None or daily_report.get("frozenStakeAmount") is not None
        payout_available = daily_report.get("payoutAmount") is not None
        return_available = payout_available or daily_report.get("profit") is not None
        roi_available = daily_report.get("roi") is not None or daily_report.get("settledRoi") is not None
        note_parts.append("daily_report")
    elif daily_summary:
        settled_bet_count = int(daily_summary.get("buy_count") or 0)
        stake_available = daily_summary.get("total_stake") is not None
        payout_available = daily_summary.get("total_return") is not None
        return_available = payout_available or daily_summary.get("profit") is not None
        roi_available = daily_summary.get("roi") is not None
        note_parts.append("daily_summary")

    if not daily_eval.empty:
        eval_settled = int(daily_eval["result_available"].fillna(False).astype(bool).sum()) if "result_available" in daily_eval.columns else 0
        settled_bet_count = max(settled_bet_count, eval_settled)
        if "stake_amount" in daily_eval.columns:
            stake_series = pd.to_numeric(daily_eval["stake_amount"], errors="coerce")
            stake_available = stake_available or bool(stake_series.notna().any())
        if "payout_amount" in daily_eval.columns:
            payout_series = pd.to_numeric(daily_eval["payout_amount"], errors="coerce")
            payout_available = payout_available or bool(payout_series.notna().any())
            return_available = return_available or bool(payout_series.notna().any())
        if "pnl" in daily_eval.columns:
            pnl_series = pd.to_numeric(daily_eval["pnl"], errors="coerce")
            roi_available = roi_available or bool(pnl_series.notna().any())
        note_parts.append("daily_eval")

    if post_race:
        note_parts.append("post_race_run")

    monetary_available = bool(
        settled_bet_count > 0
        and (paths["settlement_file"].exists() or paths["daily_report"].exists())
        and (stake_available or payout_available or roi_available)
    )
    return {
        "settlement_file_exists": paths["settlement_file"].exists(),
        "stake_available": stake_available,
        "return_available": return_available,
        "payout_available": payout_available,
        "roi_available": roi_available,
        "settled_bet_count": settled_bet_count,
        "monetary_settlement_available": monetary_available,
        "settlement_note": ",".join(dict.fromkeys(note_parts)) if note_parts else "",
    }


def build_backtest_coverage(start_date: str, end_date: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for date_text in _list_dates_with_artifacts(start_date, end_date):
        paths = _daily_paths(date_text)
        daily_summary = _read_json(paths["daily_summary"])
        prediction_review = _read_json(paths["prediction_review"])
        prediction_sheet = _read_json(paths["prediction_sheet"])
        frozen_bets = _read_json(paths["frozen_bets_json"])
        post_race = _read_json(paths["post_race"])
        decision_counts = _read_prediction_decision_counts(prediction_sheet)
        skip_buy_count = _read_skip_decisions_buy_count(paths["skip_decisions"])
        frozen_buy_count = _read_frozen_buy_count(frozen_bets)
        settlement_facts = _monetary_settlement_facts(date_text, paths)

        results_status = _canonical_result_status_label(
            daily_summary.get("results_status")
            or daily_summary.get("resultsStatus")
            or prediction_review.get("status")
            or post_race.get("status")
            or ""
        )
        if not results_status and daily_summary.get("results_available") is True:
            results_status = "available"

        candidates = prediction_sheet.get("candidates", []) if isinstance(prediction_sheet.get("candidates"), list) else []
        frozen_races = frozen_bets.get("races", []) if isinstance(frozen_bets.get("races"), list) else []
        prediction_hash_exists = any(str(item.get("predictionHash") or "").strip() for item in candidates)
        if not prediction_hash_exists:
            prediction_hash_exists = any(
                str(bet.get("predictionHash") or "").strip()
                for race in frozen_races
                if isinstance(race, dict)
                for bet in race.get("bets", [])
                if isinstance(bet, dict)
            )

        missing_artifacts: list[str] = []
        for key in ("skip_decisions", "prediction_sheet", "prediction_review", "post_race"):
            if not paths[key].exists():
                missing_artifacts.append(key)

        current_active_candidate_count = decision_counts["buy_count"] or frozen_buy_count or skip_buy_count

        if results_status in MISSING_RESULT_STATUSES:
            validation_type = results_status
        elif paths["prediction_sheet"].exists() and paths["frozen_bets_json"].exists():
            validation_type = "frozen_live_validation"
        elif paths["prediction_sheet"].exists():
            validation_type = "historical_simulation"
        else:
            validation_type = "insufficient_artifacts"

        note_parts: list[str] = []
        if missing_artifacts:
            note_parts.append("missing=" + ",".join(missing_artifacts))
        if not paths["daily_summary"].exists():
            note_parts.append("daily_summary_missing")
        if paths["ui_prediction_sheet"].exists():
            note_parts.append("ui_prediction_sheet")
        if prediction_review.get("status") and prediction_review.get("status") != "ok":
            note_parts.append(f"prediction_review_status={prediction_review.get('status')}")
        if settlement_facts["settlement_note"]:
            note_parts.append(f"settlement={settlement_facts['settlement_note']}")
        if not note_parts:
            note_parts.append("artifacts_checked")

        rows.append(
            {
                "date": date_text,
                "skip_decisions_exists": paths["skip_decisions"].exists(),
                "prediction_sheet_exists": paths["prediction_sheet"].exists(),
                "frozen_bets_exists": paths["frozen_bets_json"].exists() or paths["frozen_bets_csv"].exists(),
                "prediction_review_exists": paths["prediction_review"].exists(),
                "daily_summary_exists": paths["daily_summary"].exists(),
                "post_race_exists": paths["post_race"].exists(),
                "daily_report_exists": paths["daily_report"].exists(),
                "consensus_sheet_exists": paths["consensus_sheet"].exists(),
                "settlement_file_exists": settlement_facts["settlement_file_exists"],
                "daily_evaluation_race_results_exists": paths["daily_eval"].exists(),
                "results_status": results_status or "",
                "monetary_settlement_available": settlement_facts["monetary_settlement_available"],
                "current_active_candidate_count": current_active_candidate_count,
                "buy_count": decision_counts["buy_count"] or skip_buy_count,
                "watch_count": decision_counts["watch_count"],
                "paper_count": decision_counts["paper_count"],
                "predictionHash_exists": prediction_hash_exists,
                "usable_for_current_active_comparison": bool(
                    current_active_candidate_count > 0 and results_status not in MISSING_RESULT_STATUSES
                ),
                "usable_for_shadow_comparison": bool(paths["prediction_sheet"].exists()),
                "usable_for_roi": settlement_facts["monetary_settlement_available"],
                "validation_type": validation_type,
                "usable_for_frozen_validation": bool(
                    paths["prediction_sheet"].exists()
                    and (paths["frozen_bets_json"].exists() or paths["frozen_bets_csv"].exists())
                    and results_status not in MISSING_RESULT_STATUSES
                ),
                "usable_for_historical_simulation": bool(paths["prediction_sheet"].exists()),
                "note": "; ".join(note_parts),
            }
        )
    return rows


def _write_coverage_reports(rows: list[dict[str, Any]], label: str) -> tuple[Path, Path]:
    REPORTS_BACKTEST_ROOT.mkdir(parents=True, exist_ok=True)
    suffix = _suffix(label)
    csv_path = REPORTS_BACKTEST_ROOT / f"backtest_data_coverage{suffix}.csv"
    md_path = REPORTS_BACKTEST_ROOT / f"backtest_data_coverage{suffix}.md"
    pd.DataFrame(rows).to_csv(csv_path, index=False, encoding="utf-8-sig")
    md_lines = [
        f"# backtest data coverage{suffix}",
        "",
        _markdown_table(
            rows,
            [
                ("date", "date"),
                ("current_active_candidate_count", "current_active"),
                ("buy_count", "buy"),
                ("validation_type", "validation_type"),
                ("prediction_sheet_exists", "prediction_sheet"),
                ("frozen_bets_exists", "frozen_bets"),
                ("prediction_review_exists", "prediction_review"),
                ("daily_summary_exists", "daily_summary"),
                ("daily_report_exists", "daily_report"),
                ("consensus_sheet_exists", "consensus"),
                ("settlement_file_exists", "settlement"),
                ("results_status", "results_status"),
                ("usable_for_current_active_comparison", "current_active_ok"),
                ("usable_for_shadow_comparison", "shadow_ok"),
                ("usable_for_roi", "roi_ok"),
            ],
        ),
        "",
        "## notes",
        "",
    ]
    for row in rows:
        md_lines.append(f"- {row['date']}: {row['note']}")
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    return csv_path, md_path


def _write_current_active_candidate_days(rows: list[dict[str, Any]]) -> tuple[Path, Path]:
    REPORTS_BACKTEST_ROOT.mkdir(parents=True, exist_ok=True)
    csv_path = REPORTS_BACKTEST_ROOT / "current_active_candidate_days.csv"
    md_path = REPORTS_BACKTEST_ROOT / "current_active_candidate_days.md"
    out_rows: list[dict[str, Any]] = []
    for row in rows:
        paths = _daily_paths(str(row["date"]))
        frozen_buy_count = _read_frozen_buy_count(_read_json(paths["frozen_bets_json"]))
        daily_eval = _read_csv(paths["daily_eval"])
        settled_buy_count = 0
        if not daily_eval.empty and {"final_decision", "result_available"}.issubset(daily_eval.columns):
            settled_buy_mask = (
                daily_eval["final_decision"].astype(str).str.upper().eq("BUY")
                & daily_eval["result_available"].fillna(False).astype(bool)
            )
            settled_buy_count = int(settled_buy_mask.sum())
        note = "no_current_active_buy" if int(row["current_active_candidate_count"] or 0) == 0 else "has_current_active_buy"
        out_rows.append(
            {
                "date": row["date"],
                "buy_count": row["buy_count"],
                "frozen_buy_count": frozen_buy_count,
                "settled_buy_count": int(settled_buy_count),
                "monetary_settlement_available": row["monetary_settlement_available"],
                "result_status": row["results_status"],
                "note": note,
            }
        )
    pd.DataFrame(out_rows).to_csv(csv_path, index=False, encoding="utf-8-sig")
    md_path.write_text(
        "\n".join(
            [
                "# current active candidate days",
                "",
                _markdown_table(
                    out_rows,
                    [
                        ("date", "date"),
                        ("buy_count", "buy"),
                        ("frozen_buy_count", "frozen_buy"),
                        ("settled_buy_count", "settled_buy"),
                        ("monetary_settlement_available", "roi_ok"),
                        ("result_status", "result_status"),
                        ("note", "note"),
                    ],
                ),
            ]
        ),
        encoding="utf-8",
    )
    return csv_path, md_path


def _write_monetary_settlement_audit(rows: list[dict[str, Any]]) -> tuple[Path, Path]:
    REPORTS_BACKTEST_ROOT.mkdir(parents=True, exist_ok=True)
    csv_path = REPORTS_BACKTEST_ROOT / "monetary_settlement_audit.csv"
    md_path = REPORTS_BACKTEST_ROOT / "monetary_settlement_audit.md"
    out_rows: list[dict[str, Any]] = []
    for row in rows:
        paths = _daily_paths(str(row["date"]))
        facts = _monetary_settlement_facts(str(row["date"]), paths)
        out_rows.append(
            {
                "date": row["date"],
                "settlement_file": str(paths["settlement_file"]) if facts["settlement_file_exists"] else "",
                "stake_available": facts["stake_available"],
                "return_available": facts["return_available"],
                "payout_available": facts["payout_available"],
                "roi_available": facts["roi_available"],
                "settled_bet_count": facts["settled_bet_count"],
                "monetary_settlement_available": facts["monetary_settlement_available"],
                "note": facts["settlement_note"] or "none",
            }
        )
    pd.DataFrame(out_rows).to_csv(csv_path, index=False, encoding="utf-8-sig")
    md_path.write_text(
        "\n".join(
            [
                "# monetary settlement audit",
                "",
                _markdown_table(
                    out_rows,
                    [
                        ("date", "date"),
                        ("stake_available", "stake"),
                        ("payout_available", "payout"),
                        ("roi_available", "roi"),
                        ("settled_bet_count", "settled"),
                        ("monetary_settlement_available", "monetary_ok"),
                        ("note", "note"),
                    ],
                ),
            ]
        ),
        encoding="utf-8",
    )
    return csv_path, md_path


def _normalize_candidates(date_text: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    paths = _daily_paths(date_text)
    prediction_sheet = _read_json(paths["prediction_sheet"])
    daily_summary = _read_json(paths["daily_summary"])
    prediction_review = _read_json(paths["prediction_review"])
    consensus_sheet = _read_json(paths["consensus_sheet"])
    candidates = prediction_sheet.get("candidates", [])
    df = pd.DataFrame(candidates if isinstance(candidates, list) else [])
    if df.empty:
        return pd.DataFrame(), {"results_status": "", "review_status": prediction_review.get("status", "")}

    rename_map = {
        "raceId": "race_id",
        "raceNo": "race_no",
        "finalDecision": "final_decision",
        "paperDecision": "paper_decision",
        "stopReason": "stop_reason",
        "oddsStatus": "odds_status",
        "approxProb": "approx_prob",
        "realOdds": "real_odds",
        "expectedValue": "expected_value",
        "consensusGrade": "consensus_grade",
        "consensusScore": "consensus_score",
        "consensusReason": "consensus_reason",
        "matchedSources": "matched_sources",
        "exactMatchSources": "exact_match_sources",
        "axisMatchSources": "axis_match_sources",
        "boxOverlapSources": "box_overlap_sources",
    }
    df = df.rename(columns=rename_map)
    for col in (
        "date",
        "venue",
        "jcd",
        "race_no",
        "race_id",
        "combo",
        "final_decision",
        "paper_decision",
        "stop_reason",
        "odds_status",
        "approx_prob",
        "real_odds",
        "expected_value",
        "consensus_grade",
        "consensus_score",
        "consensus_reason",
    ):
        if col not in df.columns:
            df[col] = ""
    df["date"] = df["date"].replace("", date_text).fillna(date_text)
    df["race_no"] = pd.to_numeric(df["race_no"], errors="coerce").fillna(0).astype(int)
    df["approx_prob"] = pd.to_numeric(df["approx_prob"], errors="coerce")
    df["real_odds"] = pd.to_numeric(df["real_odds"], errors="coerce")
    df["expected_value"] = pd.to_numeric(df["expected_value"], errors="coerce")
    df["consensus_score"] = pd.to_numeric(df["consensus_score"], errors="coerce").fillna(0).astype(int)
    df["consensus_grade"] = df["consensus_grade"].apply(_canonical_grade)
    df["paper_decision"] = df["paper_decision"].astype(str).str.upper()
    df["final_decision"] = df["final_decision"].astype(str).str.upper()
    df["stop_reason"] = df["stop_reason"].astype(str)
    df["odds_status"] = df["odds_status"].astype(str)
    df["hard_guard_blocked"] = (
        df["stop_reason"].str.contains("hard_guard", case=False, na=False)
        | df.get("reason", pd.Series("", index=df.index)).astype(str).str.contains("hard_guard", case=False, na=False)
    )
    df["pending_odds"] = df["odds_status"].str.contains("pending", case=False, na=False)
    df["real_odds_available"] = df["real_odds"].fillna(0).gt(0) & ~df["pending_odds"]

    if consensus_sheet:
        cdf = pd.DataFrame(consensus_sheet.get("candidates", []))
        if not cdf.empty:
            cdf = cdf.rename(
                columns={
                    "race_id": "race_id",
                    "race_no": "race_no",
                    "consensus_grade": "consensus_grade_external",
                    "consensus_score": "consensus_score_external",
                    "consensus_reason": "consensus_reason_external",
                    "matched_sources": "matched_sources_external",
                }
            )
            keep_cols = [col for col in ("race_id", "consensus_grade_external", "consensus_score_external", "consensus_reason_external", "matched_sources_external") if col in cdf.columns]
            if keep_cols:
                df = df.merge(cdf[keep_cols].drop_duplicates(subset=["race_id"]), on="race_id", how="left")
                df["consensus_grade"] = df["consensus_grade"].mask(
                    df["consensus_grade"].eq("NONE") & df.get("consensus_grade_external", "").notna(),
                    df.get("consensus_grade_external", "NONE"),
                ).apply(_canonical_grade)
                df["consensus_score"] = pd.to_numeric(df["consensus_score"], errors="coerce").fillna(
                    pd.to_numeric(df.get("consensus_score_external"), errors="coerce")
                ).fillna(0).astype(int)
                df["consensus_reason"] = df["consensus_reason"].mask(
                    df["consensus_reason"].astype(str).eq("") & df.get("consensus_reason_external", "").notna(),
                    df.get("consensus_reason_external", ""),
                )

    daily_eval = _read_csv(paths["daily_eval"])
    if not daily_eval.empty:
        eval_cols = {
            "race_id": "race_id",
            "predicted_trifecta": "combo",
            "actual_trifecta": "actual_trifecta",
            "result_available": "result_available",
            "hit": "hit",
            "settled_odds": "settled_odds",
            "official_odds": "official_odds",
            "stake_amount": "stake_amount",
            "payout_amount": "payout_amount",
            "pnl": "pnl",
        }
        keep_cols = [src for src in eval_cols if src in daily_eval.columns]
        eval_df = daily_eval[keep_cols].rename(columns=eval_cols).copy()
        eval_df["race_id"] = eval_df["race_id"].astype(str)
        eval_df["combo"] = eval_df["combo"].astype(str)
        eval_df["actual_trifecta"] = eval_df.get("actual_trifecta", "").fillna("").astype(str)
        df = df.merge(eval_df, on=["race_id", "combo"], how="left")
    else:
        for col in ("actual_trifecta", "result_available", "hit", "settled_odds", "official_odds", "stake_amount", "payout_amount", "pnl"):
            df[col] = pd.NA

    results_status = _canonical_result_status_label(
        daily_summary.get("results_status")
        or daily_summary.get("resultsStatus")
        or prediction_review.get("status")
        or ""
    )
    if not results_status and daily_summary.get("results_available") is True:
        results_status = "available"
    review_status = str(prediction_review.get("status") or "").strip()

    df["actual_trifecta"] = df["actual_trifecta"].fillna("").astype(str)
    df["result_available"] = df["result_available"].apply(_boolish) | df["actual_trifecta"].ne("")
    df["hit"] = df["hit"].apply(_boolish) | (df["result_available"] & df["combo"].astype(str).eq(df["actual_trifecta"]))
    for col in ("settled_odds", "official_odds", "stake_amount", "payout_amount", "pnl"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    status_values: list[str] = []
    for _, row in df.iterrows():
        if bool(row.get("result_available")):
            status_values.append("hit" if bool(row.get("hit")) else "miss")
        elif results_status in MISSING_RESULT_STATUSES:
            status_values.append(results_status)
        elif review_status in MISSING_RESULT_STATUSES:
            status_values.append(review_status)
        elif results_status in READY_RESULT_STATUSES:
            status_values.append("pending")
        else:
            status_values.append("unavailable")
    df["canonical_result_status"] = status_values
    return df, {"results_status": results_status, "review_status": review_status}


def _apply_caps(df: pd.DataFrame, max_candidates_per_race: int, max_candidates_per_day: int) -> pd.DataFrame:
    if df.empty:
        return df
    if max_candidates_per_race > 0:
        df = (
            df.sort_values(["sort_consensus", "expected_value", "approx_prob"], ascending=[False, False, False], na_position="last")
            .groupby("race_id", dropna=False, as_index=False)
            .head(max_candidates_per_race)
        )
    if max_candidates_per_day > 0:
        df = df.sort_values(["sort_consensus", "expected_value", "approx_prob"], ascending=[False, False, False], na_position="last").head(max_candidates_per_day)
    return df.reset_index(drop=True)


def _select_variant(df: pd.DataFrame, rule: dict[str, Any], current_max_ev: float) -> pd.DataFrame:
    if df.empty:
        return df
    name = str(rule.get("name"))
    selected = df.copy()
    if name in {"current_active", "stress_missing_as_loss"}:
        selected = selected[selected["final_decision"].eq("BUY")]
    else:
        allowed_paper = {"BUY"}
        if _boolish(rule.get("include_watch")):
            allowed_paper.add("WATCH")
        if _boolish(rule.get("include_paper")):
            allowed_paper.add("PAPER")
        selected = selected[selected["paper_decision"].isin(allowed_paper) | selected["final_decision"].eq("BUY")]
        min_ev = _safe_float(rule.get("min_expected_value"))
        min_prob = _safe_float(rule.get("min_approx_prob"))
        if min_ev is not None:
            selected = selected[selected["expected_value"].fillna(-9999).ge(min_ev)]
        if min_prob is not None:
            selected = selected[selected["approx_prob"].fillna(-9999).ge(min_prob)]
        selected = selected[selected["expected_value"].fillna(current_max_ev + 9999).le(current_max_ev)]
        if _boolish(rule.get("require_real_odds")):
            selected = selected[selected["real_odds_available"]]
        if not _boolish(rule.get("allow_pending_odds")):
            selected = selected[~selected["pending_odds"]]
        if _boolish(rule.get("use_hard_guard")):
            selected = selected[~selected["hard_guard_blocked"]]
        consensus_min_grade = rule.get("consensus_min_grade")
        if consensus_min_grade:
            rank = GRADE_RANK.get(str(consensus_min_grade).upper(), 0)
            selected = selected[selected["consensus_grade"].map(GRADE_RANK).fillna(0).ge(rank)]
    selected["sort_consensus"] = selected["consensus_score"] if _boolish(rule.get("use_consensus_score")) else 0
    return _apply_caps(
        selected,
        int(rule.get("max_candidates_per_race") or 0),
        int(rule.get("max_candidates_per_day") or 0),
    )


def _counter_dict(values: pd.Series) -> dict[str, int]:
    if values.empty:
        return {}
    return {str(key): int(val) for key, val in Counter(values.fillna("").astype(str)).items() if str(key)}


def _max_drawdown(pnl_values: list[float]) -> float | None:
    if not pnl_values:
        return None
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for pnl in pnl_values:
        equity += pnl
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    return float(max_dd)


def _summarize_selection(
    date_text: str,
    variant_name: str,
    validation_type: str,
    rows: pd.DataFrame,
    missing_policy: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    candidate_count = int(len(rows))
    settled_mask = rows["canonical_result_status"].isin(["hit", "miss"]) if not rows.empty else pd.Series(dtype=bool)
    missing_mask = rows["canonical_result_status"].isin(UNRESOLVED_RESULT_STATUSES) if not rows.empty else pd.Series(dtype=bool)
    result_data_missing_mask = rows["canonical_result_status"].isin(MISSING_RESULT_STATUSES) if not rows.empty else pd.Series(dtype=bool)
    hit_mask = rows["canonical_result_status"].eq("hit") if not rows.empty else pd.Series(dtype=bool)

    settled = rows[settled_mask].copy()
    settled_bet_count = int(settled_mask.sum())
    hit_count = int(hit_mask.sum())
    unresolved_count = int((~settled_mask & ~result_data_missing_mask).sum()) if not rows.empty else 0
    result_data_missing_count = int(result_data_missing_mask.sum()) if not rows.empty else 0
    missing_count = int(missing_mask.sum()) if not rows.empty else 0

    stake = settled["stake_amount"].fillna(0.0) if not settled.empty else pd.Series(dtype=float)
    payout = settled["payout_amount"].fillna(0.0) if not settled.empty else pd.Series(dtype=float)
    if settled_bet_count > 0 and float(stake.sum()) <= 0.0 and float(payout.sum()) <= 0.0:
        total_stake = None
        total_return = None
        roi_canonical = None
        warning = "monetary_settlement_unavailable"
    elif settled_bet_count > 0:
        total_stake = float(stake.sum())
        total_return = float(payout.sum())
        roi_canonical = None if total_stake <= 0 else (total_return / total_stake - 1.0)
        warning = ""
    else:
        total_stake = None
        total_return = None
        roi_canonical = None
        warning = "no_settled_candidates" if candidate_count > 0 else "no_candidates"

    stake_all = settled["stake_amount"].fillna(0.0) if not settled.empty else pd.Series(dtype=float)
    if candidate_count > 0 and (stake_all.empty or float(stake_all.sum()) <= 0.0):
        roi_missing_as_loss = None
        hit_rate_missing_as_loss = hit_count / candidate_count if candidate_count > 0 else None
    elif candidate_count > 0:
        stress_stake = float(stake_all.sum()) + max(candidate_count - settled_bet_count, 0) * 100.0
        stress_return = float(payout.sum())
        roi_missing_as_loss = None if stress_stake <= 0 else (stress_return / stress_stake - 1.0)
        hit_rate_missing_as_loss = hit_count / candidate_count
    else:
        roi_missing_as_loss = None
        hit_rate_missing_as_loss = None

    summary = {
        "variant": variant_name,
        "date": date_text,
        "validation_type": validation_type,
        "candidateCount": candidate_count,
        "settledBetCount": settled_bet_count,
        "unresolvedCount": unresolved_count,
        "resultDataMissingCount": result_data_missing_count,
        "missingCount": missing_count,
        "hitCount": hit_count,
        "hitRate": None if settled_bet_count <= 0 else hit_count / settled_bet_count,
        "hitRate_canonical": None if settled_bet_count <= 0 else hit_count / settled_bet_count,
        "hitRate_missing_as_loss": hit_rate_missing_as_loss,
        "roi": roi_canonical,
        "roi_canonical": roi_canonical,
        "roi_missing_as_loss": roi_missing_as_loss,
        "totalStake": total_stake,
        "totalReturn": total_return,
        "averageExpectedValue": None if rows.empty else _safe_float(rows["expected_value"].dropna().mean()),
        "averageApproxProb": None if rows.empty else _safe_float(rows["approx_prob"].dropna().mean()),
        "averageRealOdds": None if rows.empty else _safe_float(rows["real_odds"].dropna().mean()),
        "coverage": None if candidate_count <= 0 else settled_bet_count / candidate_count,
        "stopReasonDistribution": _counter_dict(rows["stop_reason"]) if "stop_reason" in rows.columns else {},
        "oddsStatusDistribution": _counter_dict(rows["odds_status"]) if "odds_status" in rows.columns else {},
        "consensusGradeDistribution": _counter_dict(rows["consensus_grade"]) if "consensus_grade" in rows.columns else {},
        "paperDecisionDistribution": _counter_dict(rows["paper_decision"]) if "paper_decision" in rows.columns else {},
        "maxDrawdown": _max_drawdown([float(v) for v in settled["pnl"].fillna(0.0).tolist()]) if not settled.empty else None,
        "warning": warning,
        "missingPolicy": missing_policy,
    }
    details: list[dict[str, Any]] = []
    for _, row in rows.sort_values(["consensus_score", "expected_value", "approx_prob"], ascending=[False, False, False], na_position="last").head(10).iterrows():
        details.append(
            {
                "date": date_text,
                "variant": variant_name,
                "venue": row.get("venue", ""),
                "raceNo": int(row.get("race_no") or 0),
                "raceId": row.get("race_id", ""),
                "combo": row.get("combo", ""),
                "paperDecision": row.get("paper_decision", ""),
                "finalDecision": row.get("final_decision", ""),
                "consensusGrade": row.get("consensus_grade", "NONE"),
                "consensusScore": int(row.get("consensus_score") or 0),
                "expectedValue": _safe_float(row.get("expected_value")),
                "approxProb": _safe_float(row.get("approx_prob")),
                "realOdds": _safe_float(row.get("real_odds")),
                "resultStatus": row.get("canonical_result_status", ""),
                "stopReason": row.get("stop_reason", ""),
            }
        )
    return summary, details


def _segment_selectors() -> dict[str, Any]:
    return {
        "WATCH_all": lambda df: df[df["paper_decision"].eq("WATCH")],
        "PAPER_all": lambda df: df[df["paper_decision"].eq("PAPER")],
        "consensus_B_plus": lambda df: df[df["consensus_grade"].map(GRADE_RANK).fillna(0).ge(GRADE_RANK["B"])],
        "WATCH_consensus_B_plus": lambda df: df[df["paper_decision"].eq("WATCH") & df["consensus_grade"].map(GRADE_RANK).fillna(0).ge(GRADE_RANK["B"])],
        "PAPER_consensus_B_plus": lambda df: df[df["paper_decision"].eq("PAPER") & df["consensus_grade"].map(GRADE_RANK).fillna(0).ge(GRADE_RANK["B"])],
        "hard_guard_stopped": lambda df: df[df["hard_guard_blocked"]],
        "real_odds_pending_before_deadline": lambda df: df[df["odds_status"].astype(str).str.contains("real_odds_pending_before_deadline", case=False, na=False)],
    }


def run_shadow_experiments(start_date: str, end_date: str, label: str = "") -> dict[str, Any]:
    coverage_rows = build_backtest_coverage(start_date, end_date)
    coverage_csv_path, coverage_md_path = _write_coverage_reports(coverage_rows, label)
    current_active_csv_path, current_active_md_path = _write_current_active_candidate_days(coverage_rows)
    monetary_csv_path, monetary_md_path = _write_monetary_settlement_audit(coverage_rows)

    rules = _load_shadow_rules(SHADOW_CONFIG_PATH)
    strategy = _load_strategy_config()
    current_max_ev = float(strategy.get("buy_rules", {}).get("max_ev", 3.6))

    per_date_outputs: dict[str, Any] = {}
    summary_rows: list[dict[str, Any]] = []
    variant_frames: dict[str, list[pd.DataFrame]] = {rule["name"]: [] for rule in rules}
    segment_frames: dict[str, list[pd.DataFrame]] = {name: [] for name in _segment_selectors()}

    for coverage in coverage_rows:
        date_text = str(coverage["date"])
        if not coverage["prediction_sheet_exists"]:
            continue
        df, meta = _normalize_candidates(date_text)
        if df.empty:
            continue

        validation_type = str(coverage["validation_type"])
        date_variants: list[dict[str, Any]] = []
        date_details: dict[str, list[dict[str, Any]]] = {}
        for rule in rules:
            selected = _select_variant(df, rule, current_max_ev)
            variant_frames[rule["name"]].append(selected.assign(_variant=rule["name"], _date=date_text))
            missing_policy = "stress_missing_as_loss" if rule["name"] == "stress_missing_as_loss" else "canonical"
            summary, details = _summarize_selection(date_text, rule["name"], validation_type, selected, missing_policy)
            date_variants.append(summary)
            date_details[rule["name"]] = details
            summary_rows.append(summary)

        for segment_name, selector in _segment_selectors().items():
            segment_frames[segment_name].append(selector(df).assign(_variant=segment_name, _date=date_text))

        out_dir = REPORTS_SHADOW_ROOT / date_text
        out_dir.mkdir(parents=True, exist_ok=True)
        csv_path = out_dir / "shadow_buy_rules.csv"
        json_path = out_dir / "shadow_buy_rules.json"
        md_path = out_dir / "shadow_buy_rules.md"
        pd.DataFrame(date_variants).to_csv(csv_path, index=False, encoding="utf-8-sig")
        json_path.write_text(
            json.dumps(
                {
                    "date": date_text,
                    "validation_type": validation_type,
                    "results_status": meta["results_status"],
                    "review_status": meta["review_status"],
                    "variants": date_variants,
                    "topCandidates": date_details,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        md_lines = [
            f"# shadow buy rules {date_text}",
            "",
            f"- validation_type: {validation_type}",
            f"- results_status: {meta['results_status'] or '-'}",
            f"- review_status: {meta['review_status'] or '-'}",
            "",
            "## variant summary",
            "",
            _markdown_table(
                date_variants,
                [
                    ("variant", "variant"),
                    ("candidateCount", "candidate"),
                    ("settledBetCount", "settled"),
                    ("unresolvedCount", "unresolved"),
                    ("hitRate_canonical", "hitRate"),
                    ("roi_canonical", "roi"),
                    ("coverage", "coverage"),
                    ("warning", "warning"),
                ],
            ),
            "",
            "## top candidates",
            "",
        ]
        for rule in rules:
            md_lines.append(f"### {rule['name']}")
            md_lines.append("")
            md_lines.append(
                _markdown_table(
                    date_details[rule["name"]],
                    [
                        ("venue", "venue"),
                        ("raceNo", "R"),
                        ("combo", "combo"),
                        ("paperDecision", "paper"),
                        ("consensusGrade", "grade"),
                        ("expectedValue", "ev"),
                        ("resultStatus", "result"),
                        ("stopReason", "stop_reason"),
                    ],
                )
            )
            md_lines.append("")
        md_path.write_text("\n".join(md_lines), encoding="utf-8")
        per_date_outputs[date_text] = {"csv": str(csv_path), "json": str(json_path), "md": str(md_path)}

    REPORTS_SHADOW_ROOT.mkdir(parents=True, exist_ok=True)
    summary_dir = REPORTS_SHADOW_ROOT / "summary"
    summary_dir.mkdir(parents=True, exist_ok=True)

    aggregate_rows: list[dict[str, Any]] = []
    for rule in rules:
        frames = variant_frames[rule["name"]]
        merged = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        summary, details = _summarize_selection(
            f"{start_date}..{end_date}",
            rule["name"],
            "mixed",
            merged,
            "stress_missing_as_loss" if rule["name"] == "stress_missing_as_loss" else "canonical",
        )
        summary["dateCount"] = int(len([row for row in coverage_rows if row["prediction_sheet_exists"]]))
        summary["usableFrozenDates"] = int(sum(1 for row in coverage_rows if row["usable_for_frozen_validation"]))
        aggregate_rows.append(summary)

    segment_rows: list[dict[str, Any]] = []
    for name, frames in segment_frames.items():
        merged = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        summary, _ = _summarize_selection(f"{start_date}..{end_date}", name, "mixed", merged, "canonical")
        segment_rows.append(summary)

    suffix = _suffix(label)
    summary_csv_path = summary_dir / f"shadow_buy_rules_summary{suffix}.csv"
    summary_json_path = summary_dir / f"shadow_buy_rules_summary{suffix}.json"
    summary_md_path = summary_dir / f"shadow_buy_rules_summary{suffix}.md"
    pd.DataFrame(aggregate_rows).to_csv(summary_csv_path, index=False, encoding="utf-8-sig")
    summary_json_path.write_text(
        json.dumps(
            {
                "period": {"startDate": start_date, "endDate": end_date},
                "coverage": coverage_rows,
                "variants": aggregate_rows,
                "segments": segment_rows,
                "perDateOutputs": per_date_outputs,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    summary_md_lines = [
        f"# shadow buy rules summary {start_date}..{end_date}",
        "",
        "## variants",
        "",
        _markdown_table(
            aggregate_rows,
            [
                ("variant", "variant"),
                ("candidateCount", "candidate"),
                ("settledBetCount", "settled"),
                ("hitRate_canonical", "hitRate"),
                ("roi_canonical", "roi"),
                ("coverage", "coverage"),
                ("warning", "warning"),
            ],
        ),
        "",
        "## segments",
        "",
        _markdown_table(
            segment_rows,
            [
                ("variant", "segment"),
                ("candidateCount", "candidate"),
                ("settledBetCount", "settled"),
                ("hitRate_canonical", "hitRate"),
                ("coverage", "coverage"),
                ("warning", "warning"),
            ],
        ),
    ]
    summary_md_path.write_text("\n".join(summary_md_lines), encoding="utf-8")

    usable_dates = [row["date"] for row in coverage_rows if row["prediction_sheet_exists"]]
    unusable_dates = [row["date"] for row in coverage_rows if not row["prediction_sheet_exists"]]
    current_active_days = [row["date"] for row in coverage_rows if int(row["current_active_candidate_count"] or 0) > 0]
    roi_days = [row["date"] for row in coverage_rows if bool(row["monetary_settlement_available"])]
    hit_only_days = [row["date"] for row in coverage_rows if row["results_status"] in READY_RESULT_STATUSES and not bool(row["monetary_settlement_available"])]
    frozen_live_count = sum(1 for row in coverage_rows if row["validation_type"] == "frozen_live_validation")
    historical_count = sum(1 for row in coverage_rows if row["validation_type"] == "historical_simulation")
    result_missing_count = sum(1 for row in coverage_rows if row["validation_type"] == "result_data_missing")

    current_active = next((row for row in aggregate_rows if row["variant"] == "current_active"), None)
    relaxed_ev = next((row for row in aggregate_rows if row["variant"] == "relaxed_ev_shadow"), None)
    relaxed_probability = next((row for row in aggregate_rows if row["variant"] == "relaxed_probability_shadow"), None)
    consensus_shadow = next((row for row in aggregate_rows if row["variant"] == "consensus_assisted_shadow"), None)
    no_hard_guard = next((row for row in aggregate_rows if row["variant"] == "no_hard_guard_shadow"), None)
    watch_paper_top = next((row for row in aggregate_rows if row["variant"] == "watch_paper_top_shadow"), None)
    watch_all = next((row for row in segment_rows if row["variant"] == "WATCH_all"), None)
    paper_all = next((row for row in segment_rows if row["variant"] == "PAPER_all"), None)
    consensus_b_plus = next((row for row in segment_rows if row["variant"] == "consensus_B_plus"), None)
    real_odds_pending_segment = next((row for row in segment_rows if row["variant"] == "real_odds_pending_before_deadline"), None)

    classification = "DATA_INSUFFICIENT"
    total_settled = sum(int(row.get("settledBetCount") or 0) for row in aggregate_rows)
    if not current_active_days:
        classification = "CURRENT_ACTIVE_NO_SAMPLE"
    elif total_settled <= 0:
        classification = "DATA_INSUFFICIENT"
    elif not roi_days:
        classification = "ROI_DATA_INSUFFICIENT"
    else:
        classification = "PRODUCTION_CHANGE_NOT_ALLOWED_YET"

    historical_report = {
        "period": {"startDate": start_date, "endDate": end_date},
        "usableDates": usable_dates,
        "unusableDates": unusable_dates,
        "frozenLiveValidationCount": frozen_live_count,
        "historicalSimulationCount": historical_count,
        "resultDataMissingCount": result_missing_count,
        "coverageCsv": str(coverage_csv_path),
        "coverageMd": str(coverage_md_path),
        "currentActiveCandidateCsv": str(current_active_csv_path),
        "currentActiveCandidateMd": str(current_active_md_path),
        "monetarySettlementAuditCsv": str(monetary_csv_path),
        "monetarySettlementAuditMd": str(monetary_md_path),
        "classification": classification,
    }
    shadow_report = {
        "period": {"startDate": start_date, "endDate": end_date},
        "usableDates": usable_dates,
        "unusableDates": unusable_dates,
        "currentActiveCandidateDays": current_active_days,
        "monetarySettlementDays": roi_days,
        "hitOnlyDays": hit_only_days,
        "frozenLiveValidationCount": frozen_live_count,
        "historicalSimulationCount": historical_count,
        "resultDataMissingCount": result_missing_count,
        "currentActive": current_active,
        "relaxedEvShadow": relaxed_ev,
        "relaxedProbabilityShadow": relaxed_probability,
        "consensusAssistedShadow": consensus_shadow,
        "noHardGuardShadow": no_hard_guard,
        "watchPaperTopShadow": watch_paper_top,
        "watchAll": watch_all,
        "paperAll": paper_all,
        "consensusBPlus": consensus_b_plus,
        "realOddsPendingBeforeDeadline": real_odds_pending_segment,
        "stressMissingAsLoss": [
            {
                "variant": row["variant"],
                "roi_missing_as_loss": row["roi_missing_as_loss"],
                "hitRate_missing_as_loss": row["hitRate_missing_as_loss"],
                "missingCount": row["missingCount"],
            }
            for row in aggregate_rows
        ],
        "productionChangeAllowed": False,
        "productionChangeReason": "live settled sample と canonical coverage が不足しているため proposal 止まり。",
        "classification": classification,
        "summaryOutputs": {
            "csv": str(summary_csv_path),
            "json": str(summary_json_path),
            "md": str(summary_md_path),
        },
        "coverageOutputs": {
            "coverageCsv": str(coverage_csv_path),
            "coverageMd": str(coverage_md_path),
            "currentActiveCandidateCsv": str(current_active_csv_path),
            "currentActiveCandidateMd": str(current_active_md_path),
            "monetarySettlementAuditCsv": str(monetary_csv_path),
            "monetarySettlementAuditMd": str(monetary_md_path),
        },
    }

    REPORTS_AUDIT_ROOT.mkdir(parents=True, exist_ok=True)
    historical_json_path = REPORTS_AUDIT_ROOT / "historical_validation_start.json"
    historical_md_path = REPORTS_AUDIT_ROOT / "historical_validation_start.md"
    shadow_json_path = REPORTS_AUDIT_ROOT / f"shadow_buy_rule{suffix}_result.json" if label else REPORTS_AUDIT_ROOT / "shadow_buy_rule_experiment_result.json"
    shadow_md_path = REPORTS_AUDIT_ROOT / f"shadow_buy_rule{suffix}_result.md" if label else REPORTS_AUDIT_ROOT / "shadow_buy_rule_experiment_result.md"

    historical_json_path.write_text(json.dumps(historical_report, ensure_ascii=False, indent=2), encoding="utf-8")
    historical_md_path.write_text(
        "\n".join(
            [
                "# historical validation start",
                "",
                f"- period: {start_date}..{end_date}",
                f"- usable_dates: {', '.join(usable_dates) if usable_dates else '-'}",
                f"- unusable_dates: {', '.join(unusable_dates) if unusable_dates else '-'}",
                f"- current_active_candidate_days: {', '.join(current_active_days) if current_active_days else '-'}",
                f"- monetary_settlement_days: {', '.join(roi_days) if roi_days else '-'}",
                f"- frozen_live_validation_count: {frozen_live_count}",
                f"- historical_simulation_count: {historical_count}",
                f"- result_data_missing_count: {result_missing_count}",
                f"- classification: {classification}",
            ]
        ),
        encoding="utf-8",
    )
    shadow_json_path.write_text(json.dumps(shadow_report, ensure_ascii=False, indent=2), encoding="utf-8")
    shadow_md_path.write_text(
        "\n".join(
            [
                "# shadow buy rule experiment result",
                "",
                f"- period: {start_date}..{end_date}",
                f"- classification: {classification}",
                f"- production_change_allowed: false",
                f"- production_change_reason: {shadow_report['productionChangeReason']}",
                f"- current_active_candidate_days: {', '.join(current_active_days) if current_active_days else '-'}",
                f"- monetary_settlement_days: {', '.join(roi_days) if roi_days else '-'}",
                "",
                "## key metrics",
                "",
                f"- current_active_candidate_count: {current_active.get('candidateCount', 0) if current_active else 0}",
                f"- current_active_settledBetCount: {current_active.get('settledBetCount', 0) if current_active else 0}",
                f"- current_active_roi: {current_active.get('roi_canonical') if current_active else None}",
                f"- watch_paper_top_shadow_candidate_count: {watch_paper_top.get('candidateCount', 0) if watch_paper_top else 0}",
                f"- real_odds_pending_before_deadline_candidate_count: {real_odds_pending_segment.get('candidateCount', 0) if real_odds_pending_segment else 0}",
                "",
                "## variants",
                "",
                _markdown_table(
                    aggregate_rows,
                    [
                        ("variant", "variant"),
                        ("candidateCount", "candidate"),
                        ("settledBetCount", "settled"),
                        ("hitRate_canonical", "hitRate"),
                        ("roi_canonical", "roi"),
                        ("roi_missing_as_loss", "roi_missing_as_loss"),
                        ("coverage", "coverage"),
                    ],
                ),
                "",
                "## segments",
                "",
                _markdown_table(
                    segment_rows,
                    [
                        ("variant", "segment"),
                        ("candidateCount", "candidate"),
                        ("settledBetCount", "settled"),
                        ("hitRate_canonical", "hitRate"),
                        ("coverage", "coverage"),
                    ],
                ),
            ]
        ),
        encoding="utf-8",
    )

    return {
        "coverage": coverage_rows,
        "variants": aggregate_rows,
        "segments": segment_rows,
        "classification": classification,
        "usableDates": usable_dates,
        "unusableDates": unusable_dates,
        "currentActiveCandidateDays": current_active_days,
        "monetarySettlementDays": roi_days,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run shadow BUY rule experiments from saved artifacts.")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--label", default="")
    args = parser.parse_args()

    result = run_shadow_experiments(args.start_date, args.end_date, args.label)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
