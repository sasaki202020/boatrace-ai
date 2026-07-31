from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_refresh_script_is_research_only_and_allowlisted():
    script = (ROOT / "scripts" / "refresh_research_memory_v1.py").read_text(encoding="utf-8")

    assert "RESEARCH_ONLY" in script
    assert '"productionConnected": False' in script
    assert '"prospectiveConnected": False' in script
    assert "data/research/research_memory_v1" in script
    assert "reports" in script
    assert "data/prospective" not in script
    assert "tree_15" in script
