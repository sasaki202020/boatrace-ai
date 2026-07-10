from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

DEFAULT_TMP_ROOT = Path.home() / ".codex" / "memories" / "pytest_tmp_local"


def main(argv: list[str]) -> int:
    tmp_root = DEFAULT_TMP_ROOT
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    basetemp = tmp_root / f"basetemp_{run_id}"
    cache_dir = tmp_root / f"cache_{run_id}"
    tmp_root.mkdir(parents=True, exist_ok=True)
    basetemp.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["TMP"] = str(tmp_root)
    env["TEMP"] = str(tmp_root)
    env["TMPDIR"] = str(tmp_root)

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *argv,
        "--basetemp",
        str(basetemp),
        "-o",
        f"cache_dir={cache_dir}",
    ]
    print(f"[run_pytest_safe] tmp_root={tmp_root}", file=sys.stderr)
    return subprocess.call(cmd, cwd=str(ROOT), env=env)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
