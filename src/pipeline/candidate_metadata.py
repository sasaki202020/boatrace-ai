from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any


LEGACY_UNKNOWN = "legacy_unknown"
DEFAULT_CALIBRATOR_VERSION = "uncalibrated_identity_v1"
DEFAULT_POLICY_VERSION = "paper_shadow_policy_v1"
DEFAULT_FEATURE_VERSION = "baseline_score_features_v1"


FORWARD_METADATA_FIELDS = [
    "candidateId",
    "modelVersion",
    "calibratorVersion",
    "policyVersion",
    "predictionHash",
    "snapshotHash",
    "featureVersion",
    "rawProbability",
    "calibratedProbability",
    "odds",
    "oddsCapturedAt",
    "deadlineAt",
    "policyDecision",
    "guardDecision",
    "guardReason",
    "frozenAt",
]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _known(value: Any) -> Any:
    token = _text(value)
    return token if token else LEGACY_UNKNOWN


def _safe_float(value: Any) -> float | str:
    if value is None or value == "":
        return LEGACY_UNKNOWN
    try:
        return float(value)
    except Exception:
        return LEGACY_UNKNOWN


def _stable_hash_payload(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalized_date(value: Any) -> str:
    token = _text(value)
    digits = "".join(ch for ch in token if ch.isdigit())
    if len(digits) == 8:
        return digits
    return token or LEGACY_UNKNOWN


def _normalized_race_no(value: Any) -> str:
    try:
        return f"{int(value):02d}"
    except Exception:
        return _text(value) or LEGACY_UNKNOWN


def resolve_deadline_at(race_date: Any, deadline: Any) -> str:
    date_digits = _normalized_date(race_date)
    deadline_text = _text(deadline)
    if date_digits == LEGACY_UNKNOWN or not deadline_text:
        return LEGACY_UNKNOWN
    if "T" in deadline_text:
        return deadline_text
    if len(date_digits) != 8:
        return LEGACY_UNKNOWN
    time_text = deadline_text
    if len(time_text) == 4 and time_text.isdigit():
        time_text = f"{time_text[:2]}:{time_text[2:]}"
    if ":" not in time_text:
        return LEGACY_UNKNOWN
    try:
        parsed = datetime.strptime(f"{date_digits} {time_text[:5]}", "%Y%m%d %H:%M")
    except Exception:
        return LEGACY_UNKNOWN
    return parsed.isoformat(timespec="minutes")


def build_candidate_id(
    *,
    race_date: Any,
    race_id: Any,
    combo: Any,
    prediction_hash: Any,
    policy_version: Any,
) -> str:
    payload = "|".join(
        [
            _normalized_date(race_date),
            _text(race_id) or LEGACY_UNKNOWN,
            _text(combo) or LEGACY_UNKNOWN,
            _text(prediction_hash) or LEGACY_UNKNOWN,
            _text(policy_version) or LEGACY_UNKNOWN,
        ]
    )
    return _stable_hash_payload(payload)


def _probability(row: dict[str, Any]) -> float | str:
    for key in ("rawProbability", "raw_probability", "prob", "approx_prob", "calibratedProbability", "calibrated_prob"):
        value = _safe_float(row.get(key))
        if value != LEGACY_UNKNOWN:
            return value
    return LEGACY_UNKNOWN


def _calibrated_probability(row: dict[str, Any], raw_probability: float | str) -> float | str:
    for key in ("calibratedProbability", "calibrated_prob"):
        value = _safe_float(row.get(key))
        if value != LEGACY_UNKNOWN:
            return value
    return raw_probability


def _odds(row: dict[str, Any]) -> float | str:
    for key in ("odds", "real_odds"):
        value = _safe_float(row.get(key))
        if value != LEGACY_UNKNOWN:
            return value
    return LEGACY_UNKNOWN


def _policy_decision(row: dict[str, Any]) -> str:
    for key in ("policyDecision", "decision", "paper_decision", "paperDecision", "final_decision", "finalDecision"):
        token = _text(row.get(key))
        if token:
            return token
    return LEGACY_UNKNOWN


def _guard_decision(policy_decision: str) -> str:
    token = policy_decision.upper()
    if token in {"BUY", "WATCH", "PAPER", "PENDING"}:
        return "PASS"
    if token == LEGACY_UNKNOWN:
        return LEGACY_UNKNOWN
    return "REJECT"


def _guard_reason(row: dict[str, Any]) -> str:
    for key in ("guardReason", "reason", "stop_reason", "stopReason", "skip_reason"):
        token = _text(row.get(key))
        if token:
            return token
    return LEGACY_UNKNOWN


def enrich_candidate_metadata(
    row: dict[str, Any],
    *,
    race_date: Any,
    jcd: Any,
    race_no: Any,
    race_id: Any,
    model_version: Any,
    policy_version: Any = DEFAULT_POLICY_VERSION,
    feature_version: Any = DEFAULT_FEATURE_VERSION,
    calibrator_version: Any = DEFAULT_CALIBRATOR_VERSION,
    odds_captured_at: Any = "",
    deadline_at: Any = "",
    frozen_at: Any = "",
    snapshot_payload: Any | None = None,
) -> dict[str, Any]:
    enriched = dict(row)
    combo = _text(enriched.get("combo") or enriched.get("trifecta") or enriched.get("recommended_trifecta"))
    raw_probability = _probability(enriched)
    calibrated_probability = _calibrated_probability(enriched, raw_probability)
    odds_value = _odds(enriched)
    policy_decision = _policy_decision(enriched)
    guard_decision = _guard_decision(policy_decision)
    guard_reason = _guard_reason(enriched)

    snapshot_hash = _stable_hash_payload(
        snapshot_payload
        if snapshot_payload is not None
        else {
            "date": _normalized_date(race_date),
            "jcd": _text(jcd),
            "raceNo": _normalized_race_no(race_no),
            "raceId": _text(race_id),
            "featureVersion": _text(feature_version),
        }
    )
    prediction_hash = _text(enriched.get("predictionHash")) or _stable_hash_payload(
        {
            "raceDate": _normalized_date(race_date),
            "raceId": _text(race_id),
            "combo": combo,
            "rawProbability": raw_probability,
            "calibratedProbability": calibrated_probability,
            "odds": odds_value if odds_value != LEGACY_UNKNOWN else enriched.get("odds", enriched.get("real_odds", LEGACY_UNKNOWN)),
            "modelVersion": _text(model_version),
            "policyDecision": policy_decision,
        }
    )
    candidate_id = _text(enriched.get("candidateId")) or build_candidate_id(
        race_date=race_date,
        race_id=race_id,
        combo=combo,
        prediction_hash=prediction_hash,
        policy_version=policy_version,
    )

    enriched.update(
        {
            "candidateId": candidate_id,
            "modelVersion": _known(model_version),
            "calibratorVersion": _known(calibrator_version),
            "policyVersion": _known(policy_version),
            "predictionHash": prediction_hash,
            "snapshotHash": snapshot_hash,
            "featureVersion": _known(feature_version),
            "rawProbability": raw_probability,
            "calibratedProbability": calibrated_probability,
            "odds": odds_value,
            "oddsCapturedAt": _known(odds_captured_at),
            "deadlineAt": _known(deadline_at),
            "policyDecision": policy_decision,
            "guardDecision": guard_decision,
            "guardReason": guard_reason,
            "frozenAt": _known(frozen_at),
        }
    )
    enriched.setdefault("raceId", _text(race_id) or LEGACY_UNKNOWN)
    enriched.setdefault("raceDate", _normalized_date(race_date))
    enriched.setdefault("venueCode", _text(jcd).zfill(2) if _text(jcd).isdigit() else _text(jcd) or LEGACY_UNKNOWN)
    enriched.setdefault("raceNo", _normalized_race_no(race_no))
    enriched.setdefault("combination", combo or LEGACY_UNKNOWN)
    enriched.setdefault("frozenBetId", candidate_id)
    return enriched


def assert_unique_candidate_ids(rows: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for row in rows:
        candidate_id = _text(row.get("candidateId"))
        if not candidate_id or candidate_id == LEGACY_UNKNOWN:
            continue
        if candidate_id in seen:
            duplicates.add(candidate_id)
        seen.add(candidate_id)
    if duplicates:
        sample = ", ".join(sorted(duplicates)[:3])
        raise ValueError(f"duplicate candidateId detected: {sample}")
