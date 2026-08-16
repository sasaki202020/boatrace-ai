from __future__ import annotations

from src.feature_forward_v1.live_capture import (
    CaptureTarget,
    RequestLedger,
    _select_feasible_venues,
    _venue_limit_for_days,
)

from datetime import datetime


def _targets(jcd: str, *times: str) -> list[CaptureTarget]:
    return [
        CaptureTarget(
            "2026-08-02",
            jcd,
            index,
            datetime.fromisoformat(f"2026-08-02T{time}:00+09:00"),
        )
        for index, time in enumerate(times, start=1)
    ]


def test_collection_scope_expands_only_after_verified_days():
    assert _venue_limit_for_days(0) == 1
    assert _venue_limit_for_days(2) == 1
    assert _venue_limit_for_days(3) == 2
    assert _venue_limit_for_days(6) == 2
    assert _venue_limit_for_days(7) == 5


def test_legacy_single_venue_state_is_preserved_when_scope_expands(tmp_path):
    ledger = RequestLedger(tmp_path / "request_ledger.sqlite3")
    ledger.select_venue("2026-07-31", "10")

    assert ledger.selected_venues("2026-07-31") == ["10"]
    ledger.select_venues("2026-07-31", ["10", "11", "13"])
    assert ledger.selected_venues("2026-07-31") == ["10", "11", "13"]
    ledger.select_venue("2026-08-01", "01")
    ledger.select_venues("2026-08-01", ["02", "03", "04", "05", "06", "07"])
    assert ledger.selected_venues("2026-08-01") == ["01", "02", "03", "04", "05"]


def test_new_scope_avoids_overlapping_capture_windows():
    candidates = {
        "01": _targets("01", "10:00", "10:30"),
        "02": _targets("02", "10:00", "10:30"),
        "03": _targets("03", "10:01", "10:31"),
        "04": _targets("04", "13:00", "13:30"),
    }

    assert _select_feasible_venues(candidates, 5) == ["01", "04"]
