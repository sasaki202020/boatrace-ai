from __future__ import annotations

from pathlib import Path

from src.evaluation.paper_validation_gate import paper_validation_gate
from src.evaluation.paper_validation_summary import paper_validation_summary


ROOT = Path(__file__).resolve().parents[1]


def test_paper_validation_summary_split() -> None:
    payload = paper_validation_summary(start_date="20260425", end_date="20260507")
    summary = payload["summary"]
    assert summary["liveSettledBetCount"] == 0
    assert summary["liveRevenueGateStatus"] == "NOT_READY"
    assert summary["paperValidationGateStatus"] == "RUNNING"
    assert summary["paperSettledCandidateCount"] >= summary["watchSettledCount"] + summary["paperSettledCount"]


def test_paper_validation_gate_split() -> None:
    payload = paper_validation_gate(start_date="20260425", end_date="20260507")
    assert payload["liveRevenueGateStatus"] == "NOT_READY"
    assert payload["paperValidationGateStatus"] == "RUNNING"
    assert payload["primaryBlocker"] == "current_active_buy_sample_zero"


def test_predictions_banner_mentions_split() -> None:
    html = (ROOT / "src" / "web" / "static" / "predictions.html").read_text(encoding="utf-8")
    assert "本番BUYのlive検証は未開始です" in html
    assert "WATCH/PAPER/合意スコア候補の紙上検証を蓄積中です" in html
