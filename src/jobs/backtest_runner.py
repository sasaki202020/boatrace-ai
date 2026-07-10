from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PREDICTIONS = ROOT / "data" / "strategy_outputs" / "skip_decisions.csv"
DEFAULT_HISTORICAL = ROOT / "data" / "processed" / "historical_races.csv"
DEFAULT_SUMMARY = ROOT / "reports" / "ops" / "backtest_summary_latest.json"
DEFAULT_RACES = ROOT / "reports" / "ops" / "backtest_race_results_latest.csv"
DEFAULT_DAILY = ROOT / "reports" / "ops" / "backtest_daily_summary_latest.csv"
DEFAULT_EQUITY = ROOT / "reports" / "ops" / "backtest_equity_curve_latest.csv"
DEFAULT_REPORT = ROOT / "reports" / "ops" / "backtest_runner_report.json"


def run_cmd(cmd: list[str]) -> dict:
    if cmd and cmd[0] == "py":
        cmd = [sys.executable, *cmd[1:]]
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    return {
        "cmd": cmd,
        "returncode": int(proc.returncode),
        "stdout_tail": proc.stdout[-2000:],
        "stderr_tail": proc.stderr[-1200:],
    }


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run operational BUY/SKIP backtest and emit a compact ops report.")
    parser.add_argument("--predictions", default=str(DEFAULT_PREDICTIONS))
    parser.add_argument("--historical", default=str(DEFAULT_HISTORICAL))
    parser.add_argument("--output-summary", default=str(DEFAULT_SUMMARY))
    parser.add_argument("--output-races", default=str(DEFAULT_RACES))
    parser.add_argument("--output-daily", default=str(DEFAULT_DAILY))
    parser.add_argument("--output-equity", default=str(DEFAULT_EQUITY))
    parser.add_argument("--output-report", default=str(DEFAULT_REPORT))
    parser.add_argument("--stake-mode", choices=["auto", "flat", "bet_amount"], default="auto")
    parser.add_argument("--flat-stake", type=float, default=100.0)
    parser.add_argument("--initial-bankroll", type=float, default=100000.0)
    args = parser.parse_args()

    pred_path = Path(args.predictions)
    hist_path = Path(args.historical)
    if not pred_path.exists():
        raise FileNotFoundError(f"predictions not found: {pred_path}")
    if not hist_path.exists():
        raise FileNotFoundError(f"historical not found: {hist_path}")

    cmd = [
        "py",
        "-m",
        "src.eval.backtest_buy_skip",
        "--predictions",
        str(pred_path),
        "--historical",
        str(hist_path),
        "--output-summary",
        str(Path(args.output_summary)),
        "--output-races",
        str(Path(args.output_races)),
        "--output-daily",
        str(Path(args.output_daily)),
        "--output-equity",
        str(Path(args.output_equity)),
        "--stake-mode",
        args.stake_mode,
        "--flat-stake",
        str(args.flat_stake),
        "--initial-bankroll",
        str(args.initial_bankroll),
    ]
    run_info = run_cmd(cmd)
    summary = load_json(Path(args.output_summary))

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "status": "ok" if run_info["returncode"] == 0 else "failed",
        "inputs": {
            "predictions": str(pred_path),
            "historical": str(hist_path),
            "stake_mode": args.stake_mode,
            "flat_stake": args.flat_stake,
            "initial_bankroll": args.initial_bankroll,
        },
        "outputs": {
            "summary": str(Path(args.output_summary)),
            "races": str(Path(args.output_races)),
            "daily": str(Path(args.output_daily)),
            "equity": str(Path(args.output_equity)),
        },
        "run": run_info,
        "metrics": {
            "buy_count": summary.get("buy_count"),
            "hit_count": summary.get("hit_count"),
            "hit_rate": summary.get("hit_rate"),
            "roi": summary.get("roi"),
            "avg_odds": summary.get("avg_odds"),
            "total_stake": summary.get("total_stake"),
            "total_return": summary.get("total_return"),
            "profit": summary.get("profit"),
            "final_equity": summary.get("final_equity"),
            "max_drawdown": summary.get("max_drawdown"),
            "max_consecutive_loss": summary.get("max_consecutive_loss"),
        },
    }
    report_path = Path(args.output_report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
