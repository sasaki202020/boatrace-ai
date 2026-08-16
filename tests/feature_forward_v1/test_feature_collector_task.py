from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_feature_collector_registration_is_unattended_and_battery_safe() -> None:
    script = (
        ROOT / "scripts" / "register_normalized_live_tasks_v1.ps1"
    ).read_text(encoding="utf-8")

    assert 'BOATRACE-Feature-Forward-Collector-V1' in script
    assert "$featurePrincipal = New-ScheduledTaskPrincipal" in script
    assert "-LogonType S4U" in script
    assert "-Principal $featurePrincipal" in script
    assert "-AllowStartIfOnBatteries" in script
    assert "-DontStopIfGoingOnBatteries" in script
    assert "-RepetitionInterval (New-TimeSpan -Minutes 1)" in script


def test_feature_collector_upgrade_only_updates_the_collector_task() -> None:
    script = (
        ROOT / "scripts" / "update_feature_collector_task_v1.ps1"
    ).read_text(encoding="utf-8")

    assert 'BOATRACE-Feature-Forward-Collector-V1' in script
    assert "Set-ScheduledTask" in script
    assert "Register-ScheduledTask" not in script
    assert "run_local_prediction_settlement" not in script
    assert "FEATURE_COLLECTOR_TASK_CONFIGURATION_CONFLICT" in script
    assert "FEATURE_COLLECTOR_TASK_ELEVATION_REQUIRED" in script
    assert "Set-ScheduledTask -TaskName $TaskName -Principal $principal -Settings $settings -ErrorAction Stop" in script
    assert "function Get-TriggerSignature" in script
    assert "Compare-Object -ReferenceObject $triggerSignature" in script
    assert '$action.Arguments -notlike "*scripts\\run_live_feature_capture_v1.py*"' in script
    assert "-LogonType S4U" in script
    assert "-AllowStartIfOnBatteries" in script
    assert "-DontStopIfGoingOnBatteries" in script


def test_active_collector_enforces_policy_and_records_lifecycle() -> None:
    script = (ROOT / "scripts" / "run_live_feature_capture_v1.py").read_text(encoding="utf-8")

    assert "load_runtime_gate" in script
    assert "append_capture_lifecycle" in script
    assert '"BLOCKED_RUNTIME_GATE"' in script
    assert '"runManifestPath"' in script
    assert '"--policy"' in script
    assert '"--gate-config"' in script


def test_settlement_runner_enforces_policy_and_records_lifecycle() -> None:
    script = (ROOT / "scripts" / "run_local_prediction_settlement_v1.py").read_text(encoding="utf-8")

    assert "load_runtime_gate" in script
    assert "append_settlement_lifecycle" in script
    assert '"BLOCKED_RUNTIME_GATE"' in script
    assert '"--policy"' in script
    assert '"--gate-config"' in script


def test_lifecycle_report_requires_explicit_runtime_policy_attestation() -> None:
    script = (ROOT / "scripts" / "build_feature_lifecycle_report_v1.py").read_text(encoding="utf-8")

    assert "--runtime-policy-enforced" in script
    assert "runtime_policy_enforced=args.runtime_policy_enforced" in script
