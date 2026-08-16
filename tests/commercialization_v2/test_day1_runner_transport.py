from __future__ import annotations

import base64
from datetime import datetime
from pathlib import Path
import pytest
from zoneinfo import ZoneInfo

from src.commercialization_v2.canonical_package import build_prediction_package
from src.commercialization_v2.day1_runner import build_public_payload, execute_package, find_next_bfile


MODEL = "a2f11bf69c1b4b7ea47cca847dbe0a46f076f7c08d3361ba9e30b43f12d65da0"
SCHEMA = "a3853bdbdb75d13d4a596928c13eaa034307b14a5a8c534d31fca9acdab623dd"
APPROVAL = {
    "humanApproved": True, "realPredictionPublishApproved": True, "issueRetentionApproved": True,
    "owner": "sasaki202020", "repository": "boatrace-prediction-anchors",
    "repositoryAllowlist": ["sasaki202020/boatrace-prediction-anchors"], "branch": "main",
    "prospectivePathPrefix": "anchors/prospective/", "paymentEnabled": False,
    "profitClaimsAllowed": False, "productionAdoptionAllowed": False, "bettingEnabled": False,
    "maximumVenues": 1, "maximumRaces": 12, "maximumPackages": 1,
    "maximumExternalWrites": 1, "maximumRetries": 0,
}


class FakeTransport:
    def __init__(self): self.value = None; self.writes = 0
    def get_content(self, owner, repository, branch, path):
        if self.value is None: return None
        blob = __import__("hashlib").sha1(f"blob {len(self.value)}\0".encode() + self.value).hexdigest()
        return {"content": base64.b64encode(self.value).decode(), "sha": blob}
    def create_content(self, owner, repository, branch, path, content):
        self.value = content; self.writes += 1
        blob = __import__("hashlib").sha1(f"blob {len(content)}\0".encode() + content).hexdigest()
        return {"_http_date": "Sun, 19 Jul 2026 05:00:00 GMT", "content": {"sha": blob}, "commit": {"sha": "2" * 40}}


def package():
    rows = []
    for race in (1, 2):
        for lane in range(1, 7):
            rows.append({"raceId": f"20260720-01-{race:02}", "venue": "01", "raceNumber": race,
                         "lane": lane, "racerId": f"SYNTH-{race}-{lane}",
                         "predictedProbability": 1 / 6, "probabilityRank": lane, "topPrediction": lane == 1})
    return build_prediction_package(
        race_date="2026-07-20", generated_at_utc="2026-07-19T05:00:00+00:00",
        generated_at_jst="2026-07-19T14:00:00+09:00", candidate_id="tree_15",
        model_sha256=MODEL, feature_schema_sha256=SCHEMA, canonical_dataset_sha256="b" * 64,
        as_of_artifact_sha256="c" * 64, input_raw_sha256="d" * 64, input_rows_sha256="e" * 64,
        source_id="SYNTHETIC-PRE-RACE-FIXTURE", input_rights_status="SYNTHETIC",
        code_version="test", seed=42, predictions=rows,
    )


def test_no_input_is_read_only(tmp_path: Path):
    before = list(tmp_path.iterdir())
    assert find_next_bfile(tmp_path, now=datetime(2026, 7, 19, 12, tzinfo=ZoneInfo("Asia/Tokyo"))) is None
    assert list(tmp_path.iterdir()) == before


def test_selects_only_future_bfile(tmp_path: Path):
    for name in ("B260717.TXT", "B260720.TXT", "B260721.TXT"): (tmp_path / name).touch()
    assert find_next_bfile(tmp_path, now=datetime(2026, 7, 19, 12, tzinfo=ZoneInfo("Asia/Tokyo"))).name == "B260720.TXT"


def test_fixture_e2e_keeps_public_payload_secret_free_and_counts_only_success(tmp_path: Path):
    transport = FakeTransport()
    result = execute_package(package(), ledger_path=tmp_path / "shadow.sqlite3", approval=APPROVAL,
                             token="not-logged", transport=transport,
                             code_commit="3bac12373118794b1696debfefa8bd67cc202ba2", salt=bytes(range(32)))
    assert result.status == "PASS" and result.prospective_races == 2 and result.external_writes == 1
    public = transport.value.decode()
    for secret in ("raceDate", "venue", "racerId", "predictedProbability", "salt", "inputRawSha256"):
        assert secret not in public
    assert result.detail["ledgerIntegrity"]["valid"] is True


def test_public_payload_contains_only_contract_keys():
    payload = build_public_payload(package(), "f" * 64, code_commit="3bac12373118794b1696debfefa8bd67cc202ba2")
    assert set(payload) == {"schemaVersion", "testType", "commitment", "candidateId", "modelSha256",
                            "featureSchemaSha256", "predictionCodeCommitSha", "raceCount",
                            "clientCreatedAt", "noProfitClaim", "realPrediction"}


def test_scope_rejects_multiple_venues_before_write(tmp_path: Path):
    value = package(); value["predictions"][0]["venue"] = "02"
    transport = FakeTransport()
    with pytest.raises(ValueError, match="single_venue_required"):
        execute_package(value, ledger_path=tmp_path / "shadow.sqlite3", approval=APPROVAL,
                        token="not-logged", transport=transport,
                        code_commit="3bac12373118794b1696debfefa8bd67cc202ba2")
    assert transport.writes == 0


def test_daily_guard_rejects_second_commitment_before_write(tmp_path: Path):
    root = tmp_path / "day"
    root.mkdir()
    (root / "external_write_guard.json").write_text(
        '{"commitment": "different", "raceDate": "2026-07-20"}\n', encoding="utf-8"
    )
    transport = FakeTransport()
    with pytest.raises(ValueError, match="daily_external_write_limit_reached"):
        execute_package(package(), ledger_path=root / "shadow.sqlite3", approval=APPROVAL,
                        token="not-logged", transport=transport,
                        code_commit="3bac12373118794b1696debfefa8bd67cc202ba2", salt=bytes(range(32)))
    assert transport.writes == 0


def test_manual_resume_reuses_exact_package_without_second_write(tmp_path: Path):
    transport = FakeTransport()
    ledger_path = tmp_path / "day" / "shadow.sqlite3"
    first = execute_package(package(), ledger_path=ledger_path, approval=APPROVAL,
                            token="not-logged", transport=transport,
                            code_commit="3bac12373118794b1696debfefa8bd67cc202ba2", salt=bytes(range(32)))
    second = execute_package(package(), ledger_path=ledger_path, approval=APPROVAL,
                             token="not-logged", transport=transport,
                             code_commit="3bac12373118794b1696debfefa8bd67cc202ba2")
    assert first.status == "PASS"
    assert second.status == "PASS"
    assert second.prospective_races == 0
    assert transport.writes == 1
    assert second.detail["ledgerIntegrity"]["valid"] is True


def test_post_write_invalid_response_is_recorded_without_counting_races(tmp_path: Path):
    transport = FakeTransport()
    original = transport.create_content
    def invalid(*args, **kwargs):
        value = original(*args, **kwargs)
        value["_http_date"] = "invalid"
        return value
    transport.create_content = invalid
    result = execute_package(package(), ledger_path=tmp_path / "shadow.sqlite3", approval=APPROVAL,
                             token="not-logged", transport=transport,
                             code_commit="3bac12373118794b1696debfefa8bd67cc202ba2", salt=bytes(range(32)))
    assert result.status == "EXTERNAL_WRITE_UNVERIFIED"
    assert result.external_writes == 1 and result.prospective_races == 0
    assert result.detail["ledgerIntegrity"]["valid"] is True
