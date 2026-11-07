import argparse
from datetime import datetime
from pathlib import Path

import pandas as pd


DEFAULT_LOG = Path("reports/ops/trade_log.csv")


def main() -> None:
    parser = argparse.ArgumentParser(description="Append a single trade result record for ops review.")
    parser.add_argument("--race-id", required=True)
    parser.add_argument("--mode", choices=["trifecta", "exacta_filtered"], required=True)
    parser.add_argument("--decision", choices=["BUY", "SKIP"], required=True)
    parser.add_argument("--executed", choices=["YES", "NO"], required=True)
    parser.add_argument("--ticket", default="")
    parser.add_argument("--odds", type=float, default=None)
    parser.add_argument("--hit", choices=["HIT", "MISS", "NA"], default="NA")
    parser.add_argument("--payout", type=float, default=None)
    parser.add_argument("--note", default="")
    parser.add_argument("--log-file", default=str(DEFAULT_LOG))
    args = parser.parse_args()

    record = {
        "logged_at": datetime.now().isoformat(timespec="seconds"),
        "race_id": args.race_id,
        "mode": args.mode,
        "decision": args.decision,
        "executed": args.executed,
        "ticket": args.ticket,
        "odds": args.odds,
        "hit": args.hit,
        "payout": args.payout,
        "note": args.note,
    }

    log_path = Path(args.log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.exists():
        df = pd.read_csv(log_path)
        df = pd.concat([df, pd.DataFrame([record])], ignore_index=True)
    else:
        df = pd.DataFrame([record])
    df.to_csv(log_path, index=False, encoding="utf-8-sig")

    print(f"[saved] {log_path}")
    print(f"[rows] {len(df)}")


if __name__ == "__main__":
    main()
