from __future__ import annotations

import json
from datetime import datetime, time
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from .input_guard import validate_prediction_rows


def _probability(value: object) -> str:
    return format(Decimal(str(value)).quantize(Decimal("0.000000000001")), "f")


def conservative_cutoff(race_date: str) -> str:
    return datetime.combine(datetime.fromisoformat(race_date).date(), time.min, tzinfo=ZoneInfo("Asia/Tokyo")).isoformat()


def build_prediction_package(**values: Any) -> dict[str, Any]:
    predictions = [dict(row) for row in values.pop("predictions")]
    validate_prediction_rows(predictions, model_sha256=values["model_sha256"], expected_model_sha256=values["model_sha256"], feature_schema_sha256=values["feature_schema_sha256"], expected_feature_schema_sha256=values["feature_schema_sha256"])
    normalized = []
    for row in sorted(predictions, key=lambda item: (str(item["raceId"]), str(item["venue"]), int(item["raceNumber"]), int(item["lane"]))):
        normalized.append({"raceId": str(row["raceId"]), "venue": str(row["venue"]), "raceNumber": int(row["raceNumber"]), "lane": int(row["lane"]), "racerId": str(row["racerId"]), "predictedProbability": _probability(row["predictedProbability"]), "probabilityRank": int(row["probabilityRank"]), "topPrediction": bool(row["topPrediction"])})
    race_date = str(values["race_date"])
    return {
        "schemaVersion": 2, "raceDate": race_date, "generatedAtUtc": values["generated_at_utc"], "generatedAtJst": values["generated_at_jst"],
        "conservativeCutoff": conservative_cutoff(race_date), "candidateId": values["candidate_id"], "modelSha256": values["model_sha256"],
        "featureSchemaSha256": values["feature_schema_sha256"], "canonicalDatasetSha256": values["canonical_dataset_sha256"], "asOfArtifactSha256": values["as_of_artifact_sha256"],
        "inputRawSha256": values["input_raw_sha256"], "inputCanonicalRowsSha256": values["input_rows_sha256"], "sourceId": values["source_id"],
        "inputRightsStatus": values["input_rights_status"], "inputAcquisitionTimeAttested": bool(values.get("input_acquisition_time_attested", False)),
        "predictionCodeVersion": values["code_version"], "seed": int(values["seed"]), "predictions": normalized,
    }


def canonical_package_bytes(package: dict[str, Any]) -> bytes:
    return (json.dumps(package, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
