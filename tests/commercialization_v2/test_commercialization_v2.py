from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from src.commercialization_v2.anchor_provider import AnchorReceipt, MockAnchorProvider
from src.commercialization_v2.activation import compute_internal_prospective_readiness
from src.commercialization_v2.canonical_package import build_prediction_package, canonical_package_bytes
from src.commercialization_v2.commitment import create_commitment, verify_reveal
from src.commercialization_v2.gates import compute_v2_gate
from src.commercialization_v2.input_guard import validate_prediction_rows
from src.commercialization_v2.ledger import ShadowLedgerV2


MODEL = "a2f11bf69c1b4b7ea47cca847dbe0a46f076f7c08d3361ba9e30b43f12d65da0"
SCHEMA = "a3853bdbdb75d13d4a596928c13eaa034307b14a5a8c534d31fca9acdab623dd"
DATASET = "bc2294f85e482ac1c1e7458236be509afd5d3adc9aa7afd4ec53fc4658e54f23"
ASOF = "c1ede746393c906e7197d9a461a32fcacb34e508387b6d189d57da20089f3bcb"


def rows() -> list[dict[str, object]]:
    probabilities = ["0.420000000000", "0.220000000000", "0.140000000000", "0.100000000000", "0.070000000000", "0.050000000000"]
    return [{"raceId": "20260720-01-01", "venue": "01", "raceNumber": 1, "lane": lane,
             "racerId": f"R{lane}", "predictedProbability": probabilities[lane - 1],
             "probabilityRank": lane, "topPrediction": lane == 1} for lane in range(1, 7)]


def package() -> dict[str, object]:
    return build_prediction_package(
        race_date="2026-07-20", generated_at_utc="2026-07-19T12:00:00+00:00",
        generated_at_jst="2026-07-19T21:00:00+09:00", candidate_id="tree_15",
        model_sha256=MODEL, feature_schema_sha256=SCHEMA, canonical_dataset_sha256=DATASET,
        as_of_artifact_sha256=ASOF, input_raw_sha256="1" * 64, input_rows_sha256="2" * 64,
        source_id="synthetic_fixture", input_rights_status="UNVERIFIED", code_version="test", seed=42,
        predictions=rows(),
    )


def test_canonical_package_is_deterministic_and_has_no_result_fields() -> None:
    first = canonical_package_bytes(package())
    second = canonical_package_bytes(package())
    assert first == second
    assert first.endswith(b"\n")
    assert b"finish" not in first and b"payout" not in first and b"odds" not in first


def test_commitment_uses_random_salt_and_reveal_detects_tampering() -> None:
    raw = canonical_package_bytes(package())
    a = create_commitment(raw)
    b = create_commitment(raw)
    assert len(bytes.fromhex(a["saltHex"])) >= 32
    assert a["packageSha256"] == b["packageSha256"]
    assert a["commitment"] != b["commitment"]
    assert verify_reveal(raw, a["saltHex"], a["commitment"])
    assert not verify_reveal(raw + b"x", a["saltHex"], a["commitment"])
    assert not verify_reveal(raw, "00" * 32, a["commitment"])
    with pytest.raises(ValueError, match="salt_minimum_32_bytes"):
        create_commitment(raw, salt=b"short")


@pytest.mark.parametrize("field", ["result", "winner", "finishPosition", "payout", "finalOdds", "settlement", "着順", "払戻"])
def test_input_guard_rejects_result_semantics(field: str) -> None:
    bad = rows(); bad[0][field] = 1
    with pytest.raises(ValueError, match="result_field_detected"):
        validate_prediction_rows(bad, model_sha256=MODEL, expected_model_sha256=MODEL,
                                 feature_schema_sha256=SCHEMA, expected_feature_schema_sha256=SCHEMA)


def test_input_guard_rejects_integrity_errors() -> None:
    with pytest.raises(ValueError, match="six_boats_required"):
        validate_prediction_rows(rows()[:5], model_sha256=MODEL, expected_model_sha256=MODEL,
                                 feature_schema_sha256=SCHEMA, expected_feature_schema_sha256=SCHEMA)
    duplicate = rows(); duplicate[5]["lane"] = 5
    with pytest.raises(ValueError, match="lane_integrity"):
        validate_prediction_rows(duplicate, model_sha256=MODEL, expected_model_sha256=MODEL,
                                 feature_schema_sha256=SCHEMA, expected_feature_schema_sha256=SCHEMA)
    invalid = rows(); invalid[0]["predictedProbability"] = math.inf
    with pytest.raises(ValueError, match="invalid_probability"):
        validate_prediction_rows(invalid, model_sha256=MODEL, expected_model_sha256=MODEL,
                                 feature_schema_sha256=SCHEMA, expected_feature_schema_sha256=SCHEMA)
    with pytest.raises(ValueError, match="model_hash_mismatch"):
        validate_prediction_rows(rows(), model_sha256="0" * 64, expected_model_sha256=MODEL,
                                 feature_schema_sha256=SCHEMA, expected_feature_schema_sha256=SCHEMA)


def test_mock_anchor_verifies_cutoff_body_allowlist_and_update_state() -> None:
    provider = MockAnchorProvider("allowed/repo")
    payload = {"schemaVersion": 2, "commitment": "a" * 64, "raceDate": "2026-07-20"}
    receipt = provider.publish_anchor(payload, repository="allowed/repo", server_created_at="2026-07-19T14:59:59+00:00")
    verified = provider.verify_anchor_receipt(receipt, payload=payload, repository_allowlist={"allowed/repo"}, cutoff="2026-07-20T00:00:00+09:00")
    assert verified["status"] == "EXTERNALLY_COMMITTED"
    equal = provider.publish_anchor(payload, repository="allowed/repo", server_created_at="2026-07-19T15:00:00+00:00")
    assert provider.verify_anchor_receipt(equal, payload=payload, repository_allowlist={"allowed/repo"}, cutoff="2026-07-20T00:00:00+09:00")["status"] == "LATE_COMMIT_REJECTED"
    late = provider.publish_anchor(payload, repository="allowed/repo", server_created_at="2026-07-19T15:00:01+00:00")
    assert provider.verify_anchor_receipt(late, payload=payload, repository_allowlist={"allowed/repo"}, cutoff="2026-07-20T00:00:00+09:00")["status"] == "LATE_COMMIT_REJECTED"
    with pytest.raises(ValueError, match="repository_not_allowed"):
        provider.verify_anchor_receipt(receipt, payload=payload, repository_allowlist={"other/repo"}, cutoff="2026-07-20T00:00:00+09:00")
    edited = AnchorReceipt(**{**receipt.__dict__, "updated_at": "2026-07-19T15:01:00+00:00"})
    assert provider.verify_anchor_receipt(edited, payload=payload, repository_allowlist={"allowed/repo"}, cutoff="2026-07-20T00:00:00+09:00")["reviewRequired"] is True


def test_v2_ledger_is_append_only_and_result_does_not_change_prediction_hash(tmp_path: Path) -> None:
    ledger = ShadowLedgerV2(tmp_path / "shadow.sqlite3", expected_model_sha256=MODEL, expected_schema_sha256=SCHEMA)
    raw = canonical_package_bytes(package()); commitment = create_commitment(raw, salt=bytes(range(32)))
    package_id = ledger.append_prediction_package(package(), raw, commitment)
    before = ledger.prediction_digest()
    with pytest.raises(ValueError, match="duplicate_race_date_package"):
        ledger.append_prediction_package(package(), raw, commitment)
    ledger.append_result_package("2026-07-20", [{"raceId": "20260720-01-01", "winningLane": 1}], "3" * 64)
    assert ledger.prediction_digest() == before
    assert ledger.verify_integrity()["valid"] is True
    with pytest.raises(Exception): ledger.connection.execute("UPDATE prediction_rows SET predicted_probability='0' WHERE lane=1")
    with pytest.raises(Exception): ledger.connection.execute("DELETE FROM prediction_rows WHERE lane=1")
    assert package_id


def test_v2_ledger_rejects_package_model_or_schema_substitution(tmp_path: Path) -> None:
    ledger = ShadowLedgerV2(tmp_path / "shadow.sqlite3", expected_model_sha256=MODEL, expected_schema_sha256=SCHEMA)
    changed = package(); changed["modelSha256"] = "0" * 64
    raw = canonical_package_bytes(changed); commitment = create_commitment(raw, salt=bytes(range(32)))
    with pytest.raises(ValueError, match="model_hash_mismatch"):
        ledger.append_prediction_package(changed, raw, commitment)


def test_v2_ledger_rejects_source_record_without_chain_entry(tmp_path: Path) -> None:
    ledger = ShadowLedgerV2(tmp_path / "shadow.sqlite3", expected_model_sha256=MODEL, expected_schema_sha256=SCHEMA)
    raw = canonical_package_bytes(package())
    package_id = ledger.append_prediction_package(package(), raw, create_commitment(raw, salt=bytes(range(32))))
    ledger.connection.execute(
        "INSERT INTO prediction_rows VALUES(?,?,?,?,?,?)",
        ("orphan-row", package_id, "20260720-01-99", 1, "0.5", "0" * 64),
    )
    ledger.connection.commit()
    assert ledger.verify_integrity()["reason"] == "chain_source_set_mismatch"


def test_v2_ledger_rejects_unknown_chain_record_type(tmp_path: Path) -> None:
    ledger = ShadowLedgerV2(tmp_path / "shadow.sqlite3")
    ledger.connection.execute(
        "INSERT INTO ledger_chain(record_type,record_id,previous_hash,record_hash) VALUES(?,?,?,?)",
        ("unknown", "x", "0" * 64, "1" * 64),
    )
    ledger.connection.commit()
    assert ledger.verify_integrity()["reason"] == "unknown_chain_record_type"


def test_gate_stages_never_enable_payment_or_legacy_shortcut() -> None:
    base = compute_v2_gate({"prospectiveInputVerified": True})
    assert base["stage"] == "NOT_STARTED" and base["paymentEnabled"] is False
    stage_a = compute_v2_gate({"verifiedProspectiveDays": 7, "verifiedProspectiveRaces": 300, "pipelineIntegrityPassed": True})
    assert stage_a["stage"] == "PIPELINE_PROVEN" and stage_a["paymentEnabled"] is False
    stage_b = compute_v2_gate({"verifiedProspectiveDays": 30, "verifiedProspectiveRaces": 1500, "pipelineIntegrityPassed": True, "aggregateLogLossImproved": True, "aggregateBrierImproved": True, "weeklyMajorityImproved": True, "eceAcceptable": True, "venueDependencyAcceptable": True})
    assert stage_b["stage"] == "MODEL_PROSPECTIVE_SIGNAL"
    stage_c = compute_v2_gate({"verifiedProspectiveDays": 90, "verifiedProspectiveRaces": 5000, "pipelineIntegrityPassed": True, "logLossCiUpperBelowZero": True, "aggregateBrierImproved": True, "monthlyMajorityImproved": True, "eceAcceptable": True, "venueDependencyAcceptable": True, "laneDependencyAcceptable": True, "inputSourceRightsVerified": True, "externalTimestampVerified": True})
    assert stage_c["stage"] == "COMMERCIALIZATION_REVIEW_READY"
    assert stage_c["paymentStatus"] == "HUMAN_REVIEW_REQUIRED"
    assert stage_c["paymentEnabled"] is False and stage_c["profitClaimsAllowed"] is False and stage_c["productionAdoptionAllowed"] is False


def test_internal_shadow_does_not_require_commercial_source_rights_or_acquisition_time() -> None:
    readiness = compute_internal_prospective_readiness({
        "candidateIntegrityStatus": "CANDIDATE_FREEZE_INTEGRITY_PASS_WITH_COVERAGE_GAP",
        "modelHashMatches": True,
        "featureSchemaHashMatches": True,
        "inputSchemaStatus": "PRE_RACE_SCHEMA_VERIFIED",
        "resultFieldCount": 0,
        "externalAnchorRepositoryApproved": True,
        "githubCredentialConfigured": True,
        "commitmentDryRunPassed": True,
        "appendOnlyLedgerIntegrityPassed": True,
        "inputSourceRightsVerified": False,
        "inputAcquisitionTimeAttested": False,
        "paymentEnabled": False,
        "profitClaimsAllowed": False,
        "productionAdoptionAllowed": False,
    })
    assert readiness["internalProspectiveUse"] == "ALLOWED_WITH_RESTRICTIONS"
    assert readiness["shadowStatus"] == "READY_FOR_DAY1"
    assert readiness["commercialDataUse"] == "UNVERIFIED"


def test_internal_shadow_fails_closed_on_schema_anchor_or_safety_gate() -> None:
    base = {
        "candidateIntegrityStatus": "CANDIDATE_FREEZE_INTEGRITY_PASS_WITH_COVERAGE_GAP",
        "modelHashMatches": True,
        "featureSchemaHashMatches": True,
        "inputSchemaStatus": "PRE_RACE_SCHEMA_VERIFIED",
        "resultFieldCount": 0,
        "externalAnchorRepositoryApproved": True,
        "githubCredentialConfigured": True,
        "commitmentDryRunPassed": True,
        "appendOnlyLedgerIntegrityPassed": True,
        "paymentEnabled": False,
        "profitClaimsAllowed": False,
        "productionAdoptionAllowed": False,
    }
    for field, unsafe in (
        ("inputSchemaStatus", "UNVERIFIED"),
        ("resultFieldCount", 1),
        ("externalAnchorRepositoryApproved", False),
        ("githubCredentialConfigured", False),
        ("paymentEnabled", True),
    ):
        values = {**base, field: unsafe}
        assert compute_internal_prospective_readiness(values)["shadowStatus"] == "BLOCKED"
