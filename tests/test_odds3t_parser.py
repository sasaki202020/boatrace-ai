from __future__ import annotations

import json

from src.ingest.parsers.odds3t_parser import parse_odds3t_document, parse_odds3t_html
from src.pipeline import debug_odds3t


def test_odds3t_parser_extracts_many_combos_from_fixture(odds3t_html) -> None:
    html = odds3t_html
    parsed = parse_odds3t_document(html, "20260419-02-04")
    odds = parse_odds3t_html(html, "20260419-02-04")
    assert parsed["dataStatus"] == "available"
    assert parsed["parsedOddsCount"] >= 100
    assert "1-2-3" in odds
    assert parsed["sampleCombos"]


def test_debug_odds3t_returns_json_on_failure(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(debug_odds3t, "fetch_odds3t_html", lambda **kwargs: {
        "url": "https://example.invalid/odds3t",
        "fetchedAt": "2026-04-24T00:00:00",
        "fetchStatus": "unavailable",
        "dataStatus": "unavailable",
        "missingReason": ["odds_fetch_http_error"],
        "parseWarnings": [],
        "html": "",
        "parsed": {},
        "rawHtmlPath": str(tmp_path / "raw.html"),
        "fallbackUsed": False,
        "errorType": "odds_fetch_http_error",
        "errorMessage": "timeout",
        "containsOddsKeyword": False,
        "parsedOddsCount": 0,
        "sampleCombos": [],
        "tableCount": 0,
        "rawHtmlLength": 0,
    })
    report = debug_odds3t.debug_odds3t(target_date="2026-04-24", jcd="01", rno=1)
    assert report["errorType"] == "odds_fetch_http_error"
    assert report["parsedOddsCount"] == 0
    assert "rawHtmlPath" in report
    json.dumps(report, ensure_ascii=False)
