from src.feature_forward_v1.course_start_contract import build_course_start_contract_audit


def _row(**values):
    return {
        "featureGroup": "course_and_start_exhibition",
        "captureTimestampVerified": True,
        "secondsBeforeDeadline": 420,
        "values": values,
    }


def test_course_start_contract_records_predeadline_proof():
    result = build_course_start_contract_audit([
        _row(courseEntry=1, startExhibition=0.12, tilt=0, bodyWeight=52),
    ])

    assert result["contractPass"] is True
    assert result["preDeadlineEvidenceCount"] == 1
    assert result["resultLeakageCount"] == 0
    assert result["featureContract"]["startExhibition"]["meaning"].find("実ST") >= 0


def test_course_start_contract_rejects_result_like_values():
    result = build_course_start_contract_audit([
        _row(courseEntry=1, winnerBoat=1, startExhibition=0.12),
    ])

    assert result["contractPass"] is False
    assert result["resultLeakageCount"] == 1
