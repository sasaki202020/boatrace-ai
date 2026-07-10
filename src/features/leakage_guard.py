from __future__ import annotations

from typing import Any

from src.features.feature_policy import allowed_features


STAGE_BLOCKLIST = {
    "pre_race": {"result", "odds3t", "payout", "finish_order"},
    "beforeinfo": {"result", "payout", "finish_order"},
    "odds": {"result", "payout", "finish_order"},
    "result": set(),
}


def filter_for_stage(payload: dict[str, Any], stage: str) -> dict[str, Any]:
    allowed = allowed_features(stage)
    block = STAGE_BLOCKLIST.get(str(stage or "pre_race").lower(), STAGE_BLOCKLIST["pre_race"])
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if key in block:
            continue
        if key in allowed or key in {"boat_no", "racer_name", "branch", "age", "weight", "f_count", "l_count", "source", "data_status"}:
            out[key] = value
    return out
