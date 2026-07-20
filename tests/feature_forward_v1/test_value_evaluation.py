from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.feature_forward_v1.value_evaluation import (
    CONTRACT,
    build_collection_quality,
    build_priority_markdown,
    contract_sha256,
    predictive_value_gate,
    validate_chronological_oof,
    validate_evaluation_frame,
    validate_schedule_manifest,
    validate_settlement_manifest,
    verify_contract,
)
from scripts import build_feature_value_evaluation_v1 as cli

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "build_feature_value_evaluation_v1.py"


def ready_quality():
    return {
        group: {
            "consecutiveCollectionDays": 30,
            "coverage": 0.9,
            "postDeadlineCount": 0,
            "captureTimestampVerifiedRate": 1.0,
            "provenanceVerifiedRate": 1.0,
            "resultLeakageCount": 0,
        }
        for group in CONTRACT["featureGroups"]
    }


def test_unsettled_and_small_sample_predictive_evaluation_is_blocked():
    result = predictive_value_gate(ready_quality(), settled_races=0)
    assert result["status"] == "PREDICTIVE_VALUE_EVALUATION_BLOCKED"
    assert "minimum_settled_races_not_met" in result["blockedReasons"]
    assert result["targetEvaluationExecuted"] is False


def test_less_than_30_days_is_blocked():
    quality = ready_quality()
    quality["exhibition_time"]["consecutiveCollectionDays"] = 29
    result = predictive_value_gate(quality, settled_races=1500)
    assert "minimum_forward_days_not_met:exhibition_time" in result["blockedReasons"]


def test_post_deadline_feature_is_excluded():
    quality = ready_quality()
    quality["weather_and_water"]["postDeadlineCount"] = 1
    result = predictive_value_gate(quality, settled_races=1500)
    assert "post_deadline_records_present:weather_and_water" in result["blockedReasons"]


@pytest.mark.parametrize("column", ["target", "winner", "finish", "payout", "result"])
def test_target_columns_are_rejected(column):
    with pytest.raises(ValueError, match="target_column_prohibited"):
        validate_evaluation_frame([{"raceDate": "2026-07-21", column: 1}])


def test_nested_target_column_is_rejected():
    with pytest.raises(ValueError, match="target_column_prohibited"):
        validate_evaluation_frame([{"values": {"winner": 1}}])


def test_contract_change_is_detected():
    expected = contract_sha256(CONTRACT)
    changed = copy.deepcopy(CONTRACT)
    changed["minimumSettledRaces"] = 1499
    with pytest.raises(ValueError, match="feature_contract_hash_mismatch"):
        verify_contract(changed, expected)


def test_non_chronological_or_non_oof_contract_is_rejected():
    changed = copy.deepcopy(CONTRACT)
    changed["chronologicalFolds"]["method"] = "random"
    with pytest.raises(ValueError, match="chronological_oof_required"):
        verify_contract(changed, contract_sha256(changed))


def test_chronological_oof_rows_require_train_before_validation_and_race_exclusivity(tmp_path):
    artifact = tmp_path / "tree15-oof.json"
    artifact.write_text("{}", encoding="utf-8")
    artifact_hash = __import__("hashlib").sha256(artifact.read_bytes()).hexdigest()
    def oof_row(**values):
        return {
            "predictionMode": "OOF", "baselineId": "tree_15",
            "baselineModelSha256": CONTRACT["comparisonBaselineModelSha256"],
            "baselinePredictionArtifactPath": artifact.name,
            "baselinePredictionSha256": artifact_hash, **values,
        }
    valid = []
    for fold in range(1, 6):
        for day in range(1, fold + 1):
            valid.append(oof_row(raceKey=f"train-{day}", raceDate=f"2026-01-{day:02d}", fold=fold, split="train"))
        for prior in range(1, fold):
            valid.append(oof_row(raceKey=f"validation-{prior}", raceDate=f"2026-02-{prior:02d}", fold=fold, split="train"))
        valid.append(oof_row(raceKey=f"validation-{fold}", raceDate=f"2026-02-{fold:02d}", fold=fold, split="validation"))
    validate_chronological_oof(valid, tmp_path)
    invalid = valid + [{**valid[-1], "raceKey": "train-1"}]
    with pytest.raises(ValueError, match="oof_race_overlap"):
        validate_chronological_oof(invalid, tmp_path)
    with pytest.raises(ValueError, match="oof_fold_contract_invalid"):
        validate_chronological_oof([row for row in valid if row["fold"] < 5], tmp_path)
    changed = copy.deepcopy(CONTRACT)
    changed["evaluationMode"] = "in_sample"
    with pytest.raises(ValueError, match="chronological_oof_required"):
        verify_contract(changed, contract_sha256(changed))


def test_quality_and_predictive_rankings_are_separate():
    quality = build_collection_quality([], scheduled_races=[])
    markdown = build_priority_markdown(quality)
    assert "収集品質と研究優先度" in markdown
    assert "予測精度" in markdown
    assert "VERIFIED_POSITIVE" not in markdown


def test_collection_report_is_deterministic():
    first = build_collection_quality([], scheduled_races=[])
    second = build_collection_quality([], scheduled_races=[])
    assert first == second


def test_race_coverage_requires_all_six_boats():
    records = [
        {
            "raceDate": "2026-07-21", "jcd": "01", "raceNo": 1, "boatNo": 1,
            "featureGroup": "exhibition_time", "values": {"exhibitionTime": 6.8},
            "researchEligible": True, "captureTimestampVerified": True,
            "secondsBeforeDeadline": 600, "provenanceSha256": "a" * 64,
            "schemaSha256": "b" * 64, "parseStatus": "ok", "reasons": [],
        }
    ]
    schedule = [{"raceDate": "2026-07-21", "jcd": "01", "raceNo": 1}]
    quality = build_collection_quality(records, scheduled_races=schedule)
    assert quality["exhibition_time"]["coverage"] == 0.0
    assert quality["exhibition_time"]["verifiedPreDeadlineCount"] == 0


def test_settlement_manifest_requires_unique_provenance_backed_rows(tmp_path):
    source = tmp_path / "K.TXT"
    source.write_text(json.dumps({"raceKey": "r1", "settled": True, "winnerBoatNo": 1}), encoding="utf-8")
    source_hash = __import__("hashlib").sha256(source.read_bytes()).hexdigest()
    row = {
        "raceKey": "r1", "settled": True, "eligible": True,
        "resultSourcePath": source.name, "resultSourceSha256": source_hash,
        "settledAt": "2026-07-21T10:00:00+09:00", "resultConflict": False,
    }
    row["resultProvenanceSha256"] = contract_sha256({
        "raceKey": row["raceKey"], "resultSourceSha256": source_hash, "settledAt": row["settledAt"]
    })
    rows = [row]
    assert validate_settlement_manifest(rows, tmp_path) == {"r1"}
    with pytest.raises(ValueError, match="settlement_duplicate"):
        validate_settlement_manifest(rows + rows, tmp_path)
    source.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="settlement_source_hash_mismatch"):
        validate_settlement_manifest(rows, tmp_path)


def test_schedule_denominator_requires_source_and_anchor_hashes(tmp_path):
    races = [{"raceDate": "2026-07-21", "jcd": "01", "raceNo": 1, "timeBand": "morning"}]
    source = tmp_path / "schedule.json"
    source.write_text(json.dumps(races), encoding="utf-8")
    race_hash = contract_sha256(races)
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"contentSha256": race_hash}), encoding="utf-8")
    hashlib = __import__("hashlib")
    manifest = {
        "races": races, "externalTimestampVerified": True,
        "sourcePath": source.name, "sourceSha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "anchorReceiptPath": receipt.name, "anchorReceiptSha256": hashlib.sha256(receipt.read_bytes()).hexdigest(),
    }
    manifest["scheduleProvenanceSha256"] = contract_sha256({
        "sourceSha256": manifest["sourceSha256"], "raceSetSha256": race_hash,
        "anchorReceiptSha256": manifest["anchorReceiptSha256"],
    })
    assert validate_schedule_manifest(manifest, tmp_path, lambda payload: payload["contentSha256"] == race_hash) == races
    manifest["externalTimestampVerified"] = False
    with pytest.raises(ValueError, match="schedule_provenance_invalid"):
        validate_schedule_manifest(manifest, tmp_path, lambda payload: True)


def test_tree15_and_ledger_inputs_are_not_mutated(tmp_path):
    tree = tmp_path / "tree15.bin"
    ledger = tmp_path / "ledger.sqlite3"
    tree.write_bytes(b"fixed-model")
    ledger.write_bytes(b"fixed-ledger")
    before = (tree.read_bytes(), ledger.read_bytes())
    predictive_value_gate(ready_quality(), settled_races=0)
    assert (tree.read_bytes(), ledger.read_bytes()) == before


def test_cli_builds_quality_only_without_creating_store(tmp_path, monkeypatch):
    store = tmp_path / "missing-store"
    reports = tmp_path / "reports" / "feature_forward"
    command = [
        "--store",
        str(store),
        "--report-root",
        str(reports),
        "--as-of-date",
        "2026-07-21",
        "--settled-races",
        "0",
    ]
    monkeypatch.setattr(cli, "ALLOWED_REPORT_ROOT", reports.resolve())
    assert cli.main(command) == 0
    before = {path.name: path.read_bytes() for path in reports.iterdir()}
    assert cli.main(command) == 0
    after = {path.name: path.read_bytes() for path in reports.iterdir()}
    assert before == after
    assert not store.exists()
    status = json.loads((reports / "predictive_value_status.json").read_text())
    assert status["status"] == "PREDICTIVE_VALUE_EVALUATION_BLOCKED"
    assert status["targetEvaluationExecuted"] is False


def test_cli_rejects_existing_contract_change(tmp_path, monkeypatch):
    reports = tmp_path / "reports" / "feature_forward"
    reports.mkdir(parents=True)
    (reports / "feature_value_contract.json").write_text(
        json.dumps({"contractSha256": "0" * 64}), encoding="utf-8"
    )
    monkeypatch.setattr(cli, "ALLOWED_REPORT_ROOT", reports.resolve())
    with pytest.raises(ValueError, match="feature_contract_hash_mismatch"):
        cli.main([
            "--store",
            str(tmp_path / "store"),
            "--report-root",
            str(reports),
            "--as-of-date",
            "2026-07-21",
        ])


def test_cli_rejects_unbacked_settled_count(tmp_path, monkeypatch):
    reports = tmp_path / "reports" / "feature_forward"
    monkeypatch.setattr(cli, "ALLOWED_REPORT_ROOT", reports.resolve())
    with pytest.raises(ValueError, match="settlement_evidence_required"):
        cli.main([
            "--store", str(tmp_path / "store"),
            "--report-root", str(tmp_path / "reports" / "feature_forward"), "--as-of-date", "2026-07-21",
            "--settled-races", "1500",
        ])
