from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

MODEL_ARTIFACTS = [
    ROOT / "models" / "win_model.joblib",
    ROOT / "models" / "model_bundle.json",
    ROOT / "models" / "probability_calibrator.json",
]

RUNTIME_OUTPUTS = [
    ROOT / "data" / "model_outputs" / "today_win_proba.csv",
    ROOT / "data" / "strategy_outputs" / "trifecta_candidates.csv",
    ROOT / "data" / "strategy_outputs" / "skip_decisions.csv",
]


def run_step(label: str, cmd: list[str]) -> dict:
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    return {
        "label": label,
        "cmd": cmd,
        "returncode": int(proc.returncode),
        "stdout_tail": proc.stdout[-2000:],
        "stderr_tail": proc.stderr[-1200:],
    }


def copy_if_exists(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def backup_files(paths: list[Path], backup_root: Path) -> None:
    for path in paths:
        rel = path.relative_to(ROOT)
        copy_if_exists(path, backup_root / rel)


def restore_files(paths: list[Path], backup_root: Path) -> None:
    for path in paths:
        rel = path.relative_to(ROOT)
        backup = backup_root / rel
        if backup.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, path)


def sync_current_snapshot() -> None:
    current_dir = ROOT / "models" / "current"
    current_dir.mkdir(parents=True, exist_ok=True)
    for src in MODEL_ARTIFACTS:
        copy_if_exists(src, current_dir / src.name)


def save_candidate_artifacts(run_dir: Path) -> None:
    candidate_dir = ROOT / "models" / "candidate"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    for src in MODEL_ARTIFACTS:
        copy_if_exists(src, candidate_dir / src.name)
        copy_if_exists(src, run_dir / "models" / src.name)
    for src in RUNTIME_OUTPUTS:
        copy_if_exists(src, run_dir / src.relative_to(ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(description="Train a candidate model safely, backtest it, and preserve current production artifacts.")
    parser.add_argument("--promote-current-snapshot", action="store_true", help="Refresh models/current from production artifacts before retraining.")
    args = parser.parse_args()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = ROOT / "reports" / "ops" / "candidate_runs" / timestamp
    backup_dir = run_dir / "backup_before_retrain"
    run_dir.mkdir(parents=True, exist_ok=True)

    if args.promote_current_snapshot:
        sync_current_snapshot()

    backup_files(MODEL_ARTIFACTS + RUNTIME_OUTPUTS, backup_dir)

    steps = [
        ("train_model", ["py", "-m", "src.models.train_win_model"]),
        ("train_calibrator", ["py", "-m", "src.eval.train_probability_calibrator"]),
        ("predict_win_proba", ["py", "-m", "src.models.predict_win_proba"]),
        ("generate_candidates", ["py", "-m", "src.strategy.generate_trifecta_candidates"]),
        ("evaluate_ev_skip", ["py", "-m", "src.strategy.evaluate_ev_and_skip"]),
        (
            "candidate_backtest",
            [
                "py",
                "-m",
                "src.jobs.backtest_runner",
                "--output-summary",
                str(run_dir / "candidate_backtest_summary.json"),
                "--output-races",
                str(run_dir / "candidate_backtest_race_results.csv"),
                "--output-daily",
                str(run_dir / "candidate_backtest_daily_summary.csv"),
                "--output-equity",
                str(run_dir / "candidate_backtest_equity_curve.csv"),
                "--output-report",
                str(run_dir / "candidate_backtest_runner_report.json"),
            ],
        ),
    ]

    step_reports: list[dict] = []
    overall_ok = True
    try:
        for label, cmd in steps:
            rep = run_step(label, cmd)
            step_reports.append(rep)
            if rep["returncode"] != 0:
                overall_ok = False
                break

        if overall_ok:
            save_candidate_artifacts(run_dir)
    finally:
        restore_files(MODEL_ARTIFACTS + RUNTIME_OUTPUTS, backup_dir)

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "status": "ok" if overall_ok else "failed",
        "run_dir": str(run_dir),
        "backup_dir": str(backup_dir),
        "steps": step_reports,
        "candidate_artifacts_saved": overall_ok,
    }
    report_path = run_dir / "retrain_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
