from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.data.audit_win_training_data import audit_training_data, write_audit_outputs


def _make_clean_training_frame() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for date_idx, date in enumerate(["2026-04-01", "2026-04-02", "2026-04-03"], start=1):
        race_id = f"{date.replace('-', '')}_01"
        for boat_no in range(1, 7):
            rows.append(
                {
                    "race_id": race_id,
                    "date": date,
                    "venue": "TEST",
                    "race_number": 1,
                    "boat_no": boat_no,
                    "finish_position": boat_no,
                    "national_2ren_rate": 0.10 * boat_no + 0.01 * date_idx,
                    "local_2ren_rate": 0.08 * boat_no + 0.01 * date_idx,
                    "avg_st": 0.10 + 0.01 * boat_no,
                }
            )
    return pd.DataFrame(rows)


def _make_bad_training_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "race_id": "20260404_01",
                "date": "2026-04-04",
                "venue": "TEST",
                "race_number": 1,
                "boat_no": 1,
                "finish_position": 2,
                "national_2ren_rate": None,
                "local_2ren_rate": None,
                "avg_st": 0.15,
            },
            {
                "race_id": "20260404_01",
                "date": "2026-04-04",
                "venue": "TEST",
                "race_number": 1,
                "boat_no": 1,
                "finish_position": 2,
                "national_2ren_rate": None,
                "local_2ren_rate": None,
                "avg_st": 0.15,
            },
            {
                "race_id": "20260404_01",
                "date": "2026-04-04",
                "venue": "TEST",
                "race_number": 1,
                "boat_no": 2,
                "finish_position": 3,
                "national_2ren_rate": None,
                "local_2ren_rate": None,
                "avg_st": 0.18,
            },
            {
                "race_id": "20260404_01",
                "date": "2026-04-04",
                "venue": "TEST",
                "race_number": 1,
                "boat_no": 3,
                "finish_position": 4,
                "national_2ren_rate": None,
                "local_2ren_rate": None,
                "avg_st": 0.21,
            },
            {
                "race_id": "20260404_01",
                "date": "2026-04-04",
                "venue": "TEST",
                "race_number": 1,
                "boat_no": 4,
                "finish_position": 5,
                "national_2ren_rate": None,
                "local_2ren_rate": None,
                "avg_st": 0.24,
            },
            {
                "race_id": "20260404_01",
                "date": "2026-04-04",
                "venue": "TEST",
                "race_number": 1,
                "boat_no": 5,
                "finish_position": 6,
                "national_2ren_rate": None,
                "local_2ren_rate": None,
                "avg_st": 0.27,
            },
        ]
    )


def test_audit_win_training_data_accepts_clean_six_boat_frame(tmp_path: Path) -> None:
    df = _make_clean_training_frame()

    summary, issues = audit_training_data(df)
    json_path, csv_path = write_audit_outputs(summary, issues, output_dir=tmp_path)

    assert summary.can_train is True
    assert summary.required_columns_missing == []
    assert summary.non_six_boat_race_count == 0
    assert summary.target_win_invalid_race_count == 0
    assert summary.time_series_split_possible is True
    assert all(rate == 0.0 for rate in summary.missing_rate_by_feature.values())
    assert issues.empty
    assert json_path.exists()
    assert csv_path.exists()


def test_audit_win_training_data_flags_bad_frame(tmp_path: Path) -> None:
    df = _make_bad_training_frame()

    summary, issues = audit_training_data(df)
    write_audit_outputs(summary, issues, output_dir=tmp_path)

    assert summary.can_train is False
    assert summary.invalid_finish_position_count == 0
    assert summary.invalid_boat_no_count == 0
    assert summary.non_six_boat_race_count == 1
    assert summary.target_win_invalid_race_count == 1
    assert summary.missing_rate_by_feature["national_2ren_rate"] == 1.0
    assert summary.missing_rate_by_feature["local_2ren_rate"] == 1.0
    assert any(row.issue_type == "duplicate_race_boat_row" for row in issues.itertuples())
    assert any(row.issue_type == "non_six_boat_race" for row in issues.itertuples())
    assert any(row.issue_type == "target_win_invalid_race" for row in issues.itertuples())
    assert any(row.issue_type == "feature_missing_rate" for row in issues.itertuples())


def test_audit_win_training_data_flags_null_race_id_and_invalid_date() -> None:
    df = pd.DataFrame(
        [
            {
                "race_id": None,
                "date": "not-a-date",
                "venue": "TEST",
                "race_number": 1,
                "boat_no": 1,
                "finish_position": 1,
                "national_2ren_rate": 0.5,
                "local_2ren_rate": 0.4,
                "avg_st": 0.12,
            },
            {
                "race_id": "20260405_01",
                "date": "2026-04-05",
                "venue": "TEST",
                "race_number": 1,
                "boat_no": 2,
                "finish_position": 2,
                "national_2ren_rate": 0.4,
                "local_2ren_rate": 0.3,
                "avg_st": 0.13,
            },
        ]
    )

    summary, issues = audit_training_data(df)

    assert summary.can_train is False
    assert summary.null_race_id_count == 1
    assert summary.invalid_date_count == 1
    assert any(row.issue_type == "null_race_id_row" for row in issues.itertuples())
    assert any(row.issue_type == "invalid_date_row" for row in issues.itertuples())
