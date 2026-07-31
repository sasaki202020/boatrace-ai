from __future__ import annotations

from scripts.run_live_feature_capture_v1 import _cumulative_capture_status


def test_cumulative_capture_status_reports_empty_store_without_fake_success(tmp_path):
    status = _cumulative_capture_status(tmp_path / "store")

    assert status["snapshotCount"] == 0
    assert status["researchEligibleSnapshotCount"] == 0
    assert status["featureRecordCount"] == 0
    assert status["requestCount"] == 0
    assert status["integrity"]["valid"] is True
