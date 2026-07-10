from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CURRENT = ROOT / "reports" / "ops" / "backtest_summary_latest.json"
DEFAULT_BASELINE = ROOT / "reports" / "ops" / "model_guard_baseline.json"
DEFAULT_REPORT = ROOT / "reports" / "ops" / "model_guard_latest.json"


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _to_float(v) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare latest backtest against a baseline and decide if it is safe to adopt.")
    parser.add_argument("--current", default=str(DEFAULT_CURRENT))
    parser.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    parser.add_argument("--output", default=str(DEFAULT_REPORT))
    parser.add_argument("--min-roi", type=float, default=1.00)
    parser.add_argument("--min-buy-count", type=int, default=3)
    parser.add_argument("--max-drawdown", type=float, default=-0.20)
    parser.add_argument("--promote-on-pass", action="store_true")
    args = parser.parse_args()

    current_path = Path(args.current)
    baseline_path = Path(args.baseline)
    current = load_json(current_path)
    baseline = load_json(baseline_path)

    current_roi = _to_float(current.get("roi"))
    current_buy = int(current.get("buy_count") or 0)
    current_dd = _to_float(current.get("max_drawdown"))

    pass_thresholds = True
    reasons: list[str] = []
    if current_roi is None or current_roi < args.min_roi:
        pass_thresholds = False
        reasons.append(f"roi below threshold ({current_roi} < {args.min_roi})")
    if current_buy < args.min_buy_count:
        pass_thresholds = False
        reasons.append(f"buy_count below threshold ({current_buy} < {args.min_buy_count})")
    if current_dd is not None and current_dd < args.max_drawdown:
        pass_thresholds = False
        reasons.append(f"max_drawdown too deep ({current_dd} < {args.max_drawdown})")

    baseline_roi = _to_float(baseline.get("roi")) if baseline else None
    beats_baseline = True
    if baseline:
        if current_roi is None or baseline_roi is None:
            beats_baseline = False
            reasons.append("baseline comparison unavailable")
        elif current_roi < baseline_roi:
            beats_baseline = False
            reasons.append(f"roi below baseline ({current_roi} < {baseline_roi})")

    status = "PASS" if pass_thresholds and beats_baseline else "HOLD"

    if status == "PASS" and args.promote_on_pass and current_path.exists():
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(current_path, baseline_path)

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "status": status,
        "thresholds": {
            "min_roi": args.min_roi,
            "min_buy_count": args.min_buy_count,
            "max_drawdown": args.max_drawdown,
        },
        "current": {
            "path": str(current_path),
            "roi": current_roi,
            "buy_count": current_buy,
            "hit_rate": _to_float(current.get("hit_rate")),
            "max_drawdown": current_dd,
            "profit": _to_float(current.get("profit")),
        },
        "baseline": {
            "path": str(baseline_path),
            "exists": bool(baseline),
            "roi": baseline_roi,
            "buy_count": int(baseline.get("buy_count") or 0) if baseline else None,
            "hit_rate": _to_float(baseline.get("hit_rate")) if baseline else None,
            "max_drawdown": _to_float(baseline.get("max_drawdown")) if baseline else None,
        },
        "reasons": reasons,
        "promoted": bool(status == "PASS" and args.promote_on_pass),
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
