from __future__ import annotations

import json

from scripts.build_market_evaluation_reports_v1 import build_reports


def test_report_builder_is_blocked_without_explicit_local_inputs(tmp_path) -> None:
    result = build_reports(output_dir=tmp_path, snapshots_path=None, ev_path=None)
    assert result["baseline"]["status"] == "BLOCKED_NO_LOCAL_SNAPSHOT_INPUT"
    assert result["ev"]["status"] == "BLOCKED_NO_SETTLED_EVALUATION_INPUT"
    report = json.loads((tmp_path / "market_baseline_report.json").read_text(encoding="utf-8"))
    assert report["productionAdoptionAllowed"] is False
