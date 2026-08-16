from __future__ import annotations

import json
import zipfile
from pathlib import Path

from src.pipeline.import_k_results import import_k_results


def test_import_k_results_detects_import_skip_replace_and_invalid_name(tmp_path, monkeypatch, official_k_file) -> None:
    tmp_root = tmp_path / "import"
    inbox = tmp_root / "inbox"
    target = tmp_root / "raw" / "official" / "results"
    inbox.mkdir(parents=True, exist_ok=True)
    target.mkdir(parents=True, exist_ok=True)

    k_bytes = official_k_file.read_bytes()
    (inbox / "K260406.TXT").write_bytes(k_bytes)
    (inbox / "K260407.TXT").write_bytes(k_bytes)
    (inbox / "badname.txt").write_text("bad", encoding="utf-8")

    (target / "K260406.TXT").write_bytes(k_bytes)
    (target / "K260407.TXT").write_bytes(b"different")

    zip_path = inbox / "bundle.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("K260408.TXT", k_bytes)

    monkeypatch.setattr("src.pipeline.import_k_results.REPORT_ROOT", tmp_root / "reports" / "backtest")
    monkeypatch.setattr("src.pipeline.import_k_results.ARCHIVE_ROOT", tmp_root / "_archive")

    result = import_k_results(input_dir=str(inbox), target_dir=str(target))
    summary = result["summary"]
    rows = result["rows"]

    actions = {row["fileName"]: row["action"] for row in rows}
    assert actions["K260406.TXT"] == "skipped_existing_same"
    assert actions["K260407.TXT"] == "replaced_existing_different"
    assert actions["badname.txt"] == "invalid_name"
    assert actions["K260408.TXT"] == "imported"
    assert summary["importedFileCount"] >= 1
    assert summary["skippedFileCount"] >= 1
    assert summary["replacedFileCount"] >= 1
    assert summary["invalidNameFileCount"] >= 1

    archived = list((tmp_root / "_archive").rglob("K260407_*.TXT"))
    assert archived
    assert json.loads((tmp_root / "reports" / "backtest" / "k_result_import_manifest.json").read_text(encoding="utf-8"))["summary"]["importedFileCount"] >= 1
    assert (tmp_root / "reports" / "backtest" / "k_result_import_manifest.csv").exists()

    for row in rows:
        if row["action"] != "invalid_name":
            assert int(row["parsedRaceCount"]) >= 0
            assert "resultTxtOkCount" in row
