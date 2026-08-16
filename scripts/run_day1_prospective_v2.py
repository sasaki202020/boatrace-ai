from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.commercialization_v2.canonical_package import build_prediction_package
from src.commercialization_v2.day1_readiness import generate_prediction_rows, sha256_file, validate_runtime_bfile
from src.commercialization_v2.day1_runner import execute_package, find_next_bfile
from src.commercialization_v2.github_contents_anchor import GitHubContentsTransport


EXPECTED = {
    "model": "a2f11bf69c1b4b7ea47cca847dbe0a46f076f7c08d3361ba9e30b43f12d65da0",
    "schema": "a3853bdbdb75d13d4a596928c13eaa034307b14a5a8c534d31fca9acdab623dd",
    "dataset": "bc2294f85e482ac1c1e7458236be509afd5d3adc9aa7afd4ec53fc4658e54f23",
    "asof": "c1ede746393c906e7197d9a461a32fcacb34e508387b6d189d57da20089f3bcb",
}
DEFAULT_B_ROOT = Path(r"C:\Users\goo10\競艇\boatrace-ai-mvp\data\raw\official\entries")


def _sha256_rows(frame: pd.DataFrame) -> str:
    return hashlib.sha256(frame.to_csv(index=False, lineterminator="\n").encode()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one forward-only Day 1 prospective package")
    parser.add_argument("--b-root", type=Path, default=DEFAULT_B_ROOT)
    parser.add_argument("--approval", type=Path, default=ROOT / "reports/commercialization_v2/day1/day1_real_anchor_approval_manifest.json")
    args = parser.parse_args()
    now = datetime.now(ZoneInfo("Asia/Tokyo"))
    source = find_next_bfile(args.b_root, now=now)
    if source is None:
        print(json.dumps({"status": "WAITING_FOR_NEXT_BFILE", "externalWrites": 0}))
        return 0
    approval = json.loads(args.approval.read_text(encoding="utf-8"))
    model = ROOT / "data/commercialization_v1/frozen_candidate/tree_15.joblib"
    history_path = ROOT / "data/offline_model_v3/canonical_race_results.csv"
    asof = ROOT / "data/offline_model_v3/asof_features.csv"
    if sha256_file(model) != EXPECTED["model"] or sha256_file(history_path) != EXPECTED["dataset"] or sha256_file(asof) != EXPECTED["asof"]:
        raise SystemExit("frozen_hash_mismatch")
    entries = validate_runtime_bfile(source)
    venues = sorted(entries["jcd"].astype(str).unique())
    selected = entries[entries["jcd"].astype(str) == venues[0]].copy()
    races = sorted(selected["race_no"].astype(int).unique())[:12]
    selected = selected[selected["race_no"].astype(int).isin(races)].copy()
    if selected["race_id"].nunique() > 12 or selected["jcd"].nunique() != 1:
        raise SystemExit("day1_scope_invalid")
    race_date = str(selected["date"].iloc[0])
    day_root = ROOT / "data/commercialization_v2/day1" / race_date
    ledger_path = day_root / "shadow.sqlite3"
    package = None
    if ledger_path.exists():
        connection = sqlite3.connect(f"file:{ledger_path.as_posix()}?mode=ro", uri=True)
        row = connection.execute("SELECT package_json FROM prediction_packages WHERE race_date=?", (race_date,)).fetchone()
        connection.close()
        package = json.loads(row[0]) if row else None
    if package is not None:
        code_commit = str(package["predictionCodeVersion"])
    else:
        history = pd.read_csv(history_path)
        predictions = generate_prediction_rows(selected, history, model_path=model, expected_model_sha256=EXPECTED["model"])
        code_commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()
        package = build_prediction_package(
            race_date=race_date, generated_at_utc=now.astimezone(ZoneInfo("UTC")).isoformat(), generated_at_jst=now.isoformat(),
            candidate_id="tree_15", model_sha256=EXPECTED["model"], feature_schema_sha256=EXPECTED["schema"],
            canonical_dataset_sha256=EXPECTED["dataset"], as_of_artifact_sha256=EXPECTED["asof"],
            input_raw_sha256=sha256_file(source), input_rows_sha256=_sha256_rows(selected), source_id=source.name,
            input_rights_status="UNVERIFIED_COMMERCIAL_USE", code_version=code_commit, seed=42, predictions=predictions,
        )
    token = os.environ.get("BOATRACE_ANCHOR_GITHUB_TOKEN", "")
    transport = GitHubContentsTransport(token, api_base=os.environ.get("BOATRACE_ANCHOR_GITHUB_API_BASE", "https://api.github.com"))
    result = execute_package(package, ledger_path=ledger_path, approval=approval, token=token,
                             transport=transport, code_commit=code_commit)
    print(json.dumps({"status": result.status, "externalWrites": result.external_writes,
                      "prospectiveRaces": result.prospective_races}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
