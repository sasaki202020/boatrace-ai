from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from src.normalize.schema import RaceSnapshot


ROOT = Path(__file__).resolve().parents[2]
UI_ROOT = ROOT / "data" / "ui"


def _boat_to_ui(boat: Any) -> dict[str, Any]:
    if hasattr(boat, "to_dict"):
        boat = boat.to_dict()
    elif hasattr(boat, "__dict__"):
        boat = boat.__dict__
    return {
        "boat_no": int(boat.get("boat_no", 0) or 0),
        "racer_name": boat.get("racer_name"),
        "racer_id": boat.get("racer_id"),
        "branch": boat.get("branch"),
        "class": boat.get("cls") or boat.get("class"),
        "age": boat.get("age"),
        "weight": boat.get("weight"),
        "avg_st": boat.get("avg_st"),
        "national_win_rate": boat.get("national_win_rate"),
        "national_2rate": boat.get("national_2rate"),
        "national_3rate": boat.get("national_3rate"),
        "local_win_rate": boat.get("local_win_rate"),
        "local_2rate": boat.get("local_2rate"),
        "local_3rate": boat.get("local_3rate"),
        "motor_no": boat.get("motor_no"),
        "motor_2rate": boat.get("motor_2rate"),
        "boat_no_equipment": boat.get("boat_no_equipment"),
        "boat_2rate": boat.get("boat_2rate"),
        "f_count": boat.get("f_count"),
        "l_count": boat.get("l_count"),
        "boat_score": boat.get("boat_score"),
        "score_rank": boat.get("score_rank"),
        "scoreRank": boat.get("score_rank"),
        "score_reason": boat.get("score_reason"),
        "scoreReason": boat.get("score_reason"),
        "exhibition_time": boat.get("exhibition_time"),
        "exhibition_st": boat.get("exhibition_st"),
        "exhibitionTime": boat.get("exhibition_time"),
        "startExhibitionCourse": boat.get("start_exhibition_course"),
        "startExhibitionSt": boat.get("start_exhibition_st") or boat.get("exhibition_st"),
        "tilt": boat.get("tilt"),
        "propeller": boat.get("propeller"),
        "partsExchange": boat.get("parts_exchange") or [],
        "weightAdjustment": boat.get("weight_adjustment"),
        "dataStatus": boat.get("data_status") or "missing",
        "source": boat.get("source") or {},
    }


def _prediction_to_ui(pred: Any) -> dict[str, Any]:
    if hasattr(pred, "to_dict"):
        pred = pred.to_dict()
    elif hasattr(pred, "__dict__"):
        pred = pred.__dict__
    extra = pred.get("extra") or {}
    return {
        "combo": pred.get("combo") or pred.get("trifecta") or "",
        "prob": pred.get("prob"),
        "odds": pred.get("odds"),
        "expectedValue": pred.get("expected_value"),
        "edge": pred.get("edge"),
        "rank": pred.get("rank"),
        "probRank": pred.get("prob_rank"),
        "evRank": pred.get("ev_rank"),
        "grade": pred.get("grade"),
        "decision": pred.get("decision"),
        "reason": pred.get("reason"),
        "stage": pred.get("extra", {}).get("stage"),
        "beforeinfoReason": pred.get("extra", {}).get("beforeinfo_reason"),
        "quality": extra.get("quality"),
        "firstBoatScore": extra.get("first_boat_score"),
        "secondBoatScore": extra.get("second_boat_score"),
        "thirdBoatScore": extra.get("third_boat_score"),
    }


def _status_text(data_status: dict[str, str] | str | None) -> str:
    if isinstance(data_status, str):
        return data_status
    if not data_status:
        return "missing"
    values = [str(data_status.get(key) or "").lower() for key in ("racelist", "beforeinfo", "odds3t", "result")]
    if any(value in {"ok", "available", "ready"} for value in values):
        return "available"
    if any(value == "pending" for value in values):
        return "pending"
    for value in values:
        if value:
            return value
    return "pending"


def _snapshot_iter(snapshot_rows: list[Any]) -> list[tuple[RaceSnapshot, list[Any]]]:
    normalized: list[tuple[RaceSnapshot, list[Any]]] = []
    for item in snapshot_rows:
        if isinstance(item, tuple) and len(item) == 2:
            snapshot, predictions = item
            normalized.append((snapshot, list(predictions or [])))
            continue
        if isinstance(item, RaceSnapshot):
            normalized.append((item, list(item.predictions)))
    return normalized


def build_ui_payload(snapshot_rows: list[Any]) -> dict[str, Any]:
    rows = _snapshot_iter(snapshot_rows)
    now = datetime.now().isoformat(timespec="seconds")
    if not rows:
        return {
            "date": "",
            "venue": "",
            "event": "",
            "stage": "missing",
            "updatedAt": now,
            "jcd": "",
            "dataStatus": "missing",
            "modelVersion": "baseline_rule_v1",
            "races": [],
        }

    first_snapshot = rows[0][0]
    races = []
    for snapshot, predictions in sorted(rows, key=lambda item: item[0].rno):
        races.append(
            {
                "raceNumber": snapshot.rno,
                "deadline": snapshot.deadline or "",
                "status": snapshot.stage,
                "raceTitle": snapshot.race_title or "",
                "boats": [_boat_to_ui(boat) for boat in snapshot.boats],
                "aiPredictions": [_prediction_to_ui(pred) for pred in predictions[:10]],
                "startExhibition": snapshot.start_exhibition or [],
                "weather": snapshot.weather,
                "dataStatus": snapshot.data_status,
                "dataStatusReason": snapshot.data_status_reason or snapshot.source.get("data_status_reason") or {},
                "dataStatusText": _status_text(snapshot.data_status),
                "missingReason": snapshot.data_status_reason or snapshot.source.get("data_status_reason") or {},
                "beforeInfo": snapshot.before_info or snapshot.source.get("beforeinfo") or {},
                "result": snapshot.result or {},
                "odds3t": snapshot.odds3t or {},
                "source": snapshot.source or {},
                "updatedAt": getattr(snapshot, "updated_at", now) or now,
            }
        )

    payload = {
        "date": first_snapshot.date,
        "venue": first_snapshot.venue_name,
        "event": "",
        "stage": first_snapshot.stage,
        "updatedAt": now,
        "jcd": first_snapshot.jcd,
        "dataStatus": first_snapshot.data_status,
        "dataStatusReason": first_snapshot.data_status_reason,
        "modelVersion": first_snapshot.model_version,
        "source": first_snapshot.source,
        "beforeInfo": first_snapshot.before_info or {},
        "races": races,
    }
    return payload


def write_ui_payload(payload: dict[str, Any], *, output_dir: Path) -> Path:
    import json

    output_dir.mkdir(parents=True, exist_ok=True)
    jcd = str(payload.get("jcd") or "").zfill(2)
    if not jcd and payload.get("races"):
        first_race = payload["races"][0]
        source = first_race.get("source") or {}
        url = str(source.get("racelistUrl") or source.get("url") or "")
        if "jcd=" in url:
            import re

            m = re.search(r"jcd=(\d{1,2})", url)
            if m:
                jcd = f"{int(m.group(1)):02d}"
    path = output_dir / f"raceyosou_{jcd or '00'}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
