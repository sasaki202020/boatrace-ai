from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from src.evaluation.settle_results import _normalize_prediction_row  # type: ignore
from src.features.leakage_guard import filter_for_stage
from src.ingest.official_fetcher import JCD_TO_VENUE
from src.normalize.race_snapshot import build_race_snapshot
from src.normalize.schema import Boat
from src.predict.baseline_score_model import MODEL_VERSION, score_boats
from src.predict.ev_filter import decide_prediction
from src.predict.make_ui_json import build_ui_payload
from src.predict.trifecta_builder import build_trifecta_candidates
from src.pipeline.discover_venues import discover_venues_for_date


ROOT = Path(__file__).resolve().parents[2]
NORMALIZED_ROOT = ROOT / "data" / "normalized"
UI_ROOT = ROOT / "data" / "ui"
RAW_OFFICIAL_ROOT = ROOT / "data" / "raw" / "official"
BACKFILL_ROOT = ROOT / "data" / "predictions_backfill"
ARCHIVE_ROOT = ROOT / "_archive"


def _normalize_date(value: str) -> str:
    token = str(value).strip().lower()
    if token == "today":
        return date.today().strftime("%Y%m%d")
    digits = "".join(ch for ch in token if ch.isdigit())
    if len(digits) != 8:
        raise ValueError(f"invalid date: {value!r}")
    return digits


def _normalize_jcd(value: str) -> str:
    digits = "".join(ch for ch in str(value).strip() if ch.isdigit())
    return digits.zfill(2) if digits else ""


def _daterange(start_date: str, end_date: str) -> list[str]:
    start = datetime.strptime(start_date, "%Y%m%d").date()
    end = datetime.strptime(end_date, "%Y%m%d").date()
    days: list[str] = []
    current = start
    while current <= end:
        days.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return days


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _archive_existing(path: Path, *, date8: str, kind: str) -> None:
    if not path.exists():
        return
    archive_dir = ARCHIVE_ROOT / date8 / kind
    archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%H%M%S")
    archived = archive_dir / f"{path.stem}_{stamp}{path.suffix}"
    try:
        shutil.move(str(path), str(archived))
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass


def _boat_from_dict(raw: dict[str, Any]) -> Boat:
    payload = dict(raw)
    if "class" in payload and "cls" not in payload:
        payload["cls"] = payload.pop("class")
    if "motor_2ren_rate" in payload and "motor_2rate" not in payload:
        payload["motor_2rate"] = payload.pop("motor_2ren_rate")
    if "boat_2ren_rate" in payload and "boat_2rate" not in payload:
        payload["boat_2rate"] = payload.pop("boat_2ren_rate")
    if "racer_class" in payload and "cls" not in payload:
        payload["cls"] = payload.pop("racer_class")
    return Boat(**payload)


def _has_odds(payload: dict[str, Any]) -> bool:
    odds = payload.get("odds3t") or payload.get("odds_3t") or {}
    return isinstance(odds, dict) and bool(odds)


def _has_beforeinfo(payload: dict[str, Any]) -> bool:
    before_info = payload.get("beforeInfo") or payload.get("before_info") or {}
    start_exhibition = payload.get("startExhibition") or payload.get("start_exhibition") or []
    return bool(before_info) or bool(start_exhibition)


def _has_result(payload: dict[str, Any]) -> bool:
    result = payload.get("result") or {}
    if not isinstance(result, dict):
        return False
    status = str(result.get("raceStatus") or result.get("race_status") or "").strip().lower()
    combo = result.get("trifectaCombo") or result.get("trifecta_combo") or result.get("trifecta")
    payout = result.get("trifectaPayout") or result.get("trifecta_payout") or result.get("payout")
    return bool(combo and payout is not None and status in {"ok", "refund", "canceled", "no_contest", "available_without_trifecta"})


def _has_result_txt(payload: dict[str, Any]) -> bool:
    result = payload.get("result") or {}
    if not isinstance(result, dict):
        return False
    source = result.get("source") or payload.get("source") or {}
    if not isinstance(source, dict):
        source = {}
    result_source = str(source.get("resultSource") or result.get("resultSource") or result.get("result_source") or "").strip().lower()
    status = str(result.get("raceStatus") or result.get("race_status") or "").strip().lower()
    combo = result.get("trifectaCombo") or result.get("trifecta_combo") or result.get("trifecta")
    payout = result.get("trifectaPayout") or result.get("trifecta_payout") or result.get("payout")
    return bool(result_source == "official_txt_k" and combo and payout is not None and status in {"ok", "refund", "canceled", "no_contest", "available_without_trifecta"})


def _input_flags_for_payload(date8: str, venue_jcd: str, payload: dict[str, Any]) -> dict[str, Any]:
    normalized_path = NORMALIZED_ROOT / date8 / venue_jcd / "race_1.json"
    ui_path = UI_ROOT / date8 / f"raceyosou_{venue_jcd}.json"
    frozen_path = ROOT / "data" / "predictions" / date8 / f"frozen_bets_{venue_jcd}.json"
    backfilled_path = BACKFILL_ROOT / date8 / f"backfilled_bets_{venue_jcd}.json"
    raw_dir = RAW_OFFICIAL_ROOT / date8 / venue_jcd
    has_racelist_raw = raw_dir.exists() and any(raw_dir.glob("racelist_*.html"))
    has_odds_raw = raw_dir.exists() and any(raw_dir.glob("odds3t_*.html"))
    has_result_raw = raw_dir.exists() and any(raw_dir.glob("result_*.html"))
    has_normalized = False
    has_odds = False
    has_beforeinfo = False
    has_result = False
    has_result_txt = False
    if normalized_path.exists():
        has_normalized = True
        normalized_payload = _load_json(normalized_path) or {}
        has_odds = _has_odds(normalized_payload)
        has_beforeinfo = _has_beforeinfo(normalized_payload)
        has_result = _has_result(normalized_payload)
        has_result_txt = _has_result_txt(normalized_payload)
    else:
        venue_dir = NORMALIZED_ROOT / date8 / venue_jcd
        if venue_dir.exists():
            has_normalized = any(venue_dir.glob("race_*.json"))
            for race_path in sorted(venue_dir.glob("race_*.json")):
                normalized_payload = _load_json(race_path) or {}
                has_odds = has_odds or _has_odds(normalized_payload)
                has_beforeinfo = has_beforeinfo or _has_beforeinfo(normalized_payload)
                has_result = has_result or _has_result(normalized_payload)
                has_result_txt = has_result_txt or _has_result_txt(normalized_payload)
    raw_racelist = has_racelist_raw
    has_ui = ui_path.exists()
    has_frozen = frozen_path.exists()
    has_backfilled = backfilled_path.exists()
    can_backfill_odds_stage = has_normalized and has_odds
    can_settle = (has_frozen or has_backfilled or has_ui) and has_result
    if not raw_racelist:
        missing_reason = "raw_racelist_missing"
    elif not has_normalized:
        missing_reason = "normalized_missing"
    elif has_result_txt and not has_result:
        missing_reason = "result_html_missing_but_txt_available"
    elif not has_odds:
        missing_reason = "odds_missing"
    elif not has_ui and not has_frozen and not has_backfilled:
        if has_result_raw:
            missing_reason = "no_prediction_source"
        elif has_odds_raw or has_result_raw:
            missing_reason = "no_prediction_source"
        else:
            missing_reason = "no_prediction_source"
    elif not has_result and has_result_raw:
        missing_reason = "result_missing"
    elif not has_result:
        missing_reason = "prediction_missing"
    else:
        missing_reason = ""
    return {
        "date": date8,
        "jcd": venue_jcd,
        "venue": JCD_TO_VENUE.get(venue_jcd, venue_jcd),
        "hasRawRacelist": raw_racelist,
        "hasNormalizedRace": has_normalized,
        "hasUiJson": has_ui,
        "hasOdds": has_odds,
        "hasBeforeinfo": has_beforeinfo,
        "hasResult": has_result,
        "hasResultTxt": has_result_txt,
        "hasParsedResultTxt": has_result_txt,
        "resultSource": "official_txt_k" if has_result_txt else ("official_html" if has_result else ""),
        "hasFrozenBets": has_frozen,
        "hasBackfilledBets": has_backfilled,
        "canBackfillOddsStage": can_backfill_odds_stage,
        "canSettle": can_settle or has_result_txt,
        "canSettleFromTxt": can_settle or has_result_txt,
        "missingReason": missing_reason,
        "uiPath": str(ui_path) if has_ui else "",
        "frozenPath": str(frozen_path) if has_frozen else "",
        "backfilledPath": str(backfilled_path) if has_backfilled else "",
        "rawRacelistPath": str(raw_dir),
        "normalizedPath": str(normalized_path.parent if normalized_path.exists() else NORMALIZED_ROOT / date8 / venue_jcd),
    }


def _classify_no_input_reason(
    *,
    date8: str,
    normalized_dir: Path,
    ui_dir: Path,
    pred_dir: Path,
    backfill_dir: Path,
    raw_dir: Path,
) -> str:
    has_any_directory = raw_dir.exists() or normalized_dir.exists() or ui_dir.exists() or pred_dir.exists() or backfill_dir.exists()
    if has_any_directory:
        if raw_dir.exists() and not any(raw_dir.glob("*/racelist_*.html")) and not any(raw_dir.glob("racelist_*.html")):
            return "no_racelist_raw"
        if raw_dir.exists() and not any(raw_dir.glob("*/result_*.html")) and not any(raw_dir.glob("result_*.html")) and not any(raw_dir.glob("*/K*.TXT")) and not any(raw_dir.glob("K*.TXT")):
            return "no_result_raw"
        if ui_dir.exists() and not any(ui_dir.glob("raceyosou_*.json")):
            return "no_ui_json"
        if pred_dir.exists() and not any(pred_dir.glob("frozen_bets_*.json")):
            return "no_prediction_source"
        if backfill_dir.exists() and not any(backfill_dir.glob("backfilled_bets_*.json")):
            return "no_prediction_source"
    discovered = discover_venues_for_date(date8)
    venues = discovered.get("venues") or []
    if not has_any_directory and not venues:
        return "no_venue_discovery"
    if venues:
        return "date_not_collected"
    return "date_not_held"


def audit_backfill_inputs(*, start_date: str, end_date: str, jcd: str = "all", stage: str = "odds") -> dict[str, Any]:
    start8 = _normalize_date(start_date)
    end8 = _normalize_date(end_date)
    days = _daterange(start8, end8)
    rows: list[dict[str, Any]] = []
    for day in days:
        day_norm = NORMALIZED_ROOT / day
        day_ui = UI_ROOT / day
        day_raw = RAW_OFFICIAL_ROOT / day
        day_preds = ROOT / "data" / "predictions" / day
        day_backfill = BACKFILL_ROOT / day
        venue_ids: set[str] = set()
        if day_norm.exists():
            venue_ids.update(p.name.zfill(2) for p in day_norm.iterdir() if p.is_dir())
        if day_ui.exists():
            venue_ids.update(p.stem.split("_")[-1].zfill(2) for p in day_ui.glob("raceyosou_*.json"))
        if day_preds.exists():
            venue_ids.update(p.stem.rsplit("_", 1)[-1].zfill(2) for p in day_preds.glob("frozen_bets_*.json") if p.name != "frozen_bets_all.json")
        if day_backfill.exists():
            venue_ids.update(p.stem.rsplit("_", 1)[-1].zfill(2) for p in day_backfill.glob("backfilled_bets_*.json") if p.name != "backfilled_bets_all.json")
        if day_raw.exists():
            venue_ids.update(p.name.zfill(2) for p in day_raw.iterdir() if p.is_dir())
        if not venue_ids:
            raw_dir = day_raw
            normalized_dir = day_norm
            ui_dir = day_ui
            pred_dir = day_preds
            backfill_dir = day_backfill
            missing_reason = _classify_no_input_reason(
                date8=day,
                normalized_dir=normalized_dir,
                ui_dir=ui_dir,
                pred_dir=pred_dir,
                backfill_dir=backfill_dir,
                raw_dir=raw_dir,
            )
            rows.append(
                {
                    "date": day,
                    "jcd": jcd if jcd != "all" else "all",
                    "hasRawRacelist": False,
                    "hasNormalizedRace": False,
                    "hasUiJson": False,
                    "hasOdds": False,
                    "hasBeforeinfo": False,
                    "hasResult": False,
                    "hasResultTxt": False,
                    "hasParsedResultTxt": False,
                    "resultSource": "",
                    "hasFrozenBets": False,
                    "hasBackfilledBets": False,
                    "canBackfillOddsStage": False,
                    "canSettle": False,
                    "canSettleFromTxt": False,
                    "missingReason": missing_reason,
                }
            )
            continue
        for venue_jcd in sorted(venue_ids):
            if jcd != "all" and venue_jcd != _normalize_jcd(jcd):
                continue
            payload = {}
            normalized_dir = day_norm / venue_jcd
            if normalized_dir.exists():
                race_files = sorted(normalized_dir.glob("race_*.json"))
                if race_files:
                    payload = _load_json(race_files[0]) or {}
            rows.append(_input_flags_for_payload(day, venue_jcd, payload))
    summary = {
        "dateRange": f"{start8}_{end8}",
        "stage": stage,
        "jcd": jcd,
        "rows": len(rows),
        "days": len(days),
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
    }
    return {"summary": summary, "rows": rows}


def _prediction_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _build_snapshot(payload: dict[str, Any], *, stage: str) -> tuple[Any, dict[str, Any]]:
    boats = [_boat_from_dict(boat) for boat in payload.get("boats") or [] if isinstance(boat, dict)]
    source = dict(payload.get("source") or {})
    source_cutoff = max(
        [
            str(source.get("racelistFetchedAt") or ""),
            str(source.get("beforeinfoFetchedAt") or ""),
            str(source.get("odds3tFetchedAt") or ""),
        ]
    )
    leakage_payload = {}
    for boat in payload.get("boats") or []:
        if isinstance(boat, dict):
            leakage_payload.update(filter_for_stage(dict(boat), stage))
    leakage_guard_status = "ok" if leakage_payload is not None else "warning"
    snapshot = build_race_snapshot(
        date=str(payload.get("date") or ""),
        jcd=str(payload.get("jcd") or "").zfill(2),
        venue_name=str(payload.get("venueName") or payload.get("venue_name") or JCD_TO_VENUE.get(str(payload.get("jcd") or "").zfill(2), "")),
        rno=int(payload.get("rno") or payload.get("raceNo") or payload.get("race_number") or 0),
        deadline=str(payload.get("deadline") or ""),
        race_title=str(payload.get("raceTitle") or payload.get("race_title") or ""),
        stage=stage,
        boats=boats,
        before_info=dict(payload.get("beforeInfo") or payload.get("before_info") or {}),
        weather=payload.get("weather"),
        start_exhibition=list(payload.get("startExhibition") or payload.get("start_exhibition") or []),
        odds3t=dict(payload.get("odds3t") or {}),
        result={},
        source=source,
        data_status=payload.get("dataStatus") or payload.get("data_status") or {},
        data_status_reason=payload.get("dataStatusReason") or payload.get("data_status_reason") or {},
        model_version=str(payload.get("modelVersion") or MODEL_VERSION),
        updated_at=str(payload.get("updatedAt") or payload.get("updated_at") or ""),
    )
    meta = {
        "sourceDataCutoff": source_cutoff,
        "leakageGuardStatus": leakage_guard_status,
    }
    return snapshot, meta


def _predict_snapshot(snapshot, *, stage: str) -> list[dict[str, Any]]:
    scored = score_boats([boat.to_dict() for boat in snapshot.boats], stage=stage)
    predictions = build_trifecta_candidates(scored, odds3t=snapshot.odds3t if stage == "odds" else None)
    decided = [decide_prediction(pred.to_dict(), stage=stage, data_status=snapshot.data_status) for pred in predictions]
    out: list[dict[str, Any]] = []
    for pred in decided[:10]:
        row = _normalize_prediction_row(
            {
                "combo": pred.get("combo"),
                "decision": pred.get("decision"),
                "prob": pred.get("prob"),
                "odds": pred.get("odds"),
                "expectedValue": pred.get("expected_value"),
                "edge": pred.get("edge"),
                "rank": pred.get("rank"),
                "probRank": pred.get("prob_rank"),
                "evRank": pred.get("ev_rank"),
                "reason": pred.get("reason"),
                "modelVersion": snapshot.model_version,
                "stage": stage,
                "dataStatus": snapshot.data_status,
            },
            source="backfill",
        )
        out.append(row)
    return out


def _build_venue_payload(
    date8: str,
    venue_jcd: str,
    venue_rows: list[tuple[Any, list[dict[str, Any]], dict[str, Any]]],
    stage: str,
    *,
    input_availability: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    races: list[dict[str, Any]] = []
    total_bets = 0
    total_buys = 0
    source_cutoffs: list[str] = []
    for snapshot, bets, meta in venue_rows:
        total_bets += len(bets)
        total_buys += sum(1 for bet in bets if str(bet.get("decision") or "").upper() == "BUY")
        if meta.get("sourceDataCutoff"):
            source_cutoffs.append(str(meta["sourceDataCutoff"]))
        races.append(
            {
                "rno": snapshot.rno,
                "raceNo": snapshot.rno,
                "bets": bets,
                "betCount": len(bets),
                "buyCount": sum(1 for bet in bets if str(bet.get("decision") or "").upper() == "BUY"),
                "dataStatus": snapshot.data_status,
                "sourceDataCutoff": meta.get("sourceDataCutoff", ""),
                "leakageGuardStatus": meta.get("leakageGuardStatus", "ok"),
            }
        )
    cutoff = max(source_cutoffs) if source_cutoffs else ""
    return {
        "date": date8,
        "jcd": venue_jcd,
        "venue": JCD_TO_VENUE.get(venue_jcd, venue_jcd),
        "stage": stage,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "backfillGeneratedAt": datetime.now().isoformat(timespec="seconds"),
        "freezeType": "backfill",
        "sourceDataCutoff": cutoff,
        "leakageGuardStatus": "ok",
        "warning": "backfilled_predictions_not_live",
        "modelVersion": MODEL_VERSION,
        "races": races,
        "totalBetCount": total_bets,
        "totalBuyCount": total_buys,
        "inputAvailability": input_availability or [],
        "predictionHash": _prediction_hash({"date": date8, "jcd": venue_jcd, "races": races, "freezeType": "backfill"}),
    }


def _write_backfill_payloads(date8: str, payloads: list[dict[str, Any]]) -> list[str]:
    written: list[str] = []
    out_dir = BACKFILL_ROOT / date8
    out_dir.mkdir(parents=True, exist_ok=True)
    all_path = out_dir / "backfilled_bets_all.json"
    _archive_existing(all_path, date8=date8, kind="backfill_bets")
    all_payload = {
        "date": date8,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "freezeType": "backfill",
        "backfillGeneratedAt": datetime.now().isoformat(timespec="seconds"),
        "venues": [
            {
                "jcd": payload.get("jcd"),
                "venue": payload.get("venue"),
                "frozenPath": str(out_dir / f"backfilled_bets_{str(payload.get('jcd') or '').zfill(2)}.json"),
                "betCount": int(payload.get("totalBetCount") or 0),
                "buyCount": int(payload.get("totalBuyCount") or 0),
            }
            for payload in payloads
        ],
        "totalBetCount": sum(int(payload.get("totalBetCount") or 0) for payload in payloads),
        "totalBuyCount": sum(int(payload.get("totalBuyCount") or 0) for payload in payloads),
        "inputAvailability": [row for payload in payloads for row in (payload.get("inputAvailability") or [])],
        "predictionHash": _prediction_hash({"date": date8, "venues": payloads, "freezeType": "backfill"}),
        "warning": "backfilled_predictions_not_live",
        "backfillPredictionHash": _prediction_hash({"date": date8, "venues": payloads, "freezeType": "backfill"}),
    }
    all_path.write_text(json.dumps(all_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    written.append(str(all_path))
    for payload in payloads:
        venue_jcd = str(payload.get("jcd") or "").zfill(2)
        if not venue_jcd:
            continue
        path = out_dir / f"backfilled_bets_{venue_jcd}.json"
        _archive_existing(path, date8=date8, kind="backfill_bets")
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(str(path))
    return written


def backfill_predictions(*, start_date: str, end_date: str, jcd: str = "all", stage: str = "odds", dry_run: bool = False) -> dict[str, Any]:
    start8 = _normalize_date(start_date)
    end8 = _normalize_date(end_date)
    days = _daterange(start8, end8)
    summary_rows: list[dict[str, Any]] = []
    written: list[str] = []
    input_availability: list[dict[str, Any]] = []
    for day in days:
        day_audit = audit_backfill_inputs(start_date=day, end_date=day, jcd=jcd, stage=stage)
        input_availability.extend(day_audit.get("rows") or [])
        day_dir = NORMALIZED_ROOT / day
        if not day_dir.exists():
            continue
        if jcd == "all":
            venue_dirs = [p for p in day_dir.iterdir() if p.is_dir()]
        else:
            venue_dirs = [day_dir / f"{int(jcd):02d}"]
        venue_payloads: list[dict[str, Any]] = []
        for venue_dir in sorted(venue_dirs):
            if not venue_dir.exists():
                continue
            venue_jcd = venue_dir.name.zfill(2)
            if jcd != "all" and venue_jcd != f"{int(jcd):02d}":
                continue
            venue_rows: list[tuple[Any, list[dict[str, Any]], dict[str, Any]]] = []
            for path in sorted(venue_dir.glob("race_*.json")):
                payload = _load_json(path)
                if not payload:
                    continue
                if not payload.get("boats"):
                    continue
                snapshot, meta = _build_snapshot(payload, stage=stage)
                if str(snapshot.data_status.get("odds3t") or "").lower() not in {"ok", "available", "ready"} and stage == "odds":
                    meta["leakageGuardStatus"] = "ok"
                predictions = _predict_snapshot(snapshot, stage=stage)
                venue_rows.append((snapshot, predictions, meta))
            venue_input_availability = [row for row in (day_audit.get("rows") or []) if str(row.get("jcd") or "").zfill(2) == venue_jcd]
            if venue_rows:
                venue_payload = _build_venue_payload(day, venue_jcd, venue_rows, stage, input_availability=venue_input_availability)
                venue_payloads.append(venue_payload)
        total_bet_count = sum(int(payload.get("totalBetCount") or 0) for payload in venue_payloads)
        total_buy_count = sum(int(payload.get("totalBuyCount") or 0) for payload in venue_payloads)
        summary_rows.append(
            {
                "date": day,
                "venueCount": len(venue_payloads),
                "totalBetCount": total_bet_count,
                "totalBuyCount": total_buy_count,
                "freezeType": "backfill",
                "warning": "backfilled_predictions_not_live",
                "inputAvailability": day_audit.get("rows") or [],
            }
        )
        if dry_run:
            continue
        written.extend(_write_backfill_payloads(day, venue_payloads))
    possible_rows = [row for row in input_availability if row.get("canBackfillOddsStage")]
    impossible_rows = [row for row in input_availability if not row.get("canBackfillOddsStage")]
    reason_counts: dict[str, int] = {}
    for row in input_availability:
        reason = str(row.get("missingReason") or "unknown")
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
    return {
        "dateRange": f"{start8}_{end8}",
        "stage": stage,
        "days": len(days),
        "summaryRows": summary_rows,
        "inputAvailability": input_availability,
        "dryRun": bool(dry_run),
        "dryRunSummary": {
            "targetDays": len(days),
            "backfillPossibleDays": len({row.get("date") for row in possible_rows}),
            "backfillImpossibleDays": len({row.get("date") for row in impossible_rows}),
            "backfillPossibleRows": len(possible_rows),
            "backfillImpossibleRows": len(impossible_rows),
            "plannedPredictionCount": sum(int(row.get("totalBetCount") or 0) for row in summary_rows),
            "reasonCounts": reason_counts,
        },
        "written": written,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
    }


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Generate shadow backfill predictions from existing normalized data.")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--jcd", default="all")
    parser.add_argument("--stage", default="odds", choices=["pre_race", "odds", "beforeinfo"])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = backfill_predictions(start_date=args.start_date, end_date=args.end_date, jcd=args.jcd, stage=args.stage, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
