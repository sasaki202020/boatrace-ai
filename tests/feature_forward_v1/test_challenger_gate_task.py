from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_readiness_gate_runner_uses_only_allowlisted_research_report() -> None:
    script = (ROOT / "scripts" / "run_course_start_challenger_gate_v1.ps1").read_text(
        encoding="utf-8"
    )

    assert "run_course_start_challenger_v1.py" in script
    assert 'reports\\feature_forward' in script
    assert "data\\prospective\\predictions" in script
    assert "data\\prospective\\settlements" in script
    assert "production" not in script.lower().replace("no production", "")
    assert "--model-artifact" in script


def test_readiness_gate_registration_is_single_task_and_non_destructive() -> None:
    script = (
        ROOT / "scripts" / "register_course_start_challenger_gate_v1.ps1"
    ).read_text(encoding="utf-8")

    assert 'BOATRACE-CourseStart-Challenger-Gate-V1' in script
    assert "EXISTING_TASK_CONFIGURATION_CONFLICT" in script
    assert "-MultipleInstances IgnoreNew" in script
    assert "-RepetitionInterval (New-TimeSpan -Minutes 30)" in script
    assert "-Force" not in script
    assert "Remove-ScheduledTask" not in script
