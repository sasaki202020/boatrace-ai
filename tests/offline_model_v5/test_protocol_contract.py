from __future__ import annotations

from pathlib import Path

from src.offline_model_v5.experiment import GATED_MAXIMA, STATIC_ALPHAS, gate_training_columns


def test_candidate_grid_is_predeclared_and_bounded() -> None:
    assert STATIC_ALPHAS == (0.02, 0.05, 0.10)
    assert GATED_MAXIMA == (0.05, 0.10, 0.20)
    assert len(STATIC_ALPHAS) + len(GATED_MAXIMA) == 6


def test_gate_training_columns_do_not_include_result() -> None:
    forbidden = {"target", "winner", "finish_position", "result", "payout", "final_odds"}
    assert forbidden.isdisjoint(gate_training_columns())


def test_v5_has_no_production_or_prospective_write_path() -> None:
    root = Path(__file__).resolve().parents[2]
    text = "\n".join(path.read_text(encoding="utf-8") for path in (root / "src/offline_model_v5").glob("*.py"))
    assert "data/live_edge_v1" not in text
    assert "strict_live" not in text
    assert "frozen_bets" not in text
    assert "requests." not in text
    assert "urllib" not in text
