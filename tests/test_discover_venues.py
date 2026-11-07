from __future__ import annotations

import json

from src.pipeline import discover_venues


def test_discover_venues_historical_date_from_index(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(discover_venues, "ROOT", tmp_path)
    monkeypatch.setattr(discover_venues, "NORMALIZED_ROOT", tmp_path / "data" / "normalized")
    monkeypatch.setattr(
        discover_venues,
        "_fetch_index_html",
        lambda date8, **kwargs: (
            '<html><a href="/owpc/pc/race/racelist?hd=20260424&jcd=24&rno=1">大村</a></html>',
            "ok",
        ),
    )

    payload = discover_venues.discover_venues_for_date("2026-04-24")
    venues_path = tmp_path / "data" / "normalized" / "20260424" / "venues.json"
    assert venues_path.exists()
    saved = json.loads(venues_path.read_text(encoding="utf-8"))
    assert saved["venues"][0]["discoveryMethod"] == "official_index"
    assert payload["venues"][0]["jcd"] == "24"


def test_discover_venues_existing_raw_and_fallback_warning(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(discover_venues, "ROOT", tmp_path)
    monkeypatch.setattr(discover_venues, "NORMALIZED_ROOT", tmp_path / "data" / "normalized")
    monkeypatch.setattr(discover_venues, "_fetch_index_html", lambda date8, **kwargs: ("", "unavailable"))
    monkeypatch.setattr(discover_venues, "_existing_raw_venues", lambda date8: [{"jcd": "24", "venueName": "大村", "isOpen": True, "sourceUrl": "file://raw", "fetchedAt": "2026-04-24T00:00:00", "discoveryMethod": "existing_raw"}])
    monkeypatch.setattr(discover_venues, "_existing_ui_venues", lambda date8: [])
    monkeypatch.setattr(discover_venues, "_probe_racelist_venues", lambda date8, force=False: [])

    payload = discover_venues.discover_venues_for_date("2026-04-24")
    assert payload["venues"][0]["discoveryMethod"] == "existing_raw"
    assert payload["discoveryMethod"] == "existing_raw"
    assert payload["warnings"] == []

    monkeypatch.setattr(discover_venues, "_existing_raw_venues", lambda date8: [])
    payload2 = discover_venues.discover_venues_for_date("2026-04-23", force=True)
    assert payload2["venues"]
    assert payload2["warnings"]
    assert "fallback_known_venues_used" in payload2["warnings"]
    assert payload2["venues"][0]["discoveryMethod"] == "fallback_known_venues"

