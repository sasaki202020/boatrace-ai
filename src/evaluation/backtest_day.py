from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from src.evaluation.settle_results import settle_daily_predictions


ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = ROOT / "reports" / "daily"


def build_backtest_summary(settlement: dict[str, Any]) -> dict[str, Any]:
    settlements = settlement.get("settlements") or []
    buy_count = int(settlement.get("buyCount") or 0)
    hit_count = int(settlement.get("hitCount") or 0)
    settled_bet_count = int(settlement.get("settledBetCount") or 0)
    stake_amount = float(settlement.get("settledStakeAmount") or 0.0)
    payout_amount = float(settlement.get("payoutAmount") or 0.0)
    hit_rate = (hit_count / settled_bet_count) if settled_bet_count else None
    recovery_rate = (payout_amount / stake_amount) if stake_amount > 0 else None
    roi = ((payout_amount - stake_amount) / stake_amount) if stake_amount > 0 else None
    return {
        "date": settlement.get("date", ""),
        "jcd": settlement.get("jcd", "all"),
        "raceCount": settlement.get("raceCount", len(settlements)),
        "buyCount": buy_count,
        "hitCount": hit_count,
        "settledBetCount": settled_bet_count,
        "unresolvedBetCount": int(settlement.get("unresolvedBetCount") or 0),
        "voidBetCount": int(settlement.get("voidBetCount") or 0),
        "hitRate": hit_rate,
        "stakeAmount": float(settlement.get("frozenStakeAmount") or settlement.get("stakeAmount") or 0.0),
        "settledStakeAmount": stake_amount,
        "unresolvedStakeAmount": float(settlement.get("unresolvedStakeAmount") or 0.0),
        "voidStakeAmount": float(settlement.get("voidStakeAmount") or 0.0),
        "payoutAmount": payout_amount,
        "recoveryRate": recovery_rate,
        "roi": roi,
        "settledRoi": roi,
        "resultsStatus": settlement.get("resultsStatus", "missing"),
        "resultOkCount": int(settlement.get("resultOkCount") or 0),
        "resultParseErrorCount": int(settlement.get("resultParseErrorCount") or 0),
        "resultPendingCount": int(settlement.get("resultPendingCount") or 0),
        "resultMissingCount": int(settlement.get("resultMissingCount") or 0),
        "settlements": settlements,
        "generatedAt": settlement.get("generatedAt"),
        "warnings": settlement.get("warnings") or [],
    }


def run_backtest_day(*, date: str, jcd: str = "all", stake_per_buy: int = 100, timeout: float = 30.0) -> dict[str, Any]:
    settlement = settle_daily_predictions(date=date, jcd=jcd, stake_per_buy=stake_per_buy, timeout=timeout)
    summary = build_backtest_summary(settlement)
    report_dir = REPORT_ROOT / date
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / f"{date.replace('-', '')}_backtest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Run a one-day backtest from settled predictions.")
    parser.add_argument("--date", required=True, help="YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--jcd", default="all")
    parser.add_argument("--stake", "--stake-per-buy", dest="stake", type=int, default=100)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    date = args.date if "-" in args.date else f"{args.date[:4]}-{args.date[4:6]}-{args.date[6:8]}"
    result = run_backtest_day(date=date, jcd=args.jcd, stake_per_buy=args.stake, timeout=args.timeout)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
