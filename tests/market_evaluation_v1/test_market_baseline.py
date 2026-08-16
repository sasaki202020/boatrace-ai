from __future__ import annotations

import pytest

from src.market_evaluation_v1.market_baseline import (
    ALL_TRIFECTA_COMBOS,
    MarketBaselineError,
    calculate_market_probabilities,
)


def _odds_rows() -> list[dict[str, object]]:
    return [{"trifecta": combo, "odds": 2.0} for combo in ALL_TRIFECTA_COMBOS]


def test_market_probabilities_cover_120_combinations_and_sum_to_one() -> None:
    result = calculate_market_probabilities(_odds_rows())
    assert len(result) == 120
    assert sum(row["marketProbability"] for row in result) == pytest.approx(1.0)
    assert all(row["rawImpliedProbability"] == pytest.approx(0.5) for row in result)


def test_incomplete_table_is_fail_closed() -> None:
    with pytest.raises(MarketBaselineError, match="incomplete_trifecta_table"):
        calculate_market_probabilities(_odds_rows()[:-1])


def test_duplicate_or_invalid_odds_are_rejected() -> None:
    rows = _odds_rows()
    rows[1]["trifecta"] = rows[0]["trifecta"]
    with pytest.raises(MarketBaselineError, match="duplicate_trifecta"):
        calculate_market_probabilities(rows)

    rows = _odds_rows()
    rows[0]["odds"] = 0
    with pytest.raises(MarketBaselineError, match="odds_not_positive_finite"):
        calculate_market_probabilities(rows)
