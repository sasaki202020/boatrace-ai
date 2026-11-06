from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def test_prediction_research_has_no_training_or_network_path():
    files=list((ROOT/"src/prediction_research").glob("*.py")); files.append(ROOT/"scripts/build_prediction_research_readiness.py")
    text="\n".join(p.read_text(encoding="utf-8") for p in files if p.exists()).lower()
    for forbidden in ("requests.","urllib","http://","https://",".fit(","predict_proba","data/live_edge_v1","data/commercialization_v2/day1"): assert forbidden not in text

