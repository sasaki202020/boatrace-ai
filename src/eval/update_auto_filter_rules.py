from __future__ import annotations

import argparse
from pathlib import Path

from src.eval.generate_auto_filter_rules import main as generate_auto_filter_rules_main


def main() -> int:
    parser = argparse.ArgumentParser(description="Weekly refresh for AUTO_FILTER rules.")
    parser.add_argument("--recent-days", type=int, default=35)
    parser.add_argument("--recent-races", type=int, default=500)
    parser.add_argument("--min-rows", type=int, default=300)
    parser.add_argument("--output-rules", default=None)
    parser.add_argument("--output-report", default=None)
    args = parser.parse_args()

    argv = [
        "--source",
        "recent",
        "--recent-days",
        str(args.recent_days),
        "--recent-races",
        str(args.recent_races),
        "--min-rows",
        str(args.min_rows),
    ]
    if args.output_rules:
        argv.extend(["--output-rules", args.output_rules])
    if args.output_report:
        argv.extend(["--output-report", args.output_report])
    return generate_auto_filter_rules_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
