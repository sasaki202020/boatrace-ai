from __future__ import annotations

import zipfile
from pathlib import Path

from src.pipeline.check_k_inbox import check_k_inbox


def test_check_k_inbox_handles_empty_dir(tmp_path, monkeypatch) -> None:
    tmp_root = tmp_path / "check_empty"
    inbox = tmp_root / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    target = tmp_root / "raw" / "official" / "results"
    target.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("src.pipeline.check_k_inbox.REPORT_ROOT", tmp_root / "reports" / "backtest")
    monkeypatch.setattr("src.pipeline.check_k_inbox.DEFAULT_TARGET_DIR", target)
    monkeypatch.setattr(
        "src.pipeline.check_k_inbox.export_missing_k_checklist",
        lambda **kwargs: {"summary": {"missingDates": ["20260406"]}, "rows": [{"expectedFileName": "K260406.TXT"}]},
    )

    result = check_k_inbox(input_dir=str(inbox), start_date="20260401", end_date="20260425")
    summary = result["summary"]

    assert summary["inputDirExists"] is True
    assert summary["totalEntries"] == 0
    assert summary["kFileCount"] == 0
    assert summary["recommendedNextAction"] == "place_missing_k_files_in_inbox"


def test_check_k_inbox_detects_txt_zip_invalid_and_existing(tmp_path, monkeypatch, official_k_file) -> None:
    tmp_root = tmp_path / "check_mixed"
    inbox = tmp_root / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    target = tmp_root / "raw" / "official" / "results"
    target.mkdir(parents=True, exist_ok=True)

    (inbox / "K260406.TXT").write_bytes(official_k_file.read_bytes())
    (inbox / "badname.txt").write_text("bad", encoding="utf-8")
    with zipfile.ZipFile(inbox / "bundle.zip", "w") as zf:
        zf.writestr("K260407.TXT", official_k_file.read_bytes())
    (target / "K260406.TXT").write_bytes(official_k_file.read_bytes())

    monkeypatch.setattr("src.pipeline.check_k_inbox.REPORT_ROOT", tmp_root / "reports" / "backtest")
    monkeypatch.setattr("src.pipeline.check_k_inbox.DEFAULT_TARGET_DIR", target)
    monkeypatch.setattr(
        "src.pipeline.check_k_inbox.export_missing_k_checklist",
        lambda **kwargs: {"summary": {"missingDates": ["20260406", "20260407"]}, "rows": [{"expectedFileName": "K260406.TXT"}, {"expectedFileName": "K260407.TXT"}]},
    )

    result = check_k_inbox(input_dir=str(inbox), start_date="20260401", end_date="20260425")
    summary = result["summary"]
    rows = result["rows"]
    actions = {row["fileName"]: row["action"] for row in rows}
    by_name = {row["fileName"]: row for row in rows}

    assert summary["txtFileCount"] >= 2
    assert summary["zipFileCount"] == 1
    assert summary["kFileCount"] == 2
    assert summary["importTargetCount"] >= 1
    assert summary["skipTargetCount"] >= 1
    assert summary["invalidTargetCount"] >= 1
    assert actions["K260406.TXT"] == "skip_candidate"
    assert actions["badname.txt"] == "invalid"
    assert actions["K260407.TXT"] == "import_candidate"
    assert by_name["K260406.TXT"]["existsInMissingList"] is True
    assert by_name["K260406.TXT"]["isValidKFileName"] is True
    assert by_name["badname.txt"]["isValidKFileName"] is False
    assert by_name["K260407.TXT"]["existsInMissingList"] is True
