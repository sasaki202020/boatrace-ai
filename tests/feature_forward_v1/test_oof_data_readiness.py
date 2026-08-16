from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.feature_forward_v1.collector import CollectorConfig, FeatureCollector
from src.feature_forward_v1.lifecycle_ledger import LifecycleLedger
from src.feature_forward_v1.manual_ingest_preflight import preflight_manual_inbox
from src.feature_forward_v1.oof_data_readiness import (
    build_oof_data_readiness,
    render_oof_data_readiness_markdown,
)
from scripts.build_oof_data_readiness_v1 import verify_lifecycle_ledger_read_only


ROOT = Path(__file__).resolve().parents[2]
SPEC = json.loads(
    (ROOT / "config" / "feature_forward_v1" / "oof_evaluation_spec.json").read_text(
        encoding="utf-8"
    )
)
JST = timezone(timedelta(hours=9))


def _approval() -> dict:
    return {
        "manualIngestAllowed": True,
        "automatedNetworkFetchAllowed": False,
        "automatedCollectionAllowed": False,
        "allowedSourceTypes": ["LOCAL_PERSONAL_SNAPSHOT"],
        "allowedSourceLocationPrefixes": ["file:///personal-inbox"],
    }


def _manual_payload(now: datetime) -> dict:
    deadline = now.astimezone(JST) + timedelta(minutes=10)
    return {
        "schemaVersion": 2,
        "sourceType": "LOCAL_PERSONAL_SNAPSHOT",
        "sourceLocation": "file:///personal-inbox/capture.json",
        "fetchedAtUtc": now.isoformat(),
        "fetchedAtJst": now.astimezone(JST).isoformat(),
        "raceDeadlineJst": deadline.isoformat(),
        "clockDriftSeconds": 0.0,
        "raceDate": deadline.date().isoformat(),
        "jcd": "01",
        "raceNo": 1,
        "boats": [
            {
                "boatNo": boat_no,
                "groups": {
                    "course_and_start_exhibition": {
                        "courseEntry": boat_no,
                        "startExhibition": 0.10,
                        "tilt": 0.0,
                        "bodyWeight": 50.0,
                    },
                    "exhibition_time": {"exhibitionTime": 6.70},
                    "weather_and_water": {
                        "weather": "晴",
                        "airTemp": 30.0,
                        "waterTemp": 27.0,
                        "windDirection": "北",
                        "windSpeed": 2.0,
                        "waveHeight": 2.0,
                    },
                },
            }
            for boat_no in range(1, 7)
        ],
    }


def _write_manual_input(
    inbox: Path, payload: dict, *, digest: str | None = None, name: str = "capture.json"
) -> Path:
    inbox.mkdir(parents=True, exist_ok=True)
    path = inbox / name
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    path.write_bytes(raw)
    (path.with_name(path.name + ".sha256")).write_text(
        (digest or hashlib.sha256(raw).hexdigest()) + "\n", encoding="ascii"
    )
    return path


def _append_lifecycle_event(store_root: Path) -> Path:
    database = store_root / "race_lifecycle.sqlite3"
    ledger = LifecycleLedger(database)
    ledger.append_event(
        snapshot_id="snapshot-1",
        target_date="2026-08-16",
        venue="01",
        race_no=1,
        stage="CAPTURE",
        status_code="VALID_CAPTURE",
        occurred_at_utc="2026-08-16T03:00:00+00:00",
        collector_run_id="collector-1",
        task_run_id="task-1",
        attempt_no=0,
        source_policy_hash="a" * 64,
        config_hash="b" * 64,
        code_commit="c" * 40,
        reason_detail="captured",
        evidence_ref="feature:snapshot-1",
    )
    ledger.close()
    return database


def _folds(count: int) -> list[dict]:
    return [
        {"fold": number, "validationRaceCount": count, "validationDateCount": 5}
        for number in range(1, 6)
    ]


def _current(**changes: object) -> dict:
    current = {
        "forwardCollectionDays": 30,
        "validCaptureCount": 500,
        "featureSettledRaceCount": 500,
        "matureCaptureCoverage": 0.80,
        "totalEligibleRaceCount": 450,
        "initialTrainRaceCount": 75,
        "validationRaceCount": 375,
        "oofDateCount": 25,
        "oofRaceCount": 375,
        "newUnknownCount": 0,
        "terminalConflictCount": 0,
        "leakageCount": 0,
        "hashChainValid": True,
        "productionRelevantFailureCount": 0,
    }
    current.update(changes)
    return current


def test_empty_manual_inbox_is_a_normal_non_appendable_state(tmp_path):
    result = preflight_manual_inbox(
        inbox=tmp_path / "inbox",
        approval=_approval(),
        store_root=tmp_path / "store",
        now=datetime(2026, 8, 16, 3, tzinfo=timezone.utc),
    )

    assert result["status"] == "EMPTY_MANUAL_INBOX"
    assert result["readyFileCount"] == 0
    assert result["rejectedFileCount"] == 0
    assert not (tmp_path / "store").exists()


def test_valid_manual_input_requires_a_matching_hash_and_is_preflight_ready(tmp_path):
    now = datetime(2026, 8, 16, 3, tzinfo=timezone.utc)
    path = _write_manual_input(tmp_path / "inbox", _manual_payload(now))

    result = preflight_manual_inbox(
        inbox=path.parent,
        approval=_approval(),
        store_root=tmp_path / "store",
        now=now,
    )

    assert result["status"] == "MANUAL_INGEST_PREFLIGHT_READY"
    assert result["readyFileCount"] == 1
    assert result["rejectedFileCount"] == 0
    assert result["readyPaths"] == [path]


def test_manual_preflight_accepts_an_older_but_predeadline_snapshot(tmp_path):
    now = datetime(2026, 8, 16, 3, 30, tzinfo=timezone.utc)
    payload = _manual_payload(now - timedelta(minutes=20))
    payload["raceDeadlineJst"] = (now.astimezone(JST) + timedelta(minutes=10)).isoformat()
    payload["raceDate"] = now.astimezone(JST).date().isoformat()
    path = _write_manual_input(tmp_path / "inbox", payload)

    result = preflight_manual_inbox(
        inbox=path.parent,
        approval=_approval(),
        store_root=tmp_path / "store",
        now=now,
    )

    assert result["status"] == "MANUAL_INGEST_PREFLIGHT_READY"


def test_manual_preflight_rejects_duplicate_before_append(tmp_path):
    now = datetime.now(timezone.utc)
    inbox = tmp_path / "inbox"
    path = _write_manual_input(inbox, _manual_payload(now))
    collector = FeatureCollector(
        CollectorConfig(
            store_root=tmp_path / "store",
            allowed_source_types=("LOCAL_PERSONAL_SNAPSHOT",),
            parser_version="fixture-v1",
            contract_version="feature-forward-v1",
            allowed_source_location_prefixes=("file:///personal-inbox",),
        )
    )
    assert collector.capture(path.read_bytes()).status == "CAPTURED"

    result = preflight_manual_inbox(
        inbox=inbox,
        approval=_approval(),
        store_root=tmp_path / "store",
        now=now,
    )

    assert result["status"] == "MANUAL_INGEST_PREFLIGHT_BLOCKED"
    assert result["records"][0]["reasons"] == ["DUPLICATE_SNAPSHOT"]


def test_manual_preflight_rejects_duplicate_inbox_before_any_append(tmp_path):
    now = datetime(2026, 8, 16, 3, tzinfo=timezone.utc)
    inbox = tmp_path / "inbox"
    payload = _manual_payload(now)
    _write_manual_input(inbox, payload, name="first.json")
    _write_manual_input(inbox, payload, name="second.json")

    result = preflight_manual_inbox(
        inbox=inbox,
        approval=_approval(),
        store_root=tmp_path / "store",
        now=now,
    )

    assert result["status"] == "MANUAL_INGEST_PREFLIGHT_BLOCKED"
    assert result["readyFileCount"] < result["fileCount"]
    assert any("DUPLICATE_INBOX_SNAPSHOT" in row["reasons"] for row in result["records"])
    assert not (tmp_path / "store" / "feature_forward.sqlite3").exists()


def test_manual_preflight_blocks_a_corrupt_lifecycle_ledger_before_append(tmp_path):
    now = datetime(2026, 8, 16, 3, tzinfo=timezone.utc)
    store_root = tmp_path / "store"
    database = _append_lifecycle_event(store_root)
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER no_update_race_lifecycle_events")
        connection.execute("UPDATE race_lifecycle_events SET event_hash='0'")
    _write_manual_input(tmp_path / "inbox", _manual_payload(now))

    result = preflight_manual_inbox(
        inbox=tmp_path / "inbox",
        approval=_approval(),
        store_root=store_root,
        now=now,
    )

    assert result["status"] == "MANUAL_INGEST_PREFLIGHT_BLOCKED"
    assert result["records"][0]["reasons"] == ["LIFECYCLE_INTEGRITY_INVALID"]


def test_readiness_audit_counts_reverse_lifecycle_time_order(tmp_path):
    database = _append_lifecycle_event(tmp_path / "store")
    ledger = LifecycleLedger(database)
    ledger.append_event(
        snapshot_id="snapshot-2",
        target_date="2026-08-16",
        venue="01",
        race_no=2,
        stage="CAPTURE",
        status_code="VALID_CAPTURE",
        occurred_at_utc="2026-08-16T02:00:00+00:00",
        collector_run_id="collector-2",
        task_run_id="task-2",
        attempt_no=0,
        source_policy_hash="a" * 64,
        config_hash="b" * 64,
        code_commit="c" * 40,
        reason_detail="captured",
        evidence_ref="feature:snapshot-2",
    )
    ledger.close()

    result = verify_lifecycle_ledger_read_only(database)

    assert result["valid"] is True
    assert result["timeOrderViolationCount"] == 1


def test_manual_preflight_fails_closed_when_feature_store_payload_is_malformed(tmp_path):
    now = datetime.now(timezone.utc)
    store_root = tmp_path / "store"
    collector = FeatureCollector(
        CollectorConfig(
            store_root=store_root,
            allowed_source_types=("LOCAL_PERSONAL_SNAPSHOT",),
            parser_version="fixture-v1",
            contract_version="feature-forward-v1",
            allowed_source_location_prefixes=("file:///personal-inbox",),
        )
    )
    original = _write_manual_input(tmp_path / "original", _manual_payload(now))
    assert collector.capture(original.read_bytes()).status == "CAPTURED"
    database = store_root / "feature_forward.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER no_update_feature_records")
        connection.execute("UPDATE feature_records SET payload_json='{not-json'")
    _write_manual_input(tmp_path / "inbox", _manual_payload(now), name="new.json")

    result = preflight_manual_inbox(
        inbox=tmp_path / "inbox",
        approval=_approval(),
        store_root=store_root,
        now=now,
    )

    assert result["status"] == "MANUAL_INGEST_PREFLIGHT_BLOCKED"
    assert result["records"][0]["reasons"] == ["FEATURE_STORE_INTEGRITY_INVALID"]


@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    [
        (lambda payload: payload.update(jcd="25"), "RACE_IDENTITY_INVALID"),
        (lambda payload: payload.update(sourceType="UNAPPROVED_SOURCE"), "SOURCE_NOT_APPROVED"),
        (
            lambda payload: payload["boats"][0]["groups"].pop("exhibition_time"),
            "SCHEMA_MISMATCH",
        ),
        (
            lambda payload: payload.update(sourceLocation="file:///unapproved/capture.json"),
            "SOURCE_LOCATION_NOT_APPROVED",
        ),
    ],
)
def test_manual_preflight_rejects_identity_schema_and_provenance_violations(
    tmp_path, mutation, expected_reason
):
    now = datetime(2026, 8, 16, 3, tzinfo=timezone.utc)
    payload = _manual_payload(now)
    mutation(payload)
    path = _write_manual_input(tmp_path / "inbox", payload)

    result = preflight_manual_inbox(
        inbox=path.parent,
        approval=_approval(),
        store_root=tmp_path / "store",
        now=now,
    )

    assert result["status"] == "MANUAL_INGEST_PREFLIGHT_BLOCKED"
    assert expected_reason in result["records"][0]["reasons"]


@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    [
        (
            lambda payload: payload.update(
                raceDeadlineJst=(
                    datetime.fromisoformat(payload["fetchedAtJst"]) - timedelta(seconds=1)
                ).isoformat()
            ),
            "POST_DEADLINE",
        ),
        (
            lambda payload: payload["boats"][0]["groups"]["course_and_start_exhibition"].update(
                result=1
            ),
            "RESULT_LEAKAGE",
        ),
    ],
)
def test_manual_preflight_rejects_time_order_and_result_leakage(tmp_path, mutation, expected_reason):
    now = datetime(2026, 8, 16, 3, tzinfo=timezone.utc)
    payload = _manual_payload(now)
    mutation(payload)
    path = _write_manual_input(tmp_path / "inbox", payload)

    result = preflight_manual_inbox(
        inbox=path.parent,
        approval=_approval(),
        store_root=tmp_path / "store",
        now=now,
    )

    assert result["status"] == "MANUAL_INGEST_PREFLIGHT_BLOCKED"
    assert expected_reason in result["records"][0]["reasons"]
    assert not (tmp_path / "store").exists()


def test_manual_preflight_rejects_hash_mismatch_before_append(tmp_path):
    now = datetime(2026, 8, 16, 3, tzinfo=timezone.utc)
    path = _write_manual_input(tmp_path / "inbox", _manual_payload(now), digest="0" * 64)

    result = preflight_manual_inbox(
        inbox=path.parent,
        approval=_approval(),
        store_root=tmp_path / "store",
        now=now,
    )

    assert result["status"] == "MANUAL_INGEST_PREFLIGHT_BLOCKED"
    assert result["records"][0]["reasons"] == ["INPUT_HASH_MISMATCH"]


@pytest.mark.parametrize(
    ("changes", "fold_count", "expected_reason", "remaining_key", "expected_remaining"),
    [
        ({"forwardCollectionDays": 29}, 75, "minimum_forward_days_not_met", "forwardCollectionDays", 1),
        ({"matureCaptureCoverage": 0.79}, 75, "minimum_coverage_not_met", "matureCaptureCoverage", 0.01),
        ({"featureSettledRaceCount": 499}, 75, "minimum_feature_settled_races_not_met", "featureSettledRaceCount", 1),
        ({"oofRaceCount": 374, "validationRaceCount": 374}, 75, "minimum_oof_races_not_met", "oofRaceCount", 1),
        ({}, 74, "minimum_validation_races_per_fold_not_met", "minimumFoldValidationRaceCount", 1),
    ],
)
def test_diagnostic_readiness_reports_exact_missing_amounts(
    changes, fold_count, expected_reason, remaining_key, expected_remaining
):
    report = build_oof_data_readiness(
        spec=SPEC,
        current=_current(**changes),
        folds=_folds(fold_count),
        manual_ingest={"status": "EMPTY_MANUAL_INBOX"},
    )

    assert report["diagnosticReady"] is False
    assert expected_reason in report["blockedReasons"]
    assert report["remaining"]["diagnostic"][remaining_key] == pytest.approx(expected_remaining)


def test_diagnostic_boundary_is_ready_at_30_days_500_races_80_percent_75_fold_and_375_oof():
    report = build_oof_data_readiness(
        spec=SPEC,
        current=_current(),
        folds=_folds(75),
        manual_ingest={"status": "EMPTY_MANUAL_INBOX"},
    )

    assert report["diagnosticReady"] is True
    assert report["decisionReady"] is False
    assert report["status"] == "DIAGNOSTIC_READY_AWAITING_DECISION_DATA"
    assert report["oofExecution"] == {"executed": False, "permitted": False}


@pytest.mark.parametrize(
    ("changes", "fold_count", "expected_reason", "remaining_key", "expected_remaining"),
    [
        ({"featureSettledRaceCount": 1499}, 250, "minimum_feature_settled_races_not_met", "featureSettledRaceCount", 1),
        (
            {"oofRaceCount": 1249, "validationRaceCount": 1249},
            250,
            "minimum_oof_races_not_met",
            "oofRaceCount",
            1,
        ),
        ({}, 249, "minimum_validation_races_per_fold_not_met", "minimumFoldValidationRaceCount", 1),
    ],
)
def test_decision_readiness_reports_exact_missing_amounts(
    changes, fold_count, expected_reason, remaining_key, expected_remaining
):
    current = _current(
        validCaptureCount=1500,
        featureSettledRaceCount=1500,
        totalEligibleRaceCount=1500,
        initialTrainRaceCount=250,
        validationRaceCount=1250,
        oofRaceCount=1250,
    )
    current.update(changes)
    report = build_oof_data_readiness(
        spec=SPEC,
        current=current,
        folds=_folds(fold_count),
        manual_ingest={"status": "EMPTY_MANUAL_INBOX"},
    )

    assert report["decisionReady"] is False
    assert expected_reason in report["decisionBlockedReasons"]
    assert report["remaining"]["decision"][remaining_key] == pytest.approx(expected_remaining)


def test_decision_boundary_is_data_ready_but_never_runs_oof_automatically():
    report = build_oof_data_readiness(
        spec=SPEC,
        current=_current(
            validCaptureCount=1500,
            featureSettledRaceCount=1500,
            totalEligibleRaceCount=1500,
            initialTrainRaceCount=250,
            validationRaceCount=1250,
            oofRaceCount=1250,
        ),
        folds=_folds(250),
        manual_ingest={"status": "EMPTY_MANUAL_INBOX"},
    )

    assert report["diagnosticReady"] is True
    assert report["decisionReady"] is True
    assert report["status"] == "DECISION_DATA_READY_AWAITING_EXPLICIT_APPROVAL"
    assert report["predictionEdgeStatus"] == "PREDICTION_EDGE_UNPROVEN"
    assert report["productionAdoptionAllowed"] is False
    assert report["oofExecution"] == {"executed": False, "permitted": False}


def test_readiness_markdown_names_current_gap_coverage_integrity_and_next_data():
    report = build_oof_data_readiness(
        spec=SPEC,
        current=_current(forwardCollectionDays=29, featureSettledRaceCount=499),
        folds=_folds(74),
        manual_ingest={"status": "EMPTY_MANUAL_INBOX"},
    )

    markdown = render_oof_data_readiness_markdown(report)

    assert "## 1. 現在値" in markdown
    assert "## 5. 最弱fold" in markdown
    assert "## 6. Coverage" in markdown
    assert "## 7. Integrity" in markdown
    assert "## 8. 次に必要なデータ" in markdown
    assert "OOF dates remaining" in markdown
    assert "OOF評価は実行していない" in markdown


def test_automated_network_fetch_stays_disabled_in_the_fixed_source_policy():
    approval = json.loads(
        (ROOT / "config" / "feature_forward_v1" / "source_approval.json").read_text(
            encoding="utf-8"
        )
    )

    assert approval["manualIngestAllowed"] is True
    assert approval["automatedNetworkFetchAllowed"] is False
    assert approval["automatedCollectionAllowed"] is False


def test_readiness_cli_writes_blocked_report_for_empty_local_data_root(tmp_path):
    data_root = tmp_path / "data-root"
    (data_root / "data" / "research" / "feature_forward_v1" / "inbox").mkdir(parents=True)
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "build_oof_data_readiness_v1.py"),
            "--data-root",
            str(data_root),
            "--oof-spec",
            str(ROOT / "config" / "feature_forward_v1" / "oof_evaluation_spec.json"),
            "--approval",
            str(ROOT / "config" / "feature_forward_v1" / "source_approval.json"),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    report_path = data_root / "reports" / "feature_forward_v1" / "oof_readiness_latest.json"
    markdown_path = data_root / "reports" / "feature_forward_v1" / "oof_readiness_latest.md"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["status"] == "BLOCKED_WAITING_FOR_EXTERNAL_DATA"
    assert report["manualIngest"]["status"] == "EMPTY_MANUAL_INBOX"
    assert report["oofExecution"] == {"executed": False, "permitted": False}
    assert report["automatedNetworkFetchAllowed"] is False
    for field in ("gitSha", "dataSnapshot", "runtimeHash", "generatedAtUtc", "command", "exitCode"):
        assert field in report
    assert markdown_path.is_file()
