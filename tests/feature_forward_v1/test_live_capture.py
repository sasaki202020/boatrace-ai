from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.feature_forward_v1 import live_capture
from src.feature_forward_v1.live_capture import (
    CaptureTarget, RequestLedger, _race_identity_matches, build_envelope,
    run_capture_cycle,
)


def test_build_envelope_uses_three_forward_only_groups():
    html = (
        Path(__file__).parents[1]
        / "fixtures/real_pages/20260610_01_1/beforeinfo.html"
    ).read_text(encoding="utf-8")
    target = CaptureTarget(
        "2026-06-10", "01", 1,
        datetime.fromisoformat("2026-06-10T12:00:00+09:00"),
    )
    envelope = build_envelope(
        target, html, datetime.fromisoformat("2026-06-10T02:53:00+00:00")
    )
    assert len(envelope["boats"]) == 6
    assert set(envelope["boats"][0]["groups"]) == {
        "course_and_start_exhibition", "exhibition_time", "weather_and_water",
    }
    assert envelope["boats"][0]["groups"]["exhibition_time"]["exhibitionTime"] == 6.78
    assert "result" not in str(envelope).lower()
    assert _race_identity_matches(html, target) is True
    wrong = CaptureTarget(
        "2026-06-10", "02", 1,
        datetime.fromisoformat("2026-06-10T12:00:00+09:00"),
    )
    assert _race_identity_matches(html, wrong) is False


def _due_target(now: datetime) -> CaptureTarget:
    now_jst = now.astimezone(live_capture.JST)
    return CaptureTarget(
        now_jst.date().isoformat(),
        "01",
        1,
        now_jst + timedelta(seconds=420),
    )


def test_persisted_jst_daily_budget_blocks_thirteenth_request_before_http(
    tmp_path, monkeypatch
):
    now = datetime.fromisoformat("2026-08-16T06:00:00+00:00")
    target = _due_target(now)
    store = tmp_path / "store"
    ledger = RequestLedger(store / "request_ledger.sqlite3")
    ledger.select_venues(target.race_date, [target.jcd])
    for race_no in range(1, 13):
        ledger.append(
            target=CaptureTarget(
                target.race_date,
                "02",
                race_no,
                target.deadline_jst,
            ),
            requested_at_utc=now - timedelta(minutes=20 - race_no),
            status_code=200,
            response_sha256="a" * 64,
            outcome="HTTP_OK",
        )
    ledger.connection.close()

    monkeypatch.setattr(live_capture, "targets_from_bfile", lambda path, at: [target])
    http_calls = []
    monkeypatch.setattr(
        live_capture.requests,
        "get",
        lambda *args, **kwargs: http_calls.append((args, kwargs)),
    )

    result = run_capture_cycle(
        b_file=tmp_path / "unused-b-file.txt",
        store_root=store,
        now=now,
        requests_per_day=12,
    )

    assert result == {"status": "DAILY_BUDGET_EXHAUSTED", "networkRequests": 0}
    assert http_calls == []


def test_cross_host_redirect_stops_without_follow_up_http(tmp_path, monkeypatch):
    now = datetime.fromisoformat("2026-08-16T06:00:00+00:00")
    target = _due_target(now)
    store = tmp_path / "store"
    ledger = RequestLedger(store / "request_ledger.sqlite3")
    ledger.select_venues(target.race_date, [target.jcd])
    ledger.connection.close()

    class RedirectResponse:
        status_code = 302
        content = b""
        url = target.url
        headers = {"Location": "https://redirect.example/next"}

    calls = []

    def fake_get(*args, **kwargs):
        calls.append((args, kwargs))
        return RedirectResponse()

    monkeypatch.setattr(live_capture, "targets_from_bfile", lambda path, at: [target])
    monkeypatch.setattr(live_capture.requests, "get", fake_get)

    result = run_capture_cycle(
        b_file=tmp_path / "unused-b-file.txt",
        store_root=store,
        now=now,
        requests_per_day=12,
    )

    assert result == {
        "status": "FEATURE_COLLECTION_STOPPED",
        "networkRequests": 1,
        "reason": "HTTP_302_REDIRECT",
    }
    assert len(calls) == 1
    assert calls[0][1]["allow_redirects"] is False
