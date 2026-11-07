from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATUS_PATH = ROOT / "reports" / "daily" / "active_phase_status.json"
SUMMARY_PATH = ROOT / "reports" / "daily" / "odds_refresh_summary.csv"
PHASES = ["morning", "late", "final"]


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        text = str(value).strip()
        if not text:
            return default
        return float(text)
    except Exception:
        return default


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        text = str(value).strip()
        if not text:
            return default
        return int(float(text))
    except Exception:
        return default


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _read_summary_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows: list[dict[str, Any]] = []
        for row in reader:
            row = dict(row)
            row["date"] = str(row.get("date", "")).strip()
            row["phase"] = str(row.get("phase", "")).strip().lower()
            row["adoption_score"] = _coerce_float(row.get("adoption_score"), 0.0)
            row["real_odds_available"] = _coerce_int(row.get("real_odds_available"), 0)
            row["pending_unpublished"] = _coerce_int(row.get("pending_unpublished"), 0)
            row["real_odds_missing_fetch"] = _coerce_int(row.get("real_odds_missing_fetch"), 0)
            rows.append(row)
        return rows


def _latest_dates(rows: list[dict[str, Any]], limit: int = 3) -> list[str]:
    dates = sorted({row["date"] for row in rows if row.get("date")})
    return dates[-limit:] if dates else []


def _completeness(rows: list[dict[str, Any]], dates: list[str]) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    filtered = [row for row in rows if row.get("date") in dates]
    by_date: dict[str, set[str]] = defaultdict(set)
    for row in filtered:
        phase = str(row.get("phase", "")).strip().lower()
        if phase in PHASES:
            by_date[str(row["date"])].add(phase)

    complete_dates: list[str] = []
    incomplete_dates: list[str] = []
    details: list[dict[str, Any]] = []
    for date_value in dates:
        phases = sorted(by_date.get(date_value, set()), key=lambda value: PHASES.index(value))
        missing = [phase for phase in PHASES if phase not in phases]
        if not missing and len(phases) == len(PHASES):
            complete_dates.append(date_value)
        else:
            incomplete_dates.append(date_value)
        details.append({"date": date_value, "phases": phases, "missing": missing})
    return complete_dates, incomplete_dates, details


def _leaderboard(rows: list[dict[str, Any]], dates: list[str]) -> list[dict[str, Any]]:
    filtered = [row for row in rows if row.get("date") in dates and row.get("phase") in PHASES]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in filtered:
        grouped[str(row["phase"])].append(row)

    leaderboard: list[dict[str, Any]] = []
    for phase in PHASES:
        phase_rows = grouped.get(phase, [])
        if not phase_rows:
            continue
        leaderboard.append(
            {
                "phase": phase,
                "avg_score": round(sum(row["adoption_score"] for row in phase_rows) / len(phase_rows), 3),
                "avg_available": round(sum(row["real_odds_available"] for row in phase_rows) / len(phase_rows), 3),
                "avg_pending": round(sum(row["pending_unpublished"] for row in phase_rows) / len(phase_rows), 3),
                "avg_missing": round(sum(row["real_odds_missing_fetch"] for row in phase_rows) / len(phase_rows), 3),
                "rows": len(phase_rows),
            }
        )

    leaderboard.sort(
        key=lambda row: (
            float(row["avg_score"]),
            float(row["avg_available"]),
            -float(row["avg_pending"]),
            -float(row["avg_missing"]),
        ),
        reverse=True,
    )
    return leaderboard


def _print_table(rows: list[dict[str, Any]], headers: list[str]) -> None:
    widths = {header: len(header) for header in headers}
    for row in rows:
        for header in headers:
            widths[header] = max(widths[header], len(str(row.get(header, ""))))

    def render(values: dict[str, Any]) -> str:
        return "  ".join(str(values.get(header, "")).ljust(widths[header]) for header in headers)

    print(render({header: header for header in headers}))
    for row in rows:
        print(render(row))


def _warnings(status: dict[str, Any], rows: list[dict[str, Any]], leaderboard: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    if status.get("incomplete_dates"):
        warnings.append("incomplete dates detected")

    baseline = status.get("baseline", {})
    current = status.get("current_metrics", {})
    if isinstance(baseline, dict) and isinstance(current, dict):
        baseline_available = _coerce_float(baseline.get("avg_available", 0.0), 0.0)
        baseline_missing = _coerce_float(baseline.get("avg_missing", 0.0), 0.0)
        current_available = _coerce_float(current.get("avg_available", 0.0), 0.0)
        current_missing = _coerce_float(current.get("avg_missing", 0.0), 0.0)
        if baseline_available > 0 and current_available < baseline_available * 0.8:
            warnings.append(f"avg_available drop vs baseline ({current_available} < {baseline_available * 0.8})")
        if baseline_missing >= 0 and current_missing > baseline_missing * 1.5:
            warnings.append(f"avg_missing rise vs baseline ({current_missing} > {baseline_missing * 1.5})")

    mode = str(status.get("mode", "fixed")).strip().lower()
    active_phase = str(status.get("active_phase", "final")).strip().lower()
    locked_until = str(status.get("locked_until", "")).strip()
    if mode == "fixed" and active_phase != "final" and not locked_until and len(leaderboard) == 0:
        warnings.append("active_phase remains final too long")

    if len(rows) < 3:
        warnings.append("insufficient summary data for stable leaderboard")
    return warnings


def main() -> None:
    status = _read_json(STATUS_PATH)
    rows = _read_summary_rows(SUMMARY_PATH)

    print("Daily Ops Dashboard")
    print(f"status_path: reports/daily/active_phase_status.json")
    print(f"summary_path: reports/daily/odds_refresh_summary.csv")
    print()

    if not status:
        print("Status: no active_phase_status.json data available.")
    else:
        fields = [
            "mode",
            "active_phase",
            "candidate_phase",
            "candidate_streak",
            "locked_until",
            "last_reevaluation_date",
            "reason",
        ]
        for field in fields:
            print(f"{field}: {status.get(field, '-')}")

    print()
    if not rows:
        print("No summary data available.")
    else:
        dates = _latest_dates(rows, limit=3)
        if not dates:
            print("No summary data available.")
        else:
            print(f"Recent dates: {', '.join(dates)}")
            print()
            leaderboard = _leaderboard(rows, dates)
            if leaderboard:
                print("Phase leaderboard (last 3 dates)")
                _print_table(leaderboard, ["phase", "avg_score", "avg_available", "avg_pending", "avg_missing", "rows"])
            else:
                print("No summary data available.")

            complete_dates, incomplete_dates, details = _completeness(rows, dates)
            print()
            print("Completeness")
            print(f"complete_dates: {', '.join(complete_dates) if complete_dates else '-'}")
            print(f"incomplete_dates: {', '.join(incomplete_dates) if incomplete_dates else '-'}")
            for item in details:
                missing = ", ".join(item["missing"]) if item["missing"] else "-"
                print(f"{item['date']}: missing={missing}")

            print()
            print("Warnings")
            warnings = _warnings(status, rows, leaderboard)
            if warnings:
                for warning in warnings:
                    print(f"- {warning}")
            else:
                print("- none")


if __name__ == "__main__":
    main()
