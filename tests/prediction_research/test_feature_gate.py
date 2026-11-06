from src.prediction_research.feature_gate import evaluate_feature_candidate, evaluate_research_start_gate

def ready_feature(**overrides):
    value = {"predictionTimestampVerified": True, "resultLeakageCount": 0, "coverage": 0.80, "deterministicParsing": True, "provenanceStored": True, "forwardCollectionDays": 30, "existingPathChanged": False}
    value.update(overrides); return value

def test_feature_candidate_requires_every_gate():
    assert evaluate_feature_candidate(ready_feature())["status"] == "PROSPECTIVE_FEATURE_CANDIDATE"
    for field, value in (("predictionTimestampVerified", False), ("resultLeakageCount", 1), ("coverage", .799), ("deterministicParsing", False), ("provenanceStored", False), ("forwardCollectionDays", 29), ("existingPathChanged", True)):
        result = evaluate_feature_candidate(ready_feature(**{field: value}))
        assert result["status"] == "FEATURE_SOURCE_NOT_READY"
        assert field in result["failedConditions"]

def test_research_start_gate_requires_a_or_b_and_never_auto_trains():
    blocked = evaluate_research_start_gate(settled_races=1499, recurring_error_segment=True, feature_candidates=[{"status":"FEATURE_SOURCE_NOT_READY","forwardCollectionDays":100}])
    assert blocked["researchStartAllowed"] is False and blocked["automaticTrainingAllowed"] is False
    assert evaluate_research_start_gate(settled_races=1500, recurring_error_segment=True, feature_candidates=[])["trigger"] == "A"
    b = evaluate_research_start_gate(settled_races=0, recurring_error_segment=False, feature_candidates=[{"status":"PROSPECTIVE_FEATURE_CANDIDATE","forwardCollectionDays":30}])
    assert b["researchStartAllowed"] is True and b["trigger"] == "B"

