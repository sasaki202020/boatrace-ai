from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.feature_forward_v1 import runtime_lifecycle
from src.feature_forward_v1.lifecycle_ledger import LifecycleLedger
from src.feature_forward_v1.runtime_lifecycle import (
    RuntimeGateContext,
    RuntimeGateError,
    append_capture_lifecycle,
    append_settlement_lifecycle,
    load_runtime_gate,
)
from scripts.run_local_prediction_settlement_v1 import result_not_due_dates


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _gate(tmp_path: Path) -> RuntimeGateContext:
    return RuntimeGateContext(
        root=tmp_path,
        policy_path=tmp_path / "policy.json",
        policy_hash="a" * 64,
        policy_version=2,
        config_path=tmp_path / "config.json",
        config_hash="b" * 64,
        code_commit="c" * 40,
        settlement_grace_minutes=30,
    )


def _create_request_store(store_root: Path) -> None:
    store_root.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(store_root / "request_ledger.sqlite3") as connection:
        connection.executescript(
            """
            CREATE TABLE requests(
              race_key TEXT PRIMARY KEY,
              status_code INTEGER,
              outcome TEXT
            );
            CREATE TABLE state(key TEXT PRIMARY KEY, value TEXT);
            INSERT INTO state(key, value) VALUES ('venues:2026-08-02', '01');
            """
        )
    with sqlite3.connect(store_root / "feature_forward.sqlite3") as connection:
        connection.executescript(
            """
            CREATE TABLE snapshots(
              snapshot_id TEXT,
              race_date TEXT,
              jcd TEXT,
              race_no INTEGER,
              status TEXT,
              research_eligible INTEGER,
              reasons_json TEXT
            );
            INSERT INTO snapshots VALUES
              ('snap-1', '2026-08-02', '01', 1, 'CAPTURED', 1, '[]');
            """
        )


def _prediction() -> dict:
    value = {
        "raceId": "20260802-01-01",
        "raceDate": "2026-08-02",
        "deadlineJst": "2026-08-02T10:00:00+09:00",
        "predictionSha256": "prediction-hash",
    }
    return value


def _settlement(prediction: dict) -> dict:
    value = {
        "raceId": prediction["raceId"],
        "predictionSha256": prediction["predictionSha256"],
        "resultSourceSha256": "result-hash",
        "winnerBoat": 1,
        "settlementStatus": "settled",
    }
    value["settlementSha256"] = hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return value


def test_runtime_gate_passes_and_rejects_policy_or_config_tampering(tmp_path, monkeypatch):
    source = Path(__file__).parents[2]
    policy = source / "config/feature_forward_v1/source_approval.json"
    config = source / "reports/feature_forward/feature_value_contract.json"
    root = tmp_path
    (root / "config/feature_forward_v1").mkdir(parents=True)
    (root / "reports/feature_forward").mkdir(parents=True)
    (root / "config/feature_forward_v1/source_approval.json").write_bytes(policy.read_bytes())
    (root / "reports/feature_forward/feature_value_contract.json").write_bytes(config.read_bytes())
    policy_hash = hashlib.sha256(policy.read_bytes()).hexdigest()
    config_hash = hashlib.sha256(config.read_bytes()).hexdigest()
    gate_path = root / "config/feature_forward_v1/runtime_gate.json"
    _write_json(
        gate_path,
        {
            "schemaVersion": 1,
            "policyPath": "config/feature_forward_v1/source_approval.json",
            "expectedPolicySha256": policy_hash,
            "configPath": "reports/feature_forward/feature_value_contract.json",
            "expectedConfigSha256": config_hash,
            "settlementGraceMinutes": 30,
        },
    )
    monkeypatch.setattr(runtime_lifecycle, "_git_commit", lambda root: "d" * 40)

    context = load_runtime_gate(root, gate_config_path=gate_path)
    assert context.policy_hash == policy_hash
    assert context.config_hash == config_hash

    gate_path.write_text(gate_path.read_text(encoding="utf-8").replace(policy_hash, "e" * 64), encoding="utf-8")
    with pytest.raises(RuntimeGateError, match="source_policy_hash_mismatch"):
        load_runtime_gate(root, gate_config_path=gate_path)


def test_active_collector_stops_before_store_or_network_on_gate_failure(tmp_path, monkeypatch):
    from scripts import run_live_feature_capture_v1

    status = tmp_path / "status.json"
    store = tmp_path / "store"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_live_feature_capture_v1.py",
            "--b-root",
            str(tmp_path / "entries"),
            "--store",
            str(store),
            "--status",
            str(status),
            "--gate-config",
            str(tmp_path / "missing-gate.json"),
        ],
    )

    assert run_live_feature_capture_v1.main() == 2
    payload = json.loads(status.read_text(encoding="utf-8"))
    assert payload["status"] == "BLOCKED_RUNTIME_GATE"
    assert payload["networkRequests"] == 0
    assert payload["productionWrites"] == 0
    assert not store.exists()


def test_capture_lifecycle_is_complete_and_idempotent(tmp_path, monkeypatch):
    store = tmp_path / "store"
    _create_request_store(store)
    monkeypatch.setattr(
        runtime_lifecycle,
        "_schedule_rows",
        lambda _: [
            {
                "raceDate": "2026-08-02",
                "venue": "01",
                "raceNo": 1,
                "deadlineJst": datetime(2026, 8, 2, 10, tzinfo=runtime_lifecycle.JST),
            },
            {
                "raceDate": "2026-08-02",
                "venue": "02",
                "raceNo": 1,
                "deadlineJst": datetime(2026, 8, 2, 10, tzinfo=runtime_lifecycle.JST),
            },
        ],
    )

    result = append_capture_lifecycle(
        b_file=tmp_path / "B260802.TXT",
        store_root=store,
        gate=_gate(tmp_path),
        collector_run_id="capture-1",
        task_run_id="task-1",
        now_utc=datetime(2026, 8, 2, 0, 0, tzinfo=timezone.utc),
    )
    repeat = append_capture_lifecycle(
        b_file=tmp_path / "B260802.TXT",
        store_root=store,
        gate=_gate(tmp_path),
        collector_run_id="capture-2",
        task_run_id="task-2",
        now_utc=datetime(2026, 8, 2, 0, 1, tzinfo=timezone.utc),
    )

    assert result["newUnknownCount"] == 0
    assert result["statusCounts"] == {
        "NOT_SELECTED_BY_DAILY_CAP": 1,
        "SELECTED": 1,
        "VALID_CAPTURE": 2,
    }
    assert repeat["createdEvents"] == 0
    assert repeat["integrity"]["valid"] is True


def test_settlement_pending_transitions_and_mature_coverage(tmp_path, monkeypatch):
    store = tmp_path / "store"
    _create_request_store(store)
    monkeypatch.setattr(
        runtime_lifecycle,
        "_schedule_rows",
        lambda _: [{
            "raceDate": "2026-08-02",
            "venue": "01",
            "raceNo": 1,
            "deadlineJst": datetime(2026, 8, 2, 10, tzinfo=runtime_lifecycle.JST),
        }],
    )
    append_capture_lifecycle(
        b_file=tmp_path / "B260802.TXT",
        store_root=store,
        gate=_gate(tmp_path),
        collector_run_id="capture-1",
        task_run_id="task-1",
        now_utc=datetime(2026, 8, 2, 0, tzinfo=timezone.utc),
    )
    prediction = _prediction()
    _write_json(tmp_path / "predictions/20260802/20260802-01-01.json", prediction)

    before = append_settlement_lifecycle(
        store_root=store,
        prediction_root=tmp_path / "predictions",
        settlement_root=tmp_path / "settlements",
        result_root=tmp_path / "results",
        gate=_gate(tmp_path),
        collector_run_id="settlement-1",
        task_run_id="task-2",
        now_utc=datetime(2026, 8, 2, 0, 30, tzinfo=timezone.utc),
    )
    overdue = append_settlement_lifecycle(
        store_root=store,
        prediction_root=tmp_path / "predictions",
        settlement_root=tmp_path / "settlements",
        result_root=tmp_path / "results",
        gate=_gate(tmp_path),
        collector_run_id="settlement-2",
        task_run_id="task-2",
        now_utc=datetime(2026, 8, 2, 2, tzinfo=timezone.utc),
    )
    _write_json(tmp_path / "settlements/20260802/20260802-01-01.json", _settlement(prediction))
    settled = append_settlement_lifecycle(
        store_root=store,
        prediction_root=tmp_path / "predictions",
        settlement_root=tmp_path / "settlements",
        result_root=tmp_path / "results",
        gate=_gate(tmp_path),
        collector_run_id="settlement-3",
        task_run_id="task-2",
        now_utc=datetime(2026, 8, 2, 3, tzinfo=timezone.utc),
    )

    assert before["statusCounts"] == {"PENDING_NOT_DUE": 1}
    assert overdue["statusCounts"] == {"PENDING_OVERDUE": 1}
    assert overdue["overdueSettlementPendingCount"] == 1
    assert settled["statusCounts"] == {"SETTLED": 1}
    assert settled["matureSettlementCoverage"] == 1.0
    assert settled["integrity"]["valid"] is True


def test_current_day_result_file_not_due_does_not_become_overdue(
    tmp_path, monkeypatch
):
    store = tmp_path / "store"
    _create_request_store(store)
    monkeypatch.setattr(
        runtime_lifecycle,
        "_schedule_rows",
        lambda _: [{
            "raceDate": "2026-08-05",
            "venue": "01",
            "raceNo": 1,
            "deadlineJst": datetime(2026, 8, 5, 10, tzinfo=runtime_lifecycle.JST),
        }],
    )
    with sqlite3.connect(store / "request_ledger.sqlite3") as connection:
        connection.execute(
            "INSERT INTO state(key, value) VALUES (?, ?)",
            ("venues:2026-08-05", "01"),
        )
    with sqlite3.connect(store / "feature_forward.sqlite3") as connection:
        connection.execute(
            "INSERT INTO snapshots VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("snap-current", "2026-08-05", "01", 1, "CAPTURED", 1, "[]"),
        )
    append_capture_lifecycle(
        b_file=tmp_path / "B260805.TXT",
        store_root=store,
        gate=_gate(tmp_path),
        collector_run_id="capture-current",
        task_run_id="task-current",
        now_utc=datetime(2026, 8, 5, 0, tzinfo=timezone.utc),
    )
    prediction = {
        "raceId": "20260805-01-01",
        "raceDate": "2026-08-05",
        "deadlineJst": "2026-08-05T10:00:00+09:00",
        "predictionSha256": "prediction-current",
    }
    _write_json(tmp_path / "predictions/20260805/20260805-01-01.json", prediction)

    result = append_settlement_lifecycle(
        store_root=store,
        prediction_root=tmp_path / "predictions",
        settlement_root=tmp_path / "settlements",
        result_root=tmp_path / "results",
        gate=_gate(tmp_path),
        collector_run_id="settlement-current",
        task_run_id="task-current",
        now_utc=datetime(2026, 8, 5, 12, tzinfo=timezone.utc),
        result_not_due_dates={"2026-08-05"},
    )

    assert result["statusCounts"] == {"PENDING_NOT_DUE": 1}
    assert result["overdueSettlementPendingCount"] == 0
    assert result["matureSettlementEligibleRaces"] == 0


def test_result_not_due_dates_parses_k_filename():
    assert result_not_due_dates({"notDueFiles": ["B260806.TXT", "K260805.TXT"]}) == {
        "2026-08-05"
    }
