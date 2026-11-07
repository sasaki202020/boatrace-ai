from __future__ import annotations

from src.evaluation import tuning_gate as tuning_gate_mod


def test_tuning_gate_blocks_when_live_settled_bets_low(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(tuning_gate_mod, "REPORT_MONITORING_ROOT", tmp_path / "reports" / "monitoring")
    monkeypatch.setattr(
        tuning_gate_mod,
        "live_operation_summary",
        lambda **kwargs: {"summary": {"liveSettledBetCount": 99, "liveSettlementCoverage": 0.8, "resultParseErrorRate": 0.0, "frozenBetsMissingDays": 0, "predictionHashMissingDays": 0, "canTuneWithLiveOnly": False}},
    )

    result = tuning_gate_mod.tuning_gate(start_date="20260425", end_date="20260426")
    summary = result["summary"]

    assert summary["canStartTuning"] is False
    assert "liveSettledBetCount_below_100" in summary["reasons"]


def test_tuning_gate_blocks_when_coverage_low(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(tuning_gate_mod, "REPORT_MONITORING_ROOT", tmp_path / "reports" / "monitoring")
    monkeypatch.setattr(
        tuning_gate_mod,
        "live_operation_summary",
        lambda **kwargs: {"summary": {"liveSettledBetCount": 120, "liveSettlementCoverage": 0.4, "resultParseErrorRate": 0.0, "frozenBetsMissingDays": 0, "predictionHashMissingDays": 0, "canTuneWithLiveOnly": False}},
    )

    result = tuning_gate_mod.tuning_gate(start_date="20260425", end_date="20260426")
    summary = result["summary"]

    assert summary["canStartTuning"] is False
    assert "liveSettlementCoverage_below_0.5" in summary["reasons"]


def test_tuning_gate_allows_when_conditions_met(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(tuning_gate_mod, "REPORT_MONITORING_ROOT", tmp_path / "reports" / "monitoring")
    monkeypatch.setattr(
        tuning_gate_mod,
        "live_operation_summary",
        lambda **kwargs: {"summary": {"liveSettledBetCount": 120, "liveSettlementCoverage": 0.75, "resultParseErrorRate": 0.0, "frozenBetsMissingDays": 0, "predictionHashMissingDays": 0, "canTuneWithLiveOnly": True}},
    )

    result = tuning_gate_mod.tuning_gate(start_date="20260425", end_date="20260426")
    summary = result["summary"]

    assert summary["canStartTuning"] is True
    assert summary["reasons"] == []
    assert summary["nextRequiredAction"] == "start_tuning_review"
