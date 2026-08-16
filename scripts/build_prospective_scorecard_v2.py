from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from src.commercialization_v2.scorecard import build_scorecard


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--ledger", type=Path, required=True); parser.add_argument("--output", type=Path, required=True); args = parser.parse_args(); result = build_scorecard(args.ledger); args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"); return 0


if __name__ == "__main__": raise SystemExit(main())
