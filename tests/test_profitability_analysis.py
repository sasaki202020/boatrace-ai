from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.profitability_analysis import (
    AnalysisPaths,
    metric_summary,
    recommend_actions,
    run_analysis,
)


def test_metric_summary_uses_odds_return() -> None:
    frame = pd.DataFrame([
        {"pred_rank": 1, "pred_proba": 0.3, "expected_value": 1.2, "win_odds": 2.0, "win": 1},
        {"pred_rank": 1, "pred_proba": 0.2, "expected_value": 0.8, "win_odds": 4.0, "win": 0},
    ])

    summary = metric_summary(frame, "top1_win", min_sample=10)

    assert summary["bets"] == 2
    assert summary["hit_count"] == 1
    assert summary["hit_rate"] == 0.5
    assert summary["roi_pct"] == 100.0
    assert summary["profit_units"] == 0.0
    assert summary["sample_status"] == "insufficient_sample"


def test_recommendation_blocks_without_positive_cross_day_evidence() -> None:
    strategy_summary = [{
        "strategy": "top1_win",
        "bets": 100,
        "roi_pct": 90.0,
        "sample_status": "enough_sample",
    }]
    slices = pd.DataFrame([{
        "strategy": "top1_win",
        "slice_type": "course_id",
        "slice_value": "01",
        "bets": 100,
        "unique_days": 3,
        "roi_pct": 120.0,
    }])

    recommendation = recommend_actions(strategy_summary, slices)

    assert recommendation["decision"] == "paper_trade_only"
    assert recommendation["live_betting_allowed"] is False


def test_run_analysis_writes_shadow_outputs_and_bankroll_guard(tmp_path: Path) -> None:
    daily = tmp_path / "daily"
    for day in ("2026-06-11", "2026-06-12", "2026-06-13"):
        day_dir = daily / day
        day_dir.mkdir(parents=True)
        rows = []
        for race_no in range(1, 41):
            rows.append({
                "race_date": day,
                "course_id": "01",
                "race_id": f"{day}-{race_no}",
                "race_number": race_no,
                "lane": 1,
                "pred_rank": 1,
                "pred_proba": 0.4,
                "expected_value": 1.2,
                "win_odds": 2.0,
                "win": 1 if race_no % 2 == 0 else 0,
                "result_status": "settled",
            })
        pd.DataFrame(rows).to_csv(day_dir / "settlement.csv", index=False)

    payload = run_analysis(
        AnalysisPaths(daily_root=daily, output_dir=tmp_path / "out"),
        min_sample=100,
        min_days=3,
        min_roi_pct=105.0,
        bankroll_yen=10000,
    )

    assert payload["analysis_status"] == "shadow_only"
    assert payload["current"]["days"] == 3
    assert payload["recommendation"]["decision"] == "paper_trade_only"
    assert payload["bankroll_guard"]["unit_stake_yen"] == 0
    assert (tmp_path / "out" / "profitability_summary.json").exists()
    assert (tmp_path / "out" / "profitability_report.md").exists()
    assert (tmp_path / "out" / "profitability_slices.csv").exists()
