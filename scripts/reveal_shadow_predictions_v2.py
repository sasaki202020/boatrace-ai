from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from src.commercialization_v2.commitment import verify_reveal


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a reveal bundle after the conservative cutoff.")
    parser.add_argument("--package", type=Path, required=True); parser.add_argument("--salt", type=Path, required=True); parser.add_argument("--anchor", type=Path, required=True); parser.add_argument("--output", type=Path, required=True); parser.add_argument("--now", required=True)
    args = parser.parse_args(); raw = args.package.read_bytes(); salt = args.salt.read_text(encoding="ascii").strip(); anchor = json.loads(args.anchor.read_text(encoding="utf-8"))
    if datetime.fromisoformat(args.now) < datetime.fromisoformat(anchor["conservativeCutoff"]): raise SystemExit("reveal_before_cutoff_prohibited")
    if not verify_reveal(raw, salt, anchor["commitment"]): raise SystemExit("reveal_verification_failed")
    bundle = {"anchor": anchor, "saltHex": salt, "predictionPackage": json.loads(raw), "rawInputIncluded": False}; args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"); return 0


if __name__ == "__main__": raise SystemExit(main())
