from __future__ import annotations

from datetime import date
from pathlib import Path

from src.pipeline import run_daily_pre_race


ROOT = Path(__file__).resolve().parents[1]


def test_deferred_odds_evaluation_omits_only_odds_dependent_steps() -> None:
    specs = run_daily_pre_race.build_pre_race_step_specs(
        date(2026, 8, 15),
        delay=1.0,
        py_cmd="python",
        odds_fetch_timeout=180,
        defer_odds_evaluation=True,
    )

    labels = [label for label, *_rest in specs]

    assert labels == [
        "fetch_entries",
        "parse_fixed_width",
        "build_features",
        "train_model",
        "train_calibrator",
        "predict_win_proba",
        "generate_trifecta_candidates",
    ]


def test_morning_route_defers_odds_work_to_the_refresh_runner() -> None:
    script = (ROOT / "scripts" / "run_paper_ops_morning.bat").read_text(encoding="utf-8")

    assert "run_daily_pre_race --date !RUN_DATE_ISO! --defer-odds-evaluation" in script
    assert "run_daily_odds_refresh --date !RUN_DATE_ISO! --phase final --refresh --pending-only" in script
    assert 'call "%SCRIPT_DIR%run_odds_refresh.bat"' not in script


def test_morning_route_builds_prediction_sheet_without_nested_batch_call() -> None:
    script = (ROOT / "scripts" / "run_paper_ops_morning.bat").read_text(encoding="utf-8")

    assert "scripts\\build_prediction_sheet.py --date !RUN_DATE_ISO!" in script
    assert 'call "%SCRIPT_DIR%run_prediction_sheet.bat"' not in script
