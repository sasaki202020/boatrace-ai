from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CURRENT_SUMMARY = ROOT / "reports" / "ops" / "backtest_summary_latest.json"
CURRENT_GUARD = ROOT / "reports" / "ops" / "model_guard_latest.json"
LATEST_CANDIDATE_ROOT = ROOT / "reports" / "ops" / "candidate_runs"
CURRENT_MODEL_DIR = ROOT / "models" / "current"
CANDIDATE_MODEL_DIR = ROOT / "models" / "candidate"


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def find_latest_candidate_run() -> Path | None:
    if not LATEST_CANDIDATE_ROOT.exists():
        return None
    runs = [p for p in LATEST_CANDIDATE_ROOT.iterdir() if p.is_dir()]
    if not runs:
        return None
    return sorted(runs)[-1]


def _to_float(v):
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def copy_tree_contents(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        target = dst / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare latest candidate model against current baseline and optionally promote it.")
    parser.add_argument("--candidate-run", default=None, help="Specific candidate run directory. Defaults to latest under reports/ops/candidate_runs.")
    parser.add_argument("--min-roi-gain", type=float, default=0.0)
    parser.add_argument("--min-buy-ratio", type=float, default=0.8)
    parser.add_argument("--max-dd-worse", type=float, default=0.05)
    parser.add_argument("--promote", action="store_true")
    args = parser.parse_args()

    candidate_run = Path(args.candidate_run) if args.candidate_run else find_latest_candidate_run()
    if candidate_run is None:
        report = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "status": "HOLD",
            "candidate_run": None,
            "current_guard_status": load_json(CURRENT_GUARD).get("status"),
            "thresholds": {
                "min_roi_gain": args.min_roi_gain,
                "min_buy_ratio": args.min_buy_ratio,
                "max_dd_worse": args.max_dd_worse,
            },
            "current": load_json(CURRENT_SUMMARY),
            "candidate": {},
            "reasons": ["candidate run missing"],
            "promoted": False,
        }
        out = ROOT / "reports" / "ops" / "model_compare_latest.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    candidate_summary = load_json(candidate_run / "candidate_backtest_summary.json")
    current_summary = load_json(CURRENT_SUMMARY)
    current_guard = load_json(CURRENT_GUARD)

    cand_roi = _to_float(candidate_summary.get("roi"))
    curr_roi = _to_float(current_summary.get("roi"))
    cand_buy = int(candidate_summary.get("buy_count") or 0)
    curr_buy = int(current_summary.get("buy_count") or 0)
    cand_dd = _to_float(candidate_summary.get("max_drawdown"))
    curr_dd = _to_float(current_summary.get("max_drawdown"))

    reasons: list[str] = []
    passed = True

    if cand_roi is None:
        passed = False
        reasons.append("candidate roi missing")
    if curr_roi is not None and cand_roi is not None and cand_roi < curr_roi + args.min_roi_gain:
        passed = False
        reasons.append(f"candidate roi below required gain ({cand_roi} < {curr_roi}+{args.min_roi_gain})")
    if curr_buy > 0 and cand_buy < int(curr_buy * args.min_buy_ratio):
        passed = False
        reasons.append(f"candidate buy_count too low ({cand_buy} < {int(curr_buy * args.min_buy_ratio)})")
    if curr_dd is not None and cand_dd is not None and cand_dd < curr_dd - args.max_dd_worse:
        passed = False
        reasons.append(f"candidate drawdown too deep ({cand_dd} < {curr_dd}-{args.max_dd_worse})")
    if not current_summary:
        reasons.append("current summary missing, comparison used candidate only")

    promoted = False
    if passed and args.promote and CANDIDATE_MODEL_DIR.exists():
        copy_tree_contents(CANDIDATE_MODEL_DIR, ROOT / "models")
        copy_tree_contents(CANDIDATE_MODEL_DIR, CURRENT_MODEL_DIR)
        promoted = True

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "status": "PASS" if passed else "HOLD",
        "candidate_run": str(candidate_run),
        "current_guard_status": current_guard.get("status"),
        "thresholds": {
            "min_roi_gain": args.min_roi_gain,
            "min_buy_ratio": args.min_buy_ratio,
            "max_dd_worse": args.max_dd_worse,
        },
        "current": {
            "roi": curr_roi,
            "buy_count": curr_buy,
            "max_drawdown": curr_dd,
        },
        "candidate": {
            "roi": cand_roi,
            "buy_count": cand_buy,
            "max_drawdown": cand_dd,
        },
        "reasons": reasons,
        "promoted": promoted,
    }
    out = ROOT / "reports" / "ops" / "model_compare_latest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
