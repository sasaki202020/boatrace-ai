from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / "reports" / "ops" / "daily_pipeline_report.json"
GUARD_REPORT_PATH = ROOT / "reports" / "ops" / "model_guard_latest.json"


def run_step(label: str, cmd: list[str], cwd: Path = ROOT) -> dict:
    if cmd and cmd[0] == "py":
        cmd = [sys.executable, *cmd[1:]]
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    return {
        "label": label,
        "cmd": cmd,
        "returncode": int(proc.returncode),
        "stdout_tail": proc.stdout[-2000:],
        "stderr_tail": proc.stderr[-1200:],
    }


def append_step(steps: list[tuple[str, list[str]]], label: str, *cmd: str) -> None:
    steps.append((label, list(cmd)))


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def should_trigger_retrain(guard_report: dict) -> bool:
    status = str(guard_report.get("status") or "").upper()
    if status == "PASS":
        return False
    reasons = [str(r) for r in (guard_report.get("reasons") or [])]
    if not reasons:
        return True
    keywords = ("roi below threshold", "buy_count below threshold", "roi below baseline")
    return any(any(key in reason for key in keywords) for reason in reasons)


def main() -> None:
    parser = argparse.ArgumentParser(description="Safe daily operations runner: update, predict, backtest, dashboard, guard.")
    parser.add_argument(
        "--mode",
        choices=["update", "train", "predict", "backtest", "guard", "retrain", "compare", "full"],
        default="full",
    )
    parser.add_argument("--results-date", default=(datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"))
    parser.add_argument("--allow-no-robots", action="store_true")
    parser.add_argument("--skip-fetch-results", action="store_true")
    parser.add_argument("--skip-live-odds", action="store_true")
    parser.add_argument("--promote-on-pass", action="store_true")
    parser.add_argument("--conditional-retrain", action="store_true", help="After guard HOLD, build a candidate model and compare it.")
    parser.add_argument("--promote-candidate", action="store_true", help="If compare passes, promote candidate into production artifacts.")
    parser.add_argument("--promote-current-snapshot", action="store_true", help="Refresh models/current before candidate retrain.")
    args = parser.parse_args()

    steps: list[tuple[str, list[str]]] = []

    if args.mode in {"update", "full"}:
        if not args.skip_fetch_results:
            cmd = ["py", "src/data/fetch_race_results.py", "--date", args.results_date]
            if args.allow_no_robots:
                cmd.append("--allow-no-robots")
            steps.append(("fetch_results", cmd))
        append_step(steps, "build_historical", "py", "-m", "src.data.build_historical_races")
        append_step(steps, "build_features", "py", "-m", "src.features.build_features")

    if args.mode in {"train", "full"}:
        append_step(steps, "train_model", "py", "-m", "src.models.train_win_model")
        append_step(steps, "train_calibrator", "py", "-m", "src.eval.train_probability_calibrator")

    if args.mode in {"predict", "full"}:
        append_step(steps, "predict_win_proba", "py", "-m", "src.models.predict_win_proba")
        append_step(steps, "generate_candidates", "py", "-m", "src.strategy.generate_trifecta_candidates")
        if not args.skip_live_odds:
            live_cmd = ["py", "src/data/fetch_live_odds.py"]
            if args.allow_no_robots:
                live_cmd.append("--allow-no-robots")
            steps.append(("fetch_live_odds", live_cmd))
        append_step(steps, "evaluate_ev_skip", "py", "-m", "src.strategy.evaluate_ev_and_skip")
        append_step(steps, "analyze_gate_health", "py", "-m", "src.eval.analyze_gate_health")
        append_step(steps, "build_daily_report", "py", "src/report/build_daily_report.py")

    if args.mode in {"backtest", "full"}:
        append_step(steps, "backtest_runner", "py", "-m", "src.jobs.backtest_runner")
        append_step(
            steps,
            "compare_calibrated_ev",
            "py",
            "-m",
            "src.eval.compare_calibrated_ev",
            "--backtest-races",
            "reports/ops/backtest_race_results_latest.csv",
            "--out-summary",
            "reports/calibrated_ev_summary.json",
            "--out-comparison",
            "reports/calibrated_ev_comparison.csv",
            "--out-diff",
            "reports/calibrated_ev_topdiff.csv",
            "--out-feature-gaps",
            "reports/calibrated_ev_feature_gaps.csv",
        )
        append_step(
            steps,
            "evaluate_experiments",
            "py",
            "src/eval/evaluate_experiments.py",
            "--predictions",
            "data/strategy_outputs/skip_decisions.csv",
            "--results",
            "data/processed/historical_races.csv",
            "--run-id",
            f"daily_ops_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "--window",
            "recent30",
        )
        append_step(steps, "build_ops_dashboard", "py", "src/report/build_ops_dashboard.py")

    if args.mode in {"guard", "full"}:
        guard_cmd = ["py", "-m", "src.jobs.model_guard"]
        if args.promote_on_pass:
            guard_cmd.append("--promote-on-pass")
        steps.append(("model_guard", guard_cmd))

    if args.mode == "retrain":
        retrain_cmd = ["py", "-m", "src.jobs.retrain"]
        if args.promote_current_snapshot:
            retrain_cmd.append("--promote-current-snapshot")
        steps.append(("candidate_retrain", retrain_cmd))

    if args.mode == "compare":
        compare_cmd = ["py", "-m", "src.jobs.model_compare"]
        if args.promote_candidate:
            compare_cmd.append("--promote")
        steps.append(("candidate_compare", compare_cmd))

    step_reports: list[dict] = []
    overall_ok = True
    for label, cmd in steps:
        rep = run_step(label, cmd)
        step_reports.append(rep)
        if rep["returncode"] != 0:
            overall_ok = False
            break

    conditional_retrain_ran = False
    if overall_ok and args.conditional_retrain and args.mode in {"guard", "full"}:
        guard_report = load_json(GUARD_REPORT_PATH)
        if should_trigger_retrain(guard_report):
            conditional_retrain_ran = True
            retrain_cmd = ["py", "-m", "src.jobs.retrain"]
            if args.promote_current_snapshot:
                retrain_cmd.append("--promote-current-snapshot")
            retrain_rep = run_step("candidate_retrain", retrain_cmd)
            step_reports.append(retrain_rep)
            if retrain_rep["returncode"] != 0:
                overall_ok = False
            else:
                compare_cmd = ["py", "-m", "src.jobs.model_compare"]
                if args.promote_candidate:
                    compare_cmd.append("--promote")
                compare_rep = run_step("candidate_compare", compare_cmd)
                step_reports.append(compare_rep)
                if compare_rep["returncode"] != 0:
                    overall_ok = False

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": args.mode,
        "results_date": args.results_date,
        "status": "ok" if overall_ok else "failed",
        "conditional_retrain_requested": bool(args.conditional_retrain),
        "conditional_retrain_ran": conditional_retrain_ran,
        "promote_candidate_requested": bool(args.promote_candidate),
        "steps": step_reports,
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
