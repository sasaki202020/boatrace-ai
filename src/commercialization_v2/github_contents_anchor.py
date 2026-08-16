from __future__ import annotations

import base64
import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Protocol


APPROVED_OWNER = "sasaki202020"
APPROVED_REPOSITORY = "boatrace-prediction-anchors"
APPROVED_BRANCH = "main"
APPROVED_PATH_PREFIX = "anchors/synthetic/"
REQUIRED_PUBLIC_KEYS = {
    "schemaVersion", "testType", "commitment", "candidateId", "modelSha256",
    "featureSchemaSha256", "syntheticRowCount", "syntheticRaceCount",
    "clientCreatedAt", "noProfitClaim", "realPrediction",
}
FORBIDDEN_KEYS = {
    "prediction",
    "predictions",
    "predicted_probability",
    "probability",
    "race",
    "race_id",
    "racer",
    "racer_id",
    "odds",
    "result",
    "reveal",
    "roi",
    "buy",
    "ev",
    "payment",
}


@dataclass(frozen=True)
class CommitmentTarget:
    owner: str
    repository: str
    branch: str
    allowed_path_prefix: str


class ContentsTransport(Protocol):
    def get_content(self, owner: str, repository: str, branch: str, path: str) -> Mapping[str, Any] | None: ...
    def create_content(self, owner: str, repository: str, branch: str, path: str, content: bytes) -> Mapping[str, Any]: ...


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise ValueError("unexpected_redirect")


class GitHubContentsTransport:
    """Minimal GitHub Contents API client. It has no Issue API methods."""

    def __init__(self, token: str, *, api_base: str = "https://api.github.com") -> None:
        if not token:
            raise ValueError("credential_missing")
        if api_base != "https://api.github.com":
            raise ValueError("github_api_base_not_allowed")
        self._token = token
        self._base = api_base
        self._opener = urllib.request.build_opener(_RejectRedirects())

    def _request(self, method: str, endpoint: str, payload: Mapping[str, Any] | None = None) -> Mapping[str, Any] | None:
        url = f"{self._base}{endpoint}"
        body = json.dumps(dict(payload), separators=(",", ":")).encode() if payload is not None else None
        request = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "boatrace-synthetic-anchor-v2",
            },
        )
        try:
            with self._opener.open(request, timeout=20) as response:
                if response.geturl() != url:
                    raise ValueError("unexpected_redirect")
                result = json.loads(response.read().decode("utf-8"))
                if isinstance(result, dict):
                    result["_http_date"] = response.headers.get("Date", "")
                return result
        except urllib.error.HTTPError as exc:
            if method == "GET" and exc.code == 404:
                return None
            if exc.code in {401, 403, 404, 409, 422, 429}:
                raise ValueError(f"github_fail_closed_http_{exc.code}") from None
            raise ValueError("github_request_failed") from None
        except urllib.error.URLError:
            raise ValueError("github_request_failed") from None

    @staticmethod
    def _endpoint(owner: str, repository: str, path: str) -> str:
        safe_owner = urllib.parse.quote(owner, safe="")
        safe_repository = urllib.parse.quote(repository, safe="")
        safe_path = "/".join(urllib.parse.quote(part, safe="") for part in path.split("/"))
        return f"/repos/{safe_owner}/{safe_repository}/contents/{safe_path}"

    def get_content(self, owner: str, repository: str, branch: str, path: str) -> Mapping[str, Any] | None:
        endpoint = self._endpoint(owner, repository, path) + "?ref=" + urllib.parse.quote(branch, safe="")
        return self._request("GET", endpoint)

    def create_content(self, owner: str, repository: str, branch: str, path: str, content: bytes) -> Mapping[str, Any]:
        payload = json.loads(content)
        commitment = str(payload.get("commitment", ""))
        anchor_type = "prospective" if payload.get("testType") == "PROSPECTIVE_COMMITMENT" else "synthetic"
        result = self._request(
            "PUT",
            self._endpoint(owner, repository, path),
            {
                "message": f"anchor: {anchor_type} {commitment[:12]}",
                "branch": branch,
                "content": base64.b64encode(content).decode("ascii"),
            },
        )
        if not isinstance(result, Mapping):
            raise ValueError("github_content_response_incomplete")
        return result


def canonical_synthetic_bytes(package: Mapping[str, Any]) -> bytes:
    def inspect(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                normalized = re.sub(r"[^a-z0-9]", "_", str(key).casefold()).strip("_")
                if normalized in FORBIDDEN_KEYS:
                    raise ValueError(f"synthetic_package_forbidden_field:{normalized}")
                inspect(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                inspect(item)

    if set(package) != REQUIRED_PUBLIC_KEYS:
        raise ValueError("synthetic_public_schema_mismatch")
    if package.get("testType") != "SYNTHETIC_EXTERNAL_ANCHOR":
        raise ValueError("synthetic_test_type_required")
    if package.get("schemaVersion") != 2 or package.get("candidateId") != "tree_15":
        raise ValueError("synthetic_fixed_fixture_required")
    if not re.fullmatch(r"[0-9a-f]{64}", str(package.get("commitment", ""))):
        raise ValueError("commitment_invalid")
    for key in ("modelSha256", "featureSchemaSha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(package.get(key, ""))):
            raise ValueError(f"{key}_invalid")
    if package.get("syntheticRowCount") != 6 or package.get("syntheticRaceCount") != 1:
        raise ValueError("synthetic_fixed_counts_required")
    try:
        created_at = datetime.fromisoformat(str(package.get("clientCreatedAt", "")))
    except ValueError:
        raise ValueError("client_created_at_invalid") from None
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise ValueError("client_created_at_timezone_required")
    if package.get("noProfitClaim") is not True or package.get("realPrediction") is not False:
        raise ValueError("synthetic_safety_flags_invalid")
    inspect(package)
    return (json.dumps(dict(package), sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _validate_path_prefix(prefix: str) -> None:
    if prefix != APPROVED_PATH_PREFIX:
        if (
            not prefix
            or prefix.startswith(("/", "\\"))
            or "\\" in prefix
            or "%" in prefix
            or any(part in {"", ".", ".."} for part in prefix.rstrip("/").split("/"))
        ):
            raise ValueError("path_traversal_not_allowed")
        raise ValueError("path_prefix_not_allowed")


class SyntheticAnchorCommitService:
    def __init__(self, *, target: CommitmentTarget, token: str, transport: ContentsTransport) -> None:
        self.target = target
        self._token_present = bool(token)
        self.transport = transport

    def _validate_preflight(self) -> None:
        if self.target.owner != APPROVED_OWNER:
            raise ValueError("owner_not_allowed")
        if self.target.repository != APPROVED_REPOSITORY:
            raise ValueError("repository_not_allowed")
        if self.target.branch != APPROVED_BRANCH:
            raise ValueError("branch_not_allowed")
        _validate_path_prefix(self.target.allowed_path_prefix)
        if not self._token_present:
            raise ValueError("credential_missing")

    @staticmethod
    def _decode_content(response: Mapping[str, Any]) -> bytes:
        encoded = str(response.get("content", "")).replace("\n", "")
        try:
            return base64.b64decode(encoded, validate=True)
        except Exception:
            raise ValueError("github_content_decode_failed") from None

    def publish(self, package: Mapping[str, Any]) -> dict[str, Any]:
        self._validate_preflight()
        raw = canonical_synthetic_bytes(package)
        package_hash = hashlib.sha256(raw).hexdigest()
        commitment = str(package["commitment"])
        path = f"{self.target.allowed_path_prefix}{commitment}.json"
        _validate_path_prefix(path[: -len(f"{commitment}.json")])

        existing = self.transport.get_content(
            self.target.owner, self.target.repository, self.target.branch, path
        )
        if existing is not None:
            if self._decode_content(existing) != raw:
                raise ValueError("existing_content_mismatch")
            return {
                "status": "IDEMPOTENT",
                "package_sha256": package_hash,
                "readback_sha256": package_hash,
                "path": path,
                "object_sha": str(existing.get("sha", "")),
                "commit_sha": "",
            }

        created = self.transport.create_content(
            self.target.owner, self.target.repository, self.target.branch, path, raw
        )
        fetched = self.transport.get_content(
            self.target.owner, self.target.repository, self.target.branch, path
        )
        if fetched is None:
            raise ValueError("readback_missing")
        readback = self._decode_content(fetched)
        readback_hash = hashlib.sha256(readback).hexdigest()
        if readback != raw or readback_hash != package_hash:
            raise ValueError("readback_hash_mismatch")
        content = created.get("content") if isinstance(created.get("content"), Mapping) else {}
        commit = created.get("commit") if isinstance(created.get("commit"), Mapping) else {}
        committer = commit.get("committer") if isinstance(commit.get("committer"), Mapping) else {}
        return {
            "status": "CREATED",
            "package_sha256": package_hash,
            "readback_sha256": readback_hash,
            "path": path,
            "object_sha": str(content.get("sha", "")),
            "commit_sha": str(commit.get("sha", "")),
            "committed_at": str(committer.get("date", "")),
        }
