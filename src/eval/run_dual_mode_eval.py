import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd


def run(cmd: list[str]) -> None:
    print("[run]", " ".join(cmd))
    proc = subprocess.run(cmd, text=True)
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_dual_mode_rows(experiments_dir: Path) -> pd.DataFrame:
    files = sorted(experiments_dir.glob("*_dual_mode_summary.json"))
    rows: list[dict] = []
    for fp in files:
        try:
            payload = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        run_prefix = payload.get("run_prefix", fp.stem.replace("_dual_mode_summary", ""))
        run_date = ""
        for token in run_prefix.split("_"):
            if token.isdigit() and len(token) == 8:
                run_date = token
                break
        for r in payload.get("results", []):
            row = dict(r)
            row["run_prefix"] = run_prefix
            row["run_date"] = run_date
            rows.append(row)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    for col in ["buy", "trifecta_roi", "exacta_roi"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["run_dt"] = pd.to_datetime(df["run_date"], format="%Y%m%d", errors="coerce")
    return df.sort_values("run_dt")


def consecutive_bad_count(df: pd.DataFrame, mode: str, metric_col: str, window: str = "recent30") -> int:
    sub = df[(df["window"] == window) & (df["mode"] == mode)].sort_values("run_dt", ascending=False)
    cnt = 0
    for _, row in sub.iterrows():
        v = pd.to_numeric(row.get(metric_col), errors="coerce")
        if pd.isna(v):
            break
        if float(v) < 1.0:
            cnt += 1
        else:
            break
    return cnt


def write_ops_guard(run_prefix: str) -> None:
    experiments_dir = Path("reports/experiments")
    df = load_dual_mode_rows(experiments_dir)
    tri_streak = consecutive_bad_count(df, mode="trifecta", metric_col="trifecta_roi")
    exa_streak = consecutive_bad_count(df, mode="exacta_filtered", metric_col="exacta_roi")

    # Guard rule: stop mode when ROI<1.0 continues 2 runs or more.
    trifecta_enabled = tri_streak < 2
    exacta_enabled = exa_streak < 2

    existing_mode = "NORMAL"
    flags_path = Path("data/strategy_outputs/mode_flags.json")
    if flags_path.exists():
        try:
            existing_payload = json.loads(flags_path.read_text(encoding="utf-8"))
            existing_mode = str(existing_payload.get("strategy_mode", "NORMAL") or "NORMAL").upper()
        except Exception:
            existing_mode = "NORMAL"

    guard = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "window": "recent30",
        "rule": "disable mode when ROI < 1.0 for 2 consecutive runs",
        "streaks": {
            "trifecta_recent30_below_1": tri_streak,
            "exacta_recent30_below_1": exa_streak,
        },
        "mode_flags": {
            "trifecta_enabled": trifecta_enabled,
            "exacta_enabled": exacta_enabled,
            "strategy_mode": existing_mode,
        },
    }

    ops_dir = Path("reports/ops")
    ops_dir.mkdir(parents=True, exist_ok=True)
    guard_path = ops_dir / f"{run_prefix}_ops_guard.json"
    guard_path.write_text(json.dumps(guard, ensure_ascii=False, indent=2), encoding="utf-8")

    flags_path.parent.mkdir(parents=True, exist_ok=True)
    flags_path.write_text(json.dumps(guard["mode_flags"], ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[saved] {guard_path}")
    print(f"[saved] {flags_path}")
    print(
        "[guard]",
        f"trifecta_enabled={trifecta_enabled}",
        f"exacta_enabled={exacta_enabled}",
        f"(tri_streak={tri_streak}, exa_streak={exa_streak})",
    )


def fmt_num(v: object, digits: int = 4) -> str:
    n = pd.to_numeric(v, errors="coerce")
    if pd.isna(n):
        return "-"
    return f"{float(n):.{digits}f}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run trifecta + exacta mode evaluations in one command.")
    parser.add_argument("--predictions", default="data/strategy_outputs/skip_decisions.csv")
    parser.add_argument("--results", default="data/processed/historical_races.csv")
    parser.add_argument("--run-prefix", default="dual_mode")
    parser.add_argument("--windows", default="recent30,all", help="comma-separated: recent30,all,recent60")
    parser.add_argument("--exacta-output", default="data/strategy_outputs/skip_decisions_exacta_mode.csv")
    parser.add_argument("--min-first-win-proba", type=float, default=0.1673)
    parser.add_argument("--min-approx-prob", type=float, default=0.1552)
    parser.add_argument("--min-ev", type=float, default=34.82)
    args = parser.parse_args()

    py = sys.executable
    windows = [w.strip() for w in args.windows.split(",") if w.strip()]

    # Build exacta-mode filtered predictions.
    run(
        [
            py,
            "-m",
            "src.strategy.build_exacta_mode_predictions",
            "--input",
            args.predictions,
            "--output",
            args.exacta_output,
            "--min-first-win-proba",
            str(args.min_first_win_proba),
            "--min-approx-prob",
            str(args.min_approx_prob),
            "--min-ev",
            str(args.min_ev),
        ]
    )

    results_rows: list[dict] = []
    for window in windows:
        trifecta_run = f"{args.run_prefix}_trifecta_{window}"
        exacta_run = f"{args.run_prefix}_exacta_{window}"

        # Trifecta mode
        run(
            [
                py,
                "src/eval/evaluate_experiments.py",
                "--predictions",
                args.predictions,
                "--results",
                args.results,
                "--run-id",
                trifecta_run,
                "--window",
                window,
            ]
        )
        run(
            [
                py,
                "src/eval/evaluate_exacta_proxy.py",
                "--predictions",
                args.predictions,
                "--results",
                args.results,
                "--run-id",
                trifecta_run,
                "--window",
                window,
            ]
        )

        # Exacta mode (filtered BUY)
        run(
            [
                py,
                "src/eval/evaluate_experiments.py",
                "--predictions",
                args.exacta_output,
                "--results",
                args.results,
                "--run-id",
                exacta_run,
                "--window",
                window,
            ]
        )
        run(
            [
                py,
                "src/eval/evaluate_exacta_proxy.py",
                "--predictions",
                args.exacta_output,
                "--results",
                args.results,
                "--run-id",
                exacta_run,
                "--window",
                window,
            ]
        )

        tri_sum_path = Path("reports/experiments") / trifecta_run / "summary.json"
        tri_exa_path = Path("reports/experiments") / trifecta_run / "exacta_proxy_summary.json"
        ex_sum_path = Path("reports/experiments") / exacta_run / "summary.json"
        ex_exa_path = Path("reports/experiments") / exacta_run / "exacta_proxy_summary.json"

        tri_sum = load_json(tri_sum_path)
        tri_exa = load_json(tri_exa_path)
        ex_sum = load_json(ex_sum_path)
        ex_exa = load_json(ex_exa_path)

        results_rows.append(
            {
                "window": window,
                "mode": "trifecta",
                "buy": tri_sum["buy_race_count"],
                "trifecta_roi": tri_sum["roi"],
                "trifecta_hit_rate": tri_sum["exact_hit_rate"],
                "exacta_roi": tri_exa["exacta"]["roi"],
                "exacta_hit_rate": tri_exa["exacta"]["hit_rate"],
            }
        )
        results_rows.append(
            {
                "window": window,
                "mode": "exacta_filtered",
                "buy": ex_sum["buy_race_count"],
                "trifecta_roi": ex_sum["roi"],
                "trifecta_hit_rate": ex_sum["exact_hit_rate"],
                "exacta_roi": ex_exa["exacta"]["roi"],
                "exacta_hit_rate": ex_exa["exacta"]["hit_rate"],
            }
        )

    out = {
        "run_prefix": args.run_prefix,
        "windows": windows,
        "exacta_filter": {
            "min_first_win_proba": args.min_first_win_proba,
            "min_approx_prob": args.min_approx_prob,
            "min_ev": args.min_ev,
        },
        "results": results_rows,
    }

    out_path = Path("reports/experiments") / f"{args.run_prefix}_dual_mode_summary.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== dual mode summary ===")
    for row in results_rows:
        print(
            f"{row['window']:>8} | {row['mode']:<14} | BUY={row['buy']:>3} | "
            f"tri_roi={fmt_num(row.get('trifecta_roi'))} | tri_hit={fmt_num(row.get('trifecta_hit_rate'))} | "
            f"exa_roi={fmt_num(row.get('exacta_roi'))} | exa_hit={fmt_num(row.get('exacta_hit_rate'))}"
        )
    print(f"[saved] {out_path}")

    # Auto-refresh ops dashboard after each dual-mode run.
    dashboard_cmd = [py, "-m", "src.report.build_ops_dashboard"]
    print("[run]", " ".join(dashboard_cmd))
    dashboard_proc = subprocess.run(dashboard_cmd, text=True)
    if dashboard_proc.returncode != 0:
        print("[warn] dashboard build failed")

    write_ops_guard(args.run_prefix)


if __name__ == "__main__":
    main()
