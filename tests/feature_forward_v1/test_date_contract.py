from datetime import date, datetime
from zoneinfo import ZoneInfo

from src.feature_forward_v1.date_contract import resolve_input_contract
from scripts.run_local_prediction_settlement_v1 import select_business_b_file


JST = ZoneInfo("Asia/Tokyo")


def test_august_4_run_does_not_require_future_files() -> None:
    result = resolve_input_contract(
        run_started_at=datetime(2026, 8, 4, 16, 43, tzinfo=JST),
        available_b_files={"B260804.TXT"},
        available_k_files={"K260803.TXT"},
        settlement_candidate_dates={date(2026, 8, 3)},
        settled_dates=set(),
    )

    assert result["captureBusinessDate"] == "2026-08-04"
    assert result["requiredBFile"] == "B260804.TXT"
    assert result["settlementTargetDates"] == ["2026-08-03"]
    assert result["requiredKFiles"] == ["K260803.TXT"]
    assert result["optionalPrefetchBFile"] == "B260805.TXT"
    assert set(result["notDueFiles"]) == {"B260805.TXT", "K260804.TXT"}
    assert result["inputState"] == "READY"
    assert result["blockedReason"] is None


def test_current_day_result_file_is_not_due() -> None:
    result = resolve_input_contract(
        run_started_at=datetime(2026, 8, 4, 16, 43, tzinfo=JST),
        available_b_files={"B260804.TXT"},
        available_k_files={"K260803.TXT"},
        settlement_candidate_dates={date(2026, 8, 3)},
        settled_dates=set(),
    )

    assert "K260804.TXT" in result["notDueFiles"]
    assert result["officialAvailable"]["K260804.TXT"] is False
    assert result["canonicalAvailable"]["K260804.TXT"] is False


def test_business_date_is_fixed_for_the_whole_run() -> None:
    result = resolve_input_contract(
        run_started_at=datetime(2026, 8, 4, 23, 59, 59, tzinfo=JST),
        available_b_files={"B260804.TXT", "B260805.TXT"},
        available_k_files={"K260803.TXT", "K260804.TXT"},
        settlement_candidate_dates={date(2026, 8, 3), date(2026, 8, 4)},
        settled_dates=set(),
    )

    assert result["captureBusinessDate"] == "2026-08-04"
    assert result["requiredBFile"] == "B260804.TXT"
    assert result["optionalPrefetchBFile"] == "B260805.TXT"
    assert result["requiredKFiles"] == ["K260803.TXT"]
    assert "K260804.TXT" in result["notDueFiles"]


def test_prediction_runner_selects_business_date_not_lexicographic_latest(
    tmp_path,
) -> None:
    (tmp_path / "B260804.TXT").write_text("current", encoding="ascii")
    (tmp_path / "B260805.TXT").write_text("prefetch", encoding="ascii")

    selected = select_business_b_file(tmp_path, date(2026, 8, 4))

    assert selected is not None
    assert selected.name == "B260804.TXT"


def test_prediction_runner_does_not_use_future_b_as_current_input(tmp_path) -> None:
    (tmp_path / "B260805.TXT").write_text("prefetch", encoding="ascii")

    assert select_business_b_file(tmp_path, date(2026, 8, 4)) is None
