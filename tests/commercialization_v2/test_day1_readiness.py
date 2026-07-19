from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.commercialization_v2.day1_readiness import (
    MODEL_FEATURES,
    audit_only_inference,
    audit_bfile,
    generate_prediction_rows,
    schema_signature,
    validate_runtime_bfile,
)


def _bfile(*, lanes: int = 6, extra: bytes = b"") -> bytes:
    rows = []
    for lane in range(1, lanes + 1):
        row = f"{lane} {4000 + lane:04d}".encode("ascii") + b" " * 65
        rows.append(row)
    return b"\r\n".join([
        b"STARTB",
        b"01BBGN",
        "1R 電話投票締切予定12:34".encode("cp932"),
        *rows,
        extra,
        b"END",
    ])


def test_supported_bfile_schema_is_deterministic(tmp_path: Path) -> None:
    path = tmp_path / "B260720.TXT"
    path.write_bytes(_bfile())
    first = audit_bfile(path, relative_root=tmp_path)
    second = audit_bfile(path, relative_root=tmp_path)
    assert first["parseStatus"] == "SUPPORTED"
    assert first["resultLikeFieldCount"] == 0
    assert first["resultLikeRecordCount"] == 0
    assert first["schemaSignature"] == second["schemaSignature"]
    assert first["raceCount"] == 1 and first["laneRowCount"] == 6

    canonical = validate_runtime_bfile(path)
    assert pd.api.types.is_integer_dtype(canonical["lane"])
    assert canonical["lane"].tolist() == [1, 2, 3, 4, 5, 6]


def test_runtime_guard_rejects_unknown_result_and_incomplete_races(tmp_path: Path) -> None:
    result = tmp_path / "B260720.TXT"
    result.write_bytes(_bfile(extra=b"RESULT CONFIRMED payout winner"))
    with pytest.raises(ValueError, match="result_like_record"):
        validate_runtime_bfile(result)

    incomplete = tmp_path / "B260721.TXT"
    incomplete.write_bytes(_bfile(lanes=5))
    with pytest.raises(ValueError, match="six_boats_required"):
        validate_runtime_bfile(incomplete)


def test_runtime_guard_rejects_unknown_schema_and_duplicate_lane(tmp_path: Path) -> None:
    unknown = tmp_path / "B260720.TXT"
    unknown.write_bytes(b"not-a-supported-b-file")
    with pytest.raises(ValueError, match="unsupported_schema"):
        validate_runtime_bfile(unknown)

    duplicate = tmp_path / "B260721.TXT"
    duplicate.write_bytes(_bfile().replace(b"6 4006", b"5 4006"))
    with pytest.raises(ValueError, match="lane_integrity"):
        validate_runtime_bfile(duplicate)

    unknown_record = tmp_path / "B260722.TXT"
    unknown_record.write_bytes(_bfile(extra=b"UNRECOGNIZED_RECORD"))
    with pytest.raises(ValueError, match="unknown_record_type"):
        validate_runtime_bfile(unknown_record)


def test_signature_uses_content_contract_not_filename_or_mtime(tmp_path: Path) -> None:
    left = tmp_path / "B260720.TXT"
    right = tmp_path / "B991231.TXT"
    left.write_bytes(_bfile())
    right.write_bytes(_bfile())
    assert schema_signature(left.read_bytes()) == schema_signature(right.read_bytes())
    with pytest.raises(ValueError, match="schema_signature_not_allowlisted"):
        validate_runtime_bfile(left, supported_signatures={"0" * 64})


def test_runtime_guard_requires_end_marker(tmp_path: Path) -> None:
    path = tmp_path / "B260720.TXT"
    path.write_bytes(_bfile().removesuffix(b"END"))
    with pytest.raises(ValueError, match="end_marker_missing"):
        validate_runtime_bfile(path)


def test_runtime_guard_rejects_noncanonical_lane_spelling(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "B260720.TXT"
    path.write_bytes(_bfile())
    frame = pd.DataFrame(
        {
            "date": ["2026-07-20"] * 6,
            "jcd": ["01"] * 6,
            "race_no": [1] * 6,
            "lane": ["01", "2", "3", "4", "5", "6"],
            "racer_id": list(range(4001, 4007)),
            "race_id": ["20260720-01-01"] * 6,
        }
    )
    monkeypatch.setattr("src.commercialization_v2.day1_readiness.BoatRaceParser.parse_entries_file", lambda _: frame)

    with pytest.raises(ValueError, match="lane_integrity"):
        validate_runtime_bfile(path)


def test_runtime_guard_normalizes_parser_lane_strings_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "B260720.TXT"
    path.write_bytes(_bfile())
    frame = pd.DataFrame(
        {
            "date": ["2026-07-20"] * 6,
            "jcd": ["01"] * 6,
            "race_no": [1] * 6,
            "lane": ["1", "2", "3", "4", "5", "6"],
            "racer_id": list(range(4001, 4007)),
            "race_id": ["20260720-01-01"] * 6,
        }
    )
    monkeypatch.setattr("src.commercialization_v2.day1_readiness.BoatRaceParser.parse_entries_file", lambda _: frame)

    canonical = validate_runtime_bfile(path)

    assert pd.api.types.is_integer_dtype(canonical["lane"])
    assert canonical["lane"].tolist() == [1, 2, 3, 4, 5, 6]


def test_historical_pre_race_rank_text_is_not_result_leakage(tmp_path: Path) -> None:
    path = tmp_path / "B260720.TXT"
    path.write_bytes(_bfile(extra="全国順位 当地順位 事前ランキング".encode("cp932")))
    assert validate_runtime_bfile(path)["race_id"].nunique() == 1


def test_audit_inference_rejects_model_hash_before_loading(tmp_path: Path) -> None:
    model = tmp_path / "model.joblib"
    model.write_bytes(b"not-a-model")
    with pytest.raises(ValueError, match="model_hash_mismatch"):
        audit_only_inference(None, None, model_path=model, expected_model_sha256="0" * 64)  # type: ignore[arg-type]


def test_prediction_rows_use_canonical_multiindex_identity(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    race_id = "20260720-01-01"
    entries = pd.DataFrame(
        {
            "race_id": [race_id] * 6,
            "lane": list(range(1, 7)),
            "jcd": [1] * 6,
            "race_no": [1] * 6,
            "racer_id": list(range(4001, 4007)),
        }
    )
    features = pd.DataFrame({name: np.ones(6) for name in MODEL_FEATURES})
    features["race_id"] = race_id
    features["lane"] = list(range(1, 7))

    class FakeModel:
        feature_names_in_ = np.asarray(MODEL_FEATURES)

        def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
            assert tuple(frame.columns) == MODEL_FEATURES
            positive = np.arange(1, 7, dtype=float)
            return np.column_stack([1.0 - positive / 10.0, positive / 10.0])

    model_path = tmp_path / "model.joblib"
    model_path.write_bytes(b"fixed-model")
    expected_hash = hashlib.sha256(model_path.read_bytes()).hexdigest()
    monkeypatch.setattr("src.commercialization_v2.day1_readiness.build_frozen_features", lambda *_: features)
    monkeypatch.setattr("src.commercialization_v2.day1_readiness.joblib.load", lambda _: FakeModel())

    rows = generate_prediction_rows(
        entries,
        pd.DataFrame(),
        model_path=model_path,
        expected_model_sha256=expected_hash,
    )

    assert [row["lane"] for row in rows] == [1, 2, 3, 4, 5, 6]
    assert {row["raceId"] for row in rows} == {race_id}
    assert sum(row["predictedProbability"] for row in rows) == pytest.approx(1.0)


def test_prediction_rows_reject_noncanonical_lane_type(tmp_path: Path) -> None:
    entries = pd.DataFrame({"lane": ["1"]})
    model_path = tmp_path / "model.joblib"
    model_path.write_bytes(b"fixed-model")
    expected_hash = hashlib.sha256(model_path.read_bytes()).hexdigest()

    with pytest.raises(ValueError, match="noncanonical_lane_type"):
        generate_prediction_rows(entries, pd.DataFrame(), model_path=model_path, expected_model_sha256=expected_hash)
