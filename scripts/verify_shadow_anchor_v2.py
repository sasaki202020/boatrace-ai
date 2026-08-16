from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from src.commercialization_v2.anchor_provider import AnchorReceipt, MockAnchorProvider


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a previously saved receipt without network access.")
    parser.add_argument("--anchor", type=Path, required=True); parser.add_argument("--receipt", type=Path, required=True); parser.add_argument("--allow-repository", action="append", required=True); parser.add_argument("--cutoff", required=True)
    args = parser.parse_args(); payload = json.loads(args.anchor.read_text(encoding="utf-8")); receipt = AnchorReceipt(**json.loads(args.receipt.read_text(encoding="utf-8")))
    result = MockAnchorProvider(receipt.repository).verify_anchor_receipt(receipt, payload=payload, repository_allowlist=set(args.allow_repository), cutoff=args.cutoff)
    print(json.dumps(result, sort_keys=True)); return 0


if __name__ == "__main__": raise SystemExit(main())
