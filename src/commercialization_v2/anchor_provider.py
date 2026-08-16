from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any


def canonical_anchor_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


@dataclass(frozen=True)
class AnchorReceipt:
    provider: str
    repository: str
    external_id: str
    url: str
    created_at: str
    updated_at: str
    body_hash: str
    receipt_hash: str


class AnchorProvider(ABC):
    @abstractmethod
    def build_anchor_payload(self, **values: Any) -> dict[str, Any]: ...
    @abstractmethod
    def publish_anchor(self, payload: dict[str, Any], **values: Any) -> AnchorReceipt: ...
    @abstractmethod
    def fetch_anchor_receipt(self, external_id: str) -> AnchorReceipt: ...
    @abstractmethod
    def verify_anchor_receipt(self, receipt: AnchorReceipt, **values: Any) -> dict[str, Any]: ...
    @abstractmethod
    def get_server_timestamp(self, receipt: AnchorReceipt) -> str: ...


class MockAnchorProvider(AnchorProvider):
    def __init__(self, repository: str) -> None:
        self.repository = repository; self.receipts: dict[str, AnchorReceipt] = {}

    def build_anchor_payload(self, **values: Any) -> dict[str, Any]:
        allowed = ("schemaVersion", "commitment", "raceDate", "conservativeCutoff", "candidateId", "modelSha256", "featureSchemaSha256", "packageRowCount", "raceCount", "createdAt", "noProfitClaim")
        return {key: values[key] for key in allowed}

    def publish_anchor(self, payload: dict[str, Any], *, repository: str, server_created_at: str) -> AnchorReceipt:
        body_hash = hashlib.sha256(canonical_anchor_bytes(payload)).hexdigest(); external_id = str(len(self.receipts) + 1)
        base = {"provider": "mock_external_anchor", "repository": repository, "external_id": external_id, "url": f"mock://{repository}/{external_id}", "created_at": server_created_at, "updated_at": server_created_at, "body_hash": body_hash}
        receipt = AnchorReceipt(**base, receipt_hash=hashlib.sha256(json.dumps(base, sort_keys=True).encode()).hexdigest())
        self.receipts[external_id] = receipt; return receipt

    def fetch_anchor_receipt(self, external_id: str) -> AnchorReceipt:
        if external_id not in self.receipts: raise ValueError("anchor_unavailable")
        return self.receipts[external_id]

    def verify_anchor_receipt(self, receipt: AnchorReceipt, *, payload: dict[str, Any], repository_allowlist: set[str], cutoff: str) -> dict[str, Any]:
        if receipt.repository not in repository_allowlist: raise ValueError("repository_not_allowed")
        if receipt.body_hash != hashlib.sha256(canonical_anchor_bytes(payload)).hexdigest(): raise ValueError("anchor_body_mismatch")
        if receipt.updated_at != receipt.created_at:
            return {"status": "INVALID_COMMITMENT", "externalTimestampVerified": False, "reviewRequired": True, "reason": "anchor_updated_after_creation"}
        base = {key: getattr(receipt, key) for key in ("provider", "repository", "external_id", "url", "created_at", "updated_at", "body_hash")}
        if receipt.receipt_hash != hashlib.sha256(json.dumps(base, sort_keys=True).encode()).hexdigest(): raise ValueError("receipt_hash_mismatch")
        created = datetime.fromisoformat(receipt.created_at); limit = datetime.fromisoformat(cutoff)
        status = "EXTERNALLY_COMMITTED" if created < limit else "LATE_COMMIT_REJECTED"
        return {"status": status, "externalTimestampVerified": status == "EXTERNALLY_COMMITTED", "reviewRequired": False}

    def get_server_timestamp(self, receipt: AnchorReceipt) -> str: return receipt.created_at
