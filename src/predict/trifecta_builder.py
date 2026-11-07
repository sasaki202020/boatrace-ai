from __future__ import annotations

from itertools import permutations
from typing import Any

from src.normalize.schema import Prediction
from src.predict.baseline_score_model import softmax


def _odds_map(odds3t: list[dict[str, Any]] | dict[str, Any] | None) -> dict[str, float]:
    if isinstance(odds3t, dict):
        out: dict[str, float] = {}
        for key, value in odds3t.items():
            try:
                out[str(key)] = float(value)
            except Exception:
                continue
        return out
    out: dict[str, float] = {}
    for row in odds3t or []:
        combo = str(row.get("combo") or row.get("trifecta") or "").strip()
        if not combo:
            continue
        try:
            out[combo] = float(row.get("odds"))
        except Exception:
            continue
    return out


def _safe_score(value: Any) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def build_trifecta_candidates(
    boat_scores: list[dict[str, Any]],
    odds3t: list[dict[str, Any]] | dict[str, Any] | None = None,
) -> list[Prediction]:
    ordered = sorted(
        [row for row in boat_scores if row.get("boat_no") is not None],
        key=lambda row: _safe_score(row.get("boat_score")),
        reverse=True,
    )[:6]
    lanes = [int(row["boat_no"]) for row in ordered]
    score_by_lane = {int(row["boat_no"]): _safe_score(row.get("boat_score")) for row in ordered}
    if len(lanes) < 3:
        return []

    lane_probs = dict(zip(lanes, softmax([score_by_lane[lane] for lane in lanes], temperature=1.15)))
    odds_lookup = _odds_map(odds3t)

    rows: list[Prediction] = []
    for first, second, third in permutations(lanes, 3):
        first_prob = lane_probs.get(first, 0.0)
        second_choices = [lane for lane in lanes if lane != first]
        second_probs = softmax([score_by_lane[lane] for lane in second_choices], temperature=1.25)
        second_prob_map = dict(zip(second_choices, second_probs))
        third_choices = [lane for lane in second_choices if lane != second]
        third_probs = softmax([score_by_lane[lane] for lane in third_choices], temperature=1.35)
        third_prob_map = dict(zip(third_choices, third_probs))

        combo_prob = first_prob * second_prob_map.get(second, 0.0) * third_prob_map.get(third, 0.0)
        combo = f"{first}-{second}-{third}"
        odds = odds_lookup.get(combo)
        expected_value = (combo_prob * odds) if odds is not None else None
        edge = (expected_value - 1.0) if expected_value is not None else None
        if expected_value is None:
            grade = "C"
        elif expected_value >= 1.15:
            grade = "A"
        elif expected_value >= 1.05:
            grade = "B"
        else:
            grade = "C"

        quality = round(score_by_lane[first] - score_by_lane[third], 4)
        rows.append(
            Prediction(
                combo=combo,
                prob=round(combo_prob, 6),
                odds=odds,
                expected_value=round(expected_value, 4) if expected_value is not None else None,
                edge=round(edge, 4) if edge is not None else None,
                rank=0,
                grade=grade,
                decision="WATCH" if odds is None else "PENDING",
                reason="pre_race_score_high_odds_pending" if odds is None else "odds_available",
                prob_rank=0,
                ev_rank=0,
                extra={
                    "first_boat_score": score_by_lane[first],
                    "second_boat_score": score_by_lane[second],
                    "third_boat_score": score_by_lane[third],
                    "quality": quality,
                },
            )
        )

    prob_sorted = sorted(rows, key=lambda row: row.prob, reverse=True)
    for idx, row in enumerate(prob_sorted, start=1):
        row.prob_rank = idx
        row.rank = idx
    ev_sorted = sorted(
        rows,
        key=lambda row: (row.expected_value if row.expected_value is not None else -1.0, row.prob),
        reverse=True,
    )
    for idx, row in enumerate(ev_sorted, start=1):
        row.ev_rank = idx
    return prob_sorted
