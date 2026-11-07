from __future__ import annotations

from src.ingest.parsers.racelist_parser import parse_racelist_html


def test_racelist_parser_missing_on_empty_html() -> None:
    parsed = parse_racelist_html("", target_date="2026-04-24", jcd="24", race_no=1)
    assert parsed["dataStatus"] == "missing"
    assert len(parsed["boats"]) == 6
