from datetime import datetime, timedelta, timezone

import pytest

from src.feature_forward_v1.source_policy import (
    FetchDecision,
    PersonalResearchPolicy,
    RequestLimiter,
    classify_response,
)


def policy(**overrides):
    values = {
        "usage_mode": "PERSONAL_RESEARCH_ONLY",
        "personal_research_allowed": True,
        "local_storage_allowed": True,
        "local_analysis_allowed": True,
        "personal_model_training_allowed": True,
        "personal_prediction_use_allowed": True,
        "manual_ingest_allowed": True,
        "automated_fetch_allowed": False,
        "network_safety_integrated": True,
        "allowed_https_hosts": ("www.boatrace.jp",),
        "commercial_use_allowed": False,
        "redistribution_allowed": False,
        "public_release_allowed": False,
        "paid_service_allowed": False,
        "minimum_interval_seconds": 60,
        "requests_per_race": 1,
        "requests_per_day": 12,
        "retries_per_race": 0,
    }
    values.update(overrides)
    return PersonalResearchPolicy(**values)


def test_personal_research_is_independent_of_commercial_rights():
    result = policy().evaluate()
    assert result.personal_gate == "ALLOWED_WITH_RESTRICTIONS"
    assert result.automated_fetch_gate == "MANUAL_ONLY"
    assert result.commercial_gate == "PROHIBITED"


@pytest.mark.parametrize(
    "field,value",
    [
        ("commercial_use_allowed", True),
        ("redistribution_allowed", True),
        ("public_release_allowed", True),
        ("paid_service_allowed", True),
        ("minimum_interval_seconds", 59),
        ("requests_per_race", 2),
        ("retries_per_race", 1),
    ],
)
def test_unsafe_policy_fails_closed(field, value):
    with pytest.raises(ValueError):
        policy(**{field: value})


def test_automated_fetch_requires_integrated_persistent_safety_path():
    result = policy(automated_fetch_allowed=True).evaluate()
    assert result.automated_fetch_gate == "READY"
    with pytest.raises(ValueError, match="automated_fetch_safety_incomplete"):
        policy(automated_fetch_allowed=True, network_safety_integrated=False)


def test_request_limiter_enforces_same_url_race_and_daily_limits():
    limiter = RequestLimiter(policy(requests_per_day=2))
    now = datetime(2026, 7, 21, 0, 0, tzinfo=timezone.utc)
    assert limiter.authorize("https://www.boatrace.jp/r1", "race-1", now) is FetchDecision.ALLOW
    limiter.record("https://www.boatrace.jp/r1", "race-1", now)
    assert limiter.authorize("https://www.boatrace.jp/r1", "race-1", now + timedelta(seconds=59)) is FetchDecision.INTERVAL_BLOCKED
    assert limiter.authorize("https://www.boatrace.jp/r1", "race-1", now + timedelta(seconds=60)) is FetchDecision.RACE_BUDGET_EXHAUSTED
    assert limiter.authorize("https://www.boatrace.jp/r2", "race-2", now + timedelta(seconds=60)) is FetchDecision.ALLOW
    limiter.record("https://www.boatrace.jp/r2", "race-2", now + timedelta(seconds=60))
    assert limiter.authorize("https://www.boatrace.jp/r3", "race-3", now + timedelta(seconds=120)) is FetchDecision.DAILY_BUDGET_EXHAUSTED


@pytest.mark.parametrize("url", ["http://www.boatrace.jp/r1", "https://evil.example/r1"])
def test_request_limiter_rejects_non_https_or_unapproved_host(url):
    limiter = RequestLimiter(policy())
    assert limiter.authorize(url, "race-1", datetime.now(timezone.utc)) is FetchDecision.COLLECTION_STOPPED


@pytest.mark.parametrize("status", [403, 429])
def test_access_refusal_stops_collection(status):
    assert classify_response(status, "normal page").stop_collection is True


@pytest.mark.parametrize("status", [300, 301, 302, 307, 308, 399])
def test_redirect_response_stops_collection(status):
    classification = classify_response(status, "normal page")
    assert classification.stop_collection is True
    assert classification.reason == f"HTTP_{status}_REDIRECT"


@pytest.mark.parametrize("body", ["CAPTCHA", "私はロボットではありません", "access denied"])
def test_bot_or_refusal_page_stops_collection(body):
    assert classify_response(200, body).stop_collection is True
