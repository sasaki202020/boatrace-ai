from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from urllib.parse import urlsplit


class FetchDecision(Enum):
    ALLOW = "ALLOW"
    INTERVAL_BLOCKED = "INTERVAL_BLOCKED"
    RACE_BUDGET_EXHAUSTED = "RACE_BUDGET_EXHAUSTED"
    DAILY_BUDGET_EXHAUSTED = "DAILY_BUDGET_EXHAUSTED"
    COLLECTION_STOPPED = "COLLECTION_STOPPED"


class PolicyGateError(ValueError):
    """Raised when a runtime collection policy cannot be verified."""


@dataclass(frozen=True)
class GateResult:
    personal_gate: str
    automated_fetch_gate: str
    commercial_gate: str


@dataclass(frozen=True)
class PersonalResearchPolicy:
    usage_mode: str
    personal_research_allowed: bool
    local_storage_allowed: bool
    local_analysis_allowed: bool
    personal_model_training_allowed: bool
    personal_prediction_use_allowed: bool
    manual_ingest_allowed: bool
    automated_fetch_allowed: bool
    network_safety_integrated: bool
    allowed_https_hosts: tuple[str, ...]
    commercial_use_allowed: bool
    redistribution_allowed: bool
    public_release_allowed: bool
    paid_service_allowed: bool
    minimum_interval_seconds: int
    requests_per_race: int
    requests_per_day: int
    retries_per_race: int

    def __post_init__(self) -> None:
        if self.usage_mode != "PERSONAL_RESEARCH_ONLY":
            raise ValueError("usage_mode_must_be_personal_research_only")
        if not all((self.personal_research_allowed, self.local_storage_allowed, self.local_analysis_allowed,
                    self.personal_model_training_allowed, self.personal_prediction_use_allowed,
                    self.manual_ingest_allowed)):
            raise ValueError("personal_research_permissions_incomplete")
        if any((self.commercial_use_allowed, self.redistribution_allowed, self.public_release_allowed, self.paid_service_allowed)):
            raise ValueError("non_personal_use_must_remain_disabled")
        if type(self.minimum_interval_seconds) is not int or self.minimum_interval_seconds < 60:
            raise ValueError("minimum_interval_below_60_seconds")
        if type(self.requests_per_race) is not int or type(self.requests_per_day) is not int or type(self.retries_per_race) is not int:
            raise ValueError("request_budget_type_invalid")
        if self.requests_per_race != 1 or self.requests_per_day < 1 or self.retries_per_race != 0:
            raise ValueError("unsafe_request_budget")
        if self.automated_fetch_allowed and (
            not self.network_safety_integrated or not self.allowed_https_hosts
        ):
            raise ValueError("automated_fetch_safety_incomplete")
        if any(not host or "://" in host or "/" in host for host in self.allowed_https_hosts):
            raise ValueError("allowed_host_invalid")

    def evaluate(self) -> GateResult:
        return GateResult(
            personal_gate="ALLOWED_WITH_RESTRICTIONS",
            automated_fetch_gate="READY" if self.automated_fetch_allowed else "MANUAL_ONLY",
            commercial_gate="PROHIBITED",
        )


@dataclass(frozen=True)
class ResponseClassification:
    stop_collection: bool
    reason: str


def classify_response(status_code: int, body: str) -> ResponseClassification:
    text = str(body).casefold()
    refusal_markers = ("captcha", "私はロボットではありません", "access denied", "利用を拒否")
    if 300 <= status_code < 400:
        return ResponseClassification(True, f"HTTP_{status_code}_REDIRECT")
    if status_code in {403, 429}:
        return ResponseClassification(True, f"HTTP_{status_code}")
    if any(marker.casefold() in text for marker in refusal_markers):
        return ResponseClassification(True, "ACCESS_REFUSAL_PAGE")
    return ResponseClassification(False, "OK")


class RequestLimiter:
    def __init__(self, policy: PersonalResearchPolicy) -> None:
        self.policy = policy
        self._last_by_url: dict[str, datetime] = {}
        self._race_counts: dict[str, int] = {}
        self._day_counts: dict[str, int] = {}
        self._stopped = False

    def stop(self) -> None:
        self._stopped = True

    def authorize(self, url: str, race_key: str, now: datetime) -> FetchDecision:
        if self._stopped:
            return FetchDecision.COLLECTION_STOPPED
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in self.policy.allowed_https_hosts:
            return FetchDecision.COLLECTION_STOPPED
        previous = self._last_by_url.get(url)
        if previous is not None and (now - previous).total_seconds() < self.policy.minimum_interval_seconds:
            return FetchDecision.INTERVAL_BLOCKED
        if self._race_counts.get(race_key, 0) >= self.policy.requests_per_race:
            return FetchDecision.RACE_BUDGET_EXHAUSTED
        day_key = now.date().isoformat()
        if self._day_counts.get(day_key, 0) >= self.policy.requests_per_day:
            return FetchDecision.DAILY_BUDGET_EXHAUSTED
        return FetchDecision.ALLOW

    def record(self, url: str, race_key: str, now: datetime) -> None:
        if self.authorize(url, race_key, now) is not FetchDecision.ALLOW:
            raise RuntimeError("request_not_authorized")
        self._last_by_url[url] = now
        self._race_counts[race_key] = self._race_counts.get(race_key, 0) + 1
        day_key = now.date().isoformat()
        self._day_counts[day_key] = self._day_counts.get(day_key, 0) + 1


def policy_from_manifest(manifest: dict) -> PersonalResearchPolicy:
    required = ("minimumRequestIntervalSeconds", "requestsPerRace", "requestsPerDay", "retriesPerRace")
    if any(key not in manifest for key in required):
        raise ValueError("request_budget_missing")
    hosts = manifest.get("allowedHttpsHosts")
    if not isinstance(hosts, list) or any(not isinstance(value, str) for value in hosts):
        raise ValueError("allowed_hosts_invalid")
    return PersonalResearchPolicy(
        usage_mode=manifest.get("usageMode"),
        personal_research_allowed=manifest.get("personalResearchAllowed") is True,
        local_storage_allowed=manifest.get("localStorageAllowed") is True,
        local_analysis_allowed=manifest.get("localAnalysisAllowed") is True,
        personal_model_training_allowed=manifest.get("personalModelTrainingAllowed") is True,
        personal_prediction_use_allowed=manifest.get("personalPredictionUseAllowed") is True,
        manual_ingest_allowed=manifest.get("manualIngestAllowed") is True,
        automated_fetch_allowed=manifest.get("automatedNetworkFetchAllowed") is True,
        network_safety_integrated=manifest.get("networkSafetyIntegrated") is True,
        allowed_https_hosts=tuple(hosts),
        commercial_use_allowed=manifest.get("commercialUseAllowed") is True,
        redistribution_allowed=manifest.get("redistributionAllowed") is True,
        public_release_allowed=manifest.get("publicReleaseAllowed") is True,
        paid_service_allowed=manifest.get("paidServiceAllowed") is True,
        minimum_interval_seconds=manifest["minimumRequestIntervalSeconds"],
        requests_per_race=manifest["requestsPerRace"],
        requests_per_day=manifest["requestsPerDay"],
        retries_per_race=manifest["retriesPerRace"],
    )


def load_policy_manifest(
    path: Path,
    *,
    require_automated_fetch: bool = False,
) -> tuple[PersonalResearchPolicy, dict[str, object]]:
    """Load and attest a policy without exposing its contents in metadata.

    The scheduled collector integration can require automated fetching explicitly.
    Manual/personal research manifests remain valid when that requirement is false.
    """
    path = Path(path)
    if not path.is_file():
        raise PolicyGateError("policy_file_missing")
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PolicyGateError("policy_file_invalid") from exc
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 2:
        raise PolicyGateError("policy_schema_unsupported")
    try:
        policy = policy_from_manifest(payload)
    except (KeyError, TypeError, ValueError) as exc:
        raise PolicyGateError("policy_schema_invalid") from exc
    if policy.automated_fetch_allowed:
        prefixes = payload.get("allowedSourceLocationPrefixes")
        if (
            payload.get("rightsStatus") != "INTERNAL_RESEARCH_APPROVED"
            or payload.get("writtenConfirmation") is not True
            or payload.get("numericStorageAllowed") is not True
            or payload.get("rawStorageAllowed") is not True
            or policy.requests_per_day != 12
            or not isinstance(prefixes, list)
            or not prefixes
            or any(not isinstance(value, str) or not value for value in prefixes)
        ):
            raise PolicyGateError("automated_fetch_attestation_incomplete")
        evidence_value = payload.get("evidencePath")
        evidence_sha256 = payload.get("evidenceSha256")
        if not isinstance(evidence_value, str) or not evidence_value:
            raise PolicyGateError("automated_fetch_evidence_missing")
        evidence_path = Path(evidence_value)
        if not evidence_path.is_absolute():
            evidence_path = path.parent / evidence_path
        if not evidence_path.is_file():
            raise PolicyGateError("automated_fetch_evidence_missing")
        if (
            not isinstance(evidence_sha256, str)
            or len(evidence_sha256) != 64
            or any(char not in "0123456789abcdef" for char in evidence_sha256)
        ):
            raise PolicyGateError("automated_fetch_evidence_sha256_invalid")
        try:
            actual_evidence_sha256 = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
        except OSError as exc:
            raise PolicyGateError("automated_fetch_evidence_unreadable") from exc
        if actual_evidence_sha256 != evidence_sha256:
            raise PolicyGateError("automated_fetch_evidence_sha256_mismatch")
    if require_automated_fetch and not policy.automated_fetch_allowed:
        raise PolicyGateError("automated_fetch_not_allowed")
    return policy, {
        "policyLoaded": True,
        "policyPath": str(path.resolve()),
        "policyHash": hashlib.sha256(raw).hexdigest(),
        "policyVersion": payload["schemaVersion"],
        "automatedFetchRequired": require_automated_fetch,
        "automatedFetchAllowed": policy.automated_fetch_allowed,
        "commercialUseAllowed": policy.commercial_use_allowed,
    }
