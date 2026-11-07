from __future__ import annotations

from src.core.ids import canonical_race_id as core_canonical_race_id
from src.demo.run_demo_day import canonical_race_id as demo_canonical_race_id
from src.odds.fetch_daily_trifecta_odds import canonical_race_id as odds_canonical_race_id
from src.utils.race_id import canonical_race_id, normalize_race_id


def test_canonical_race_id_formats_consistently() -> None:
    assert canonical_race_id("2026-04-03", "1", "1") == "20260403-01-01"
    assert canonical_race_id("20260403", 22, 8) == "20260403-22-08"
    assert canonical_race_id("2026/04/03", "09", "12") == "20260403-09-12"


def test_normalize_race_id_handles_legacy_delimiters() -> None:
    assert normalize_race_id("20260403_22_08") == "20260403-22-08"
    assert normalize_race_id("d20260403-c22-r08") == "20260403-22-08"
    assert normalize_race_id("20260403-22-08") == "20260403-22-08"


def test_demo_and_odds_import_the_shared_helper() -> None:
    assert demo_canonical_race_id is canonical_race_id
    assert odds_canonical_race_id is canonical_race_id
    assert core_canonical_race_id is canonical_race_id
