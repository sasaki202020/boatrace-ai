from __future__ import annotations

from pathlib import Path


def test_v6_has_no_prospective_or_production_write_path() -> None:
    root = Path(__file__).resolve().parents[2]
    files = list((root / "src/offline_model_v6").glob("*.py"))
    text = "\n".join(path.read_text(encoding="utf-8") for path in files)
    forbidden = (
        "commercialization_v2",
        "frozen_bets",
        "strict_live",
        "data/live_edge_v1",
        "requests.",
        "urllib",
        "paymentEnabled=true",
    )
    assert all(item not in text for item in forbidden)


def test_v6_runner_does_not_import_prospective_runtime() -> None:
    root = Path(__file__).resolve().parents[2]
    runner = (root / "scripts/run_offline_model_v6.py").read_text(encoding="utf-8")
    assert "run_day1" not in runner
    assert "autopilot" not in runner
    assert "productionAdoptionAllowed" in runner
