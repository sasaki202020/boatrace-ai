from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, Mapping

from .github_contents_anchor import (
    APPROVED_BRANCH,
    APPROVED_OWNER,
    APPROVED_REPOSITORY,
    CommitmentTarget,
    ContentsTransport,
)


PROSPECTIVE_PATH_PREFIX = "anchors/prospective/"
PUBLIC_KEYS = {
    "schemaVersion", "testType", "commitment", "candidateId", "modelSha256",
    "featureSchemaSha256", "predictionCodeCommitSha", "raceCount",
    "clientCreatedAt", "noProfitClaim", "realPrediction",
}
FORBIDDEN_TOKENS = {
    "race_date", "venue", "race_number", "racer", "racer_id", "probability",
    "raw_b", "input_hash", "salt", "package", "odds", "result", "payout",
}


def _normalized_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).casefold()).strip("_")


def canonical_prospective_bytes(payload: Mapping[str, Any]) -> bytes:
    if set(payload) != PUBLIC_KEYS:
        raise ValueError("prospective_public_schema_mismatch")
    if any(_normalized_key(key) in FORBIDDEN_TOKENS for key in payload):
        raise ValueError("prospective_secret_field_prohibited")
    if payload.get("schemaVersion") != 2 or payload.get("testType") != "PROSPECTIVE_COMMITMENT":
        raise ValueError("prospective_contract_invalid")
    if payload.get("candidateId") != "tree_15":
        raise ValueError("candidate_not_allowed")
    for key in ("commitment", "modelSha256", "featureSchemaSha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(payload.get(key, ""))):
            raise ValueError(f"{key}_invalid")
    if not re.fullmatch(r"[0-9a-f]{40}", str(payload.get("predictionCodeCommitSha", ""))):
        raise ValueError("prediction_code_commit_invalid")
    if not isinstance(payload.get("raceCount"), int) or not 1 <= int(payload["raceCount"]) <= 12:
        raise ValueError("race_count_invalid")
    try:
        created = datetime.fromisoformat(str(payload.get("clientCreatedAt", "")))
    except ValueError:
        raise ValueError("client_created_at_invalid") from None
    if created.tzinfo is None or created.utcoffset() is None:
        raise ValueError("client_created_at_timezone_required")
    if payload.get("noProfitClaim") is not True or payload.get("realPrediction") is not True:
        raise ValueError("prospective_safety_flags_invalid")
    return (json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def validate_real_approval(approval: Mapping[str, Any]) -> None:
    exact = {
        "owner": APPROVED_OWNER,
        "repository": APPROVED_REPOSITORY,
        "branch": APPROVED_BRANCH,
        "prospectivePathPrefix": PROSPECTIVE_PATH_PREFIX,
        "maximumVenues": 1,
        "maximumRaces": 12,
        "maximumPackages": 1,
        "maximumExternalWrites": 1,
        "maximumRetries": 0,
    }
    if any(approval.get(key) != value for key, value in exact.items()):
        raise ValueError("real_approval_scope_mismatch")
    if approval.get("repositoryAllowlist") != [f"{APPROVED_OWNER}/{APPROVED_REPOSITORY}"]:
        raise ValueError("repository_allowlist_mismatch")
    if any(approval.get(key) is not True for key in ("humanApproved", "realPredictionPublishApproved", "issueRetentionApproved")):
        raise ValueError("real_publish_not_approved")
    if any(approval.get(key) is not False for key in ("paymentEnabled", "profitClaimsAllowed", "productionAdoptionAllowed", "bettingEnabled")):
        raise ValueError("safety_gate_mismatch")


class ProspectiveAnchorCommitService:
    def __init__(self, *, target: CommitmentTarget, token: str, transport: ContentsTransport) -> None:
        self.target = target
        self.token_present = bool(token)
        self.transport = transport

    @staticmethod
    def _content(response: Mapping[str, Any]) -> bytes:
        try:
            return base64.b64decode(str(response.get("content", "")).replace("\n", ""), validate=True)
        except Exception:
            raise ValueError("github_content_decode_failed") from None

    def publish(self, payload: Mapping[str, Any], *, cutoff: datetime, approval: Mapping[str, Any]) -> dict[str, Any]:
        validate_real_approval(approval)
        if not self.token_present:
            raise ValueError("credential_missing")
        if self.target != CommitmentTarget(APPROVED_OWNER, APPROVED_REPOSITORY, APPROVED_BRANCH, PROSPECTIVE_PATH_PREFIX):
            raise ValueError("prospective_target_not_allowed")
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise ValueError("cutoff_timezone_required")
        raw = canonical_prospective_bytes(payload)
        if datetime.fromisoformat(str(payload["clientCreatedAt"])) >= cutoff:
            raise ValueError("LATE_COMMIT_REJECTED")
        commitment = str(payload["commitment"])
        path = f"{PROSPECTIVE_PATH_PREFIX}{commitment}.json"
        existing = self.transport.get_content(APPROVED_OWNER, APPROVED_REPOSITORY, APPROVED_BRANCH, path)
        if existing is not None:
            if self._content(existing) != raw:
                raise ValueError("existing_content_mismatch")
            return {
                "status": "IDEMPOTENT", "path": path, "contentSha256": hashlib.sha256(raw).hexdigest(),
                "objectSha": str(existing.get("sha", "")), "commitSha": "",
                "serverCreatedAt": "", "externalWriteCount": 0, "prospectiveRaces": 0,
            }
        try:
            created = self.transport.create_content(APPROVED_OWNER, APPROVED_REPOSITORY, APPROVED_BRANCH, path, raw)
        except ValueError:
            recovered = self.transport.get_content(APPROVED_OWNER, APPROVED_REPOSITORY, APPROVED_BRANCH, path)
            if recovered is None or self._content(recovered) != raw:
                raise
            return {
                "status": "EXTERNAL_WRITE_UNVERIFIED", "path": path,
                "contentSha256": hashlib.sha256(raw).hexdigest(), "objectSha": str(recovered.get("sha", "")),
                "commitSha": "", "serverCreatedAt": "", "externalWriteCount": 1,
                "prospectiveRaces": 0,
            }
        server_date_raw = str(created.get("_http_date", ""))
        try:
            server_date = parsedate_to_datetime(server_date_raw)
        except (TypeError, ValueError):
            server_date = None
        late = server_date is not None and (server_date.tzinfo is None or server_date >= cutoff)
        try:
            fetched = self.transport.get_content(APPROVED_OWNER, APPROVED_REPOSITORY, APPROVED_BRANCH, path)
            readback_valid = fetched is not None and self._content(fetched) == raw
        except ValueError:
            readback_valid = False
        content = created.get("content") if isinstance(created.get("content"), Mapping) else {}
        commit = created.get("commit") if isinstance(created.get("commit"), Mapping) else {}
        object_sha = str(content.get("sha", ""))
        commit_sha = str(commit.get("sha", ""))
        git_blob_sha = hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()
        fetched_sha = str(fetched.get("sha", "")) if isinstance(fetched, Mapping) else ""
        identity_valid = bool(object_sha == git_blob_sha == fetched_sha and re.fullmatch(r"[0-9a-f]{40}", commit_sha))
        status = "CREATED"
        if server_date is None or not readback_valid or not identity_valid:
            status = "EXTERNAL_WRITE_UNVERIFIED"
        elif late:
            status = "LATE_COMMIT_REJECTED"
        return {
            "status": status, "path": path,
            "contentSha256": hashlib.sha256(raw).hexdigest(),
            "objectSha": object_sha, "commitSha": commit_sha,
            "serverCreatedAt": server_date.isoformat() if server_date else "", "externalWriteCount": 1,
            "prospectiveRaces": int(payload["raceCount"]) if status == "CREATED" else 0,
        }
