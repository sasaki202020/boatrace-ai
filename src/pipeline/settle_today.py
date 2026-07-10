from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from src.evaluation.settle_results import settle_daily_predictions


def settle_today(*, target_date: str, jcd: str = "all") -> dict:
    return settle_daily_predictions(date=target_date, jcd=jcd, allow_live_fallback=False)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Settle the daily MVP pipeline.")
    parser.add_argument("--date", required=True, help="YYYYMMDD")
    parser.add_argument("--jcd", default="all")
    parser.add_argument("--stake", type=int, default=100)
    args = parser.parse_args()
    if args.date.lower() == "today":
        target_date = date.today().isoformat()
    else:
        target_date = args.date if "-" in args.date else f"{args.date[:4]}-{args.date[4:6]}-{args.date[6:8]}"
    result = settle_daily_predictions(
        date=target_date,
        jcd=str(args.jcd).zfill(2) if str(args.jcd).isdigit() else "all",
        stake_per_buy=args.stake,
        allow_live_fallback=False,
    )
    compact = {
        "date": result.get("date"),
        "jcd": result.get("jcd"),
        "raceCount": result.get("raceCount"),
        "resultReadyCount": result.get("resultReadyCount"),
        "resultMissingCount": result.get("resultMissingCount"),
        "buyCount": result.get("buyCount"),
        "betCount": result.get("betCount"),
        "frozenStakeAmount": result.get("frozenStakeAmount"),
        "settledBetCount": result.get("settledBetCount"),
        "settledStakeAmount": result.get("settledStakeAmount"),
        "unresolvedBetCount": result.get("unresolvedBetCount"),
        "unresolvedStakeAmount": result.get("unresolvedStakeAmount"),
        "voidBetCount": result.get("voidBetCount"),
        "voidStakeAmount": result.get("voidStakeAmount"),
        "hitCount": result.get("hitCount"),
        "missCount": result.get("missCount"),
        "stakeAmount": result.get("stakeAmount"),
        "payoutAmount": result.get("payoutAmount"),
        "roi": result.get("roi"),
        "settledRoi": result.get("settledRoi"),
        "hitRate": result.get("hitRate"),
        "resultsStatus": result.get("resultsStatus"),
        "missingCount": result.get("missingCount"),
        "files": {
            "summary": str(Path("reports") / "daily" / f"{result.get('date')}_summary.json"),
            "settlement": str(Path("reports") / "daily" / f"{result.get('date')}_settlement.json"),
            "bets": str(Path("reports") / "daily" / f"{result.get('date')}_bets.csv"),
            "results": str(Path("reports") / "daily" / f"{result.get('date')}_results.csv"),
        },
    }
    print(json.dumps(compact, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
