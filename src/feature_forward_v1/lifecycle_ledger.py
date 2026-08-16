from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


CAPTURE_STATUS = frozenset(
    {
        "VALID_CAPTURE",
        "PENDING_CAPTURE",
        "PENDING_VALIDATION",
        "NOT_SELECTED_BY_DAILY_CAP",
        "CAPTURE_WINDOW_COLLISION",
        "TASK_NOT_RUNNING",
        "NETWORK_ERROR",
        "RATE_LIMIT",
        "SOURCE_UNAVAILABLE",
        "PARSE_FAILURE",
        "DEADLINE_PASSED",
        "RACE_CANCELLED",
        "UNKNOWN_LEGACY",
    }
)
SELECTION_STATUS = frozenset({"SELECTED", "NOT_SELECTED_BY_DAILY_CAP"})
SETTLEMENT_STATUS = frozenset(
    {
        "SETTLED",
        "RESULT_PENDING",
        "PENDING_NOT_DUE",
        "PENDING_WITHIN_GRACE",
        "PENDING_OVERDUE",
        "RESULT_UNAVAILABLE",
        "RACE_CANCELLED",
        "KEY_MISMATCH",
        "SETTLEMENT_TASK_NOT_RUNNING",
        "SOURCE_UNAVAILABLE",
        "DUPLICATE_OR_CONFLICT",
        "UNKNOWN_LEGACY",
    }
)
STAGES = frozenset({"SELECTION", "CAPTURE", "VALIDATION", "SETTLEMENT"})
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEX40 = re.compile(r"^[0-9a-f]{40}$")


class LifecycleConflictError(RuntimeError):
    """Raised when a terminal lifecycle outcome would be rewritten."""


class LifecycleValidationError(ValueError):
    """Raised when a lifecycle event violates the storage contract."""


@dataclass(frozen=True)
class AppendResult:
    created: bool
    event_id: str
    ledger_sequence: int
    event_hash: str


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class LifecycleLedger:
    """A separate append-only race lifecycle chain.

    This class does not run during existing collection imports. The database is
    opened and the schema is created only when an explicit lifecycle runner uses it.
    """

    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.database_path)
        self.connection.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS race_lifecycle_events (
              ledger_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
              event_id TEXT NOT NULL UNIQUE,
              dedupe_key TEXT NOT NULL UNIQUE,
              snapshot_id TEXT,
              target_date TEXT NOT NULL,
              venue TEXT NOT NULL,
              race_no INTEGER NOT NULL,
              stage TEXT NOT NULL,
              status_code TEXT NOT NULL,
              terminal INTEGER NOT NULL,
              occurred_at_utc TEXT NOT NULL,
              collector_run_id TEXT NOT NULL,
              task_run_id TEXT NOT NULL,
              attempt_no INTEGER NOT NULL,
              source_policy_hash TEXT NOT NULL,
              config_hash TEXT NOT NULL,
              code_commit TEXT NOT NULL,
              reason_detail TEXT NOT NULL,
              evidence_ref TEXT NOT NULL,
              previous_event_hash TEXT NOT NULL,
              event_hash TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_lifecycle_race_stage
              ON race_lifecycle_events(target_date, venue, race_no, stage);
            CREATE TRIGGER IF NOT EXISTS no_update_race_lifecycle_events
              BEFORE UPDATE ON race_lifecycle_events
              BEGIN SELECT RAISE(ABORT, 'append_only_update_prohibited'); END;
            CREATE TRIGGER IF NOT EXISTS no_delete_race_lifecycle_events
              BEFORE DELETE ON race_lifecycle_events
              BEGIN SELECT RAISE(ABORT, 'append_only_delete_prohibited'); END;
            """
        )
        self.connection.commit()

    @staticmethod
    def _validate_event(
        *,
        target_date: str,
        venue: str,
        race_no: int,
        stage: str,
        status_code: str,
        occurred_at_utc: str,
        attempt_no: int,
        source_policy_hash: str,
        config_hash: str,
        code_commit: str,
    ) -> None:
        try:
            datetime.fromisoformat(target_date)
        except ValueError as exc:
            raise LifecycleValidationError("target_date_invalid") from exc
        if not re.fullmatch(r"\d{2}", venue) or not 1 <= int(venue) <= 24:
            raise LifecycleValidationError("venue_invalid")
        if type(race_no) is not int or not 1 <= race_no <= 12:
            raise LifecycleValidationError("race_no_invalid")
        if stage not in STAGES:
            raise LifecycleValidationError("stage_invalid")
        if stage == "SELECTION":
            # VALID_CAPTURE remains accepted for compatibility with the v1 test
            # contract; runtime selection events use SELECTED explicitly.
            allowed = SELECTION_STATUS | CAPTURE_STATUS
        elif stage in {"CAPTURE", "VALIDATION"}:
            allowed = CAPTURE_STATUS
        else:
            allowed = SETTLEMENT_STATUS
        if status_code not in allowed:
            raise LifecycleValidationError("status_code_invalid")
        try:
            parsed = datetime.fromisoformat(occurred_at_utc)
        except ValueError as exc:
            raise LifecycleValidationError("occurred_at_invalid") from exc
        if parsed.tzinfo is None:
            raise LifecycleValidationError("occurred_at_timezone_required")
        if type(attempt_no) is not int or attempt_no < 0:
            raise LifecycleValidationError("attempt_no_invalid")
        if not HEX64.fullmatch(source_policy_hash):
            raise LifecycleValidationError("source_policy_hash_invalid")
        if not HEX64.fullmatch(config_hash):
            raise LifecycleValidationError("config_hash_invalid")
        if not HEX40.fullmatch(code_commit):
            raise LifecycleValidationError("code_commit_invalid")

    def append_event(
        self,
        *,
        snapshot_id: str | None,
        target_date: str,
        venue: str,
        race_no: int,
        stage: str,
        status_code: str,
        occurred_at_utc: str,
        collector_run_id: str,
        task_run_id: str,
        attempt_no: int,
        source_policy_hash: str,
        config_hash: str,
        code_commit: str,
        reason_detail: str,
        evidence_ref: str,
        terminal: bool | None = None,
    ) -> AppendResult:
        self._validate_event(
            target_date=target_date,
            venue=venue,
            race_no=race_no,
            stage=stage,
            status_code=status_code,
            occurred_at_utc=occurred_at_utc,
            attempt_no=attempt_no,
            source_policy_hash=source_policy_hash,
            config_hash=config_hash,
            code_commit=code_commit,
        )
        for name, value in {
            "collector_run_id": collector_run_id,
            "task_run_id": task_run_id,
            "reason_detail": reason_detail,
            "evidence_ref": evidence_ref,
        }.items():
            if not isinstance(value, str) or not value:
                raise LifecycleValidationError(f"{name}_required")
        if snapshot_id is not None and (not isinstance(snapshot_id, str) or not snapshot_id):
            raise LifecycleValidationError("snapshot_id_invalid")
        non_terminal_statuses = {
            "RESULT_PENDING",
            "PENDING_NOT_DUE",
            "PENDING_WITHIN_GRACE",
            "PENDING_OVERDUE",
            "PENDING_CAPTURE",
            "PENDING_VALIDATION",
        }
        terminal = status_code not in non_terminal_statuses if terminal is None else terminal
        if type(terminal) is not bool:
            raise LifecycleValidationError("terminal_invalid")

        base = {
            "snapshotId": snapshot_id,
            "targetDate": target_date,
            "venue": venue,
            "raceNo": race_no,
            "stage": stage,
            "statusCode": status_code,
            "terminal": terminal,
            "collectorRunId": collector_run_id,
            "taskRunId": task_run_id,
            "attemptNo": attempt_no,
            "sourcePolicyHash": source_policy_hash,
            "configHash": config_hash,
            "codeCommit": code_commit,
            "reasonDetail": reason_detail,
            "evidenceRef": evidence_ref,
        }
        dedupe_payload = {
            key: base[key]
            for key in (
                "snapshotId",
                "targetDate",
                "venue",
                "raceNo",
                "stage",
                "statusCode",
                "attemptNo",
                "sourcePolicyHash",
                "configHash",
                "codeCommit",
                "reasonDetail",
                "evidenceRef",
            )
        }
        # Run identifiers and wall-clock timestamps are provenance fields, not
        # identity fields. Re-running the same observed state must be idempotent.
        dedupe_key = _stable_hash(dedupe_payload)
        event_id = _stable_hash({"dedupeKey": dedupe_key})
        existing = self.connection.execute(
            "SELECT ledger_sequence,event_id,event_hash FROM race_lifecycle_events WHERE dedupe_key=?",
            (dedupe_key,),
        ).fetchone()
        if existing:
            return AppendResult(False, existing["event_id"], existing["ledger_sequence"], existing["event_hash"])

        # A code commit identifies the implementation that observed an event,
        # but it does not change the observed race state. Keep replay idempotent
        # when only that provenance field changed between runs.
        canonical_existing = self.connection.execute(
            """
            SELECT ledger_sequence,event_id,event_hash
            FROM race_lifecycle_events
            WHERE snapshot_id IS ?
              AND target_date=?
              AND venue=?
              AND race_no=?
              AND stage=?
              AND status_code=?
              AND terminal=?
              AND attempt_no=?
              AND source_policy_hash=?
              AND config_hash=?
              AND reason_detail=?
              AND evidence_ref=?
            ORDER BY ledger_sequence
            LIMIT 1
            """,
            (
                snapshot_id,
                target_date,
                venue,
                race_no,
                stage,
                status_code,
                int(terminal),
                attempt_no,
                source_policy_hash,
                config_hash,
                reason_detail,
                evidence_ref,
            ),
        ).fetchone()
        if canonical_existing:
            return AppendResult(
                False,
                canonical_existing["event_id"],
                canonical_existing["ledger_sequence"],
                canonical_existing["event_hash"],
            )

        terminal_rows = self.connection.execute(
            """
            SELECT status_code FROM race_lifecycle_events
            WHERE target_date=? AND venue=? AND race_no=? AND stage=? AND terminal=1
            ORDER BY ledger_sequence
            """,
            (target_date, venue, race_no, stage),
        ).fetchall()
        if terminal_rows:
            raise LifecycleConflictError("terminal_status_conflict")

        previous = self.connection.execute(
            "SELECT event_hash FROM race_lifecycle_events ORDER BY ledger_sequence DESC LIMIT 1"
        ).fetchone()
        previous_hash = previous["event_hash"] if previous else "0" * 64
        event_payload = {
            **base,
            "eventId": event_id,
            "previousEventHash": previous_hash,
        }
        event_hash = _stable_hash(event_payload)
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO race_lifecycle_events(
                  event_id,dedupe_key,snapshot_id,target_date,venue,race_no,stage,status_code,
                  terminal,occurred_at_utc,collector_run_id,task_run_id,attempt_no,
                  source_policy_hash,config_hash,code_commit,reason_detail,evidence_ref,
                  previous_event_hash,event_hash
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    event_id,
                    dedupe_key,
                    snapshot_id,
                    target_date,
                    venue,
                    race_no,
                    stage,
                    status_code,
                    int(terminal),
                    occurred_at_utc,
                    collector_run_id,
                    task_run_id,
                    attempt_no,
                    source_policy_hash,
                    config_hash,
                    code_commit,
                    reason_detail,
                    evidence_ref,
                    previous_hash,
                    event_hash,
                ),
            )
        return AppendResult(True, event_id, cursor.lastrowid, event_hash)

    def count(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM race_lifecycle_events").fetchone()[0])

    def verify_integrity(self) -> dict[str, object]:
        previous = "0" * 64
        expected_sequence = 1
        for row in self.connection.execute(
            "SELECT * FROM race_lifecycle_events ORDER BY ledger_sequence"
        ):
            if row["ledger_sequence"] != expected_sequence:
                return {"valid": False, "reason": "ledger_sequence_gap"}
            if row["previous_event_hash"] != previous:
                return {"valid": False, "reason": "previous_event_hash"}
            base = {
                "snapshotId": row["snapshot_id"],
                "targetDate": row["target_date"],
                "venue": row["venue"],
                "raceNo": row["race_no"],
                "stage": row["stage"],
                "statusCode": row["status_code"],
                "terminal": bool(row["terminal"]),
                "collectorRunId": row["collector_run_id"],
                "taskRunId": row["task_run_id"],
                "attemptNo": row["attempt_no"],
                "sourcePolicyHash": row["source_policy_hash"],
                "configHash": row["config_hash"],
                "codeCommit": row["code_commit"],
                "reasonDetail": row["reason_detail"],
                "evidenceRef": row["evidence_ref"],
            }
            dedupe_payload = {
                key: base[key]
                for key in (
                    "snapshotId",
                    "targetDate",
                    "venue",
                    "raceNo",
                    "stage",
                    "statusCode",
                    "attemptNo",
                    "sourcePolicyHash",
                    "configHash",
                    "codeCommit",
                    "reasonDetail",
                    "evidenceRef",
                )
            }
            dedupe_key = _stable_hash(dedupe_payload)
            if row["dedupe_key"] != dedupe_key:
                return {"valid": False, "reason": "dedupe_key"}
            expected = _stable_hash(
                {**base, "eventId": row["event_id"], "previousEventHash": previous}
            )
            if row["event_hash"] != expected:
                return {"valid": False, "reason": "event_hash"}
            previous = row["event_hash"]
            expected_sequence += 1
        return {"valid": True, "recordCount": expected_sequence - 1, "tailHash": previous}

    def close(self) -> None:
        self.connection.close()
