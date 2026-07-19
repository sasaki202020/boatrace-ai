from __future__ import annotations

from typing import Any


def compute_internal_prospective_readiness(values: dict[str, Any]) -> dict[str, Any]:
    """Evaluate internal shadow readiness separately from commercial rights."""
    requirements = {
        "candidateIntegrity": values.get("candidateIntegrityStatus")
        in {
            "CANDIDATE_FREEZE_INTEGRITY_PASS",
            "CANDIDATE_FREEZE_INTEGRITY_PASS_WITH_COVERAGE_GAP",
        },
        "modelHashMatches": values.get("modelHashMatches") is True,
        "featureSchemaHashMatches": values.get("featureSchemaHashMatches") is True,
        "preRaceSchemaVerified": values.get("inputSchemaStatus") == "PRE_RACE_SCHEMA_VERIFIED",
        "resultFieldsAbsent": int(values.get("resultFieldCount", -1)) == 0,
        "externalAnchorRepositoryApproved": values.get("externalAnchorRepositoryApproved") is True,
        "githubCredentialConfigured": values.get("githubCredentialConfigured") is True,
        "commitmentDryRunPassed": values.get("commitmentDryRunPassed") is True,
        "appendOnlyLedgerIntegrityPassed": values.get("appendOnlyLedgerIntegrityPassed") is True,
        "paymentDisabled": values.get("paymentEnabled") is False,
        "profitClaimsDisabled": values.get("profitClaimsAllowed") is False,
        "productionAdoptionDisabled": values.get("productionAdoptionAllowed") is False,
    }
    ready = all(requirements.values())
    internal_allowed = all(requirements[name] for name in (
        "candidateIntegrity", "modelHashMatches", "featureSchemaHashMatches",
        "preRaceSchemaVerified", "resultFieldsAbsent", "paymentDisabled",
        "profitClaimsDisabled", "productionAdoptionDisabled",
    ))
    return {
        "schemaVersion": 1,
        "candidateIntegrityStatus": values.get("candidateIntegrityStatus"),
        "cutoffFeasibility": "RUNTIME_VERIFIED_PER_PACKAGE",
        "inputRightsStatus": "UNVERIFIED_COMMERCIAL_USE",
        "commercialDataUse": "UNVERIFIED",
        "internalProspectiveUse": "ALLOWED_WITH_RESTRICTIONS" if internal_allowed else "BLOCKED",
        "shadowStatus": "READY_FOR_DAY1" if ready else "BLOCKED",
        "requirements": requirements,
        "blockingReasons": [name for name, passed in requirements.items() if not passed],
        "inputSourceRightsVerifiedRequiredForInternalShadow": False,
        "inputAcquisitionTimeAttestedRequiredForInternalShadow": False,
        "runtimeExternalCommitRequired": True,
        "paymentEnabled": False,
        "profitClaimsAllowed": False,
        "productionAdoptionAllowed": False,
    }
