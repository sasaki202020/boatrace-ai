"""Run-scoped B/K date contract for the local prospective pipeline."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Iterable
from zoneinfo import ZoneInfo


JST = ZoneInfo("Asia/Tokyo")


def _as_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).replace("/", "-"))


def _file_name(prefix: str, value: date) -> str:
    return f"{prefix}{value:%y%m%d}.TXT"


def _normalized_files(values: Iterable[str]) -> set[str]:
    return {str(value).upper() for value in values}


def _jst_datetime(value: date, *, end_of_day: bool = False) -> datetime:
    return datetime.combine(
        value,
        time(23, 59, 59) if end_of_day else time(0, 0),
        tzinfo=JST,
    )


def resolve_input_contract(
    *,
    run_started_at: datetime,
    available_b_files: Iterable[str],
    available_k_files: Iterable[str],
    settlement_candidate_dates: Iterable[date | datetime | str],
    settled_dates: Iterable[date | datetime | str],
    explicit_business_date: date | datetime | str | None = None,
    official_b_files: Iterable[str] | None = None,
    official_k_files: Iterable[str] | None = None,
    required_due_at_jst: datetime | None = None,
    grace_minutes: int = 30,
) -> dict[str, object]:
    """Resolve required and not-yet-due inputs without looking ahead a day.

    The returned business date is fixed from ``run_started_at`` (or the
    explicit date), so a run cannot silently change its date at midnight.
    Missing future files are represented as ``notDueFiles`` and never make a
    run blocked.
    """
    if run_started_at.tzinfo is None:
        raise ValueError("run_started_at_must_be_timezone_aware")
    started_jst = run_started_at.astimezone(JST)
    business_date = (
        _as_date(explicit_business_date)
        if explicit_business_date is not None
        else started_jst.date()
    )

    available_b = _normalized_files(available_b_files)
    available_k = _normalized_files(available_k_files)
    official_b = _normalized_files(
        available_b if official_b_files is None else official_b_files
    )
    official_k = _normalized_files(
        available_k if official_k_files is None else official_k_files
    )

    settled = {_as_date(value) for value in settled_dates}
    pending_dates = sorted(
        {
            _as_date(value)
            for value in settlement_candidate_dates
            if _as_date(value) < business_date and _as_date(value) not in settled
        }
    )

    required_b = _file_name("B", business_date)
    required_k = [_file_name("K", value) for value in pending_dates]
    optional_prefetch_b = _file_name("B", business_date + timedelta(days=1))
    current_day_k = _file_name("K", business_date)
    not_due = [optional_prefetch_b, current_day_k]

    required_files = [required_b, *required_k]
    missing_required = [
        name
        for name in required_files
        if not (
            name in available_b
            if name.startswith("B")
            else name in available_k
        )
    ]

    due_at = required_due_at_jst.astimezone(JST) if required_due_at_jst else None
    grace_deadline = (
        due_at + timedelta(minutes=grace_minutes) if due_at else None
    )
    if not missing_required:
        input_state = "READY"
        blocked_reason = None
    elif grace_deadline and started_jst >= grace_deadline:
        input_state = "BLOCKED_UPSTREAM_OVERDUE"
        blocked_reason = "missing_required_files_after_grace"
    else:
        input_state = "WAITING_NOT_DUE"
        blocked_reason = None

    all_files = [required_b, *required_k, optional_prefetch_b, current_day_k]
    return {
        "captureBusinessDate": business_date.isoformat(),
        "requiredBFile": required_b,
        "settlementTargetDates": [value.isoformat() for value in pending_dates],
        "requiredKFiles": required_k,
        "optionalPrefetchBFile": optional_prefetch_b,
        "notDueFiles": list(dict.fromkeys(not_due)),
        "officialAvailable": {
            name: name in (official_b if name.startswith("B") else official_k)
            for name in all_files
        },
        "canonicalAvailable": {
            name: name in (available_b if name.startswith("B") else available_k)
            for name in all_files
        },
        "dueAtJst": due_at.isoformat() if due_at else None,
        "graceDeadlineAtJst": grace_deadline.isoformat() if grace_deadline else None,
        "inputState": input_state,
        "blockedReason": blocked_reason,
        "missingRequiredFiles": missing_required,
        "runStartedAtJst": started_jst.isoformat(),
    }
