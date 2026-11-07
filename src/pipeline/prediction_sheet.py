from __future__ import annotations

import json
from collections import Counter
import csv
import hashlib
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from src.predict.baseline_score_model import MODEL_VERSION
from src.pipeline.candidate_metadata import (
    DEFAULT_FEATURE_VERSION,
    DEFAULT_POLICY_VERSION,
    assert_unique_candidate_ids,
    enrich_candidate_metadata,
    resolve_deadline_at,
)
from src.utils.date_paths import (
    compact_date_str,
    find_existing_daily_report_dir,
    get_daily_report_dir,
    list_daily_report_dirs,
    parse_daily_dir_date,
    normalize_date_str,
)


ROOT = Path(__file__).resolve().parents[2]
REPORTS_DAILY_ROOT = ROOT / "reports" / "daily"
REPORTS_PREDICTIONS_ROOT = ROOT / "reports" / "predictions"
REPORTS_CONSENSUS_ROOT = ROOT / "reports" / "consensus"
REPO_AUDIT_ROOT = ROOT / "reports" / "repo_audit"
UI_ROOT = ROOT / "data" / "ui"

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


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except Exception:
        return pd.DataFrame()


def _safe_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        if isinstance(value, float) and pd.isna(value):
            return None
        text = str(value).strip()
        if not text:
            return None
        num = pd.to_numeric(text, errors="coerce")
        if pd.isna(num):
            return None
        return float(num)
    except Exception:
        return None


def _safe_int(value: object) -> int | None:
    num = _safe_float(value)
    if num is None:
        return None
    try:
        return int(num)
    except Exception:
        return None


def _today_iso() -> str:
    return date.today().isoformat()


def _compact(value: str) -> str:
    return compact_date_str(value)


def _stable_hash_payload(row: dict[str, Any]) -> str:
    payload = {
        "combo": str(row.get("combo") or "").strip(),
        "decision": str(row.get("final_decision") or row.get("decision") or "").upper(),
        "prob": row.get("approx_prob") if row.get("approx_prob") is not None else row.get("prob"),
        "odds": row.get("real_odds") if row.get("real_odds") is not None else row.get("odds"),
        "expectedValue": row.get("expected_value") if row.get("expected_value") is not None else row.get("expectedValue"),
        "edge": row.get("edge"),
        "rank": row.get("confidence_rank") if row.get("confidence_rank") is not None else row.get("rank"),
        "probRank": row.get("probRank") or row.get("prob_rank"),
        "evRank": row.get("evRank") or row.get("ev_rank"),
        "reason": row.get("reason") or "",
        "modelVersion": row.get("modelVersion") or row.get("model_version") or "",
        "stage": row.get("stage") or "",
    }
    return hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _load_final_goal_progress() -> dict[str, Any]:
    return _load_json(REPO_AUDIT_ROOT / "final_goal_progress.json")


def _load_health_check() -> dict[str, Any]:
    return _load_json(REPO_AUDIT_ROOT / "health_check.json")


def _latest_ready_date() -> str:
    health = _load_health_check()
    ready = str(health.get("latest_complete_ops_date") or "").strip()
    if ready:
        return normalize_date_str(ready)
    progress = _load_final_goal_progress()
    ready = str(progress.get("latest_complete_ops_date") or "").strip()
    if ready:
        return normalize_date_str(ready)
    daily_dirs = list_daily_report_dirs(REPORTS_DAILY_ROOT)
    dates: list[str] = []
    for d in daily_dirs:
        skip = d / "skip_decisions.csv"
        if skip.exists():
            dates.append(d.name)
    if dates:
        return max(dates)
    return _today_iso()


def _load_preflight(date_text: str) -> dict[str, Any]:
    normalized = normalize_date_str(date_text)
    daily_dir = find_existing_daily_report_dir(normalized, REPORTS_DAILY_ROOT)
    return _load_json(daily_dir / "preflight_source_check.json")


def _latest_prediction_date_from_root(root: Path) -> str:
    dates: list[str] = []
    if not root.exists():
        return ""
    for p in root.iterdir():
        if not p.is_dir():
            continue
        normalized = parse_daily_dir_date(p.name)
        if not normalized:
            continue
        sheet = p / "prediction_sheet.json"
        if sheet.exists():
            dates.append(normalized)
    return max(dates) if dates else ""


def latest_prediction_date() -> str:
    ui_latest = _latest_prediction_date_from_root(UI_ROOT)
    report_latest = _latest_prediction_date_from_root(REPORTS_PREDICTIONS_ROOT)
    candidates = [d for d in (ui_latest, report_latest) if d]
    return max(candidates) if candidates else ""


def latest_prediction_sheet_path() -> Path | None:
    latest = latest_prediction_date()
    if not latest:
        return None
    ui_path = UI_ROOT / _compact(latest) / "prediction_sheet.json"
    if ui_path.exists():
        return ui_path
    report_path = REPORTS_PREDICTIONS_ROOT / latest / "prediction_sheet.json"
    if report_path.exists():
        return report_path
    return None


def _load_sheet(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _default_consensus_fields() -> dict[str, Any]:
    return {
        "consensusGrade": "NONE",
        "consensusScore": 0,
        "matchedSources": [],
        "exactMatchSources": [],
        "axisMatchSources": [],
        "firstSecondAxisMatchSources": [],
        "boxOverlapSources": [],
        "consensusReason": "外部予想なし",
        "externalSourceCount": 0,
        "exactComboMatch": 0,
        "firstAxisMatch": 0,
        "firstSecondAxisMatch": 0,
        "boxOverlapMatch": 0,
        "sourceCount": 0,
    }


def _candidate_consensus_key(candidate: dict[str, Any]) -> tuple[str, int, str]:
    jcd = str(candidate.get("jcd") or "").zfill(2)
    race_no = int(candidate.get("raceNo") or candidate.get("race_no") or 0)
    combo = str(candidate.get("combo") or "").strip()
    return jcd, race_no, combo


def _load_consensus_for_date(date_text: str) -> dict[str, Any]:
    normalized = normalize_date_str(date_text)
    ui_path = UI_ROOT / _compact(normalized) / "consensus_sheet.json"
    if ui_path.exists():
        return _load_sheet(ui_path)
    report_path = REPORTS_CONSENSUS_ROOT / normalized / "consensus_sheet.json"
    if report_path.exists():
        return _load_sheet(report_path)
    return {}


def _merge_consensus_into_payload(payload: dict[str, Any], source_date: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return payload
    consensus = _load_consensus_for_date(source_date)
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        return payload
    by_key: dict[tuple[str, int, str], dict[str, Any]] = {}
    for row in consensus.get("candidates") or []:
        if not isinstance(row, dict):
            continue
        key = (
            str(row.get("jcd") or "").zfill(2),
            int(row.get("race_no") or row.get("raceNo") or 0),
            str(row.get("ai_combo") or row.get("combo") or "").strip(),
        )
        by_key[key] = {
            "consensusGrade": row.get("consensusGrade") or row.get("consensus_grade") or "NONE",
            "consensusScore": row.get("consensusScore") if row.get("consensusScore") is not None else row.get("consensus_score") or 0,
            "matchedSources": row.get("matchedSources") or row.get("matched_sources") or [],
            "exactMatchSources": row.get("exactMatchSources") or row.get("exact_match_sources") or [],
            "axisMatchSources": row.get("axisMatchSources") or row.get("axis_match_sources") or [],
            "firstSecondAxisMatchSources": row.get("firstSecondAxisMatchSources") or row.get("first_second_axis_match_sources") or [],
            "boxOverlapSources": row.get("boxOverlapSources") or row.get("box_overlap_sources") or [],
            "consensusReason": row.get("consensusReason") or row.get("consensus_reason") or "",
            "externalSourceCount": row.get("externalSourceCount") if row.get("externalSourceCount") is not None else row.get("external_source_count") or 0,
            "exactComboMatch": row.get("exactComboMatch") if row.get("exactComboMatch") is not None else row.get("exact_combo_match") or 0,
            "firstAxisMatch": row.get("firstAxisMatch") if row.get("firstAxisMatch") is not None else row.get("first_axis_match") or 0,
            "firstSecondAxisMatch": row.get("firstSecondAxisMatch") if row.get("firstSecondAxisMatch") is not None else row.get("first_second_axis_match") or 0,
            "boxOverlapMatch": row.get("boxOverlapMatch") if row.get("boxOverlapMatch") is not None else row.get("box_overlap_match") or 0,
            "sourceCount": row.get("sourceCount") if row.get("sourceCount") is not None else row.get("source_count") or row.get("external_source_count") or 0,
        }
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        candidate.update(by_key.get(_candidate_consensus_key(candidate), _default_consensus_fields()))
    payload["consensusNotice"] = "合意スコアは表示専用。BUY判定・EV計算・予想ロジックには未使用。"
    if consensus.get("summary"):
        payload.setdefault("summary", {})["consensus"] = consensus.get("summary", {})
    return payload


def resolve_prediction_sheet(requested_date: str | None = None) -> dict[str, Any]:
    requested = normalize_date_str(requested_date) if requested_date else _today_iso()
    today = _today_iso()
    fallback_reason = ""
    source_date = ""
    payload: dict[str, Any] = {}

    def load_for(date_text: str) -> dict[str, Any]:
        ui_path = UI_ROOT / _compact(date_text) / "prediction_sheet.json"
        if ui_path.exists():
            return _load_sheet(ui_path)
        report_path = REPORTS_PREDICTIONS_ROOT / date_text / "prediction_sheet.json"
        if report_path.exists():
            return _load_sheet(report_path)
        return {}

    if requested_date:
        payload = load_for(requested)
        if payload:
            source_date = str(payload.get("sourceDate") or payload.get("date") or requested)
            payload = _merge_consensus_into_payload(payload, source_date)
            return {
                "status": "ok",
                "requestedDate": requested,
                "sourceDate": source_date,
                "fallbackReason": "explicit_date",
                "data": payload,
            }
        latest_complete = _latest_ready_date()
        if latest_complete:
            payload = load_for(latest_complete)
            if payload:
                payload = _merge_consensus_into_payload(payload, latest_complete)
                return {
                    "status": "ok",
                    "requestedDate": requested,
                    "sourceDate": latest_complete,
                    "fallbackReason": "fallback_latest_complete_ops",
                    "data": payload,
                }
        latest = latest_prediction_date()
        if latest:
            payload = load_for(latest)
            if payload:
                payload = _merge_consensus_into_payload(payload, latest)
                return {
                    "status": "ok",
                    "requestedDate": requested,
                    "sourceDate": latest,
                    "fallbackReason": "fallback_latest_available",
                    "data": payload,
                }
        return {
            "status": "missing",
            "requestedDate": requested,
            "sourceDate": "",
            "fallbackReason": "missing",
            "data": None,
        }

    preflight = _load_preflight(today)
    if str(preflight.get("sourceClassification") or "") == "ready":
        payload = load_for(today)
        if payload:
            payload = _merge_consensus_into_payload(payload, today)
            return {
                "status": "ok",
                "requestedDate": today,
                "sourceDate": today,
                "fallbackReason": "today_ready",
                "data": payload,
            }
    latest_complete = _latest_ready_date()
    if latest_complete:
        payload = load_for(latest_complete)
        if payload:
            payload = _merge_consensus_into_payload(payload, latest_complete)
            return {
                "status": "ok",
                "requestedDate": today,
                "sourceDate": latest_complete,
                "fallbackReason": "fallback_latest_complete_ops",
                "data": payload,
            }
    latest = latest_prediction_date()
    if latest:
        payload = load_for(latest)
        if payload:
            payload = _merge_consensus_into_payload(payload, latest)
            return {
                "status": "ok",
                "requestedDate": today,
                "sourceDate": latest,
                "fallbackReason": "fallback_latest_available",
                "data": payload,
            }
    return {
        "status": "missing",
        "requestedDate": today,
        "sourceDate": "",
        "fallbackReason": "missing",
        "data": None,
    }


def resolve_consensus_sheet(requested_date: str | None = None) -> dict[str, Any]:
    resolved = resolve_prediction_sheet(requested_date)
    requested = str(resolved.get("requestedDate") or normalize_date_str(requested_date) if requested_date else _today_iso())
    source_date = str(resolved.get("sourceDate") or "")
    fallback_reason = str(resolved.get("fallbackReason") or "")
    if not source_date:
        return {
            "status": "missing",
            "requestedDate": requested,
            "sourceDate": "",
            "fallbackReason": fallback_reason or "missing",
            "data": None,
        }
    payload = _load_consensus_for_date(source_date)
    if payload:
        return {
            "status": "ok",
            "requestedDate": requested,
            "sourceDate": source_date,
            "fallbackReason": fallback_reason,
            "data": payload,
        }
    return {
        "status": "missing",
        "requestedDate": requested,
        "sourceDate": source_date,
        "fallbackReason": fallback_reason or "consensus_missing",
        "data": None,
    }


def resolve_source_date(requested_date: str | None = None) -> tuple[str, str, str]:
    """Return (requested_date, source_date, reason)."""
    if requested_date:
        requested = normalize_date_str(requested_date)
    else:
        requested = _today_iso()
    if requested_date:
        daily_dir = find_existing_daily_report_dir(requested, REPORTS_DAILY_ROOT)
        if (daily_dir / "skip_decisions.csv").exists():
            return requested, requested, "explicit_date"
        fallback = _latest_ready_date()
        return requested, fallback, "fallback_latest_ready"
    today_dir = find_existing_daily_report_dir(requested, REPORTS_DAILY_ROOT)
    preflight = _load_json(today_dir / "preflight_source_check.json")
    if str(preflight.get("sourceClassification") or "") == "ready" and (today_dir / "skip_decisions.csv").exists():
        return requested, requested, "today_ready"
    fallback = _latest_ready_date()
    return requested, fallback, "fallback_latest_ready"


def _extract_race_meta(row: pd.Series) -> tuple[str, str, int | None, str]:
    race_id = str(row.get("race_id") or row.get("raceId") or "").strip()
    if not race_id:
        race_id = str(row.get("race") or "").strip()
    parts = [p for p in race_id.split("-") if p]
    jcd = ""
    race_no = None
    if len(parts) >= 3:
        jcd = parts[1].zfill(2) if parts[1].isdigit() else ""
        race_no = _safe_int(parts[-1])
    venue = JCD_TO_VENUE.get(jcd, "")
    if not venue:
        venue = str(row.get("venue") or row.get("venue_label") or "").strip()
    if not jcd and venue:
        for key, label in JCD_TO_VENUE.items():
            if label == venue:
                jcd = key
                break
    deadline = str(row.get("odds_last_fetched_at") or row.get("deadline") or "").strip()
    if not deadline:
        deadline = str(row.get("betting_deadline") or "").strip()
    return venue, jcd, race_no, race_id


def _extract_expected_value(row: pd.Series) -> float | None:
    for key in ("expected_value", "net_ev", "ev", "gross_return"):
        v = _safe_float(row.get(key))
        if v is not None:
            return v
    return None


def _extract_real_odds(row: pd.Series) -> float | None:
    for key in ("real_odds", "odds"):
        v = _safe_float(row.get(key))
        if v is not None:
            return v
    return None


def _extract_reason(row: pd.Series) -> str:
    parts = [
        str(row.get("stop_reason") or "").strip(),
        str(row.get("skip_reason") or "").strip(),
        str(row.get("reason") or "").strip(),
    ]
    reason = " / ".join([p for p in parts if p])
    return reason or "なし"


def _extract_caution(row: pd.Series, final_decision: str, paper_decision: str) -> str:
    stop_reason = str(row.get("stop_reason") or "").strip()
    odds_status = str(row.get("odds_status") or "").strip().lower()
    if paper_decision == "BUY":
        return "本番BUY"
    if "hard_guard_min_ev" in stop_reason:
        return "EV条件未達"
    if "real_odds_pending_before_deadline" in stop_reason or odds_status in {"pending", "unavailable", "missing"}:
        return "オッズ未確定"
    if str(row.get("risk_flag") or "").strip().lower() in {"true", "1", "yes"}:
        return "リスク注意"
    if final_decision == "BUY":
        return "BUY候補"
    return "紙上検証"


def _paper_score(row: pd.Series) -> float:
    expected_value = _extract_expected_value(row) or 0.0
    approx_prob = _safe_float(row.get("approx_prob")) or 0.0
    odds_status = str(row.get("odds_status") or "").strip().lower()
    risk_flag = str(row.get("risk_flag") or "").strip().lower() in {"true", "1", "yes"}
    score = expected_value * 10.0 + approx_prob * 100.0
    if odds_status in {"usable", "available", "real", "estimated"}:
        score += 20.0
    if odds_status in {"pending", "missing", "unavailable"}:
        score -= 5.0
    if risk_flag:
        score -= 25.0
    stop_reason = str(row.get("stop_reason") or "").strip()
    if "hard_guard_min_ev" in stop_reason:
        score += 4.0
    if "real_odds_pending_before_deadline" in stop_reason:
        score += 2.0
    return score


def _count_by(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counter = Counter(str(item.get(key) or "") for item in items)
    return dict(sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])))


def _snake_to_camel(row: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "venue": row.get("venue") or "",
        "jcd": row.get("jcd") or "",
        "raceNo": row.get("race_no"),
        "raceId": row.get("race_id") or "",
        "deadline": row.get("deadline") or "",
        "combo": row.get("combo") or "",
        "finalDecision": row.get("final_decision") or "",
        "paperDecision": row.get("paper_decision") or "",
        "stopReason": row.get("stop_reason") or "",
        "oddsStatus": row.get("odds_status") or "",
        "approxProb": row.get("approx_prob"),
        "realOdds": row.get("real_odds"),
        "expectedValue": row.get("expected_value"),
        "riskFlag": bool(row.get("risk_flag")),
        "confidenceRank": row.get("confidence_rank"),
        "reason": row.get("reason") or "",
        "caution": row.get("caution") or "",
        "predictionHash": row.get("predictionHash") or "",
    }
    for key in (
        "candidateId",
        "modelVersion",
        "calibratorVersion",
        "policyVersion",
        "snapshotHash",
        "featureVersion",
        "rawProbability",
        "calibratedProbability",
        "oddsCapturedAt",
        "deadlineAt",
        "policyDecision",
        "guardDecision",
        "guardReason",
        "frozenAt",
    ):
        if key in row:
            payload[key] = row[key]
    return payload


def _rows_to_frozen_payload(source_date: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    races: dict[tuple[str, int], dict[str, Any]] = {}
    frozen_at = datetime.now().isoformat(timespec="seconds")
    for row in rows:
        jcd = str(row.get("jcd") or "").zfill(2)
        race_no = _safe_int(row.get("race_no")) or 0
        if not jcd or race_no <= 0:
            continue
        race_key = (jcd, race_no)
        race = races.setdefault(
            race_key,
            {
                "jcd": jcd,
                "venue": row.get("venue") or "",
                "rno": race_no,
                "raceId": row.get("race_id") or "",
                "bets": [],
            },
        )
        bet = dict(row)
        bet.setdefault("sourceType", "live_frozen")
        bet.setdefault("source", "live_frozen")
        bet.setdefault("predictionSource", "frozen")
        bet.setdefault("predictionHash", row.get("predictionHash") or _stable_hash_payload(row))
        bet.setdefault("predictionHashComputed", bet["predictionHash"])
        bet = enrich_candidate_metadata(
            bet,
            race_date=source_date,
            jcd=jcd,
            race_no=race_no,
            race_id=row.get("race_id") or row.get("raceId") or "",
            model_version=row.get("modelVersion") or MODEL_VERSION,
            policy_version=DEFAULT_POLICY_VERSION,
            feature_version=DEFAULT_FEATURE_VERSION,
            odds_captured_at=row.get("odds_captured_at") or row.get("oddsCapturedAt") or row.get("odds_last_fetched_at") or "",
            deadline_at=resolve_deadline_at(source_date, row.get("deadline") or row.get("deadlineAt") or ""),
            frozen_at=frozen_at,
            snapshot_payload={
                "date": source_date,
                "jcd": jcd,
                "raceNo": race_no,
                "raceId": row.get("race_id") or row.get("raceId") or "",
                "featureVersion": DEFAULT_FEATURE_VERSION,
            },
        )
        race["bets"].append(bet)
    for race in races.values():
        assert_unique_candidate_ids(race.get("bets", []))
    return {
        "date": source_date,
        "sourceDate": source_date,
        "sourceType": "live",
        "freezeType": "live",
        "generatedAt": frozen_at,
        "races": [races[key] for key in sorted(races)],
    }


def _write_report_files(
    source_date: str,
    requested_date: str,
    rows: list[dict[str, Any]],
    ui_rows: list[dict[str, Any]],
    summary: dict[str, Any],
    out_dir: Path,
) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "prediction_sheet.csv"
    json_path = out_dir / "prediction_sheet.json"
    md_path = out_dir / "prediction_sheet.md"
    top_watch_md = out_dir / "top_watch_candidates.md"
    frozen_csv_path = out_dir / "frozen_bets.csv"
    frozen_json_path = out_dir / "frozen_bets.json"
    frozen_data_dir = ROOT / "data" / "predictions" / _compact(source_date)
    frozen_data_dir.mkdir(parents=True, exist_ok=True)
    frozen_all_path = frozen_data_dir / "frozen_bets_all.json"
    df = pd.DataFrame(rows)
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    df.to_csv(frozen_csv_path, index=False, encoding="utf-8-sig")
    frozen_payload = _rows_to_frozen_payload(source_date, rows)
    payload = {
        "date": source_date,
        "requestedDate": requested_date,
        "sourceDate": source_date,
        "displayMode": "paper_prediction",
        "bettingAllowed": False,
        "notice": "紙上予想です。実賭けは禁止です。",
        "summary": summary,
        "candidates": ui_rows,
        "skipSummary": {
            "byFinalDecision": _count_by(rows, "final_decision"),
            "byPaperDecision": _count_by(rows, "paper_decision"),
            "byStopReason": _count_by(rows, "stop_reason"),
        },
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "source": {
            "requestedDate": requested_date,
            "resolvedDate": source_date,
        },
    }
    payload = _merge_consensus_into_payload(payload, source_date)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    frozen_json_path.write_text(json.dumps(frozen_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_lines = [
        f"# AI紙上予想 ({source_date})",
        "",
        "- 紙上予想です。実賭け禁止です。",
        "",
        f"- requestedDate: {requested_date}",
        f"- sourceDate: {source_date}",
        f"- BUY件数: {summary.get('buyCount', 0)}",
        f"- WATCH件数: {summary.get('watchCount', 0)}",
        f"- PAPER件数: {summary.get('paperCount', 0)}",
        f"- SKIP件数: {summary.get('skipCount', 0)}",
        "",
        "## TOP WATCH / PAPER",
    ]
    top_rows = [r for r in rows if r["paper_decision"] in {"BUY", "WATCH", "PAPER"}]
    top_rows = sorted(top_rows, key=lambda x: (-float(x.get("expected_value") or 0), -float(x.get("approx_prob") or 0)))[:10]
    if not top_rows:
        md_lines.append("- 候補なし")
    else:
        for item in top_rows:
            md_lines.append(
                f"- {item['venue']} {item['race_no']}R {item['paper_decision']} {item['combo']} "
                f"EV={item['expected_value']} approx={item['approx_prob']} reason={item['reason']}"
            )
    md_lines.extend(["", "## 注意", "- 予想ロジック・BUY閾値・EV計算は変更していません。"])
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    watch_top = top_rows[:10]
    watch_lines = [
        f"# TOP WATCH / PAPER ({source_date})",
        "",
        "- 紙上予想です。実賭け禁止です。",
        "",
    ]
    if not watch_top:
        watch_lines.append("- 候補なし")
    else:
        for idx, item in enumerate(watch_top, start=1):
            watch_lines.append(
                f"{idx}. {item['venue']} {item['race_no']}R {item['paper_decision']} / {item['combo']} / "
                f"EV={item['expected_value']} / approx={item['approx_prob']} / {item['reason']}"
            )
    top_watch_md.write_text("\n".join(watch_lines) + "\n", encoding="utf-8")

    frozen_all_path.write_text(json.dumps(frozen_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "csv": csv_path,
        "json": json_path,
        "md": md_path,
        "top_watch_md": top_watch_md,
        "frozen_csv": frozen_csv_path,
        "frozen_json": frozen_json_path,
        "frozen_all_json": frozen_all_path,
    }


def build_prediction_sheet(date_text: str | None = None) -> dict[str, Any]:
    requested_date, source_date, source_reason = resolve_source_date(date_text)
    daily_dir = find_existing_daily_report_dir(source_date, REPORTS_DAILY_ROOT)
    skip_path = daily_dir / "skip_decisions.csv"
    pre_race_path = daily_dir / "pre_race_run.json"
    odds_path = daily_dir / "odds_refresh_run.json"
    preflight_path = daily_dir / "preflight_source_check.json"
    if not skip_path.exists():
        fallback_date = _latest_ready_date()
        if fallback_date != source_date:
            source_date = fallback_date
            daily_dir = find_existing_daily_report_dir(source_date, REPORTS_DAILY_ROOT)
            skip_path = daily_dir / "skip_decisions.csv"
            pre_race_path = daily_dir / "pre_race_run.json"
            odds_path = daily_dir / "odds_refresh_run.json"
            preflight_path = daily_dir / "preflight_source_check.json"
    df = _load_csv(skip_path)
    if df.empty:
        raise FileNotFoundError(f"skip_decisions.csv not found or empty: {skip_path}")

    if "final_decision" not in df.columns and "decision" in df.columns:
        df["final_decision"] = df["decision"]
    if "stop_reason" not in df.columns:
        for alias in ("skip_reason", "reason"):
            if alias in df.columns:
                df["stop_reason"] = df[alias]
                break
    if "odds_status" not in df.columns and "odds" in df.columns:
        df["odds_status"] = df["odds"].apply(lambda v: "usable" if _safe_float(v) is not None else "missing")

    pre_race = _load_json(pre_race_path)
    odds_refresh = _load_json(odds_path)
    preflight = _load_json(preflight_path)
    latest_progress = _load_final_goal_progress()
    latest_complete_ops_date = str(latest_progress.get("latest_complete_ops_date") or "").strip()

    rows = df.to_dict(orient="records")
    enriched: list[dict[str, Any]] = []
    buy_count = 0
    for row in rows:
        final_decision = str(row.get("final_decision") or row.get("decision") or "").upper()
        venue, jcd, race_no, race_id = _extract_race_meta(pd.Series(row))
        approx_prob = _safe_float(row.get("approx_prob"))
        real_odds = _safe_float(row.get("odds")) or _safe_float(row.get("real_odds"))
        expected_value = _extract_expected_value(pd.Series(row))
        risk_flag = str(row.get("risk_flag") or "").strip().lower() in {"true", "1", "yes"}
        stop_reason = str(row.get("stop_reason") or row.get("skip_reason") or row.get("reason") or "").strip()
        odds_status = str(row.get("odds_status") or "").strip()
        score = _paper_score(pd.Series(row))
        paper_decision = "SKIP"
        if final_decision == "BUY":
            paper_decision = "BUY"
            buy_count += 1
        row.update(
            {
                "venue": venue,
                "jcd": jcd,
                "race_no": race_no,
                "race_id": race_id,
                "deadline": str(row.get("deadline") or row.get("odds_last_fetched_at") or "").strip(),
                "combo": str(row.get("recommended_trifecta") or row.get("combo") or "").strip(),
                "final_decision": final_decision,
                "paper_decision": paper_decision,
                "stop_reason": stop_reason,
                "odds_status": odds_status,
                "approx_prob": approx_prob,
                "real_odds": real_odds,
                "expected_value": expected_value,
                "risk_flag": risk_flag,
                "confidence_rank": 0,
                "reason": _extract_reason(pd.Series(row)),
                "caution": "",
            }
        )
        row["predictionHash"] = _stable_hash_payload(row)
        enriched.append(row)

    non_buy = [r for r in enriched if r["paper_decision"] != "BUY"]
    non_buy_sorted = sorted(
        non_buy,
        key=lambda r: (
            -(_paper_score(pd.Series(r))),
            -float(r.get("expected_value") or 0.0),
            -float(r.get("approx_prob") or 0.0),
            r.get("venue") or "",
            int(r.get("race_no") or 0),
        ),
    )
    for idx, row in enumerate(non_buy_sorted, start=1):
        if idx <= 5:
            row["paper_decision"] = "WATCH"
        elif idx <= 10:
            row["paper_decision"] = "PAPER"
        else:
            row["paper_decision"] = "SKIP"
        row["confidence_rank"] = idx
        row["caution"] = _extract_caution(pd.Series(row), str(row["final_decision"]), str(row["paper_decision"]))

    for row in enriched:
        if row["paper_decision"] == "BUY":
            row["confidence_rank"] = 1
            row["caution"] = _extract_caution(pd.Series(row), str(row["final_decision"]), "BUY")
        elif not row.get("caution"):
            row["caution"] = _extract_caution(pd.Series(row), str(row["final_decision"]), str(row["paper_decision"]))

    summary = {
        "buyCount": sum(1 for r in enriched if r["paper_decision"] == "BUY"),
        "watchCount": sum(1 for r in enriched if r["paper_decision"] == "WATCH"),
        "paperCount": sum(1 for r in enriched if r["paper_decision"] == "PAPER"),
        "skipCount": sum(1 for r in enriched if r["paper_decision"] == "SKIP"),
        "topStopReason": Counter(str(r.get("stop_reason") or "") for r in enriched if str(r.get("stop_reason") or "")).most_common(1)[0][0]
        if any(str(r.get("stop_reason") or "") for r in enriched)
        else "",
        "latestCompleteOpsDate": latest_complete_ops_date,
        "sourceReason": source_reason,
        "preflightClassification": str(preflight.get("sourceClassification") or ""),
        "preRaceStatus": str(pre_race.get("status") or ""),
        "oddsRefreshStatus": str(odds_refresh.get("status") or ""),
    }
    rows_out: list[dict[str, Any]] = []
    sheet_generated_at = datetime.now().isoformat(timespec="seconds")
    for row in sorted(
        enriched,
        key=lambda r: (
            0 if r["paper_decision"] == "BUY" else 1 if r["paper_decision"] == "WATCH" else 2 if r["paper_decision"] == "PAPER" else 3,
            r["confidence_rank"],
            -(float(r.get("expected_value") or 0.0)),
            -(float(r.get("approx_prob") or 0.0)),
        ),
    ):
        output_row = {
                "date": source_date,
                "venue": row.get("venue") or "",
                "jcd": row.get("jcd") or "",
                "race_no": int(row.get("race_no") or 0) if row.get("race_no") is not None else None,
                "race_id": row.get("race_id") or "",
                "deadline": row.get("deadline") or "",
                "combo": row.get("combo") or "",
                "final_decision": row.get("final_decision") or "",
                "paper_decision": row.get("paper_decision") or "",
                "stop_reason": row.get("stop_reason") or "",
                "odds_status": row.get("odds_status") or "",
                "approx_prob": row.get("approx_prob"),
                "real_odds": row.get("real_odds"),
                "expected_value": row.get("expected_value"),
                "risk_flag": bool(row.get("risk_flag")),
                "confidence_rank": int(row.get("confidence_rank") or 0),
                "reason": row.get("reason") or "",
                "caution": row.get("caution") or "",
                "predictionHash": row.get("predictionHash") or "",
            }
        output_row = enrich_candidate_metadata(
            output_row,
            race_date=source_date,
            jcd=output_row.get("jcd"),
            race_no=output_row.get("race_no"),
            race_id=output_row.get("race_id"),
            model_version=row.get("modelVersion") or MODEL_VERSION,
            policy_version=DEFAULT_POLICY_VERSION,
            feature_version=DEFAULT_FEATURE_VERSION,
            odds_captured_at=row.get("odds_captured_at") or row.get("oddsCapturedAt") or row.get("odds_last_fetched_at") or "",
            deadline_at=resolve_deadline_at(source_date, output_row.get("deadline")),
            frozen_at=sheet_generated_at,
            snapshot_payload={
                "date": source_date,
                "jcd": output_row.get("jcd"),
                "raceNo": output_row.get("race_no"),
                "raceId": output_row.get("race_id"),
                "featureVersion": DEFAULT_FEATURE_VERSION,
            },
        )
        rows_out.append(output_row)

    report_dir = get_daily_report_dir(source_date, REPORTS_PREDICTIONS_ROOT)
    ui_rows = [_snake_to_camel(row) for row in rows_out]
    files = _write_report_files(source_date, requested_date, rows_out, ui_rows, summary, report_dir)

    ui_dir = UI_ROOT / _compact(source_date)
    ui_dir.mkdir(parents=True, exist_ok=True)
    ui_json_path = ui_dir / "prediction_sheet.json"
    ui_payload = {
        "date": source_date,
        "sourceDate": source_date,
        "displayMode": "paper_prediction",
        "bettingAllowed": False,
        "notice": "紙上予想です。実賭けは禁止です。",
        "summary": summary,
        "candidates": ui_rows,
        "skipSummary": {
            "byFinalDecision": _count_by(rows_out, "final_decision"),
            "byPaperDecision": _count_by(rows_out, "paper_decision"),
            "byStopReason": _count_by(rows_out, "stop_reason"),
        },
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
    }
    ui_payload = _merge_consensus_into_payload(ui_payload, source_date)
    ui_json_path.write_text(json.dumps(ui_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    result = {
        "status": "ok",
        "requestedDate": requested_date,
        "sourceDate": source_date,
        "sourceReason": source_reason,
        "summary": summary,
        "files": {k: str(v) for k, v in files.items()},
        "uiJsonPath": str(ui_json_path),
    }
    return result


def latest_prediction_sheet_path() -> Path | None:
    latest = latest_prediction_date()
    if not latest:
        return None
    ui_path = UI_ROOT / _compact(latest) / "prediction_sheet.json"
    if ui_path.exists():
        return ui_path
    report_path = REPORTS_PREDICTIONS_ROOT / latest / "prediction_sheet.json"
    if report_path.exists():
        return report_path
    return None


def _load_sheet_from_path(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def load_prediction_sheet(date_text: str | None = None) -> dict[str, Any]:
    resolved = resolve_prediction_sheet(date_text)
    payload = resolved.get("data")
    return payload if isinstance(payload, dict) else {}
