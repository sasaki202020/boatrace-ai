from datetime import datetime, timezone
from pathlib import Path

from src.feature_forward_v1.live_capture import (
    CaptureTarget, _race_identity_matches, build_envelope,
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
