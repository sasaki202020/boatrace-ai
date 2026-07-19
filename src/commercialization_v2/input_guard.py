from __future__ import annotations

import math
from collections import defaultdict
from typing import Any


FORBIDDEN_TOKENS = ("result", "winner", "finish", "rank", "actual", "payout", "return", "refund", "settlement", "final", "odds", "着順", "結果", "払戻", "返還", "確定")


def _is_forbidden_field(field: object) -> bool:
    name = str(field).lower().replace("_", "")
    if name == "probabilityrank":
        return False
    return any(token in name for token in FORBIDDEN_TOKENS)


def validate_prediction_rows(rows: list[dict[str, Any]], *, model_sha256: str, expected_model_sha256: str, feature_schema_sha256: str, expected_feature_schema_sha256: str) -> dict[str, int]:
    if model_sha256 != expected_model_sha256:
        raise ValueError("model_hash_mismatch")
    if feature_schema_sha256 != expected_feature_schema_sha256:
        raise ValueError("feature_schema_hash_mismatch")
    for row in rows:
        offending = [str(key) for key in row if _is_forbidden_field(key)]
        if offending:
            raise ValueError(f"result_field_detected:{','.join(sorted(offending))}")
    grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("raceId"))].append(row)
    if not grouped:
        raise ValueError("no_prediction_rows")
    for race_id, group in grouped.items():
        if len(group) != 6:
            raise ValueError(f"six_boats_required:{race_id}")
        lanes = [int(row["lane"]) for row in group]
        if set(lanes) != set(range(1, 7)) or len(lanes) != len(set(lanes)):
            raise ValueError(f"lane_integrity:{race_id}")
        probabilities = [float(row["predictedProbability"]) for row in group]
        if any(not math.isfinite(value) or value < 0 or value > 1 for value in probabilities):
            raise ValueError(f"invalid_probability:{race_id}")
        if abs(sum(probabilities) - 1.0) > 1e-9:
            raise ValueError(f"probability_sum_mismatch:{race_id}")
    return {"raceCount": len(grouped), "rowCount": len(rows)}
