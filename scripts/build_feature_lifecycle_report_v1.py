from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.feature_forward_v1.lifecycle_audit import build_lifecycle_report


def _code_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    value = result.stdout.strip()
    return value if len(value) == 40 else None


def _worktree_dirty() -> bool | None:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return bool(result.stdout.strip())


def render_markdown(report: dict) -> str:
    hwm = report["hwm"]
    cohort = report["cohort"]
    coverage = report["coverage"]
    pacing = report["pacing"]
    lines = [
        "# Race Lifecycle HWM Report",
        "",
        f"- generatedAtUtc: `{report['generatedAtUtc']}`",
        f"- cohort: `{hwm['cohortStartDate']}` to `{hwm['cohortEndDate']}`",
        f"- snapshotId: `{hwm['snapshotId']}`",
        f"- asOfLedgerId: `{hwm['asOfLedgerId']}`",
        f"- asOfLedgerRecordId: `{hwm.get('asOfLedgerRecordId')}`",
        f"- asOfLedgerRecordHash: `{hwm.get('asOfLedgerRecordHash')}`",
        f"- sourcePolicyHash: `{hwm.get('policyHash')}`",
        f"- policyLoaded: `{hwm.get('policyLoaded')}`",
        f"- policyEnforcedAtRuntime: `{hwm.get('policyEnforcedAtRuntime')}`",
        f"- codeCommit: `{hwm.get('codeCommit')}`",
        f"- configHash: `{hwm.get('configHash')}`",
        f"- worktreeDirty: `{hwm.get('worktreeDirty')}`",
        "",
        "## Counts",
        "",
        "| Metric | Count |",
        "|---|---:|",
        f"| scheduled | {cohort['scheduledRaceCount']} |",
        f"| selected | {cohort['selectedRaceCount']} |",
        f"| response received | {cohort['responseReceivedRaceCount']} |",
        f"| valid capture | {cohort['validCaptureRaceCount']} |",
        f"| rejected capture | {cohort['rejectedCaptureRaceCount']} |",
        f"| capture failure | {cohort['captureFailureRaceCount']} |",
        f"| feature settled | {cohort['featureSettledRaceCount']} |",
        f"| prediction same cohort | {cohort['predictionCountSameCohort']} |",
        f"| prediction outside cohort | {cohort['predictionOutsideCohortCount']} |",
        f"| settlement records | {cohort['predictionSettlementCount']} |",
        "",
        "## Coverage",
        "",
        f"- valid capture / selected: `{coverage['validCaptureAgainstSelectedScope']:.4f}`",
        f"- valid capture / all schedule: `{coverage['validCaptureAgainstAllSchedule']:.4f}`",
        f"- settlement join / valid capture: `{coverage['settlementJoinAgainstValidCapture']:.4f}`",
        "",
        "## Status",
        "",
        "### Capture",
        "",
    ]
    for status, count in sorted(report["captureStatusCounts"].items()):
        lines.append(f"- `{status}`: {count}")
    lines.extend(["", "### Settlement", ""])
    for status, count in sorted(report["settlementStatusCounts"].items()):
        lines.append(f"- `{status}`: {count}")
    lines.extend(
        [
            "",
            "## Pace",
            "",
            f"- observationCalendarDays: `{pacing['observationCalendarDays']}`",
            f"- collectorRunningDays: `{pacing['collectorRunningDays']}`",
            f"- selectedRacesPerCalendarDay: `{pacing['selectedRacesPerCalendarDay']:.4f}`",
            f"- selectedRacesPerRunningDay: `{pacing['selectedRacesPerRunningDay']:.4f}`",
            f"- featureSettledPerCalendarDay: `{pacing['featureSettledPerCalendarDay']:.4f}`",
            f"- featureSettledPerRunningDay: `{pacing['featureSettledPerRunningDay']:.4f}`",
            f"- remainingFeatureSettledRaces: `{pacing['remainingFeatureSettledRaces']}`",
        ]
    )
    for key, scenario in pacing["scenarios"].items():
        lines.append(
            f"- scenario {key}/day: `{scenario['usablePerDay']:.4f}` usable/day, "
            f"`{scenario['estimatedDaysTo1500']}` days to 1500"
        )
    lines.extend(
        [
            "",
            "## Daily Counts",
            "",
            "| Date | Scheduled | Selected | Valid | Rejected | Failure | Settled |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for date, row in report["dailyCounts"].items():
        lines.append(
            f"| {date} | {row['scheduled']} | {row['selected']} | {row['validCapture']} | "
            f"{row['rejectedCapture']} | {row['captureFailure']} | {row['featureSettled']} |"
        )
    lines.extend(
        [
            "",
            "## Consistency",
            "",
            f"- scheduled equation: `{report['consistency']['scheduledEqualsNotSelectedPlusSelected']}`",
            f"- selected equation: `{report['consistency']['selectedEqualsValidPlusFailurePlusRejected']}`",
            f"- valid equation: `{report['consistency']['validEqualsSettledPlusSettlementPendingOrFailure']}`",
            f"- duplicate schedule keys: `{report['consistency']['duplicateScheduleKeys']}`",
            "",
            "## Findings",
            "",
            f"- legacy UNKNOWN preserved: `{report['findings']['legacyUnknownPreserved']}` "
            f"(current count `{report['findings']['legacyUnknownCurrentCount']}`)",
            f"- old report values reused: `{report['findings']['oldReportValuesReused']}`",
            f"- runtime policy status: `{report['findings']['runtimePolicyStatus']}`",
            f"- runtime policy action required: `{report['findings']['runtimePolicyActionRequired']}`",
            "",
            "No model, prediction, production, or scheduled-task writes are performed by this report builder.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--feature-db", type=Path, required=True)
    parser.add_argument("--request-db", type=Path, required=True)
    parser.add_argument("--entries-root", type=Path, required=True)
    parser.add_argument("--prediction-root", type=Path, required=True)
    parser.add_argument("--settlement-root", type=Path, required=True)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument(
        "--runtime-policy-enforced",
        action="store_true",
        help="Mark the report as produced after the active runner gate was verified.",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_lifecycle_report(
        feature_database=args.feature_db,
        request_database=args.request_db,
        entries_root=args.entries_root,
        prediction_root=args.prediction_root,
        settlement_root=args.settlement_root,
        policy_path=args.policy,
        config_path=args.config,
        code_commit=_code_commit(),
        runtime_policy_enforced=args.runtime_policy_enforced,
        worktree_dirty=_worktree_dirty(),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    args.output.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"status": "REPORT_WRITTEN", "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
