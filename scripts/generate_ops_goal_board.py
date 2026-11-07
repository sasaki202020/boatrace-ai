from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.pipeline.ops_goal_board import write_ops_goal_board


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Generate Ops Goal Board artifacts.")
    parser.add_argument("--date", required=True, help="YYYY-MM-DD or YYYYMMDD")
    args = parser.parse_args()
    board = write_ops_goal_board(args.date)
    print(json.dumps(board, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
