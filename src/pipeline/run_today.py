from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.ingest.official_fetcher import JCD_TO_VENUE, fetch_day
from src.normalize.race_snapshot import build_race_snapshot
from src.normalize.schema import Boat, RaceSnapshot
from src.pipeline.discover_today import discover_today
from src.predict.baseline_score_model import MODEL_VERSION, score_boats
from src.predict.ev_filter import decide_prediction
from src.predict.make_ui_json import build_ui_payload, write_ui_payload
from src.predict.trifecta_builder import build_trifecta_candidates
from src.pipeline.candidate_metadata import (
    DEFAULT_FEATURE_VERSION,
    DEFAULT_POLICY_VERSION,
    assert_unique_candidate_ids,
    enrich_candidate_metadata,
    resolve_deadline_at,
)


ROOT = Path(__file__).resolve().parents[2]
NORMALIZED_ROOT = ROOT / "data" / "normalized"
PRED_ROOT = ROOT / "data" / "predictions"
UI_ROOT = ROOT / "data" / "ui"
ERRORS_ROOT = ROOT / "reports" / "errors"
ARCHIVE_ROOT = ROOT / "_archive"


def _normalize_date(value: str) -> str:
    token = str(value).strip().lower()
    if token == "today":
        return date.today().strftime("%Y%m%d")
    digits = "".join(ch for ch in token if ch.isdigit())
    if len(digits) != 8:
        raise ValueError(f"invalid date: {value!r}")
    return digits


def _parse_races(value: str | None) -> list[int] | None:
    if not value:
        return None
    token = value.strip()
    if token.lower() == "all":
        return None
    if "-" in token:
        left, right = token.split("-", 1)
        return list(range(int(left), int(right) + 1))
    return [int(part) for part in token.split(",") if part.strip()]


def _fetch_stage_options(stage: str) -> dict[str, float]:
    if stage == "odds":
        return {"timeout": 15.0, "retries": 1, "retry_sleep": 0.5}
    if stage == "beforeinfo":
        return {"timeout": 4.0, "retries": 0, "retry_sleep": 0.0}
    if stage == "result":
        return {"timeout": 25.0, "retries": 0, "retry_sleep": 0.0}
    return {"timeout": 8.0, "retries": 0, "retry_sleep": 0.0}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _archive_existing(path: Path, *, archive_date: str, kind: str) -> None:
    if not path.exists():
        return
    archive_dir = ARCHIVE_ROOT / archive_date / kind
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


def _load_json(path: Path) -> dict[str, Any] | list[Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_raw_html(date8: str, jcd: str, race_no: int, kind: str, html: str) -> Path:
    path = ROOT / "data" / "raw" / "official" / date8 / jcd / f"{kind}_{race_no:02d}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html or "", encoding="utf-8")
    return path


def _append_error(date8: str, payload: dict[str, Any]) -> None:
    ERRORS_ROOT.mkdir(parents=True, exist_ok=True)
    path = ERRORS_ROOT / f"{date8}_errors.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _load_today_venues(date8: str) -> dict[str, Any]:
    path = NORMALIZED_ROOT / date8 / "today_venues.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return discover_today(target_date=date8)


def _boat_from_record(record: dict[str, Any], fallback_no: int) -> Boat:
    return Boat(
        boat_no=int(record.get("boat_no") or fallback_no),
        racer_name=record.get("racer_name"),
        racer_id=record.get("racer_id"),
        branch=record.get("branch"),
        cls=record.get("class"),
        age=record.get("age"),
        weight=record.get("weight"),
        avg_st=record.get("avg_st"),
        national_win_rate=record.get("national_win_rate"),
        national_2rate=record.get("national_2rate"),
        national_3rate=record.get("national_3rate"),
        local_win_rate=record.get("local_win_rate"),
        local_2rate=record.get("local_2rate"),
        local_3rate=record.get("local_3rate"),
        motor_no=record.get("motor_no"),
        motor_2rate=record.get("motor_2rate"),
        boat_no_equipment=record.get("boat_no_equipment"),
        boat_2rate=record.get("boat_2rate"),
        start_exhibition_course=record.get("start_exhibition_course"),
        start_exhibition_st=record.get("start_exhibition_st"),
        tilt=record.get("tilt"),
        propeller=record.get("propeller"),
        parts_exchange=list(record.get("parts_exchange") or record.get("partsExchange") or []),
        weight_adjustment=record.get("weight_adjustment"),
        f_count=record.get("f_count"),
        l_count=record.get("l_count"),
        exhibition_time=record.get("exhibition_time"),
        exhibition_st=record.get("exhibition_st"),
        boat_score=record.get("boat_score"),
        score_rank=record.get("score_rank"),
        score_reason=record.get("score_reason") or "",
        data_status=str(record.get("data_status") or "missing"),
        source=dict(record.get("source") or {}),
    )


def _boats_from_racelist(row: dict[str, Any], *, stage: str) -> list[Boat]:
    parsed = row.get("racelist", {}).get("parsed", {}) if isinstance(row.get("racelist"), dict) else {}
    raw_boats = parsed.get("boats") or []
    boats = [_boat_from_record(raw, idx) for idx, raw in enumerate(raw_boats, start=1) if isinstance(raw, dict)]
    if stage in {"beforeinfo", "odds", "result"}:
        beforeinfo = row.get("beforeinfo", {}).get("parsed", {}) if isinstance(row.get("beforeinfo"), dict) else {}
        by_no = {boat.boat_no: boat for boat in boats}
        for raw in beforeinfo.get("boats") or []:
            if not isinstance(raw, dict):
                continue
            no = int(raw.get("boat_no") or raw.get("no") or raw.get("lane") or 0)
            boat = by_no.get(no)
            if boat is None:
                continue
            if raw.get("exhibition_time") not in (None, ""):
                boat.exhibition_time = raw.get("exhibition_time")
            if raw.get("exhibition_st") not in (None, ""):
                boat.exhibition_st = raw.get("exhibition_st")
            if raw.get("start_exhibition_course") not in (None, ""):
                boat.start_exhibition_course = raw.get("start_exhibition_course")
            if raw.get("start_exhibition_st") not in (None, ""):
                boat.start_exhibition_st = raw.get("start_exhibition_st")
            if raw.get("tilt") not in (None, ""):
                boat.tilt = raw.get("tilt")
            if raw.get("propeller") not in (None, ""):
                boat.propeller = raw.get("propeller")
            if raw.get("partsExchange") not in (None, ""):
                boat.parts_exchange = list(raw.get("partsExchange") or [])
            if raw.get("weightAdjustment") not in (None, ""):
                boat.weight_adjustment = raw.get("weightAdjustment")
            if raw.get("data_status"):
                boat.data_status = str(raw.get("data_status"))
    seen = {boat.boat_no for boat in boats}
    for boat_no in range(1, 7):
        if boat_no not in seen:
            boats.append(_boat_from_record({"boat_no": boat_no, "data_status": "missing"}, boat_no))
    boats.sort(key=lambda boat: boat.boat_no)
    return boats[:6]


def _build_snapshot(row: dict[str, Any], *, stage: str) -> RaceSnapshot:
    racelist = row.get("racelist") or {}
    parsed = racelist.get("parsed") or {}
    beforeinfo = row.get("beforeinfo") or {}
    before_parsed = beforeinfo.get("parsed") or {}
    before_info = before_parsed.get("beforeInfo") or before_parsed.get("beforeinfo") or {}
    boats = _boats_from_racelist(row, stage=stage)
    data_status = {
        "racelist": str(racelist.get("dataStatus") or "missing"),
        "odds3t": "pending",
        "beforeinfo": "pending",
        "result": "pending",
    }
    data_status_reason = {
        "racelist": list(racelist.get("missingReason") or []),
        "beforeinfo": list(beforeinfo.get("dataStatusReason") or beforeinfo.get("missingReason") or []),
        "odds3t": list((row.get("odds3t") or {}).get("missingReason") or []),
        "result": list((row.get("result") or {}).get("missingReason") or []),
    }
    odds_parsed = {}
    if stage == "odds":
        odds = row.get("odds3t") or {}
        odds_parsed = odds.get("parsed") if isinstance(odds.get("parsed"), dict) else {}
        data_status["odds3t"] = odds.get("dataStatus") or ("available" if odds_parsed else "unavailable")
    if stage in {"beforeinfo", "odds", "result"}:
        data_status["beforeinfo"] = beforeinfo.get("dataStatus") or "pending"
    if stage == "result":
        result = row.get("result") or {}
        data_status["result"] = result.get("dataStatus") or "pending"
    snapshot = build_race_snapshot(
        date=row["date"],
        jcd=row["jcd"],
        venue_name=row.get("venue_name") or JCD_TO_VENUE.get(row["jcd"], row["jcd"]),
        rno=int(row["race_no"]),
        deadline=row.get("deadline") or parsed.get("deadline") or "",
        race_title=row.get("race_title") or parsed.get("raceTitle") or "",
        stage=stage,
        boats=boats,
        before_info=before_info,
        weather=before_parsed.get("weather") if stage in {"beforeinfo", "odds", "result"} else None,
        start_exhibition=(
            before_parsed.get("start_exhibition")
            or before_parsed.get("startExhibition")
            or before_info.get("startExhibition")
            or []
        )
        if stage in {"beforeinfo", "odds", "result"}
        else [],
        odds3t=odds_parsed if stage == "odds" else {},
        result=(row.get("result") or {}).get("parsed", {}) if stage == "result" else {},
        source={
            "racelistUrl": racelist.get("url", ""),
            "racelistFetchedAt": racelist.get("fetchedAt", ""),
            "racelistHttpStatus": racelist.get("fetchStatus", "unavailable"),
            "beforeinfo": beforeinfo,
            "beforeinfoUrl": beforeinfo.get("url", ""),
            "beforeinfoFetchedAt": beforeinfo.get("fetchedAt", ""),
            "beforeinfoHttpStatus": beforeinfo.get("fetchStatus", "unavailable"),
            "beforeinfoFallbackUsed": beforeinfo.get("fallbackUsed", False),
            "beforeinfoRawPath": beforeinfo.get("rawHtmlPath", ""),
            "beforeinfoReason": beforeinfo.get("dataStatusReason") or beforeinfo.get("missingReason") or [],
            "odds3tUrl": (row.get("odds3t") or {}).get("url", ""),
            "odds3tFetchedAt": (row.get("odds3t") or {}).get("fetchedAt", ""),
            "odds3tHttpStatus": (row.get("odds3t") or {}).get("fetchStatus", "unavailable"),
            "odds3tFallbackUsed": (row.get("odds3t") or {}).get("fallbackUsed", False),
            "result": row.get("result") or {},
            "resultUrl": (row.get("result") or {}).get("url", ""),
            "resultFetchedAt": (row.get("result") or {}).get("fetchedAt", ""),
            "resultHttpStatus": (row.get("result") or {}).get("fetchStatus", "unavailable"),
            "stage": stage,
            "modelVersion": MODEL_VERSION,
            "data_status_reason": data_status_reason,
        },
        data_status=data_status,
        data_status_reason=data_status_reason,
        model_version=MODEL_VERSION,
    )
    return snapshot


def _score_and_predict(snapshot: RaceSnapshot, *, stage: str) -> list[dict[str, Any]]:
    scored = score_boats([boat.to_dict() for boat in snapshot.boats], stage=stage)
    score_map = {int(row["boat_no"]): row for row in scored}
    for boat in snapshot.boats:
        row = score_map.get(int(boat.boat_no))
        if row:
            boat.boat_score = row["boat_score"]
            boat.score_rank = row["score_rank"]
            boat.score_reason = row["score_reason"]
            boat.data_status = row["data_status"]

    predictions = build_trifecta_candidates(scored, odds3t=snapshot.odds3t if stage == "odds" else None)
    beforeinfo_reason = (snapshot.data_status_reason or {}).get("beforeinfo") or snapshot.source.get("beforeinfoReason") or []
    if not isinstance(beforeinfo_reason, list):
        beforeinfo_reason = [str(beforeinfo_reason)]
    for pred in predictions:
        pred.extra["stage"] = stage
        pred.extra["beforeinfo_status"] = snapshot.data_status.get("beforeinfo")
        pred.extra["beforeinfo_reason"] = ",".join(str(v) for v in beforeinfo_reason if v)
    decided = [decide_prediction(pred.to_dict(), stage=stage, data_status=snapshot.data_status) for pred in predictions]

    if stage == "odds":
        buy_count = 0
        for pred in decided:
            if pred.get("decision") == "BUY":
                buy_count += 1
                if buy_count > 3:
                    pred["decision"] = "WATCH"
                    pred["reason"] = "buy_cap_reached"
    elif stage == "pre_race":
        for pred in decided:
            if pred.get("decision") == "BUY":
                pred["decision"] = "WATCH"
                pred["reason"] = "pre_race_buy_blocked"
    return decided


def _raw_html_path(date8: str, jcd: str, race_no: int, kind: str) -> Path:
    return ROOT / "data" / "raw" / "official" / date8 / jcd / f"{kind}_{race_no:02d}.html"


def _save_error(date8: str, *, stage: str, jcd: str, rno: int, error_type: str, message: str, url: str = "", fetched_at: str = "") -> None:
    _append_error(
        date8,
        {
            "date": date8,
            "jcd": jcd,
            "rno": rno,
            "stage": stage,
            "type": error_type,
            "message": message,
            "url": url,
            "fetchedAt": fetched_at,
        },
    )


def _prediction_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _load_prediction_rows(pred_path: Path) -> list[dict[str, Any]]:
    payload = _load_json(pred_path)
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        rows = payload.get("predictions")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def _normalize_combo_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        digits = [str(v).strip() for v in value if str(v).strip().isdigit()]
        return "-".join(digits[:3])
    text = str(value).strip().replace("=", "-").replace(" ", "")
    parts = [p for p in text.split("-") if p]
    digits = [p for p in parts if p.isdigit()]
    if len(digits) >= 3:
        return "-".join(digits[:3])
    return text


def _load_frozen_venue_payload(date8: str, venue_jcd: str) -> dict[str, Any] | None:
    path = PRED_ROOT / date8 / f"frozen_bets_{venue_jcd}.json"
    payload = _load_json(path)
    return payload if isinstance(payload, dict) else None


def _freeze_bets_for_race(
    snapshot: RaceSnapshot,
    predictions: list[dict[str, Any]],
    *,
    stage: str,
    frozen_at: str,
) -> dict[str, Any]:
    bets: list[dict[str, Any]] = []
    for pred in predictions[:10]:
        if not isinstance(pred, dict):
            continue
        bet = {
            "rno": snapshot.rno,
            "raceNo": snapshot.rno,
            "combo": _normalize_combo_text(pred.get("combo") or pred.get("trifecta") or pred.get("comboParts")),
            "decision": pred.get("decision"),
            "stake": 100,
            "prob": pred.get("prob"),
            "odds": pred.get("odds"),
            "expectedValue": pred.get("expectedValue") or pred.get("expected_value"),
            "edge": pred.get("edge"),
            "rank": pred.get("rank"),
            "reason": pred.get("reason") or "",
            "dataStatus": snapshot.data_status,
            "dataStatusReason": snapshot.data_status_reason,
            "modelVersion": snapshot.model_version,
            "sourceType": "live_frozen",
        }
        bet["predictionHash"] = _prediction_hash(
            {
                "date": snapshot.date,
                "jcd": snapshot.jcd,
                "rno": snapshot.rno,
                "stage": stage,
                "modelVersion": snapshot.model_version,
                "bet": bet,
            }
        )
        bet = enrich_candidate_metadata(
            bet,
            race_date=snapshot.date,
            jcd=snapshot.jcd,
            race_no=snapshot.rno,
            race_id=f"{_normalize_date(snapshot.date)}-{snapshot.jcd}-{snapshot.rno:02d}",
            model_version=snapshot.model_version,
            policy_version=DEFAULT_POLICY_VERSION,
            feature_version=DEFAULT_FEATURE_VERSION,
            odds_captured_at=snapshot.source.get("odds3tFetchedAt", ""),
            deadline_at=resolve_deadline_at(snapshot.date, snapshot.deadline),
            frozen_at=frozen_at,
            snapshot_payload=snapshot.to_dict(),
        )
        bets.append(bet)
    assert_unique_candidate_ids(bets)
    return {
        "rno": snapshot.rno,
        "raceNo": snapshot.rno,
        "comboCount": len(bets),
        "buyCount": sum(1 for bet in bets if str(bet.get("decision") or "").upper() == "BUY"),
        "bets": bets,
    }


def _frozen_race_rows(date8: str, venue_jcd: str, race_no: int) -> list[dict[str, Any]]:
    payload = _load_frozen_venue_payload(date8, venue_jcd)
    if isinstance(payload, dict):
        races = payload.get("races")
        if isinstance(races, list):
            for race in races:
                if not isinstance(race, dict):
                    continue
                if int(race.get("rno") or race.get("raceNo") or 0) == int(race_no):
                    bets = race.get("bets") or race.get("aiPredictions") or []
                    if isinstance(bets, list):
                        return [row for row in bets if isinstance(row, dict)]
    return []


def _build_frozen_venue_payload(
    *,
    date8: str,
    venue_jcd: str,
    venue_rows: list[tuple[RaceSnapshot, list[dict[str, Any]]]],
    ui_path: Path,
    stage: str,
) -> dict[str, Any]:
    races: list[dict[str, Any]] = []
    total_buy_count = 0
    total_bet_count = 0
    generated_at = datetime.now().isoformat(timespec="seconds")
    for snapshot, predictions in venue_rows:
        race_freeze = _freeze_bets_for_race(snapshot, predictions, stage=stage, frozen_at=generated_at)
        bets = race_freeze["bets"]
        total_buy_count += int(race_freeze["buyCount"] or 0)
        total_bet_count += len(bets)
        race_payload = {
            "date": date8,
            "jcd": venue_jcd,
            "venue": snapshot.venue_name,
            "rno": snapshot.rno,
            "raceNumber": snapshot.rno,
            "stage": stage,
            "generatedAt": generated_at,
            "modelVersion": snapshot.model_version,
            "freezeType": "live",
            "sourceUiJsonPath": str(ui_path),
            "predictionHash": _prediction_hash(
                {
                    "date": date8,
                    "jcd": venue_jcd,
                    "rno": snapshot.rno,
                    "stage": stage,
                    "modelVersion": snapshot.model_version,
                    "bets": bets,
                    "dataStatus": snapshot.data_status,
                }
            ),
            "bets": bets,
            "betCount": len(bets),
            "buyCount": race_freeze["buyCount"],
            "dataStatus": snapshot.data_status,
            "status": snapshot.stage,
        }
        races.append(race_payload)
    payload = {
        "date": date8,
        "jcd": venue_jcd,
        "venue": venue_rows[0][0].venue_name if venue_rows else JCD_TO_VENUE.get(venue_jcd, venue_jcd),
        "stage": stage,
        "generatedAt": generated_at,
        "modelVersion": venue_rows[0][0].model_version if venue_rows else MODEL_VERSION,
        "freezeType": "live",
        "sourceUiJsonPath": str(ui_path),
        "races": races,
        "totalBetCount": total_bet_count,
        "totalBuyCount": total_buy_count,
        "predictionHash": _prediction_hash({"date": date8, "jcd": venue_jcd, "races": races}),
    }
    return payload


def _write_frozen_payloads(date8: str, payloads: list[dict[str, Any]]) -> list[str]:
    written: list[str] = []
    if not payloads:
        return written
    root = PRED_ROOT / date8
    root.mkdir(parents=True, exist_ok=True)
    all_path = root / "frozen_bets_all.json"
    _archive_existing(all_path, archive_date=date8, kind="frozen_bets")
    all_payload = {
        "date": date8,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "freezeType": "live",
        "venues": payloads,
        "totalBetCount": sum(int(payload.get("totalBetCount") or 0) for payload in payloads),
        "totalBuyCount": sum(int(payload.get("totalBuyCount") or 0) for payload in payloads),
        "predictionHash": _prediction_hash({"date": date8, "venues": payloads}),
    }
    _write_json(all_path, all_payload)
    written.append(str(all_path))
    for payload in payloads:
        venue_jcd = str(payload.get("jcd") or "").zfill(2)
        if not venue_jcd:
            continue
        path = root / f"frozen_bets_{venue_jcd}.json"
        _archive_existing(path, archive_date=date8, kind="frozen_bets")
        _write_json(path, payload)
        written.append(str(path))
    return written


def run_today(*, target_date: str, jcd: str, races: list[int] | None, stage: str) -> dict[str, Any]:
    date8 = _normalize_date(target_date)
    normalized_dir = NORMALIZED_ROOT / date8
    pred_dir = PRED_ROOT / date8
    ui_dir = UI_ROOT / date8
    normalized_dir.mkdir(parents=True, exist_ok=True)
    pred_dir.mkdir(parents=True, exist_ok=True)
    ui_dir.mkdir(parents=True, exist_ok=True)

    if jcd == "all":
        discovery = _load_today_venues(date8)
        venue_codes = [str(v.get("jcd")).zfill(2) for v in discovery.get("venues", []) if v.get("jcd")]
    else:
        venue_codes = [str(jcd).zfill(2)]

    written_ui: list[str] = []
    written_normalized: list[str] = []
    written_predictions: list[str] = []
    frozen_payloads: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    buy_count = watch_count = skip_count = 0
    odds_ok = odds_failed = 0

    fetch_kwargs = _fetch_stage_options(stage)

    def _fetch_venue(venue_jcd: str) -> tuple[str, list[dict[str, Any]] | Exception]:
        try:
            return (
                venue_jcd,
                fetch_day(
                    target_date=date8,
                    jcd=venue_jcd,
                    races=races or list(range(1, 13)),
                    stage=stage,
                    timeout=fetch_kwargs["timeout"],
                    retries=int(fetch_kwargs["retries"]),
                    retry_sleep=fetch_kwargs["retry_sleep"],
                ),
            )
        except Exception as exc:  # pragma: no cover - defensive
            return venue_jcd, exc

    fetched_by_venue: dict[str, list[dict[str, Any]]] = {}
    venue_workers = 2 if stage == "result" else min(4, len(venue_codes))
    if len(venue_codes) > 1 and venue_workers > 1:
        with ThreadPoolExecutor(max_workers=venue_workers) as executor:
            futures = [executor.submit(_fetch_venue, venue_jcd) for venue_jcd in venue_codes]
            for future in as_completed(futures):
                venue_jcd, result = future.result()
                if isinstance(result, Exception):
                    _save_error(date8, stage=stage, jcd=venue_jcd, rno=0, error_type="fetch_error", message=str(result))
                    errors.append({"jcd": venue_jcd, "stage": stage, "type": "fetch_error", "message": str(result)})
                else:
                    fetched_by_venue[venue_jcd] = result
    else:
        for venue_jcd in venue_codes:
            fetched_by_venue[venue_jcd] = _fetch_venue(venue_jcd)[1]  # type: ignore[index]

    for venue_jcd in venue_codes:
        fetched = fetched_by_venue.get(venue_jcd)
        if fetched is None:
            continue

        venue_rows: list[tuple[RaceSnapshot, list[dict[str, Any]]]] = []
        for row in fetched:
            race_no = int(row["race_no"])
            try:
                racelist = row.get("racelist") or {}
                if racelist.get("html") is not None:
                    _write_raw_html(date8, venue_jcd, race_no, "racelist", racelist.get("html", ""))
                if stage == "odds":
                    odds_row = row.get("odds3t") or {}
                    if odds_row.get("html") is not None:
                        _write_raw_html(date8, venue_jcd, race_no, "odds3t", odds_row.get("html", ""))
                if stage == "result":
                    result_row = row.get("result") or {}
                    if result_row.get("html") is not None:
                        _write_raw_html(date8, venue_jcd, race_no, "result", result_row.get("html", ""))
                snapshot = _build_snapshot(row, stage=stage)

                norm_path = normalized_dir / venue_jcd / f"race_{race_no}.json"
                pred_path = pred_dir / venue_jcd / f"race_{race_no}.json"
                if stage == "result":
                    predictions = _frozen_race_rows(date8, venue_jcd, race_no)
                    if not predictions:
                        predictions = _load_prediction_rows(pred_path)
                else:
                    predictions = _score_and_predict(snapshot, stage=stage)
                _write_json(norm_path, snapshot.to_dict())
                if stage != "result":
                    _write_json(pred_path, predictions)
                written_normalized.append(str(norm_path))
                if stage != "result":
                    written_predictions.append(str(pred_path))
                venue_rows.append((snapshot, predictions[:10]))

                data_status = snapshot.data_status or {}
                if isinstance(data_status, dict) and str(data_status.get("odds3t") or "").lower() in {"ok", "available", "ready"}:
                    odds_ok += 1
                elif stage == "beforeinfo":
                    before_row = row.get("beforeinfo") or {}
                    before_status = str(before_row.get("dataStatus") or snapshot.data_status.get("beforeinfo") or "pending")
                    if before_status != "ok":
                        before_reason = before_row.get("dataStatusReason") or before_row.get("missingReason") or []
                        if not isinstance(before_reason, list):
                            before_reason = [str(before_reason)]
                        before_warnings = before_row.get("parseWarnings") or []
                        if not isinstance(before_warnings, list):
                            before_warnings = [str(before_warnings)]
                        error_type = "beforeinfo_unknown_error"
                        if before_status == "unavailable":
                            if not str(before_row.get("html") or "").strip():
                                error_type = "beforeinfo_fetch_empty_html"
                            elif str(before_row.get("fetchStatus") or "").lower() not in {"ok", "available", "200"}:
                                error_type = "beforeinfo_fetch_http_error"
                        elif before_status == "parse_error":
                            if "beforeinfo_parse_no_table" in before_reason or "beforeinfo_parse_no_table" in before_warnings:
                                error_type = "beforeinfo_parse_no_table"
                            else:
                                error_type = "beforeinfo_unknown_error"
                        elif "beforeinfo_before_publish" in before_reason:
                            error_type = "beforeinfo_before_publish"
                        elif "beforeinfo_parse_zero_count" in before_warnings:
                            error_type = "beforeinfo_parse_zero_count"
                        elif "beforeinfo_parse_partial" in before_reason or any(str(w).endswith("_missing") for w in before_warnings):
                            error_type = "beforeinfo_parse_partial"
                        message = ",".join([str(v) for v in before_reason + before_warnings if v]) or before_status
                        _save_error(
                            date8,
                            stage=stage,
                            jcd=venue_jcd,
                            rno=race_no,
                            error_type=error_type,
                            message=message,
                            url=str(before_row.get("url", "")),
                            fetched_at=str(before_row.get("fetchedAt", "")),
                        )
                        errors.append({"jcd": venue_jcd, "rno": race_no, "stage": stage, "type": error_type, "message": message})
                elif stage == "odds":
                    odds_failed += 1
                    odds_row = row.get("odds3t") or {}
                    missing_reason = odds_row.get("missingReason") or []
                    if not isinstance(missing_reason, list):
                        missing_reason = [str(missing_reason)]
                    error_type = str(odds_row.get("errorType") or (missing_reason[0] if missing_reason else "odds_unknown_error"))
                    error_message = str(odds_row.get("errorMessage") or (missing_reason[0] if missing_reason else "odds unavailable"))
                    _save_error(
                        date8,
                        stage=stage,
                        jcd=venue_jcd,
                        rno=race_no,
                        error_type=error_type,
                        message=error_message,
                        url=str(odds_row.get("url", "")),
                        fetched_at=str(odds_row.get("fetchedAt", "")),
                    )
                    errors.append({"jcd": venue_jcd, "rno": race_no, "stage": stage, "type": error_type, "message": error_message})
                elif stage == "result":
                    result_row = row.get("result") or {}
                    result_status = str(result_row.get("dataStatus") or "pending")
                    missing_reason = result_row.get("missingReason") or []
                    if not isinstance(missing_reason, list):
                        missing_reason = [str(missing_reason)]
                    parse_warnings = result_row.get("parseWarnings") or []
                    if not isinstance(parse_warnings, list):
                        parse_warnings = [str(parse_warnings)]
                    if result_status in {"pending", "missing", "unavailable", "parse_error"}:
                        error_type = str(result_row.get("errorType") or (missing_reason[0] if missing_reason else "result_unknown_error"))
                        error_message = str(result_row.get("errorMessage") or (missing_reason[0] if missing_reason else "result unavailable"))
                        if result_status == "pending":
                            error_type = "result_before_publish"
                        elif "result_parse_no_trifecta" in missing_reason or "result_parse_no_trifecta" in parse_warnings:
                            error_type = "result_parse_no_trifecta"
                        elif "result_parse_no_table" in parse_warnings:
                            error_type = "result_parse_no_table"
                        elif "result_parse_partial" in parse_warnings:
                            error_type = "result_parse_partial"
                        elif result_status == "unavailable" and not str(result_row.get("html") or "").strip():
                            error_type = "result_fetch_empty_html"
                        elif result_status == "unavailable" and str(result_row.get("fetchStatus") or "").lower() not in {"ok", "available", "200", "live"}:
                            error_type = "result_fetch_http_error"
                        elif result_status == "missing":
                            error_type = "result_unavailable_expected"
                        _save_error(
                            date8,
                            stage=stage,
                            jcd=venue_jcd,
                            rno=race_no,
                            error_type=error_type,
                            message=error_message,
                            url=str(result_row.get("url", "")),
                            fetched_at=str(result_row.get("fetchedAt", "")),
                        )
                        errors.append({"jcd": venue_jcd, "rno": race_no, "stage": stage, "type": error_type, "message": error_message})

                for pred in predictions[:10]:
                    decision = str(pred.get("decision") or "").upper()
                    if decision == "BUY":
                        buy_count += 1
                    elif decision == "WATCH":
                        watch_count += 1
                    else:
                        skip_count += 1
            except Exception as exc:  # pragma: no cover - defensive
                message = str(exc)
                errors.append({"jcd": venue_jcd, "rno": race_no, "stage": stage, "type": "pipeline_error", "message": message})
                _save_error(date8, stage=stage, jcd=venue_jcd, rno=race_no, error_type="pipeline_error", message=message, url=(row.get("racelist") or {}).get("url", ""), fetched_at=(row.get("racelist") or {}).get("fetchedAt", ""))
                snapshot = build_race_snapshot(
                    date=date8,
                    jcd=venue_jcd,
                    venue_name=row.get("venue_name") or JCD_TO_VENUE.get(venue_jcd, venue_jcd),
                    rno=race_no,
                    stage=stage,
                    boats=[Boat(boat_no=i, data_status="missing") for i in range(1, 7)],
                    data_status={"racelist": "missing", "odds3t": "unavailable", "beforeinfo": "pending", "result": "pending"},
                    source={
                        "racelistUrl": (row.get("racelist") or {}).get("url", ""),
                        "racelistFetchedAt": (row.get("racelist") or {}).get("fetchedAt", ""),
                        "odds3tUrl": (row.get("odds3t") or {}).get("url", ""),
                        "odds3tFetchedAt": (row.get("odds3t") or {}).get("fetchedAt", ""),
                        "stage": stage,
                        "modelVersion": MODEL_VERSION,
                        "error": message,
                    },
                )
                venue_rows.append((snapshot, []))
                norm_path = normalized_dir / venue_jcd / f"race_{race_no}.json"
                _write_json(norm_path, snapshot.to_dict())
                written_normalized.append(str(norm_path))

        payload = build_ui_payload(venue_rows)
        ui_path = write_ui_payload(payload, output_dir=ui_dir)
        written_ui.append(str(ui_path))
        if stage in {"odds", "beforeinfo"}:
            frozen_payloads.append(
                _build_frozen_venue_payload(
                    date8=date8,
                    venue_jcd=venue_jcd,
                    venue_rows=venue_rows,
                    ui_path=ui_path,
                    stage=stage,
                )
            )

    frozen_written = _write_frozen_payloads(date8, frozen_payloads) if stage in {"odds", "beforeinfo"} else []

    return {
        "date": date8,
        "jcd": jcd,
        "stage": stage,
        "race_count": len(written_normalized),
        "venues": venue_codes,
        "written": {"ui": written_ui, "normalized": written_normalized, "predictions": written_predictions, "frozen": frozen_written},
        "errors": errors,
        "buyCount": buy_count,
        "watchCount": watch_count,
        "skipCount": skip_count,
        "odds3tOkCount": odds_ok,
        "odds3tFailedCount": odds_failed,
        "ui_dir": str(ui_dir),
        "normalized_dir": str(normalized_dir),
        "pred_dir": str(pred_dir),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the daily BOATRACE pipeline.")
    parser.add_argument("--date", required=True, help="today, YYYYMMDD, or YYYY-MM-DD")
    parser.add_argument("--jcd", default="all", help="all or venue code")
    parser.add_argument("--races", default=None, help="1-12 or comma list")
    parser.add_argument("--stage", default="pre_race", choices=["pre_race", "odds", "beforeinfo", "result"])
    args = parser.parse_args()
    result = run_today(
        target_date=_normalize_date(args.date),
        jcd=str(args.jcd).zfill(2) if str(args.jcd).isdigit() else "all",
        races=_parse_races(args.races),
        stage=args.stage,
    )
    compact = {
        "date": result.get("date"),
        "jcd": result.get("jcd"),
        "stage": result.get("stage"),
        "race_count": result.get("race_count"),
        "venues": result.get("venues"),
        "buyCount": result.get("buyCount"),
        "watchCount": result.get("watchCount"),
        "skipCount": result.get("skipCount"),
        "odds3tOkCount": result.get("odds3tOkCount"),
        "odds3tFailedCount": result.get("odds3tFailedCount"),
        "written": result.get("written"),
        "errorsCount": len(result.get("errors") or []),
        "generated_at": result.get("generated_at"),
        "ui_dir": result.get("ui_dir"),
        "normalized_dir": result.get("normalized_dir"),
        "pred_dir": result.get("pred_dir"),
    }
    print(json.dumps(compact, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
