from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from src.commercialization_v2.day1_readiness import (
    generate_prediction_rows,
    sha256_file,
    validate_runtime_bfile,
)
from src.ingest.parsers.official_k_result_parser import parse_official_k_result_file

JST = ZoneInfo("Asia/Tokyo")
MODEL_SHA256 = "a2f11bf69c1b4b7ea47cca847dbe0a46f076f7c08d3361ba9e30b43f12d65da0"
FEATURE_SCHEMA_SHA256 = "a3853bdbdb75d13d4a596928c13eaa034307b14a5a8c534d31fca9acdab623dd"
DATASET_SHA256 = "bc2294f85e482ac1c1e7458236be509afd5d3adc9aa7afd4ec53fc4658e54f23"
VOID_RESULT_STATUSES = {"canceled", "refund", "no_contest", "not_held"}


def stable_hash(value: dict) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def write_new_json(path: Path, value: dict) -> bool:
    raw = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing != value:
            raise ValueError(f"append_only_conflict:{path.name}")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw, encoding="utf-8")
    return True


def generate_daily_predictions(
    *,
    b_file: Path,
    prediction_root: Path,
    model_path: Path,
    history_path: Path,
    now: datetime | None = None,
) -> dict:
    fixed_now = now
    now = fixed_now or datetime.now(JST)
    if sha256_file(model_path) != MODEL_SHA256:
        raise ValueError("model_hash_mismatch")
    if sha256_file(history_path) != DATASET_SHA256:
        raise ValueError("dataset_hash_mismatch")
    entries = validate_runtime_bfile(b_file)
    history = pd.read_csv(history_path)
    input_hash = sha256_file(b_file)
    created = skipped_late = existing = 0
    eligible_frames: list[pd.DataFrame] = []
    for race_id, frame in entries.groupby("race_id", sort=True):
        race_date = str(frame["date"].iloc[0])
        deadline = datetime.fromisoformat(
            f"{race_date}T{str(frame['deadline'].iloc[0])}:00"
        ).replace(tzinfo=JST)
        output = prediction_root / race_date.replace("-", "") / f"{race_id}.json"
        if output.exists():
            existing += 1
            continue
        if now >= deadline:
            skipped_late += 1
            continue
        eligible_frames.append(frame)
    predictions_by_race: dict[str, list[dict]] = {}
    if eligible_frames:
        rows = generate_prediction_rows(
            pd.concat(eligible_frames, ignore_index=True),
            history,
            model_path=model_path,
            expected_model_sha256=MODEL_SHA256,
        )
        for row in rows:
            predictions_by_race.setdefault(str(row["raceId"]), []).append(row)
    for race_id, frame in entries.groupby("race_id", sort=True):
        race_date = str(frame["date"].iloc[0])
        output = prediction_root / race_date.replace("-", "") / f"{race_id}.json"
        if output.exists() or str(race_id) not in predictions_by_race:
            continue
        deadline = datetime.fromisoformat(
            f"{race_date}T{str(frame['deadline'].iloc[0])}:00"
        ).replace(tzinfo=JST)
        generated_at = fixed_now or datetime.now(JST)
        if generated_at >= deadline:
            skipped_late += 1
            continue
        rows = predictions_by_race[str(race_id)]
        probabilities = [
            {
                "boatNo": int(row["lane"]),
                "probability": float(row["predictedProbability"]),
                "rank": int(row["probabilityRank"]),
            }
            for row in sorted(rows, key=lambda item: int(item["probabilityRank"]))
        ]
        body = {
            "raceDate": race_date,
            "raceId": str(race_id),
            "venue": str(frame["jcd"].iloc[0]).zfill(2),
            "raceNo": int(frame["race_no"].iloc[0]),
            "deadlineJst": deadline.isoformat(),
            "generatedAtJst": generated_at.isoformat(),
            "modelVersion": "tree_15",
            "modelSha256": MODEL_SHA256,
            "featureSchemaVersion": FEATURE_SCHEMA_SHA256,
            "inputSha256": input_hash,
            "probabilities": probabilities,
        }
        body["predictionSha256"] = stable_hash(body)
        write_new_json(output, body)
        created += 1
    return {
        "created": created,
        "existing": existing,
        "skippedLate": skipped_late,
        "inputSha256": input_hash,
        "modelSha256": MODEL_SHA256,
        "datasetSha256": DATASET_SHA256,
    }


def _winner(record: dict) -> int | None:
    for boat in record.get("boatResults", []):
        if boat.get("finishPosition") == 1:
            return int(boat["boat_no"])
    combo = record.get("trifectaCombo")
    if isinstance(combo, str) and combo[:1].isdigit():
        return int(combo[0])
    return None


def _existing_settlement_matches(
    current: dict, expected: dict
) -> bool:
    saved_hash = current.get("settlementSha256")
    unsigned = {key: value for key, value in current.items() if key != "settlementSha256"}
    if not isinstance(saved_hash, str) or stable_hash(unsigned) != saved_hash:
        return False
    keys = (
        "raceId", "raceDate", "predictionSha256", "resultSourceSha256",
        "winnerBoat", "winnerRank", "top1Correct", "winnerInTop2",
        "winnerInTop3", "resultSource",
    )
    if not all(current.get(key) == expected.get(key) for key in keys):
        return False
    expected_status = str(expected.get("resultStatus") or "ok").lower()
    expected_settlement_status = str(
        expected.get("settlementStatus") or "settled"
    ).lower()
    if expected_settlement_status == "void":
        return (
            str(current.get("resultStatus") or "").lower() == expected_status
            and str(current.get("settlementStatus") or "").lower() == "void"
        )
    if "resultStatus" in current and str(current["resultStatus"]).lower() != expected_status:
        return False
    if "settlementStatus" in current and str(current["settlementStatus"]).lower() != expected_settlement_status:
        return False
    return True


def settle_available_predictions(
    *, prediction_root: Path, settlement_root: Path, k_root: Path
) -> dict:
    created = existing = pending = conflicts = 0
    void_created = void_existing = 0
    for day_dir in sorted(path for path in prediction_root.iterdir() if path.is_dir()):
        date8 = day_dir.name
        k_file = k_root / f"K{date8[2:]}.TXT"
        files = sorted(day_dir.glob("*.json"))
        if not k_file.exists():
            pending += len(files)
            continue
        parsed = parse_official_k_result_file(k_file, date8=date8)
        results = {
            f"{date8}-{str(row['jcd']).zfill(2)}-{int(row['raceNo']):02d}": row
            for row in parsed.get("races", [])
            if row.get("raceNo") is not None
        }
        not_held_venues = {
            str(row.get("jcd") or "").zfill(2)
            for row in parsed.get("races", [])
            if row.get("raceNo") is None
            and str(row.get("raceStatus") or "").lower() == "not_held"
        }
        result_hash = sha256_file(k_file)
        for prediction_path in files:
            prediction = json.loads(prediction_path.read_text(encoding="utf-8"))
            expected_hash = prediction.pop("predictionSha256")
            if stable_hash(prediction) != expected_hash:
                conflicts += 1
                continue
            prediction["predictionSha256"] = expected_hash
            race_id = str(prediction["raceId"])
            result = results.get(race_id)
            result_status = str(
                (result or {}).get("raceStatus")
                or (result or {}).get("race_status")
                or ""
            ).lower()
            venue = str(prediction.get("venue") or "").zfill(2)
            if result is None and venue in not_held_venues:
                result_status = "not_held"
            is_void = result_status in VOID_RESULT_STATUSES
            winner = None if is_void else _winner(result or {})
            if not is_void and winner is None:
                pending += 1
                continue
            if is_void:
                settlement = {
                    "raceId": race_id,
                    "raceDate": prediction["raceDate"],
                    "predictionSha256": expected_hash,
                    "resultSourceSha256": result_hash,
                    "winnerBoat": None,
                    "winnerRank": None,
                    "top1Correct": None,
                    "winnerInTop2": None,
                    "winnerInTop3": None,
                    "settledAtJst": datetime.now(JST).isoformat(),
                    "resultSource": "official_txt_k",
                    "resultStatus": result_status,
                    "settlementStatus": "void",
                }
            else:
                ranked = sorted(prediction["probabilities"], key=lambda item: item["rank"])
                rank_by_boat = {int(item["boatNo"]): int(item["rank"]) for item in ranked}
                settlement = {
                    "raceId": race_id,
                    "raceDate": prediction["raceDate"],
                    "predictionSha256": expected_hash,
                    "resultSourceSha256": result_hash,
                    "winnerBoat": winner,
                    "winnerRank": rank_by_boat[winner],
                    "top1Correct": rank_by_boat[winner] == 1,
                    "winnerInTop2": rank_by_boat[winner] <= 2,
                    "winnerInTop3": rank_by_boat[winner] <= 3,
                    "settledAtJst": datetime.now(JST).isoformat(),
                    "resultSource": "official_txt_k",
                    "resultStatus": result_status or "ok",
                    "settlementStatus": "settled",
                }
            settlement["settlementSha256"] = stable_hash(settlement)
            output = settlement_root / date8 / f"{race_id}.json"
            if output.exists():
                current = json.loads(output.read_text(encoding="utf-8"))
                if _existing_settlement_matches(current, settlement):
                    existing += 1
                    if is_void:
                        void_existing += 1
                    continue
                conflicts += 1
                continue
            try:
                if write_new_json(output, settlement):
                    created += 1
                    if is_void:
                        void_created += 1
                else:
                    existing += 1
            except ValueError:
                conflicts += 1
    return {
        "created": created,
        "existing": existing,
        "voidCreated": void_created,
        "voidExisting": void_existing,
        "pending": pending,
        "conflicts": conflicts,
    }
