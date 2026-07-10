from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.pipeline import build_training_base as mod


def test_make_keys_normalizes_race_components() -> None:
    keys = mod._make_keys("2026-04-07", "12", "3", "6")
    assert keys["race_id"] == "20260407-12-03"
    assert keys["race_key"] == "d20260407-c12-r03"
    assert keys["boat_key"] == "20260407-12-03-L06"


def test_validate_duplicates_detects_race_key_lane_collision() -> None:
    frame = pd.DataFrame(
        [
            {"race_key": "d20260407-c12-r03", "lane": 1},
            {"race_key": "d20260407-c12-r03", "lane": 1},
        ]
    )
    assert mod._validate_duplicates(frame, "normalized_entries") == ["normalized_entries:duplicate_race_key_lane"]


def test_live_validation_rejects_result_columns() -> None:
    availability = mod._build_default_feature_availability()
    frame = pd.DataFrame(
        [
            {
                "race_date": "2026-04-07",
                "jcd": 12,
                "race_no": 3,
                "lane": 1,
                "finish_position": 1,
            }
        ]
    )
    issues = mod._validate_feature_availability(frame, availability, live_only=True)
    assert any("result_phase_columns_present" in issue for issue in issues)


def test_load_program_entries_logs_skipped_rows_and_keeps_source_file(tmp_path: Path, caplog) -> None:
    program_csv = tmp_path / "data" / "csv" / "program" / "program.csv"
    program_csv.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "date": "2026-04-07",
                "jcd": 12,
                "race_no": 3,
                "lane": 1,
                "toban": 1111,
                "name": "A",
                "tenji_time": 6.7,
                "start_exhibition_st": 0.12,
                "motor_no": 10,
                "motor_win_rate": 44.0,
                "boat_no": 20,
                "boat_win_rate": 42.0,
                "win_rate_all": 7.2,
                "win_rate_venue": 50.0,
                "avg_st": 0.15,
                "in2_rate": 51.0,
                "in3_rate": 31.0,
                "f_count": 0,
                "l_count": 0,
                "grade": "A1",
                "age": 52,
                "weight": 52.0,
            },
            {
                "date": "2026-04-07",
                "jcd": 12,
                "race_no": 3,
                "lane": None,
                "toban": 2222,
                "name": "B",
                "tenji_time": 6.8,
                "start_exhibition_st": 0.13,
                "motor_no": 11,
                "motor_win_rate": 43.0,
                "boat_no": 21,
                "boat_win_rate": 41.0,
                "win_rate_all": 6.2,
                "win_rate_venue": 40.0,
                "avg_st": 0.16,
                "in2_rate": 49.0,
                "in3_rate": 30.0,
                "f_count": 1,
                "l_count": 0,
                "grade": "B1",
                "age": 51,
                "weight": 53.0,
            },
        ]
    ).to_csv(program_csv, index=False, encoding="utf-8")

    caplog.clear()
    frame = mod._load_program_entries(program_csv)
    assert len(frame) == 1
    assert frame.loc[0, "source_file"] == "data/csv/program/program.csv"
    assert any("missing key fields" in record.message for record in caplog.records)


def test_build_training_base_creates_six_row_training_dataset(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path
    data_dir = root / "data"
    program_csv = data_dir / "csv" / "program" / "program.csv"
    result_csv = data_dir / "csv" / "result" / "result.csv"
    feature_path = data_dir / "metadata" / "feature_availability.csv"

    program_csv.parent.mkdir(parents=True, exist_ok=True)
    result_csv.parent.mkdir(parents=True, exist_ok=True)

    program_rows = []
    result_rows = []
    for lane in range(1, 7):
        program_rows.append(
            {
                "date": "2026-04-07",
                "jcd": 12,
                "race_no": 3,
                "lane": lane,
                "toban": 4000 + lane,
                "name": f"Boat{lane}",
                "tenji_time": 6.7 + lane * 0.01,
                "start_exhibition_st": 0.12 + lane * 0.01,
                "motor_no": 10 + lane,
                "motor_win_rate": 44.0 + lane,
                "boat_no": 20 + lane,
                "boat_win_rate": 42.0 + lane,
                "win_rate_all": 7.2 + lane * 0.1,
                "win_rate_venue": 50.0 + lane,
                "avg_st": 0.15 + lane * 0.01,
                "in2_rate": 51.0 + lane,
                "in3_rate": 31.0 + lane,
                "f_count": 0,
                "l_count": 0,
                "grade": "A1" if lane == 1 else "B1",
                "age": 52 - lane,
                "weight": 52.0 + lane * 0.1,
            }
        )
        result_rows.append(
            {
                "date": "2026-04-07",
                "jcd": 12,
                "race_no": 3,
                "lane": lane,
                "rank": lane,
                "combo": "1-2-3",
                "payout": 1230,
            }
        )

    pd.DataFrame(program_rows).to_csv(program_csv, index=False, encoding="utf-8")
    pd.DataFrame(result_rows).to_csv(result_csv, index=False, encoding="utf-8")

    monkeypatch.setattr(mod, "ROOT", root)
    monkeypatch.setattr(mod, "DEFAULT_PROGRAM_CSV", program_csv)
    monkeypatch.setattr(mod, "DEFAULT_RESULT_CSV", result_csv)
    monkeypatch.setattr(mod, "DEFAULT_FEATURE_AVAILABILITY_PATH", feature_path)
    monkeypatch.setattr(mod, "DEFAULT_OUT_DIR", data_dir)
    monkeypatch.setattr(mod, "DEFAULT_REPORT_DIR", root / "reports" / "data_base")

    summary = mod.build_training_base(
        out_dir=data_dir,
        report_dir=root / "reports" / "data_base",
        feature_availability_path=feature_path,
        date="2026-04-07",
    )

    training_path = data_dir / "processed" / "training_dataset.csv"
    pre_race_path = data_dir / "processed" / "pre_race_features.csv"
    entries_path = data_dir / "processed" / "normalized_entries.csv"
    results_path = data_dir / "processed" / "normalized_results.csv"

    assert training_path.exists()
    assert pre_race_path.exists()
    assert entries_path.exists()
    assert results_path.exists()

    training = pd.read_csv(training_path)
    pre_race = pd.read_csv(pre_race_path)
    entries = pd.read_csv(entries_path)
    results = pd.read_csv(results_path)

    assert len(training) == 6
    assert len(pre_race) == 6
    assert len(entries) == 6
    assert len(results) == 6
    assert training["race_key"].nunique() == 1
    assert sorted(training["lane"].tolist()) == [1, 2, 3, 4, 5, 6]
    assert "finish_position" not in pre_race.columns
    assert summary["output_counts"]["training_race_count"] == 1

