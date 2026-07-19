from __future__ import annotations

import contextlib
import ctypes
import hashlib
import json
import math
import os
import sqlite3
import subprocess
from pathlib import Path
from typing import Any, Iterator

from .ledger import ShadowLedgerV2


def _pid_exists(pid: int) -> bool:
    if os.name == "nt":
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


@contextlib.contextmanager
def acquire_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            pid = int(path.read_text(encoding="utf-8").strip())
            running = _pid_exists(pid)
        except ValueError:
            running = False
        if not running:
            path.unlink(missing_ok=True)
        else:
            raise ValueError("AUTOPILOT_ALREADY_RUNNING")
    try:
        handle = path.open("x", encoding="utf-8")
    except FileExistsError:
        raise ValueError("AUTOPILOT_ALREADY_RUNNING") from None
    try:
        handle.write(f"{os.getpid()}\n"); handle.flush()
        yield
    finally:
        handle.close()
        path.unlink(missing_ok=True)


def build_result_rows(parsed: dict[str, Any], predicted_races: set[str]) -> list[dict[str, Any]]:
    output = []
    for race in parsed.get("races", []):
        date = str(race.get("date", "")).replace("-", "")
        jcd = str(race.get("jcd", "")).zfill(2)
        race_no = race.get("raceNo", race.get("rno"))
        if not date or race_no is None:
            continue
        race_id = f"{date}-{jcd}-{int(race_no):02d}"
        if race_id not in predicted_races or str(race.get("raceStatus", "")).lower() != "ok":
            continue
        boats = list(race.get("boatResults", []))
        boat_numbers = [int(item.get("boat_no", 0)) for item in boats]
        winners = [int(item.get("boat_no", 0)) for item in boats if item.get("finishPosition") == 1]
        if len(boats) != 6 or set(boat_numbers) != set(range(1, 7)) or len(winners) != 1:
            raise ValueError("RESULT_CONFLICT_QUARANTINED")
        output.append({"raceId": race_id, "winningLane": winners[0]})
    return sorted(output, key=lambda item: item["raceId"])


def compute_stage_a(days: int, races: int, integrity: bool) -> str:
    if not integrity:
        return "FAILED_INTEGRITY_GATE"
    return "PIPELINE_PROVEN" if days >= 7 and races >= 300 else "IN_PROGRESS"


def redact_process_result(returncode: int, stdout: str, stderr: str, token: str) -> dict[str, Any]:
    safe_out = stdout.replace(token, "[REDACTED]") if token else stdout
    safe_err = stderr.replace(token, "[REDACTED]") if token else stderr
    try:
        payload = json.loads(safe_out.strip().splitlines()[-1]) if safe_out.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    return {
        "status": str(payload.get("status", "RUNNER_FAILED" if returncode else "UNKNOWN")),
        "returnCode": returncode,
        "externalWrites": int(payload.get("externalWrites", 0)),
        "prospectiveRaces": int(payload.get("prospectiveRaces", 0)),
        "stderrCategory": "NONE" if not safe_err.strip() else "RUNNER_ERROR",
    }


def aggregate_ledgers(day_root: Path) -> dict[str, Any]:
    days = races = matched = 0
    integrity = True
    winners: list[tuple[str, int, list[tuple[int, float]]]] = []
    for ledger_path in sorted(day_root.glob("*/shadow.sqlite3")):
        if ShadowLedgerV2(ledger_path).verify_integrity().get("valid") is not True:
            integrity = False
        connection = sqlite3.connect(f"file:{ledger_path.as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        verified = connection.execute("SELECT COUNT(*) FROM external_anchors WHERE status='EXTERNALLY_COMMITTED'").fetchone()[0]
        if verified:
            days += 1
            races += connection.execute("SELECT COUNT(DISTINCT race_id) FROM prediction_rows").fetchone()[0]
        matched += connection.execute("SELECT COUNT(*) FROM result_rows").fetchone()[0]
        for result in connection.execute("SELECT race_id,winning_lane FROM result_rows"):
            probs = [(int(row[0]), float(row[1])) for row in connection.execute(
                "SELECT lane,predicted_probability FROM prediction_rows WHERE race_id=? ORDER BY lane", (result["race_id"],)
            )]
            winners.append((str(result["race_id"]), int(result["winning_lane"]), probs))
        connection.close()
    logloss = brier = top1 = ece = None
    if winners:
        losses = []; briers = []; correct = []; bins: dict[int, list[tuple[float, int]]] = {}
        for _, winner, probs in winners:
            mapping = dict(probs); pwin = mapping[winner]
            losses.append(-math.log(max(pwin, 1e-15)))
            briers.append(sum((prob - (1.0 if lane == winner else 0.0)) ** 2 for lane, prob in probs))
            top_lane, confidence = max(probs, key=lambda item: (item[1], -item[0]))
            hit = int(top_lane == winner); correct.append(hit)
            bins.setdefault(min(9, int(confidence * 10)), []).append((confidence, hit))
        logloss = sum(losses) / len(losses); brier = sum(briers) / len(briers); top1 = sum(correct) / len(correct)
        ece = sum(len(items) / len(winners) * abs(sum(p for p, _ in items) / len(items) - sum(h for _, h in items) / len(items)) for items in bins.values())
    stage = compute_stage_a(days, races, integrity)
    return {
        "verifiedProspectiveDays": days, "verifiedProspectiveRaces": races,
        "resultMatchedRaces": matched, "pipelineIntegrityPassed": integrity,
        "stageAStatus": stage,
        "performance": {"raceLogLoss": logloss, "multiclassBrier": brier, "top1Accuracy": top1, "ece": ece},
        "roiCalculated": False, "paymentEnabled": False, "profitClaimsAllowed": False,
        "productionAdoptionAllowed": False,
    }


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_git_baseline(repo: Path, baseline_file: Path) -> str:
    if not baseline_file.is_file():
        raise ValueError("BLOCKED_GIT_BASELINE")
    expected = baseline_file.read_text(encoding="utf-8").strip()
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
    unstaged = subprocess.run(["git", "diff", "--quiet"], cwd=repo).returncode
    staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=repo).returncode
    untracked_code = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all", "--", "src", "scripts"],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()
    if head != expected or unstaged or staged or untracked_code:
        raise ValueError("BLOCKED_GIT_BASELINE")
    return head


def validate_stage_a_approval(approval: dict[str, Any]) -> None:
    required = {
        "stageAAutopilotApproved": True, "minimumVerifiedDays": 7,
        "minimumVerifiedRaces": 300, "approvalExpiresAtStageA": True,
        "maximumExternalWrites": 1, "maximumPackages": 1,
        "maximumVenues": 1, "maximumRaces": 12, "maximumRetries": 0,
        "paymentEnabled": False, "profitClaimsAllowed": False,
        "productionAdoptionAllowed": False, "bettingEnabled": False,
    }
    if any(approval.get(key) != value for key, value in required.items()):
        raise ValueError("BLOCKED_STAGE_A_APPROVAL")


def verify_approval_hash(approval_path: Path, expected: str) -> None:
    if not approval_path.is_file():
        raise ValueError("BLOCKED_STAGE_A_APPROVAL")
    if len(expected) != 64 or file_sha256(approval_path) != expected:
        raise ValueError("BLOCKED_STAGE_A_APPROVAL")
