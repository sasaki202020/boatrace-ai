from __future__ import annotations

import json
from shutil import copyfile
from pathlib import Path

from src.ingest import official_k_loader
from src.pipeline import collect_historical_inputs


def _fake_audit(*, start_date: str, end_date: str, jcd: str = "all") -> dict:
    return {
        "summary": {"dateRange": f"{start_date.replace('-', '')}_{end_date.replace('-', '')}", "rows": 3},
        "rows": [
            {"date": "20260420", "jcd": "24", "missingReason": "odds_missing"},
            {"date": "20260420", "jcd": "01", "missingReason": "raw_racelist_missing"},
            {"date": "20260420", "jcd": "02", "missingReason": "result_missing"},
        ],
    }


def _fake_discovery(date8: str, force: bool = False) -> dict:
    return {
        "date": date8,
        "venues": [{"jcd": "24", "venueName": "大村", "isOpen": True}],
        "warnings": [],
    }


def _fake_fetch_day(*, target_date: str, jcd: str, races, stage: str, **kwargs):
    rows = []
    for race_no in races:
        rows.append(
            {
                "date": target_date,
                "jcd": jcd,
                "venue_name": "大村",
                "race_no": race_no,
                "race_id": f"{target_date}-{jcd}-{race_no:02d}",
                "race_title": f"{race_no}R",
                "deadline": "12:00",
                "racelist": {
                    "dataStatus": "available",
                    "missingReason": [],
                    "parsed": {
                        "boats": [{"boat_no": i, "data_status": "available"} for i in range(1, 7)],
                        "dataStatus": "available",
                    },
                    "rawHtmlPath": f"/tmp/{target_date}/{jcd}/racelist_{race_no:02d}.html",
                    "fetchStatus": "live",
                    "url": "https://example.invalid/racelist",
                    "fetchedAt": "2026-04-20T10:00:00",
                },
                "beforeinfo": {"dataStatus": "pending", "missingReason": ["beforeinfo_unavailable"], "parsed": {}, "html": ""},
                "odds3t": {
                    "dataStatus": "available" if stage == "odds" else "pending",
                    "missingReason": [] if stage == "odds" else ["odds3t_unavailable"],
                    "parsed": {"1-2-3": 12.3} if stage == "odds" else {},
                    "parsedOddsCount": 120 if stage == "odds" else 0,
                    "rawHtmlPath": f"/tmp/{target_date}/{jcd}/odds3t_{race_no:02d}.html",
                    "fetchStatus": "live" if stage == "odds" else "pending",
                    "url": "https://example.invalid/odds3t",
                    "fetchedAt": "2026-04-20T10:00:00",
                },
                "result": {
                    "dataStatus": "ok" if stage == "result" else "pending",
                    "missingReason": [] if stage == "result" else ["result_unavailable"],
                    "parsed": {"raceStatus": "ok", "trifectaCombo": "1-2-3", "trifectaPayout": 1230} if stage == "result" else {},
                    "rawHtmlPath": f"/tmp/{target_date}/{jcd}/result_{race_no:02d}.html",
                    "fetchStatus": "live" if stage == "result" else "pending",
                    "url": "https://example.invalid/result",
                    "fetchedAt": "2026-04-20T10:00:00",
                },
                "stage": stage,
            }
        )
    return rows


def test_collect_historical_inputs_collects_only_targeted_stages(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(collect_historical_inputs, "ROOT", tmp_path)
    monkeypatch.setattr(collect_historical_inputs, "NORMALIZED_ROOT", tmp_path / "data" / "normalized")
    monkeypatch.setattr(collect_historical_inputs, "RAW_ROOT", tmp_path / "data" / "raw" / "official")
    monkeypatch.setattr(collect_historical_inputs, "REPORTS_ROOT", tmp_path / "reports" / "backtest")
    monkeypatch.setattr(collect_historical_inputs, "audit_historical_inputs", _fake_audit)
    monkeypatch.setattr(collect_historical_inputs, "discover_venues_for_date", _fake_discovery)
    monkeypatch.setattr(official_k_loader, "RAW_ROOT", tmp_path / "data" / "raw" / "official")
    monkeypatch.setattr(official_k_loader, "NORM_ROOT", tmp_path / "data" / "normalized")
    monkeypatch.setattr(official_k_loader, "REPORT_ROOT", tmp_path / "reports" / "backtest")

    calls: list[tuple[str, str]] = []

    def fake_fetch_day(**kwargs):
        calls.append((kwargs["jcd"], kwargs["stage"]))
        return _fake_fetch_day(**kwargs)

    monkeypatch.setattr(collect_historical_inputs, "fetch_day", fake_fetch_day)

    result = collect_historical_inputs.collect_historical_inputs(start_date="2026-04-20", end_date="2026-04-20", jcd="all", stages="odds")
    assert calls == [("24", "odds")]
    assert result["summary"]["fetchedOddsCount"] > 0
    assert result["summary"]["fetchedRacelistCount"] == 0
    assert result["summary"]["fetchedResultCount"] == 0
    assert result["files"]["summary"].endswith("_collection_summary.json")
    assert result["files"]["details"].endswith("_collection_details.csv")
    normalized = tmp_path / "data" / "normalized" / "20260420" / "24" / "race_1.json"
    assert normalized.exists()


def test_collect_historical_inputs_force_and_fetch_failure_continue(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(collect_historical_inputs, "ROOT", tmp_path)
    monkeypatch.setattr(collect_historical_inputs, "NORMALIZED_ROOT", tmp_path / "data" / "normalized")
    monkeypatch.setattr(collect_historical_inputs, "RAW_ROOT", tmp_path / "data" / "raw" / "official")
    monkeypatch.setattr(collect_historical_inputs, "REPORTS_ROOT", tmp_path / "reports" / "backtest")
    monkeypatch.setattr(collect_historical_inputs, "audit_historical_inputs", _fake_audit)
    monkeypatch.setattr(collect_historical_inputs, "discover_venues_for_date", _fake_discovery)

    raw_dir = tmp_path / "data" / "raw" / "official" / "20260420" / "24"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_file = raw_dir / "odds3t_01.html"
    raw_file.write_text("<html>cached</html>", encoding="utf-8")

    seen: list[str] = []

    def fake_fetch_day(*, target_date: str, jcd: str, races, stage: str, **kwargs):
        seen.append(f"{jcd}:{stage}:{'exists' if raw_file.exists() else 'missing'}")
        if stage == "result":
            raise RuntimeError("boom")
        return _fake_fetch_day(target_date=target_date, jcd=jcd, races=races, stage=stage, **kwargs)

    monkeypatch.setattr(collect_historical_inputs, "fetch_day", fake_fetch_day)
    result = collect_historical_inputs.collect_historical_inputs(start_date="2026-04-20", end_date="2026-04-20", jcd="24", stages="odds,result", force=True)
    assert any(item.startswith("24:odds:missing") for item in seen)
    assert result["summary"]["fetchedOddsCount"] >= 0
    assert result["summary"]["skippedExistingCount"] >= 0
    assert (tmp_path / "reports" / "backtest" / "20260420_20260420_collection_summary.json").exists()
    assert (tmp_path / "reports" / "backtest" / "20260420_20260420_collection_details.csv").exists()


def test_collect_historical_inputs_skips_existing_raw_via_cache(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(collect_historical_inputs, "ROOT", tmp_path)
    monkeypatch.setattr(collect_historical_inputs, "NORMALIZED_ROOT", tmp_path / "data" / "normalized")
    monkeypatch.setattr(collect_historical_inputs, "RAW_ROOT", tmp_path / "data" / "raw" / "official")
    monkeypatch.setattr(collect_historical_inputs, "REPORTS_ROOT", tmp_path / "reports" / "backtest")
    monkeypatch.setattr(collect_historical_inputs, "audit_historical_inputs", _fake_audit)
    monkeypatch.setattr(collect_historical_inputs, "discover_venues_for_date", _fake_discovery)

    raw_dir = tmp_path / "data" / "raw" / "official" / "20260420" / "24"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "odds3t_01.html").write_text("<html>cached odds</html>", encoding="utf-8")

    def fake_fetch_day(**kwargs):
        return [
            {
                "date": kwargs["target_date"],
                "jcd": kwargs["jcd"],
                "venue_name": "大村",
                "race_no": 1,
                "race_id": f"{kwargs['target_date']}-{kwargs['jcd']}-01",
                "race_title": "1R",
                "deadline": "12:00",
                "racelist": {"dataStatus": "available", "missingReason": [], "parsed": {"boats": [{"boat_no": i} for i in range(1, 7)]}, "fetchStatus": "cache", "url": "", "fetchedAt": ""},
                "beforeinfo": {"dataStatus": "pending", "missingReason": ["beforeinfo_unavailable"], "parsed": {}},
                "odds3t": {"dataStatus": "available", "missingReason": [], "parsed": {"1-2-3": 12.3}, "parsedOddsCount": 120, "fetchStatus": "cache", "url": "", "fetchedAt": ""},
                "result": {"dataStatus": "pending", "missingReason": ["result_unavailable"], "parsed": {}},
                "stage": "odds",
            }
        ]

    monkeypatch.setattr(collect_historical_inputs, "fetch_day", fake_fetch_day)
    result = collect_historical_inputs.collect_historical_inputs(start_date="2026-04-20", end_date="2026-04-20", jcd="24", stages="odds")
    assert result["summary"]["skippedExistingCount"] >= 1
    assert result["details"]


def test_collect_historical_inputs_result_txt_loads_k_results(tmp_path, monkeypatch, official_k_file) -> None:
    monkeypatch.setattr(collect_historical_inputs, "ROOT", tmp_path)
    monkeypatch.setattr(collect_historical_inputs, "NORMALIZED_ROOT", tmp_path / "data" / "normalized")
    monkeypatch.setattr(collect_historical_inputs, "RAW_ROOT", tmp_path / "data" / "raw" / "official")
    monkeypatch.setattr(collect_historical_inputs, "REPORTS_ROOT", tmp_path / "reports" / "backtest")
    monkeypatch.setattr(collect_historical_inputs, "audit_historical_inputs", lambda **kwargs: {"summary": {"dateRange": "20260404_20260404", "rows": 0}, "rows": []})
    monkeypatch.setattr(collect_historical_inputs, "discover_venues_for_date", lambda date8, force=False: {"date": date8, "venues": [], "warnings": []})
    monkeypatch.setattr(collect_historical_inputs, "NORMALIZED_ROOT", tmp_path / "data" / "normalized")
    monkeypatch.setattr(official_k_loader, "RAW_ROOT", tmp_path / "data" / "raw" / "official")
    monkeypatch.setattr(official_k_loader, "NORM_ROOT", tmp_path / "data" / "normalized")
    monkeypatch.setattr(official_k_loader, "REPORT_ROOT", tmp_path / "reports" / "backtest")

    dst_dir = tmp_path / "data" / "raw" / "official" / "txt"
    dst_dir.mkdir(parents=True, exist_ok=True)
    copyfile(official_k_file, dst_dir / "K260404.TXT")

    result = collect_historical_inputs.collect_historical_inputs(
        start_date="2026-04-04",
        end_date="2026-04-04",
        jcd="all",
        stages="result_txt",
        input_dir=str(dst_dir),
    )
    summary = result["summary"]
    assert summary["fetchedResultTxtCount"] == 1
    assert summary["parsedResultTxtRaceCount"] > 0
    assert summary["resultTxtOkCount"] > 0
    assert summary["resultTxtParseErrorCount"] == 0
    normalized = tmp_path / "data" / "normalized" / "20260404" / "22" / "race_1.json"
    assert normalized.exists()
    payload = json.loads(normalized.read_text(encoding="utf-8"))
    assert payload["source"]["resultSource"] == "official_txt_k"
    assert payload["dataStatus"]["result"] == "ok"


def test_collect_historical_inputs_result_txt_missing_reports_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(collect_historical_inputs, "ROOT", tmp_path)
    monkeypatch.setattr(collect_historical_inputs, "NORMALIZED_ROOT", tmp_path / "data" / "normalized")
    monkeypatch.setattr(collect_historical_inputs, "RAW_ROOT", tmp_path / "data" / "raw" / "official")
    monkeypatch.setattr(collect_historical_inputs, "REPORTS_ROOT", tmp_path / "reports" / "backtest")
    monkeypatch.setattr(collect_historical_inputs, "audit_historical_inputs", lambda **kwargs: {"summary": {"dateRange": "20260404_20260404", "rows": 0}, "rows": []})
    monkeypatch.setattr(collect_historical_inputs, "discover_venues_for_date", lambda date8, force=False: {"date": date8, "venues": [], "warnings": []})
    monkeypatch.setattr(collect_historical_inputs, "NORMALIZED_ROOT", tmp_path / "data" / "normalized")
    monkeypatch.setattr(official_k_loader, "RAW_ROOT", tmp_path / "data" / "raw" / "official")
    monkeypatch.setattr(official_k_loader, "NORM_ROOT", tmp_path / "data" / "normalized")
    monkeypatch.setattr(official_k_loader, "REPORT_ROOT", tmp_path / "reports" / "backtest")

    empty_input = tmp_path / "data" / "raw" / "official" / "txt"
    empty_input.mkdir(parents=True, exist_ok=True)
    result = collect_historical_inputs.collect_historical_inputs(
        start_date="2026-04-04",
        end_date="2026-04-04",
        jcd="all",
        stages="result_txt",
        input_dir=str(empty_input),
    )
    summary = result["summary"]
    assert summary["fetchedResultTxtCount"] == 0
    assert summary["resultTxtMissingCount"] >= 1
    assert any(str(row.get("errorType")) == "result_txt_missing" for row in result["details"])
