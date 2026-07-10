from __future__ import annotations

import re
from math import exp
from typing import Any


MODEL_VERSION = "baseline_rule_v1"


def _num(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        n = float(value)
        if n != n:
            return default
        return n
    except Exception:
        return default


def _st_num(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("Ｆ", "F").replace("ｆ", "F").replace("Ｌ", "L").replace("ｌ", "L")
    m = re.search(r"([FL]?)([+-]?\d+(?:\.\d+)?)", text)
    if m:
        try:
            val = float(m.group(2))
            if m.group(1).upper() == "F":
                return -abs(val)
            if m.group(1).upper() == "L":
                return abs(val)
            return val
        except Exception:
            return None
    return _num(value, None)


def _append_reason(parts: list[str], label: str, value: float) -> None:
    parts.append(f"{label}={value:+.3f}")


def score_boat(boat: dict[str, Any], *, lane: int, stage: str = "pre_race") -> dict[str, Any]:
    score = 0.0
    reasons: list[str] = []
    missing_penalty = 0.0

    course_bonus = {1: 1.25, 2: 0.8, 3: 0.45, 4: 0.15, 5: -0.2, 6: -0.45}
    course_score = course_bonus.get(int(lane), 0.0)
    score += course_score
    _append_reason(reasons, "course", course_score)

    for key, weight, label in [
        ("national_win_rate", 0.9, "nat"),
        ("local_win_rate", 0.8, "local"),
        ("motor_2rate", 0.6, "motor"),
        ("boat_2rate", 0.45, "boat"),
    ]:
        n = _num(boat.get(key))
        if n is None:
            missing_penalty += 0.12
            reasons.append(f"{label}=missing")
            continue
        contribution = (n / 100.0) * weight
        score += contribution
        _append_reason(reasons, label, contribution)

    avg_st = _num(boat.get("avg_st"))
    if avg_st is None:
        missing_penalty += 0.18
        reasons.append("avg_st=missing")
    else:
        contribution = max(0.0, 0.32 - avg_st) * 2.5
        score += contribution
        _append_reason(reasons, "avg_st", contribution)

    exhibition_active = stage in {"beforeinfo", "odds", "result"}
    exhibition_time = _num(boat.get("exhibition_time"))
    exhibition_st = _st_num(boat.get("exhibition_st"))
    start_exhibition_st = _st_num(boat.get("start_exhibition_st"))
    start_exhibition_course = _num(boat.get("start_exhibition_course"))
    tilt = _num(boat.get("tilt"))

    if exhibition_active:
        if exhibition_st is None and start_exhibition_st is None:
            missing_penalty += 0.08
            reasons.append("展示ST=missing")
            reasons.append("展示未取得")
        else:
            st_value = exhibition_st if exhibition_st is not None else start_exhibition_st
            contribution = max(0.0, 0.24 - st_value) * 1.25 if st_value is not None else 0.0
            score += contribution
            if contribution > 0:
                reasons.append("展示ST良好")
            _append_reason(reasons, "exhibition_st", contribution)

        if exhibition_time is None:
            missing_penalty += 0.06
            reasons.append("展示タイム=missing")
        else:
            contribution = max(0.0, 6.6 - exhibition_time) * 0.12
            score += contribution
            if contribution > 0:
                reasons.append("展示タイム上位")
            _append_reason(reasons, "exhibition_time", contribution)

        if start_exhibition_course is not None and int(start_exhibition_course) != int(lane):
            course_penalty = min(0.12, abs(int(start_exhibition_course) - int(lane)) * 0.03)
            score -= course_penalty
            _append_reason(reasons, "course_mismatch", -course_penalty)
            reasons.append("展示コースズレ")

        if tilt is not None and abs(tilt) >= 0.5:
            reasons.append("チルト変更あり")
            score -= min(0.08, abs(tilt) * 0.02)
    else:
        reasons.append("展示未使用")

    f_count = _num(boat.get("f_count"), 0.0) or 0.0
    l_count = _num(boat.get("l_count"), 0.0) or 0.0
    risk_penalty = (0.18 * f_count) + (0.08 * l_count)
    score -= risk_penalty
    _append_reason(reasons, "risk", -risk_penalty)

    if str(boat.get("data_status") or "").lower() not in {"available", "complete", "ok"}:
        missing_penalty += 0.35
        reasons.append("data_status=missing")

    score -= missing_penalty
    _append_reason(reasons, "missing", -missing_penalty)

    return {
        "boat_no": int(lane),
        "boat_score": round(score, 4),
        "boatScore": round(score, 4),
        "score_rank": None,
        "scoreRank": None,
        "score_reason": ";".join(reasons),
        "scoreReason": ";".join(reasons),
        "data_status": str(boat.get("data_status") or "missing"),
        "model_version": MODEL_VERSION,
        "stage": str(stage or "pre_race"),
    }


def score_boats(boats: list[dict[str, Any]], *, stage: str = "pre_race") -> list[dict[str, Any]]:
    scored = [score_boat(boat, lane=int(boat.get("boat_no") or idx + 1), stage=stage) for idx, boat in enumerate(boats)]
    scored.sort(key=lambda row: row["boat_score"], reverse=True)
    for idx, row in enumerate(scored, start=1):
        row["score_rank"] = idx
    return scored


def softmax(scores: list[float], temperature: float = 1.0) -> list[float]:
    if not scores:
        return []
    scaled = [s / max(1e-6, temperature) for s in scores]
    anchor = max(scaled)
    exps = [exp(s - anchor) for s in scaled]
    total = sum(exps) or 1.0
    return [v / total for v in exps]
