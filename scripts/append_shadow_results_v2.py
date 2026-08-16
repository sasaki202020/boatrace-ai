from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from src.commercialization_v2.ledger import ShadowLedgerV2


def main() -> int:
    parser = argparse.ArgumentParser(description="Append results to an existing v2 prediction ledger."); parser.add_argument("--ledger", type=Path, required=True); parser.add_argument("--race-date", required=True); parser.add_argument("--results", type=Path, required=True)
    args = parser.parse_args(); raw = args.results.read_bytes(); rows = json.loads(raw); result = ShadowLedgerV2(args.ledger).append_result_package(args.race_date, rows, hashlib.sha256(raw).hexdigest()); print(result); return 0


if __name__ == "__main__": raise SystemExit(main())
