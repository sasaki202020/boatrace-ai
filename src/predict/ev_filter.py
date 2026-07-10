from __future__ import annotations

from typing import Any


def _reason_list(*values: str) -> str:
    return ",".join(dict.fromkeys(v for v in values if v))


def _odds_ok(odds: Any) -> bool:
    try:
        value = float(odds)
        return 1.0 <= value <= 999.9
    except Exception:
        return False


def decide_prediction(pred: dict[str, Any], *, stage: str, data_status: dict[str, str] | str) -> dict[str, Any]:
    odds = pred.get("odds")
    prob = float(pred.get("prob") or 0.0)
    ev = pred.get("expected_value")
    quality = float(pred.get("extra", {}).get("quality") or 0.0)
    beforeinfo_status = pred.get("extra", {}).get("beforeinfo_status")
    beforeinfo_reason = pred.get("extra", {}).get("beforeinfo_reason")
    racelist_status = data_status if isinstance(data_status, str) else str((data_status or {}).get("racelist") or "pending")
    odds_status = data_status if isinstance(data_status, str) else str((data_status or {}).get("odds3t") or "pending")
    reasons: list[str] = []

    if stage == "pre_race":
        if odds is None:
            if racelist_status in {"missing", "unavailable"} and prob < 0.01:
                return {**pred, "decision": "SKIP", "reason": _reason_list("data_missing", "odds_pending")}
            if prob >= 0.005 and quality >= 0.05:
                return {**pred, "decision": "WATCH", "reason": _reason_list("pre_race_score_high_odds_pending")}
            return {**pred, "decision": "SKIP", "reason": _reason_list("pre_race_score_weak", "odds_pending")}
        if prob >= 0.01 and quality >= 0.02:
            return {**pred, "decision": "WATCH", "reason": _reason_list("pre_race_no_buy")}
        return {**pred, "decision": "SKIP", "reason": _reason_list("pre_race_no_buy")}

    if racelist_status in {"missing", "unavailable"} and prob < 0.01:
        reasons.append("data_missing")
    if odds is None or odds_status in {"pending", "unavailable"}:
        reasons.append("odds_pending")
        if prob >= 0.005 and quality >= 0.05 and beforeinfo_status in {None, "", "ok", "pending"}:
            return {**pred, "decision": "WATCH", "reason": _reason_list(*reasons, "beforeinfo_wait")}
        return {**pred, "decision": "SKIP", "reason": _reason_list(*reasons, "beforeinfo_wait")}
    if not _odds_ok(odds):
        return {**pred, "decision": "SKIP", "reason": _reason_list("odds_invalid", *reasons)}
    if ev is None:
        return {**pred, "decision": "WATCH", "reason": _reason_list("ev_pending", *reasons)}

    ev_value = float(ev)
    if stage == "beforeinfo":
        if beforeinfo_status in {"unavailable", "parse_error"}:
            reasons.append("beforeinfo_missing")
        if ev_value >= 1.05 and prob >= 0.01 and quality >= 0.02 and beforeinfo_status in {None, "", "ok", "pending"}:
            return {**pred, "decision": "BUY", "reason": _reason_list("ev_ok", "beforeinfo_ok", *reasons)}
        if 0.95 <= ev_value < 1.05 or beforeinfo_status in {"pending", "unavailable", "parse_error"}:
            return {**pred, "decision": "WATCH", "reason": _reason_list("ev_borderline", "beforeinfo_wait", *reasons)}
        return {**pred, "decision": "SKIP", "reason": _reason_list("ev_insufficient", "beforeinfo_bad", *reasons)}

    if ev_value >= 1.05 and prob >= 0.01 and quality >= 0.02:
        return {**pred, "decision": "BUY", "reason": _reason_list("ev_ok", *reasons)}
    if 0.95 <= ev_value < 1.05:
        return {**pred, "decision": "WATCH", "reason": _reason_list("ev_borderline", *reasons)}
    if ev_value < 0.95:
        return {**pred, "decision": "SKIP", "reason": _reason_list("ev_insufficient", *reasons)}
    return {**pred, "decision": "WATCH", "reason": _reason_list("ev_borderline", *reasons)}
