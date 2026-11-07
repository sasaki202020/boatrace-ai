from __future__ import annotations

from pathlib import Path

import scripts.build_model_policy_separation_audit as module


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_separation_audit_flags_legacy_calibration_inside_policy(tmp_path: Path) -> None:
    source_root = tmp_path / "src"
    _write(source_root / "models" / "predict.py", "def predict():\n    return {'rawProbability': 0.2}\n")
    _write(source_root / "eval" / "calibrate.py", "def calibrate(value):\n    return value\n")
    _write(
        source_root / "strategy" / "evaluate_ev_and_skip.py",
        "from src.eval.calibrate import calibrate\n"
        "def _calibrate_probability_series(value):\n    return calibrate(value)\n"
        "def decide(value):\n    return {'policyDecision': 'WATCH'}\n",
    )

    audit = module.build_model_policy_separation_audit(source_root=source_root)

    assert audit["quality"]["classification"] == "separation_warning"
    assert audit["counts"]["reverseDependencyViolationCount"] == 0
    assert audit["counts"]["legacyCouplingCount"] == 2
    assert {row["checkCode"] for row in audit["checks"] if row["status"] == "warning"} == {
        "calibration_import_inside_policy",
        "calibration_method_inside_policy",
    }
    assert "policyDecision" not in audit["contracts"]["modelOutputFields"]
    assert "calibratedProbability" in audit["contracts"]["calibrationOutputFields"]
    assert "policyDecision" in audit["contracts"]["policyOutputFields"]
    assert audit["productionBehaviorChanged"] is False


def test_separation_audit_blocks_model_to_policy_reverse_dependency(tmp_path: Path) -> None:
    source_root = tmp_path / "src"
    _write(
        source_root / "models" / "predict.py",
        "from src.strategy.evaluate_ev_and_skip import StrategyEvaluator\n",
    )
    _write(source_root / "eval" / "calibrate.py", "def calibrate(value):\n    return value\n")
    _write(source_root / "strategy" / "evaluate_ev_and_skip.py", "def decide(value):\n    return value\n")

    audit = module.build_model_policy_separation_audit(source_root=source_root)

    assert audit["quality"]["classification"] == "separation_blocked"
    assert audit["counts"]["reverseDependencyViolationCount"] == 1
    assert audit["checks"][0]["checkCode"] == "model_imports_policy"
