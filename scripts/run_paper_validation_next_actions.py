from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPORTS_ANALYSIS_ROOT = ROOT / "reports" / "analysis"
REPORTS_MONITORING_ROOT = ROOT / "reports" / "monitoring"
REPORTS_REPO_AUDIT_ROOT = ROOT / "reports" / "repo_audit"
NEXT_DATES_JSON = REPORTS_ANALYSIS_ROOT / "paper_validation_next_dates.json"
SUMMARY_JSON = REPORTS_MONITORING_ROOT / "paper_validation_summary.json"
FINAL_GOAL_JSON = REPORTS_REPO_AUDIT_ROOT / "final_goal_progress.json"
RUNNER_JSON = REPORTS_REPO_AUDIT_ROOT / "paper_validation_next_action_runner_result.json"
RUNNER_MD = REPORTS_REPO_AUDIT_ROOT / "paper_validation_next_action_runner_result.md"


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _save_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _parse_date_text(value: str | None) -> str | None:
    if not value:
        return None
    token = str(value).strip()
    if len(token) == 10 and token[4] == "-" and token[7] == "-":
        return token
    if len(token) == 8 and token.isdigit():
        return f"{token[0:4]}-{token[4:6]}-{token[6:8]}"
    return None


def _day_key(value: str | None) -> str:
    parsed = _parse_date_text(value)
    return parsed or ""


def _tail(text: str, limit: int = 1200) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


def _run_command(command: str) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=str(ROOT),
        shell=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return {
        "command": command,
        "exit_code": completed.returncode,
        "stdout_tail": _tail(completed.stdout or ""),
        "stderr_tail": _tail(completed.stderr or ""),
    }


def _load_summary_rows() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    bundle = _load_json(SUMMARY_JSON)
    rows = bundle.get("rows") if isinstance(bundle.get("rows"), list) else []
    row_map: dict[str, dict[str, Any]] = {}
    for row in rows:
        if isinstance(row, dict) and row.get("date"):
            row_map[str(row["date"])] = row
    return bundle, row_map


def _load_final_goal() -> dict[str, Any]:
    return _load_json(FINAL_GOAL_JSON)


def _load_next_dates() -> dict[str, Any]:
    return _load_json(NEXT_DATES_JSON)


def _build_plan_rows(start_date: str, end_date: str, max_actions: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    payload = _load_next_dates()
    rows = payload.get("topDates") if isinstance(payload.get("topDates"), list) else []
    actionable = {
        "run_preflight",
        "run_morning",
        "run_evening",
        "build_prediction_sheet",
        "build_prediction_review",
        "build_consensus_sheet",
        "import_k_results",
    }
    wait_actions = {"wait_for_publication", "wait_for_results", "no_action_needed"}
    planned: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    seen_dates: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        date_iso = _day_key(str(row.get("date") or ""))
        if not date_iso or date_iso < start_date or date_iso > end_date:
            continue
        if date_iso in seen_dates:
            continue
        seen_dates.add(date_iso)
        action = str(row.get("next_action") or "").strip()
        if action in wait_actions:
            skipped.append(
                {
                    "date": date_iso,
                    "next_action": action,
                    "skip_reason": action,
                    "priorityClass": row.get("priorityClass", ""),
                }
            )
            continue
        if action not in actionable:
            skipped.append(
                {
                    "date": date_iso,
                    "next_action": action,
                    "skip_reason": "unsupported_action",
                    "priorityClass": row.get("priorityClass", ""),
                }
            )
            continue
        planned.append(dict(row))
    planned = planned[:max_actions]
    return planned, skipped


def _get_preflight_class(date_iso: str) -> dict[str, Any]:
    p = ROOT / "reports" / "daily" / date_iso / "preflight_source_check.json"
    payload = _load_json(p)
    classification = str(payload.get("sourceClassification") or "").strip()
    source_ready = bool(payload.get("sourceReady")) or classification == "ready"
    return {
        "sourceClassification": classification,
        "sourceReady": source_ready,
        "path": str(p),
    }


def _collect_day_snapshot(date_iso: str) -> dict[str, Any]:
    summary_bundle, summary_rows = _load_summary_rows()
    row = summary_rows.get(date_iso, {})
    ops = _load_json(ROOT / "reports" / "daily" / date_iso / "daily_paper_ops_check.json")
    preflight = _get_preflight_class(date_iso)
    return {
        "summaryRow": row,
        "ops": ops,
        "preflight": preflight,
        "summaryBundle": summary_bundle,
    }


def _build_command_sequence(action: str, date_iso: str, preflight_ready: bool = False, prediction_sheet_exists: bool = False) -> list[str]:
    if action == "run_preflight":
        return [f"scripts\\run_paper_ops_preflight.bat {date_iso}"]
    if action == "run_morning":
        commands = [f"scripts\\run_paper_ops_preflight.bat {date_iso}"]
        if preflight_ready:
            commands.extend(
                [
                    f"scripts\\run_paper_ops_morning.bat {date_iso}",
                    f"scripts\\run_paper_ops_monitor.bat {date_iso}",
                ]
            )
        return commands
    if action == "run_evening":
        return [
            f"scripts\\run_paper_ops_evening.bat {date_iso}",
            f"scripts\\run_paper_ops_monitor.bat {date_iso}",
        ]
    if action == "build_prediction_sheet":
        return [
            f"scripts\\run_prediction_sheet.bat {date_iso}",
            f"scripts\\run_paper_ops_monitor.bat {date_iso}",
        ]
    if action == "build_prediction_review":
        return [
            f"scripts\\run_prediction_review.bat {date_iso}",
            f"scripts\\run_paper_ops_monitor.bat {date_iso}",
        ]
    if action == "build_consensus_sheet":
        if not prediction_sheet_exists:
            return []
        return [
            f"py scripts\\build_consensus_sheet.py --date {date_iso}",
            f"scripts\\run_paper_ops_monitor.bat {date_iso}",
        ]
    if action == "import_k_results":
        return [
            f"scripts\\check_k_inbox.bat {date_iso}",
            f"scripts\\import_k_results.bat {date_iso}",
            f"scripts\\run_paper_ops_evening.bat {date_iso}",
            f"scripts\\run_paper_ops_monitor.bat {date_iso}",
        ]
    return []


@dataclass
class CommandResult:
    command: str
    exit_code: int
    status: str
    skip_reason: str | None = None
    stdout_tail: str | None = None
    stderr_tail: str | None = None


def _render_md(payload: dict[str, Any]) -> str:
    lines = [
        "# paper_validation_next_action_runner_result",
        "",
        f"- mode: {payload.get('mode', '')}",
        f"- startDate: {payload.get('startDate', '')}",
        f"- endDate: {payload.get('endDate', '')}",
        f"- maxActions: {payload.get('maxActions', 0)}",
        f"- targetDateCount: {payload.get('targetDateCount', 0)}",
        f"- executedDateCount: {payload.get('executedDateCount', 0)}",
        f"- eligibleDeltaTotal: {payload.get('eligibleDeltaTotal', 0)}",
        f"- changedDays: {payload.get('changedDays', 0)}",
        f"- unchangedDays: {payload.get('unchangedDays', 0)}",
        f"- currentPaperEligibleCandidateCount: {payload.get('currentPaperEligibleCandidateCount', 0)}",
        f"- remainingPaperEligibleCandidateCount: {payload.get('remainingPaperEligibleCandidateCount', 0)}",
        f"- paperEligibleDayCount: {payload.get('paperEligibleDayCount', 0)}",
        f"- nextAction: {payload.get('nextAction', '')}",
        "",
        "## 日別処理",
    ]
    for row in payload.get("days", []):
        if not isinstance(row, dict):
            continue
        lines.append(
            f"- {row.get('date', '')} | before={row.get('before_next_action', '')} | after={row.get('after_next_action', '')} | "
            f"status={row.get('status', '')} | eligible_delta={row.get('eligible_delta', 0)} | "
            f"main_reason={row.get('main_reason', '')}"
        )
        for cmd in row.get("commands", []):
            if not isinstance(cmd, dict):
                continue
            lines.append(
                f"  - {cmd.get('status', '')} | exit={cmd.get('exit_code', '')} | {cmd.get('command', '')}"
                + (f" | skip={cmd.get('skip_reason', '')}" if cmd.get("skip_reason") else "")
            )
    lines.extend(
        [
            "",
            "## 分類内訳",
        ]
    )
    for key, value in sorted((payload.get("classificationCounts") or {}).items()):
        lines.append(f"- {key}: {value}")
    lines.extend(
        [
            "",
            "## スキップ",
        ]
    )
    for row in payload.get("skipped", []):
        if not isinstance(row, dict):
            continue
        lines.append(f"- {row.get('date', '')} | {row.get('next_action', '')} | {row.get('skip_reason', '')}")
    lines.extend(
        [
            "",
            "## 不変条件",
            f"- BUY判定未変更: {str(payload.get('buyLogicChanged', False)).lower()}",
            f"- EV計算未変更: {str(payload.get('evLogicChanged', False)).lower()}",
            f"- 予想ロジック未変更: {str(payload.get('predictionLogicChanged', False)).lower()}",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run paper validation next actions safely.")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--max-actions", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    if args.dry_run and args.execute:
        raise SystemExit("--dry-run and --execute are mutually exclusive")
    mode = "dry-run" if args.dry_run or not args.execute else "execute"

    start_date = _day_key(args.start_date)
    end_date = _day_key(args.end_date)
    if not start_date or not end_date or end_date < start_date:
        raise SystemExit("invalid date range")

    before_summary, before_summary_rows = _load_summary_rows()
    before_goal = _load_final_goal()
    planned_rows, skipped_rows = _build_plan_rows(start_date, end_date, max(0, int(args.max_actions)))

    day_results: list[dict[str, Any]] = []
    executed_days: list[str] = []
    for row in planned_rows:
        date_iso = str(row.get("date") or "")
        action = str(row.get("next_action") or "")
        before_row = before_summary_rows.get(date_iso, {})
        before_next_action = action
        day_record: dict[str, Any] = {
            "date": date_iso,
            "before_next_action": before_next_action,
            "after_next_action": before_next_action,
            "eligible_delta": 0,
            "status": "planned" if mode == "dry-run" else "pending",
            "main_reason": str(row.get("blocker") or ""),
            "commands": [],
            "before": {
                "paperCandidateCount": int(before_row.get("paperCandidateCount") or 0),
                "paperEligibleCandidateCount": int(before_row.get("paperEligibleCandidateCount") or 0),
                "paperSettledCandidateCount": int(before_row.get("paperSettledCandidateCount") or 0),
                "watchSettledCount": int(before_row.get("watchSettledCount") or 0),
                "paperSettledCount": int(before_row.get("paperSettledCount") or 0),
                "consensusCount": int(before_row.get("consensusCount") or 0),
                "resultsStatus": str(before_row.get("resultsStatus") or before_row.get("resultStatus") or ""),
            },
        }
        date_commands = _build_command_sequence(
            action,
            date_iso,
            preflight_ready=bool(row.get("preflight_classification") == "ready"),
            prediction_sheet_exists=bool(row.get("has_prediction_sheet")),
        )
        if not date_commands:
            day_record["status"] = "skipped"
            day_record["main_reason"] = str(row.get("blocker") or row.get("next_action") or "non_actionable")
            day_record["commands"].append(
                asdict(
                    CommandResult(
                        command="",
                        exit_code=0,
                        status="skip",
                        skip_reason=str(row.get("next_action") or "non_actionable"),
                    )
                )
            )
            day_results.append(day_record)
            continue

        if mode == "dry-run":
            day_record["commands"] = [
                asdict(
                    CommandResult(
                        command=cmd,
                        exit_code=0,
                        status="planned",
                    )
                )
                for cmd in date_commands
            ]
            day_results.append(day_record)
            continue

        # execute mode
        preflight_ready = False
        for idx, cmd in enumerate(date_commands):
            if action == "run_morning" and idx == 1 and not preflight_ready:
                day_record["commands"].append(
                    asdict(
                        CommandResult(
                            command=cmd,
                            exit_code=0,
                            status="skip",
                            skip_reason="preflight_not_ready",
                        )
                    )
                )
                continue
            if action == "build_consensus_sheet" and idx == 0 and not row.get("has_prediction_sheet"):
                day_record["commands"].append(
                    asdict(
                        CommandResult(
                            command=cmd,
                            exit_code=0,
                            status="skip",
                            skip_reason="prediction_sheet_missing",
                        )
                    )
                )
                continue
            result = _run_command(cmd)
            command_status = "success" if result["exit_code"] == 0 else "failure"
            cmd_record = {
                "command": cmd,
                "exit_code": result["exit_code"],
                "status": command_status,
                "stdout_tail": result["stdout_tail"],
                "stderr_tail": result["stderr_tail"],
            }
            if action == "run_morning" and idx == 0:
                preflight_snapshot = _get_preflight_class(date_iso)
                preflight_ready = bool(preflight_snapshot.get("sourceReady"))
                cmd_record["preflight_classification"] = preflight_snapshot.get("sourceClassification")
                cmd_record["preflight_ready"] = preflight_ready
            day_record["commands"].append(cmd_record)
            if result["exit_code"] != 0:
                day_record["status"] = "failed"
                day_record["main_reason"] = f"exit_{result['exit_code']}"
                break
        else:
            day_record["status"] = "success"
        executed_days.append(date_iso)
        day_results.append(day_record)

    refresh_exit = 0
    refresh_result: dict[str, Any] = {}
    if mode == "execute":
        refresh_cmd = f"scripts\\run_paper_validation_refresh.bat {end_date}"
        refresh_result = _run_command(refresh_cmd)
        refresh_exit = int(refresh_result["exit_code"])

    after_summary, after_summary_rows = _load_summary_rows()
    after_goal = _load_final_goal()
    after_next_dates = _load_next_dates() if mode == "execute" else _load_next_dates()

    for day_record in day_results:
        date_iso = str(day_record.get("date") or "")
        before_row = before_summary_rows.get(date_iso, {})
        after_row = after_summary_rows.get(date_iso, {})
        eligible_before = int(before_row.get("paperEligibleCandidateCount") or 0)
        eligible_after = int(after_row.get("paperEligibleCandidateCount") or 0)
        day_record["after"] = {
            "paperCandidateCount": int(after_row.get("paperCandidateCount") or 0),
            "paperEligibleCandidateCount": eligible_after,
            "paperSettledCandidateCount": int(after_row.get("paperSettledCandidateCount") or 0),
            "watchSettledCount": int(after_row.get("watchSettledCount") or 0),
            "paperSettledCount": int(after_row.get("paperSettledCount") or 0),
            "consensusCount": int(after_row.get("consensusCount") or 0),
            "resultsStatus": str(after_row.get("resultsStatus") or after_row.get("resultStatus") or ""),
        }
        day_record["eligible_delta"] = eligible_after - eligible_before
        if day_record["status"] == "planned":
            day_record["status"] = "skipped" if not mode == "execute" else day_record["status"]
        if mode == "execute" and day_record["status"] == "success" and day_record["eligible_delta"] == 0:
            if str(day_record.get("before_next_action") or "") == "run_morning":
                day_record["main_reason"] = "source_not_ready" if any(
                    (c.get("status") == "failure" and c.get("command", "").endswith("run_paper_ops_morning.bat " + date_iso))
                    for c in day_record.get("commands", [])
                ) else day_record["main_reason"] or "NO_SETTLED_ELIGIBLE"
            elif str(day_record.get("before_next_action") or "") == "run_evening":
                day_record["main_reason"] = "RESULTS_NOT_AVAILABLE" if not bool(after_row.get("paperSettledCandidateCount")) else day_record["main_reason"] or "NO_SETTLED_ELIGIBLE"
            elif str(day_record.get("before_next_action") or "") == "run_preflight":
                day_record["main_reason"] = "SOURCE_NOT_READY"
            else:
                day_record["main_reason"] = day_record["main_reason"] or "OTHER"
        day_record["after_next_action"] = str(after_next_dates.get("summary", {}).get("nextAction") or after_next_dates.get("nextAction") or after_goal.get("nextAction") or "")

    eligible_delta_total = sum(int(row.get("eligible_delta") or 0) for row in day_results)
    changed_days = sum(1 for row in day_results if int(row.get("eligible_delta") or 0) > 0)
    unchanged_days = sum(1 for row in day_results if int(row.get("eligible_delta") or 0) == 0)
    classification_counts: dict[str, int] = {}
    for row in day_results:
        key = str(row.get("classification") or row.get("main_reason") or "OTHER")
        classification_counts[key] = classification_counts.get(key, 0) + 1

    payload = {
        "status": "ok",
        "mode": mode,
        "startDate": start_date,
        "endDate": end_date,
        "maxActions": int(args.max_actions),
        "targetDateCount": len(day_results),
        "executedDateCount": len(executed_days),
        "eligibleDeltaTotal": eligible_delta_total,
        "changedDays": changed_days,
        "unchangedDays": unchanged_days,
        "classificationCounts": classification_counts,
        "days": day_results,
        "skipped": skipped_rows,
        "before": {
            "paperEligibleCandidateCount": int(before_goal.get("paperEligibleCandidateCount") or 0),
            "remainingPaperEligibleCandidateCount": int(before_goal.get("remainingPaperEligibleCandidateCount") or 0),
            "paperEligibleDayCount": int(before_goal.get("paperEligibleDayCount") or 0),
            "primaryBlocker": str(before_goal.get("primaryBlocker") or ""),
            "nextAction": str(before_goal.get("nextAction") or ""),
        },
        "after": {
            "paperEligibleCandidateCount": int(after_goal.get("paperEligibleCandidateCount") or 0),
            "remainingPaperEligibleCandidateCount": int(after_goal.get("remainingPaperEligibleCandidateCount") or 0),
            "paperEligibleDayCount": int(after_goal.get("paperEligibleDayCount") or 0),
            "primaryBlocker": str(after_goal.get("primaryBlocker") or ""),
            "nextAction": str(after_goal.get("nextAction") or after_next_dates.get("summary", {}).get("nextAction") or ""),
        },
        "currentPaperEligibleCandidateCount": int(after_goal.get("paperEligibleCandidateCount") or 0),
        "remainingPaperEligibleCandidateCount": int(after_goal.get("remainingPaperEligibleCandidateCount") or 0),
        "paperEligibleDayCount": int(after_goal.get("paperEligibleDayCount") or 0),
        "nextAction": str(after_goal.get("nextAction") or after_next_dates.get("summary", {}).get("nextAction") or ""),
        "buyLogicChanged": False,
        "evLogicChanged": False,
        "predictionLogicChanged": False,
        "refresh": {
            "command": f"scripts\\run_paper_validation_refresh.bat {end_date}",
            "exit_code": refresh_exit,
            "stdout_tail": refresh_result.get("stdout_tail", ""),
            "stderr_tail": refresh_result.get("stderr_tail", ""),
        },
    }

    _save_json(RUNNER_JSON, payload)
    _save_text(RUNNER_MD, _render_md(payload))
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
