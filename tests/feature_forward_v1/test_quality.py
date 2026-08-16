from src.feature_forward_v1.quality import build_quality_report


def test_quality_does_not_infer_schedule_denominator(tmp_path):
    report = build_quality_report(tmp_path / "store")

    for group in report["featureGroups"].values():
        assert group["scheduledRaces"] is None
        assert group["coverage"] is None
        assert group["status"] == "FEATURE_SOURCE_NOT_READY"
        assert group["majorMissingReason"] == "schedule_denominator_unavailable"
