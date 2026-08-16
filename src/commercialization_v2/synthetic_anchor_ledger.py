from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Mapping


def _hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(dict(value), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class SyntheticAnchorLedger:
    """Append-only ledger containing only synthetic anchor source and commit records."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS synthetic_anchor_sources(
          package_sha256 TEXT PRIMARY KEY, package_json TEXT NOT NULL,
          record_type TEXT NOT NULL CHECK(record_type='synthetic_anchor'), record_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS synthetic_anchor_commits(
          package_sha256 TEXT PRIMARY KEY REFERENCES synthetic_anchor_sources(package_sha256),
          path TEXT NOT NULL UNIQUE, object_sha TEXT NOT NULL, commit_sha TEXT NOT NULL,
          committed_at TEXT NOT NULL, readback_sha256 TEXT NOT NULL, status TEXT NOT NULL,
          record_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS synthetic_anchor_chain(
          sequence INTEGER PRIMARY KEY AUTOINCREMENT, record_type TEXT NOT NULL,
          record_id TEXT NOT NULL, previous_hash TEXT NOT NULL, record_hash TEXT NOT NULL UNIQUE,
          UNIQUE(record_type,record_id));
        CREATE TRIGGER IF NOT EXISTS synthetic_source_no_update BEFORE UPDATE ON synthetic_anchor_sources BEGIN SELECT RAISE(ABORT,'append_only_update_prohibited'); END;
        CREATE TRIGGER IF NOT EXISTS synthetic_source_no_delete BEFORE DELETE ON synthetic_anchor_sources BEGIN SELECT RAISE(ABORT,'append_only_delete_prohibited'); END;
        CREATE TRIGGER IF NOT EXISTS synthetic_commit_no_update BEFORE UPDATE ON synthetic_anchor_commits BEGIN SELECT RAISE(ABORT,'append_only_update_prohibited'); END;
        CREATE TRIGGER IF NOT EXISTS synthetic_commit_no_delete BEFORE DELETE ON synthetic_anchor_commits BEGIN SELECT RAISE(ABORT,'append_only_delete_prohibited'); END;
        CREATE TRIGGER IF NOT EXISTS synthetic_chain_no_update BEFORE UPDATE ON synthetic_anchor_chain BEGIN SELECT RAISE(ABORT,'append_only_update_prohibited'); END;
        CREATE TRIGGER IF NOT EXISTS synthetic_chain_no_delete BEFORE DELETE ON synthetic_anchor_chain BEGIN SELECT RAISE(ABORT,'append_only_delete_prohibited'); END;
        """)
        self.connection.commit()

    def _append_chain(self, record_type: str, record_id: str, payload_hash: str) -> None:
        tail = self.connection.execute("SELECT record_hash FROM synthetic_anchor_chain ORDER BY sequence DESC LIMIT 1").fetchone()
        previous = str(tail[0]) if tail else "0" * 64
        chain_hash = _hash({"recordType": record_type, "recordId": record_id, "payloadHash": payload_hash, "previousHash": previous})
        self.connection.execute(
            "INSERT INTO synthetic_anchor_chain(record_type,record_id,previous_hash,record_hash) VALUES(?,?,?,?)",
            (record_type, record_id, previous, chain_hash),
        )

    def record(self, package: Mapping[str, Any], result: Mapping[str, Any]) -> str:
        package_json = json.dumps(dict(package), sort_keys=True, separators=(",", ":")) + "\n"
        package_sha = hashlib.sha256(package_json.encode()).hexdigest()
        if package_sha != result.get("package_sha256") or package_sha != result.get("readback_sha256"):
            raise ValueError("synthetic_anchor_hash_mismatch")
        existing = self.connection.execute("SELECT record_hash FROM synthetic_anchor_sources WHERE package_sha256=?", (package_sha,)).fetchone()
        if existing:
            commit = self.connection.execute("SELECT readback_sha256 FROM synthetic_anchor_commits WHERE package_sha256=?", (package_sha,)).fetchone()
            if not commit or commit[0] != package_sha:
                raise ValueError("synthetic_anchor_duplicate_conflict")
            return package_sha
        source_payload = {"packageSha256": package_sha, "packageJson": package_json, "recordType": "synthetic_anchor"}
        source_hash = _hash(source_payload)
        commit_payload = {
            "packageSha256": package_sha,
            "path": result.get("path", ""),
            "objectSha": result.get("object_sha", ""),
            "commitSha": result.get("commit_sha", ""),
            "committedAt": result.get("committed_at", ""),
            "readbackSha256": result.get("readback_sha256", ""),
            "status": result.get("status", ""),
        }
        if not all(str(value) for value in commit_payload.values()):
            raise ValueError("synthetic_anchor_receipt_incomplete")
        commit_hash = _hash(commit_payload)
        with self.connection:
            self.connection.execute("INSERT INTO synthetic_anchor_sources VALUES(?,?,?,?)", (package_sha, package_json, "synthetic_anchor", source_hash))
            self._append_chain("synthetic_anchor", package_sha, source_hash)
            self.connection.execute(
                "INSERT INTO synthetic_anchor_commits VALUES(?,?,?,?,?,?,?,?)",
                (package_sha, commit_payload["path"], commit_payload["objectSha"], commit_payload["commitSha"], commit_payload["committedAt"], package_sha, commit_payload["status"], commit_hash),
            )
            self._append_chain("synthetic_anchor_commit", package_sha, commit_hash)
        return package_sha

    def verify(self) -> dict[str, Any]:
        sources = self.connection.execute("SELECT * FROM synthetic_anchor_sources").fetchall()
        commits = self.connection.execute("SELECT * FROM synthetic_anchor_commits").fetchall()
        chain = self.connection.execute("SELECT * FROM synthetic_anchor_chain ORDER BY sequence").fetchall()
        allowed = {"synthetic_anchor", "synthetic_anchor_commit"}
        if any(row["record_type"] not in allowed for row in chain):
            return {"valid": False, "reason": "unknown_record_type"}
        if len(sources) != len(commits) or len(chain) != len(sources) + len(commits):
            return {"valid": False, "reason": "record_chain_count_mismatch"}
        if {row["package_sha256"] for row in sources} != {row["package_sha256"] for row in commits}:
            return {"valid": False, "reason": "source_commit_identity_mismatch"}
        for row in sources:
            expected = _hash({"packageSha256": row["package_sha256"], "packageJson": row["package_json"], "recordType": row["record_type"]})
            if row["record_hash"] != expected or hashlib.sha256(row["package_json"].encode()).hexdigest() != row["package_sha256"]:
                return {"valid": False, "reason": "source_payload_hash_mismatch"}
        for row in commits:
            expected = _hash({
                "packageSha256": row["package_sha256"], "path": row["path"],
                "objectSha": row["object_sha"], "commitSha": row["commit_sha"],
                "committedAt": row["committed_at"], "readbackSha256": row["readback_sha256"],
                "status": row["status"],
            })
            if row["record_hash"] != expected:
                return {"valid": False, "reason": "commit_payload_hash_mismatch"}
        previous = "0" * 64
        for row in chain:
            table = "synthetic_anchor_sources" if row["record_type"] == "synthetic_anchor" else "synthetic_anchor_commits"
            source = self.connection.execute(f"SELECT record_hash FROM {table} WHERE package_sha256=?", (row["record_id"],)).fetchone()
            expected = _hash({"recordType": row["record_type"], "recordId": row["record_id"], "payloadHash": source[0] if source else "", "previousHash": previous})
            if not source or row["previous_hash"] != previous or row["record_hash"] != expected:
                return {"valid": False, "reason": "chain_hash_mismatch"}
            previous = row["record_hash"]
        return {"valid": True, "sourceCount": len(sources), "commitCount": len(commits), "chainCount": len(chain)}
