from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.feature_forward_v1.live_capture import run_capture_cycle


def main() -> int:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--b-file", type=Path)
    source.add_argument("--b-root", type=Path)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--status", type=Path, required=True)
    args = parser.parse_args()
    b_file = args.b_file or sorted(args.b_root.glob("B*.TXT"))[-1]
    result = run_capture_cycle(b_file=b_file, store_root=args.store)
    args.status.parent.mkdir(parents=True, exist_ok=True)
    args.status.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 2 if result["status"] in {
        "FEATURE_COLLECTION_STOPPED", "FEATURE_CAPTURE_NETWORK_ERROR"
    } else 0


if __name__ == "__main__":
    raise SystemExit(main())
