from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.feature_forward_v1.parallel_shadow import (  # noqa: E402
    ParallelShadowError,
    run_parallel_shadow,
)

JST = ZoneInfo("Asia/Tokyo")
DEFAULT_CONFIG = ROOT / "config" / "feature_forward_v1" / "parallel_shadow_config.json"


def _code_commit(repo: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ParallelShadowError("code_commit_unavailable") from exc
    value = result.stdout.strip()
    if not value:
        raise ParallelShadowError("code_commit_unavailable")
    return value


def _code_source_sha256(repo: Path) -> str:
    paths = (
        repo / "src" / "feature_forward_v1" / "parallel_shadow.py",
        repo / "scripts" / "run_parallel_shadow_v1.py",
    )
    digest = hashlib.sha256()
    for path in paths:
        if not path.is_file():
            raise ParallelShadowError("code_source_unavailable")
        digest.update(str(path.relative_to(repo)).replace("\\", "/").encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run fixed course/start challenger in a separate parallel shadow ledger."
    )
    parser.add_argument("--prediction-root", type=Path, required=True)
    parser.add_argument("--feature-store", type=Path, required=True)
    parser.add_argument("--shadow-root", type=Path, required=True)
    parser.add_argument("--model-artifact", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--code-repo", type=Path, default=ROOT)
    parser.add_argument("--code-commit")
    parser.add_argument("--now", help="Timezone-aware ISO timestamp for deterministic operation tests.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    now = datetime.fromisoformat(args.now).astimezone(JST) if args.now else datetime.now(JST)
    code_commit = args.code_commit or _code_commit(args.code_repo.resolve())
    code_source_sha256 = _code_source_sha256(args.code_repo.resolve())
    try:
        result = run_parallel_shadow(
            prediction_root=args.prediction_root,
            feature_store=args.feature_store,
            shadow_root=args.shadow_root,
            model_artifact=args.model_artifact,
            config_path=args.config,
            code_commit=code_commit,
            code_source_sha256=code_source_sha256,
            now=now,
        )
    except (OSError, ValueError, ParallelShadowError) as exc:
        print(f"PARALLEL_SHADOW_BLOCKED:{type(exc).__name__}:{exc}", file=sys.stderr)
        return 2
    print(__import__("json").dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
