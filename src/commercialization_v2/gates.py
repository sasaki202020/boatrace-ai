from __future__ import annotations

from typing import Any


def compute_v2_gate(values: dict[str, Any]) -> dict[str, Any]:
    days = int(values.get("verifiedProspectiveDays", 0)); races = int(values.get("verifiedProspectiveRaces", 0)); integrity = bool(values.get("pipelineIntegrityPassed", False))
    stage = "NOT_STARTED"
    if days >= 7 and races >= 300 and integrity: stage = "PIPELINE_PROVEN"
    if days >= 30 and races >= 1500 and integrity and all(values.get(key, False) for key in ("aggregateLogLossImproved", "aggregateBrierImproved", "weeklyMajorityImproved", "eceAcceptable", "venueDependencyAcceptable")): stage = "MODEL_PROSPECTIVE_SIGNAL"
    stage_c = days >= 90 and races >= 5000 and integrity and all(values.get(key, False) for key in ("logLossCiUpperBelowZero", "aggregateBrierImproved", "monthlyMajorityImproved", "eceAcceptable", "venueDependencyAcceptable", "laneDependencyAcceptable", "inputSourceRightsVerified", "externalTimestampVerified"))
    if stage_c: stage = "COMMERCIALIZATION_REVIEW_READY"
    return {"schemaVersion": 2, "stage": stage, "verifiedProspectiveDays": days, "verifiedProspectiveRaces": races,
            "legacyProspectiveInputVerifiedIgnored": bool(values.get("prospectiveInputVerified", False)),
            "paymentEligibility": stage_c, "paymentStatus": "HUMAN_REVIEW_REQUIRED" if stage_c else "DISABLED_BY_GATE",
            "paymentEnabled": False, "profitClaimsAllowed": False, "productionAdoptionAllowed": False}
