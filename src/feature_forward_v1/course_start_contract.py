from __future__ import annotations

from collections import Counter
from typing import Any, Iterable


FEATURE_CONTRACT: dict[str, dict[str, Any]] = {
    "courseEntry": {
        "meaning": "開催前の展示進入コース値。対象レースの確定進入ではない。",
        "source": "OFFICIAL_PUBLIC_BEFOREINFO.boats[].courseEntry",
        "availableAt": "beforeinfo capture timestamp",
        "forbidden": ["race result", "confirmed post-race entry", "target race actual start course"],
    },
    "startExhibition": {
        "meaning": "開催前のスタート展示値。対象レースの実STではない。",
        "source": "OFFICIAL_PUBLIC_BEFOREINFO.boats[].startExhibition",
        "availableAt": "beforeinfo capture timestamp",
        "forbidden": ["target race actual ST", "result-confirmed ST"],
    },
    "tilt": {
        "meaning": "開催前に表示されたチルト値。",
        "source": "OFFICIAL_PUBLIC_BEFOREINFO.boats[].tilt",
        "availableAt": "beforeinfo capture timestamp",
        "forbidden": ["post-race adjustment"],
    },
    "bodyWeight": {
        "meaning": "開催前に表示された体重値。",
        "source": "OFFICIAL_PUBLIC_BEFOREINFO.boats[].bodyWeight",
        "availableAt": "beforeinfo capture timestamp",
        "forbidden": ["post-race weight"],
    },
}


RESULT_TOKENS = {
    "result",
    "winner",
    "finish",
    "payout",
    "return",
    "refund",
    "actual",
    "rank",
    "着",
    "払戻",
    "結果",
    "確定",
}


def _contains_result_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            lowered = str(key).lower()
            if any(token.lower() in lowered for token in RESULT_TOKENS):
                return True
            if _contains_result_key(nested):
                return True
    elif isinstance(value, list):
        return any(_contains_result_key(item) for item in value)
    return False


def build_course_start_contract_audit(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    selected = [row for row in records if row.get("featureGroup") == "course_and_start_exhibition"]
    predeadline = [
        row for row in selected
        if row.get("captureTimestampVerified") is True
        and float(row.get("secondsBeforeDeadline") or 0) > 0
    ]
    result_leakage = [row for row in selected if _contains_result_key(row.get("values", {}))]
    missing_reasons = Counter(str(row.get("missingReason") or "") for row in selected)
    missing_reasons.pop("", None)
    return {
        "schemaVersion": 1,
        "featureGroup": "course_and_start_exhibition",
        "featureContract": FEATURE_CONTRACT,
        "availabilityRule": "captureTimestampVerified=true AND secondsBeforeDeadline>0",
        "raceDeadlineSource": "B-file schedule used by the runtime selected-scope ledger",
        "availableAtComparison": "availableAt < raceDeadline proven by positive verified secondsBeforeDeadline",
        "recordsExamined": len(selected),
        "preDeadlineEvidenceCount": len(predeadline),
        "preDeadlineEvidenceRate": len(predeadline) / len(selected) if selected else 0.0,
        "resultLeakageCount": len(result_leakage),
        "missingReasonCounts": dict(sorted(missing_reasons.items())),
        "contractPass": bool(selected)
        and len(predeadline) == len(selected)
        and not result_leakage,
        "oofEligibleOnlyWhenContractPass": True,
    }
