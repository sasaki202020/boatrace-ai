from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from .canonical_package import canonical_package_bytes
from .commitment import verify_reveal


def stable_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


class ShadowLedgerV2:
    TABLES = ("input_artifacts", "prediction_packages", "prediction_rows", "external_anchors", "reveals", "result_packages", "result_rows", "gate_audits", "integrity_events", "ledger_chain")

    def __init__(self, path: Path, *, expected_model_sha256: str | None = None, expected_schema_sha256: str | None = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path); self.connection.row_factory = sqlite3.Row
        self.expected_model_sha256 = expected_model_sha256; self.expected_schema_sha256 = expected_schema_sha256
        self._initialize()

    def _initialize(self) -> None:
        self.connection.executescript("""
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS input_artifacts(id TEXT PRIMARY KEY, source_id TEXT NOT NULL, input_hash TEXT NOT NULL, metadata_json TEXT NOT NULL, record_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS prediction_packages(id TEXT PRIMARY KEY, race_date TEXT NOT NULL UNIQUE, package_hash TEXT NOT NULL UNIQUE, commitment TEXT NOT NULL UNIQUE, salt_hex TEXT NOT NULL, package_json TEXT NOT NULL, model_hash TEXT NOT NULL, schema_hash TEXT NOT NULL, cutoff TEXT NOT NULL, record_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS prediction_rows(id TEXT PRIMARY KEY, package_id TEXT NOT NULL REFERENCES prediction_packages(id), race_id TEXT NOT NULL, lane INTEGER NOT NULL, predicted_probability TEXT NOT NULL, record_hash TEXT NOT NULL, UNIQUE(race_id,lane));
        CREATE TABLE IF NOT EXISTS external_anchors(id TEXT PRIMARY KEY, package_id TEXT NOT NULL REFERENCES prediction_packages(id), provider TEXT NOT NULL, external_id TEXT NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL, receipt_hash TEXT NOT NULL UNIQUE, record_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS reveals(id TEXT PRIMARY KEY, package_id TEXT NOT NULL UNIQUE REFERENCES prediction_packages(id), reveal_hash TEXT NOT NULL, revealed_at TEXT NOT NULL, record_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS result_packages(id TEXT PRIMARY KEY, race_date TEXT NOT NULL UNIQUE, source_hash TEXT NOT NULL, package_hash TEXT NOT NULL, record_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS result_rows(id TEXT PRIMARY KEY, package_id TEXT NOT NULL REFERENCES result_packages(id), race_id TEXT NOT NULL UNIQUE, winning_lane INTEGER NOT NULL, record_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS gate_audits(id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, record_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS integrity_events(id TEXT PRIMARY KEY, event_type TEXT NOT NULL, payload_json TEXT NOT NULL, record_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS ledger_chain(sequence INTEGER PRIMARY KEY AUTOINCREMENT, record_type TEXT NOT NULL, record_id TEXT NOT NULL, previous_hash TEXT NOT NULL, record_hash TEXT NOT NULL UNIQUE);
        """)
        for table in self.TABLES:
            self.connection.execute(f"CREATE TRIGGER IF NOT EXISTS v2_no_update_{table} BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT,'append_only_update_prohibited'); END")
            self.connection.execute(f"CREATE TRIGGER IF NOT EXISTS v2_no_delete_{table} BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT,'append_only_delete_prohibited'); END")
        self.connection.commit()

    def _tail(self) -> str:
        row = self.connection.execute("SELECT record_hash FROM ledger_chain ORDER BY sequence DESC LIMIT 1").fetchone()
        return str(row[0]) if row else "0" * 64

    def _chain(self, kind: str, record_id: str, payload_hash: str) -> None:
        previous = self._tail(); current = stable_hash({"type": kind, "id": record_id, "payloadHash": payload_hash, "previousHash": previous})
        self.connection.execute("INSERT INTO ledger_chain(record_type,record_id,previous_hash,record_hash) VALUES(?,?,?,?)", (kind, record_id, previous, current))

    def append_prediction_package(self, package: dict[str, Any], raw: bytes, commitment: dict[str, str]) -> str:
        if self.expected_model_sha256 and package.get("modelSha256") != self.expected_model_sha256: raise ValueError("model_hash_mismatch")
        if self.expected_schema_sha256 and package.get("featureSchemaSha256") != self.expected_schema_sha256: raise ValueError("feature_schema_hash_mismatch")
        canonical = canonical_package_bytes(package)
        if raw != canonical or hashlib.sha256(raw).hexdigest() != commitment["packageSha256"]:
            raise ValueError("package_hash_mismatch")
        if not verify_reveal(raw, commitment["saltHex"], commitment["commitment"]): raise ValueError("commitment_mismatch")
        race_date = str(package["raceDate"])
        if self.connection.execute("SELECT 1 FROM prediction_packages WHERE race_date=?", (race_date,)).fetchone(): raise ValueError("duplicate_race_date_package")
        package_id = str(uuid.uuid4())
        package_record = {"id": package_id, "raceDate": race_date, "packageHash": commitment["packageSha256"], "commitment": commitment["commitment"], "saltHex": commitment["saltHex"], "packageJson": raw.decode(), "modelHash": package["modelSha256"], "schemaHash": package["featureSchemaSha256"], "cutoff": package["conservativeCutoff"]}
        record_hash = stable_hash(package_record)
        with self.connection:
            self.connection.execute("INSERT INTO prediction_packages VALUES(?,?,?,?,?,?,?,?,?,?)", (package_id, race_date, commitment["packageSha256"], commitment["commitment"], commitment["saltHex"], raw.decode(), package["modelSha256"], package["featureSchemaSha256"], package["conservativeCutoff"], record_hash))
            self._chain("prediction_package", package_id, record_hash)
            for row in package["predictions"]:
                row_id = stable_hash({"packageId": package_id, "raceId": row["raceId"], "lane": row["lane"]})
                row_hash = stable_hash({"id": row_id, "packageId": package_id, "raceId": row["raceId"], "lane": int(row["lane"]), "predictedProbability": row["predictedProbability"]})
                self.connection.execute("INSERT INTO prediction_rows VALUES(?,?,?,?,?,?)", (row_id, package_id, row["raceId"], row["lane"], row["predictedProbability"], row_hash)); self._chain("prediction_row", row_id, row_hash)
        return package_id

    def append_anchor(self, package_id: str, receipt: Any, status: str) -> str:
        if status not in {"EXTERNALLY_COMMITTED", "VERIFIED_SYNTHETIC", "LATE_COMMIT_REJECTED", "INVALID_COMMITMENT"}: raise ValueError("invalid_anchor_status")
        anchor_id = str(uuid.uuid4())
        record_hash = stable_hash({"id": anchor_id, "packageId": package_id, "provider": receipt.provider, "externalId": receipt.external_id, "createdAt": receipt.created_at, "status": status, "receiptHash": receipt.receipt_hash})
        with self.connection:
            self.connection.execute("INSERT INTO external_anchors VALUES(?,?,?,?,?,?,?,?)", (anchor_id, package_id, receipt.provider, receipt.external_id, receipt.created_at, status, receipt.receipt_hash, record_hash)); self._chain("external_anchor", anchor_id, record_hash)
        return anchor_id

    def append_reveal(self, package_id: str, package_bytes: bytes, salt_hex: str, revealed_at: str) -> str:
        row = self.connection.execute("SELECT commitment,package_hash,package_json FROM prediction_packages WHERE id=?", (package_id,)).fetchone()
        if not row or package_bytes.decode() != row["package_json"] or hashlib.sha256(package_bytes).hexdigest() != row["package_hash"] or not verify_reveal(package_bytes, salt_hex, row["commitment"]): raise ValueError("reveal_verification_failed")
        reveal_id = str(uuid.uuid4()); reveal_hash = stable_hash({"packageHash": row["package_hash"], "saltHex": salt_hex}); record_hash = stable_hash({"id": reveal_id, "packageId": package_id, "revealHash": reveal_hash, "at": revealed_at})
        with self.connection:
            self.connection.execute("INSERT INTO reveals VALUES(?,?,?,?,?)", (reveal_id, package_id, reveal_hash, revealed_at, record_hash)); self._chain("reveal", reveal_id, record_hash)
        return reveal_id

    def append_result_package(self, race_date: str, rows: list[dict[str, Any]], source_hash: str) -> str:
        for row in rows:
            if not self.connection.execute("SELECT 1 FROM prediction_rows WHERE race_id=?", (row["raceId"],)).fetchone(): raise ValueError("prediction_required_before_result")
        payload_hash = stable_hash(rows); package_id = str(uuid.uuid4()); record_hash = stable_hash({"id": package_id, "raceDate": race_date, "sourceHash": source_hash, "packageHash": payload_hash})
        with self.connection:
            self.connection.execute("INSERT INTO result_packages VALUES(?,?,?,?,?)", (package_id, race_date, source_hash, payload_hash, record_hash)); self._chain("result_package", package_id, record_hash)
            for row in rows:
                row_id = stable_hash({"packageId": package_id, "raceId": row["raceId"]})
                row_hash = stable_hash({"id": row_id, "packageId": package_id, "raceId": row["raceId"], "winningLane": int(row["winningLane"])})
                self.connection.execute("INSERT INTO result_rows VALUES(?,?,?,?,?)", (row_id, package_id, row["raceId"], int(row["winningLane"]), row_hash)); self._chain("result_row", row_id, row_hash)
            integrity = self.verify_integrity()
            if integrity.get("valid") is not True:
                raise ValueError("result_append_integrity_failed")
        return package_id

    def prediction_digest(self) -> str:
        return stable_hash([row[0] for row in self.connection.execute("SELECT record_hash FROM prediction_rows ORDER BY race_id,lane")])

    def verify_integrity(self) -> dict[str, Any]:
        previous = "0" * 64
        chained_records: list[tuple[str, str]] = []
        table_map = {
            "input_artifact": "input_artifacts",
            "prediction_package": "prediction_packages", "prediction_row": "prediction_rows",
            "external_anchor": "external_anchors", "reveal": "reveals",
            "result_package": "result_packages", "result_row": "result_rows",
            "gate_audit": "gate_audits", "integrity_event": "integrity_events",
        }
        for row in self.connection.execute("SELECT * FROM ledger_chain ORDER BY sequence"):
            if row["previous_hash"] != previous: return {"valid": False, "reason": "chain_previous_hash_mismatch", "sequence": row["sequence"]}
            table = table_map.get(row["record_type"])
            if table is None:
                return {"valid": False, "reason": "unknown_chain_record_type", "sequence": row["sequence"]}
            source = self.connection.execute(f"SELECT record_hash FROM {table} WHERE id=?", (row["record_id"],)).fetchone()
            if not source:
                return {"valid": False, "reason": "chain_source_missing", "sequence": row["sequence"]}
            expected = stable_hash({"type": row["record_type"], "id": row["record_id"], "payloadHash": source[0], "previousHash": previous})
            if row["record_hash"] != expected:
                return {"valid": False, "reason": "chain_record_hash_mismatch", "sequence": row["sequence"]}
            chained_records.append((row["record_type"], row["record_id"]))
            previous = row["record_hash"]
        source_records = {
            (kind, str(row[0]))
            for kind, table in table_map.items()
            for row in self.connection.execute(f"SELECT id FROM {table}")
        }
        if len(chained_records) != len(set(chained_records)) or set(chained_records) != source_records:
            return {"valid": False, "reason": "chain_source_set_mismatch"}
        for table in ("input_artifacts", "gate_audits", "integrity_events"):
            if self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]:
                return {"valid": False, "reason": f"unsupported_nonempty_table:{table}"}
        for package in self.connection.execute("SELECT * FROM prediction_packages"):
            expected_package_hash = stable_hash({"id": package["id"], "raceDate": package["race_date"], "packageHash": package["package_hash"], "commitment": package["commitment"], "saltHex": package["salt_hex"], "packageJson": package["package_json"], "modelHash": package["model_hash"], "schemaHash": package["schema_hash"], "cutoff": package["cutoff"]})
            if package["record_hash"] != expected_package_hash:
                return {"valid": False, "reason": "prediction_package_payload_mismatch"}
            payload = json.loads(package["package_json"])
            stored = {(row["race_id"], int(row["lane"])): row for row in self.connection.execute("SELECT * FROM prediction_rows WHERE package_id=?", (package["id"],))}
            expected_keys = {(prediction["raceId"], int(prediction["lane"])) for prediction in payload["predictions"]}
            if set(stored) != expected_keys:
                return {"valid": False, "reason": "prediction_row_set_mismatch"}
            for prediction in payload["predictions"]:
                key = (prediction["raceId"], int(prediction["lane"]))
                row = stored.get(key)
                expected = stable_hash({"id": row["id"], "packageId": row["package_id"], "raceId": row["race_id"], "lane": int(row["lane"]), "predictedProbability": row["predicted_probability"]}) if row else None
                if row is None or row["predicted_probability"] != prediction["predictedProbability"] or row["record_hash"] != expected:
                    return {"valid": False, "reason": "prediction_row_payload_mismatch"}
        for row in self.connection.execute("SELECT * FROM external_anchors"):
            expected = stable_hash({"id": row["id"], "packageId": row["package_id"], "provider": row["provider"], "externalId": row["external_id"], "createdAt": row["created_at"], "status": row["status"], "receiptHash": row["receipt_hash"]})
            if row["record_hash"] != expected:
                return {"valid": False, "reason": "external_anchor_payload_mismatch"}
        for row in self.connection.execute("SELECT id,package_id,reveal_hash,revealed_at,record_hash FROM reveals"):
            expected = stable_hash({"id": row["id"], "packageId": row["package_id"], "revealHash": row["reveal_hash"], "at": row["revealed_at"]})
            if row["record_hash"] != expected:
                return {"valid": False, "reason": "reveal_payload_mismatch"}
        for row in self.connection.execute("SELECT id,race_date,source_hash,package_hash,record_hash FROM result_packages"):
            expected = stable_hash({"id": row["id"], "raceDate": row["race_date"], "sourceHash": row["source_hash"], "packageHash": row["package_hash"]})
            if row["record_hash"] != expected:
                return {"valid": False, "reason": "result_package_payload_mismatch"}
        for row in self.connection.execute("SELECT * FROM result_rows"):
            expected = stable_hash({"id": row["id"], "packageId": row["package_id"], "raceId": row["race_id"], "winningLane": int(row["winning_lane"])})
            if row["record_hash"] != expected:
                return {"valid": False, "reason": "result_row_payload_mismatch"}
        return {"valid": True, "recordCount": self.connection.execute("SELECT COUNT(*) FROM ledger_chain").fetchone()[0], "tailHash": previous}
