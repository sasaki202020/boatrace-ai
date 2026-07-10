from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
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


def _load_summary_rows(path: Path) -> list[dict[str, Any]]:
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
    if not dates:
        return []
    return dates[-limit:]


def _summarize_date_completeness(rows: list[dict[str, Any]], dates: list[str]) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    filtered = [row for row in rows if row.get("date") in dates]
    by_date: dict[str, set[str]] = defaultdict(set)
    for row in filtered:
        phase = str(row.get("phase", "")).strip().lower()
        if phase in PHASES:
            by_date[str(row["date"])].add(phase)

    compared_dates: list[str] = []
    incomplete_dates: list[str] = []
    checks: list[dict[str, Any]] = []
    for date_value in dates:
        phases = by_date.get(date_value, set())
        missing = [phase for phase in PHASES if phase not in phases]
        is_complete = not missing and len(phases) == len(PHASES)
        if is_complete:
            compared_dates.append(date_value)
        else:
            incomplete_dates.append(date_value)
        checks.append(
            {
                "date": date_value,
                "status": "ok" if is_complete else "missing",
                "missing": missing,
                "phases": sorted(phases, key=lambda value: PHASES.index(value)),
            }
        )
    return compared_dates, incomplete_dates, checks


def _build_phase_leaderboard(rows: list[dict[str, Any]], dates: list[str]) -> list[dict[str, Any]]:
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


def _check_phase_completeness(rows: list[dict[str, Any]], dates: list[str]) -> list[dict[str, Any]]:
    _, _, checks = _summarize_date_completeness(rows, dates)
    return checks


def _print_table(rows: list[dict[str, Any]]) -> None:
    headers = ["phase", "avg_score", "avg_available", "avg_pending", "avg_missing", "rows"]
    widths = {header: len(header) for header in headers}
    for row in rows:
        for header in headers:
            widths[header] = max(widths[header], len(str(row.get(header, ""))))

    def render(values: dict[str, Any]) -> str:
        return "  ".join(str(values.get(header, "")).ljust(widths[header]) for header in headers)

    print(render({header: header for header in headers}))
    for row in rows:
        print(render(row))


def main() -> None:
    rows = _load_summary_rows(SUMMARY_PATH)
    if not rows:
        print("No summary data available.")
        return

    dates = _latest_dates(rows, limit=3)
    if not dates:
        print("No summary data available.")
        return

    print("Summary path: reports/daily/odds_refresh_summary.csv")
    print(f"Recent dates: {', '.join(dates)}")
    print()

    compared_dates, incomplete_dates, checks = _summarize_date_completeness(rows, dates)
    print(f"compared_dates: {', '.join(compared_dates) if compared_dates else '-'}")
    print(f"incomplete_dates: {', '.join(incomplete_dates) if incomplete_dates else '-'}")
    print()

    leaderboard = _build_phase_leaderboard(rows, compared_dates)
    if not leaderboard:
        print("No summary data available.")
    else:
        print("Phase leaderboard (last 3 dates)")
        _print_table(leaderboard)

    print()
    print("Completeness check")
    for item in checks:
        if item["status"] == "ok":
            print(f"{item['date']}: ok ({', '.join(item['phases'])})")
        else:
            missing = ", ".join(item["missing"]) if item["missing"] else "unknown"
            present = ", ".join(item["phases"]) if item["phases"] else "-"
            print(f"{item['date']}: missing={missing} present={present}")


if __name__ == "__main__":
    main()
