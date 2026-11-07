from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LOG_PATH = ROOT / "data" / "processed" / "daily_update_report.json"


def run_step(label: str, cmd: list[str], cwd: Path = ROOT) -> dict:
    p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    return {
        "label": label,
        "cmd": cmd,
        "returncode": int(p.returncode),
        "stdout_tail": p.stdout[-1500:],
        "stderr_tail": p.stderr[-800:],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["results", "predict", "full"], default="full")
    parser.add_argument("--results-date", default=(datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"))
    parser.add_argument("--allow-no-robots", action="store_true")
    args = parser.parse_args()

    steps: list[tuple[str, list[str]]] = []
    if args.mode in {"results", "full"}:
        cmd = ["py", "src/data/fetch_race_results.py", "--date", args.results_date]
        if args.allow_no_robots:
            cmd.append("--allow-no-robots")
        steps.extend(
            [
                ("fetch_results", cmd),
                ("build_historical", ["py", "-m", "src.data.build_historical_races"]),
                ("build_features", ["py", "-m", "src.features.build_features"]),
                ("train_model", ["py", "-m", "src.models.train_win_model"]),
            ]
        )

    if args.mode in {"predict", "full"}:
        cmd_live = ["py", "src/data/fetch_live_odds.py"]
        if args.allow_no_robots:
            cmd_live.append("--allow-no-robots")
        steps.extend(
            [
                ("predict_win_proba", ["py", "-m", "src.models.predict_win_proba"]),
                ("generate_candidates", ["py", "-m", "src.strategy.generate_trifecta_candidates"]),
                ("fetch_live_odds", cmd_live),
                ("evaluate_ev_skip", ["py", "-m", "src.strategy.evaluate_ev_and_skip"]),
                (
                    "evaluate_experiments",
                    [
                        "py",
                        "src/eval/evaluate_experiments.py",
                        "--predictions",
                        "data/strategy_outputs/skip_decisions.csv",
                        "--results",
                        "data/processed/historical_races.csv",
                        "--run-id",
                        f"daily_update_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                        "--window",
                        "recent30",
                    ],
                ),
            ]
        )

    step_reports = []
    overall_ok = True
    for label, cmd in steps:
        rep = run_step(label, cmd)
        step_reports.append(rep)
        if rep["returncode"] != 0:
            overall_ok = False
            # 失敗時は停止（運用安全）
            break

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": args.mode,
        "results_date": args.results_date,
        "status": "ok" if overall_ok else "failed",
        "steps": step_reports,
    }
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

