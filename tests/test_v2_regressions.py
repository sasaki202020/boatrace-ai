from __future__ import annotations

from pathlib import Path
import importlib

import pandas as pd

batch_mod = importlib.import_module("src.evaluation.run_batch_evaluation_v2")
day_eval_mod = importlib.import_module("src.evaluation.run_day_evaluation_v2")
from src.evaluation.run_day_evaluation_v2 import evaluate_shadow_day_v2
from src.ingest.v2 import load_historical_tables


def test_historical_ingest_dedupes_duplicate_finish_positions(tmp_path: Path) -> None:
    historical_csv = tmp_path / "historical_races.csv"
    historical_csv.write_text(
        "\n".join(
            [
                "date,jcd,venue,race_no,finish_position,lane,racer_id",
                "2026-04-04,1,TEST,1,1,1,101",
                "2026-04-04,1,TEST,1,1,1,101",
                "2026-04-04,1,TEST,1,2,2,102",
                "2026-04-04,1,TEST,1,3,3,103",
                "2026-04-04,1,TEST,1,4,4,104",
            ]
        ),
        encoding="utf-8",
    )

    races, entries, results = load_historical_tables(historical_csv)

    assert len(races) == 1
    assert len(results) == 1
    assert results.loc[0, "status"] == "available"
    assert results.loc[0, "winning_ticket_id"] == "1-2-3"


def test_target_day_with_compare_possible_has_no_unknown_failure(tmp_path: Path) -> None:
    races = pd.DataFrame(
        {
            "race_id": ["20260403-01-01"],
            "race_key": ["d20260403-c01-r01"],
            "date": ["20260403"],
        }
    )
    entries = pd.DataFrame({"date": ["20260403"], "race_id": ["20260403-01-01"], "race_key": ["d20260403-c01-r01"]})
    results = pd.DataFrame(
        {
            "date": ["20260403"],
            "race_id": ["20260403-01-01"],
            "status": ["available"],
        }
    )
    odds = pd.DataFrame(
        {
            "date": ["20260403"],
            "race_id": ["20260403-01-01"],
            "odds": [12.3],
        }
    )
    raw_candidates = pd.DataFrame({"date": ["2026-04-03"], "race_key": ["d20260403-c01-r01"], "approx_prob": [0.1]})
    calibrated_candidates = pd.DataFrame({"date": ["2026-04-03"], "race_key": ["d20260403-c01-r01"], "calibrated_prob": [0.11]})

    summary, diff, warnings = evaluate_shadow_day_v2(
        date_str="20260403",
        compare_status="TARGET",
        db_path=tmp_path / "shadow.duckdb",
        fallback_tables={
            "races": races,
            "entries": entries,
            "results": results,
            "odds_snapshots": odds,
        },
        raw_candidates=raw_candidates,
        calibrated_candidates=calibrated_candidates,
        v1_compare_path=tmp_path / "missing.json",
    )

    assert summary["compare_possible"] is True
    assert summary["failure_reasons"] == []
    assert diff["failure_reasons"] == []
    assert warnings


def test_target_evaluation_uses_reason_taxonomy_odds_keyword(
    monkeypatch: object,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def fake_build_failure_reasons(**kwargs: object) -> list[str]:
        captured.update(kwargs)
        return []

    monkeypatch.setattr(day_eval_mod, "build_failure_reasons", fake_build_failure_reasons)

    races = pd.DataFrame({"race_id": ["20260403-01-01"], "date": ["20260403"]})
    entries = pd.DataFrame({"race_id": ["20260403-01-01"], "date": ["20260403"]})
    results = pd.DataFrame(
        {"race_id": ["20260403-01-01"], "date": ["20260403"], "status": ["available"]}
    )
    odds = pd.DataFrame(
        {"race_id": ["20260403-01-01"], "date": ["20260403"], "odds": [12.3]}
    )
    candidates = pd.DataFrame(
        {"date": ["2026-04-03"], "race_key": ["d20260403-c01-r01"], "approx_prob": [0.1]}
    )

    day_eval_mod.evaluate_shadow_day_v2(
        date_str="20260403",
        compare_status="TARGET",
        db_path=tmp_path / "shadow.duckdb",
        fallback_tables={
            "races": races,
            "entries": entries,
            "results": results,
            "odds_snapshots": odds,
        },
        raw_candidates=candidates,
        calibrated_candidates=candidates,
        v1_compare_path=tmp_path / "missing.json",
    )

    assert captured["odds_available_races"] == 1
    assert "odds_covered_races" not in captured


def test_batch_success_counts_target_only_and_excludes_hold(monkeypatch: object, tmp_path: Path) -> None:
    selected_days = pd.DataFrame(
        [
            {
                "date": "20260403",
                "status": "TARGET",
                "result_txt_ready": True,
                "raw_incomplete": False,
                "simulator_ok": True,
                "reason": "",
            },
            {
                "date": "20260404",
                "status": "HOLD",
                "result_txt_ready": True,
                "raw_incomplete": False,
                "simulator_ok": True,
                "reason": "",
            },
        ]
    )

    def fake_load_v2_sources(*, historical_path: Path, odds_root: Path):
        return {
            "races": pd.DataFrame(),
            "entries": pd.DataFrame(),
            "results": pd.DataFrame(),
            "odds_snapshots": pd.DataFrame(),
        }, []

    def fake_evaluate_shadow_day_v2(**kwargs):
        summary = {
            "compare_possible": True,
            "v1_compareable": True,
            "target_races": 1,
            "results_ready_count": 1,
            "odds_coverage": 1.0,
            "failure_reasons": [],
            "v1_raw_summary": {"buy_count": 3, "hit_count": 0, "roi": 0.0},
            "v1_calibrated_summary": {"buy_count": 3, "hit_count": 0, "roi": 0.0},
            "raw_calibrated_diff": {"candidate_rows_diff": 0, "avg_prob_diff": 0.0},
        }
        diff = {
            "date": kwargs["date_str"],
            "compare_status": kwargs["compare_status"],
            "reference_only": kwargs["compare_status"] != "TARGET",
            "v1_race_count": 1,
            "v2_race_count": 1,
            "v1_compareable": True,
            "v2_compareable": True,
            "results_ready_count": 1,
            "odds_coverage": 1.0,
            "raw_buy": 3,
            "cal_buy": 3,
            "raw_hit": 0,
            "cal_hit": 0,
            "raw_roi": 0.0,
            "cal_roi": 0.0,
            "difference_summary": {"race_count_diff": 0, "candidate_rows_diff": 0, "avg_prob_diff": 0.0, "roi_diff": 0.0, "hit_diff": 0},
            "failure_reasons": [],
            "v1_judgement": "",
            "v1_raw_summary": {},
            "v1_calibrated_summary": {},
            "raw_calibrated_diff": {},
        }
        return summary, diff, []

    monkeypatch.setattr(batch_mod, "load_v2_sources", fake_load_v2_sources)
    monkeypatch.setattr(batch_mod, "evaluate_shadow_day_v2", fake_evaluate_shadow_day_v2)
    monkeypatch.setattr(batch_mod, "duckdb_available", lambda: False)

    results, failures, summary = batch_mod.run_batch_evaluation_v2(
        selected_days=selected_days,
        db_path=tmp_path / "batch.duckdb",
        historical_path=tmp_path / "historical.csv",
        odds_root=tmp_path / "odds",
        raw_candidates_path=tmp_path / "raw.csv",
        cal_candidates_path=tmp_path / "cal.csv",
        v1_compare_dir=tmp_path / "v1",
        dry_run=False,
    )

    assert len(results) == 2
    assert summary["success_count"] == 1
    assert summary["hold_count"] == 1
    assert summary["aggregate_count"] == 1
    assert all(row.compare_status != "HOLD" or row.status == "HOLD" for row in results)
    assert all(row.reference_only is False for row in results if row.status == "SUCCESS")
    assert failures == []
