from __future__ import annotations

import pandas as pd

from src.odds.diagnose_suspicious_odds_cases import (
    SuspiciousCaseConfig,
    _classify_suspicious_reason,
)


def test_classify_suspicious_reason_covers_expected_buckets() -> None:
    config = SuspiciousCaseConfig()

    assert _classify_suspicious_reason(
        pd.Series({"odds": 650.0, "ev": 8.0, "has_real_odds": True, "odds_status": "ok", "odds_fetch_status": "success"}),
        config,
    ) == "odds_out_of_expected_range"
    assert _classify_suspicious_reason(
        pd.Series({"odds": 240.0, "ev": 6.5, "has_real_odds": True, "odds_status": "ok", "odds_fetch_status": "success"}),
        config,
    ) == "odds_inconsistent_with_ev"
    assert _classify_suspicious_reason(
        pd.Series({"odds": None, "ev": 0.0, "has_real_odds": False, "odds_status": "missing", "odds_fetch_status": ""}),
        config,
    ) == "odds_missing_for_combo"
    assert _classify_suspicious_reason(
        pd.Series({"odds": 180.0, "ev": 3.5, "has_real_odds": True, "odds_status": "ok", "odds_fetch_status": "partial_missing", "odds_missing_odds_cells": 4}),
        config,
    ) == "odds_join_partial"
    assert _classify_suspicious_reason(
        pd.Series({"odds": 180.0, "ev": 3.5, "has_real_odds": True, "odds_status": "ok", "odds_fetch_status": "success"}),
        config,
    ) == "other"
