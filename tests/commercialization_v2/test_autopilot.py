from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.commercialization_v2.autopilot import (
    acquire_lock,
    build_result_rows,
    compute_stage_a,
    redact_process_result,
    verify_git_baseline,
    validate_stage_a_approval,
    verify_approval_hash,
)


def test_lock_rejects_parallel_execution(tmp_path: Path):
    lock = tmp_path / "autopilot.lock"
    with acquire_lock(lock):
        with pytest.raises(ValueError, match="AUTOPILOT_ALREADY_RUNNING"):
            with acquire_lock(lock):
                pass
    assert not lock.exists()


def test_lock_recovers_stale_pid(tmp_path: Path):
    lock = tmp_path / "autopilot.lock"; lock.write_text("999999999\n")
    with acquire_lock(lock):
        assert lock.exists()
    assert not lock.exists()


def test_result_rows_only_include_predicted_races():
    boats1 = [{"boat_no": boat, "finishPosition": position} for position, boat in enumerate((2, 1, 3, 4, 5, 6), 1)]
    boats2 = [{"boat_no": boat, "finishPosition": position} for position, boat in enumerate((4, 2, 1, 3, 5, 6), 1)]
    parsed = {"races": [
        {"date": "20260720", "jcd": "01", "raceNo": 1, "raceStatus": "ok", "boatResults": boats1},
        {"date": "20260720", "jcd": "01", "raceNo": 2, "raceStatus": "ok", "boatResults": boats2},
    ]}
    assert build_result_rows(parsed, {"20260720-01-01"}) == [{"raceId": "20260720-01-01", "winningLane": 2}]


def test_result_conflict_is_rejected():
    parsed = {"races": [{"date": "20260720", "jcd": "01", "raceNo": 1, "raceStatus": "ok", "boatResults": [{"boat_no": 1, "finishPosition": 1}]}]}
    with pytest.raises(ValueError, match="RESULT_CONFLICT_QUARANTINED"):
        build_result_rows(parsed, {"20260720-01-01"})


def test_stage_a_requires_both_thresholds_and_integrity():
    assert compute_stage_a(7, 299, True) == "IN_PROGRESS"
    assert compute_stage_a(6, 300, True) == "IN_PROGRESS"
    assert compute_stage_a(7, 300, False) == "FAILED_INTEGRITY_GATE"
    assert compute_stage_a(7, 300, True) == "PIPELINE_PROVEN"


def test_process_reporting_never_contains_credential():
    token = "credential-must-not-appear"
    report = redact_process_result(0, '{"status":"WAITING_FOR_NEXT_BFILE"}', "", token)
    assert token not in json.dumps(report)
    assert report["status"] == "WAITING_FOR_NEXT_BFILE"


def test_task_scripts_never_embed_credentials():
    root = Path(__file__).resolve().parents[2]
    text = "\n".join((root / "scripts" / name).read_text(encoding="utf-8") for name in (
        "run_prospective_task_v2.ps1", "install_prospective_task_v2.ps1", "uninstall_prospective_task_v2.ps1"
    ))
    assert "BOATRACE_ANCHOR_GITHUB_TOKEN" not in text
    assert "gh auth token" not in text


def test_git_baseline_requires_exact_clean_head(tmp_path: Path, monkeypatch):
    baseline = tmp_path / "baseline.txt"; baseline.write_text("a" * 40)
    class Result:
        def __init__(self, stdout="", returncode=0): self.stdout = stdout; self.returncode = returncode
    def fake_run(args, **kwargs):
        if "status" in args: return Result("", 0)
        return Result("a" * 40 + "\n", 0)
    monkeypatch.setattr("src.commercialization_v2.autopilot.subprocess.run", fake_run)
    assert verify_git_baseline(tmp_path, baseline) == "a" * 40


def test_stage_a_approval_is_exact_and_cannot_enable_payment():
    approval = {"stageAAutopilotApproved": True, "minimumVerifiedDays": 7, "minimumVerifiedRaces": 300,
                "approvalExpiresAtStageA": True, "maximumExternalWrites": 1, "maximumPackages": 1,
                "maximumVenues": 1, "maximumRaces": 12, "maximumRetries": 0, "paymentEnabled": False,
                "profitClaimsAllowed": False, "productionAdoptionAllowed": False, "bettingEnabled": False}
    validate_stage_a_approval(approval)
    with pytest.raises(ValueError, match="BLOCKED_STAGE_A_APPROVAL"):
        validate_stage_a_approval({**approval, "paymentEnabled": True})


def test_approval_manifest_hash_is_fixed(tmp_path: Path):
    approval = tmp_path / "approval.json"; approval.write_text("{}\n")
    digest = __import__("hashlib").sha256(approval.read_bytes()).hexdigest()
    verify_approval_hash(approval, digest)
    approval.write_text('{"changed":true}\n')
    with pytest.raises(ValueError, match="BLOCKED_STAGE_A_APPROVAL"):
        verify_approval_hash(approval, digest)
