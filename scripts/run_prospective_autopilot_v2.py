from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))

from src.commercialization_v2.autopilot import acquire_lock, aggregate_ledgers, build_result_rows, file_sha256, redact_process_result, validate_stage_a_approval, verify_approval_hash, verify_git_baseline
from src.commercialization_v2.ledger import ShadowLedgerV2
from src.ingest.parsers.official_k_result_parser import parse_official_k_result_file


DEFAULT_B_ROOT = Path(r"C:\Users\goo10\競艇\boatrace-ai-mvp\data\raw\official\entries")
DEFAULT_K_ROOT = Path(r"C:\Users\goo10\競艇\boatrace-ai-mvp\data\raw\official\results")
RUNTIME = ROOT / "data/commercialization_v2/autopilot"
EXPECTED_APPROVAL_SHA256 = "2087100a6ff5d1939d3e10711551485b0e038c3545c78a48614cb00b287d80fc"


def github_token() -> str:
    result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=20)
    token = result.stdout.strip()
    if result.returncode or not token:
        raise ValueError("BLOCKED_RUNTIME_CREDENTIAL")
    return token


def settle_available_results(k_root: Path, day_root: Path) -> dict[str, int]:
    settled = pending = conflicts = 0
    for ledger_path in sorted(day_root.glob("*/shadow.sqlite3")):
        race_date = ledger_path.parent.name
        k_file = k_root / f"K{race_date[2:].replace('-', '')}.TXT"
        connection = sqlite3.connect(f"file:{ledger_path.as_posix()}?mode=ro", uri=True)
        predicted = {str(row[0]) for row in connection.execute("SELECT DISTINCT race_id FROM prediction_rows")}
        already = connection.execute("SELECT COUNT(*) FROM result_packages").fetchone()[0]
        connection.close()
        if already:
            continue
        if not k_file.is_file():
            pending += 1; continue
        try:
            rows = build_result_rows(parse_official_k_result_file(k_file), predicted)
            if len(rows) != len(predicted):
                pending += 1; continue
            ledger = ShadowLedgerV2(ledger_path)
            before = ledger.prediction_digest()
            ledger.append_result_package(race_date, rows, file_sha256(k_file))
            if ledger.prediction_digest() != before or ledger.verify_integrity().get("valid") is not True:
                raise ValueError("RESULT_CONFLICT_QUARANTINED")
            settled += len(rows)
        except ValueError:
            conflicts += 1
    return {"settledRaces": settled, "pendingDays": pending, "conflicts": conflicts}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one idempotent prospective Stage A controller cycle")
    parser.add_argument("--b-root", type=Path, default=DEFAULT_B_ROOT)
    parser.add_argument("--k-root", type=Path, default=DEFAULT_K_ROOT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    RUNTIME.mkdir(parents=True, exist_ok=True)
    with acquire_lock(RUNTIME / "autopilot.lock"):
        try:
            verify_git_baseline(ROOT, RUNTIME / "baseline_commit.txt")
            approval_path = ROOT / "reports/commercialization_v2/day1/day1_real_anchor_approval_manifest.json"
            verify_approval_hash(approval_path, EXPECTED_APPROVAL_SHA256)
            approval = json.loads(approval_path.read_text(encoding="utf-8"))
            validate_stage_a_approval(approval)
        except ValueError:
            baseline_ok = False
            runner = {"status": "BLOCKED_RUNTIME_GATE", "externalWrites": 0, "prospectiveRaces": 0}
        else:
            baseline_ok = True
        pre_progress = aggregate_ledgers(ROOT / "data/commercialization_v2/day1")
        stage_already_complete = pre_progress["stageAStatus"] in {"PIPELINE_PROVEN", "FAILED_INTEGRITY_GATE"}
        if baseline_ok and stage_already_complete:
            runner = {"status": "STAGE_A_COMPLETE", "externalWrites": 0, "prospectiveRaces": 0}
        elif baseline_ok and args.dry_run:
            runner = {"status": "DRY_RUN", "externalWrites": 0, "prospectiveRaces": 0}
        elif baseline_ok:
            try:
                token = github_token()
            except ValueError:
                token = ""; runner = {"status": "BLOCKED_RUNTIME_CREDENTIAL", "externalWrites": 0, "prospectiveRaces": 0}
            else:
                env = dict(os.environ)
                env.update({"BOATRACE_ANCHOR_GITHUB_TOKEN": token, "BOATRACE_ANCHOR_GITHUB_OWNER": "sasaki202020",
                            "BOATRACE_ANCHOR_GITHUB_REPO": "boatrace-prediction-anchors", "BOATRACE_ANCHOR_GITHUB_API_BASE": "https://api.github.com"})
                result = subprocess.run([sys.executable, str(ROOT / "scripts/run_day1_prospective_v2.py"), "--b-root", str(args.b_root)],
                                        cwd=ROOT, env=env, capture_output=True, text=True, timeout=600)
                runner = redact_process_result(result.returncode, result.stdout, result.stderr, token)
        settlement_blocked = pre_progress["stageAStatus"] == "FAILED_INTEGRITY_GATE"
        settlement = {"settledRaces": 0, "pendingDays": 0, "conflicts": 0} if args.dry_run or not baseline_ok or settlement_blocked else settle_available_results(args.k_root, ROOT / "data/commercialization_v2/day1")
        progress = aggregate_ledgers(ROOT / "data/commercialization_v2/day1")
        if settlement["conflicts"]:
            progress["pipelineIntegrityPassed"] = False
            progress["stageAStatus"] = "FAILED_INTEGRITY_GATE"
        report = {"schemaVersion": 2, "executedAtJst": datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(),
                  "runner": runner, "settlement": settlement, "progress": progress}
        runs = RUNTIME / "runs"; runs.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y%m%dT%H%M%S%f")
        (runs / f"{stamp}.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (RUNTIME / "latest.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        disable_failed = False
        ready_to_stop = progress["stageAStatus"] == "FAILED_INTEGRITY_GATE" or (
            progress["stageAStatus"] == "PIPELINE_PROVEN" and settlement["pendingDays"] == 0
        )
        if ready_to_stop and os.name == "nt":
            disable = subprocess.run(["schtasks", "/Change", "/TN", "BOATRACE-Prospective-Shadow-V2", "/Disable"], capture_output=True)
            disable_failed = disable.returncode != 0
        print(json.dumps({"status": runner["status"], "stageAStatus": progress["stageAStatus"],
                          "verifiedDays": progress["verifiedProspectiveDays"], "verifiedRaces": progress["verifiedProspectiveRaces"]}))
    return 1 if disable_failed else 0


if __name__ == "__main__": raise SystemExit(main())
