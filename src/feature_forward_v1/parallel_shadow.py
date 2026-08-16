from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from src.feature_forward_v1.collector import LIVE_SCHEMA_SHA256, SCHEMA_SHA256
from src.feature_forward_v1.store import stable_hash

JST = ZoneInfo("Asia/Tokyo")
CHAMPION_ID = "tree_15"
CHAMPION_MODEL_SHA256 = "a2f11bf69c1b4b7ea47cca847dbe0a46f076f7c08d3361ba9e30b43f12d65da0"
FEATURE_SCHEMA_SHA256 = "a3853bdbdb75d13d4a596928c13eaa034307b14a5a8c534d31fca9acdab623dd"
EXPECTED_CONFIG_SHA256 = "8577a9bb63b49c215cec7984c72fef1c37d8d4edaab4ee2622569565a6a98756"
FEATURE_GROUP = "course_and_start_exhibition"
SHADOW_MODE = "PARALLEL_SHADOW_ONLY"
# The collector's verified capture window ends six minutes before deadline.
# Do not freeze a fallback before that window has had a chance to complete.
SHADOW_CAPTURE_READY_SECONDS = 360
RESULT_TOKENS = (
    "result", "winner", "finish", "payout", "refund", "actual",
    "着", "払戻", "結果", "確定",
)
FEATURE_COLUMNS = ("courseEntry", "startExhibition", "tilt", "bodyWeight")


class ParallelShadowError(ValueError):
    """Fail-closed error for the research-only parallel shadow path."""


@dataclass(frozen=True)
class FeatureSnapshot:
    snapshot_id: str
    captured_at_jst: str
    deadline_jst: str
    raw_sha256: str
    schema_sha256: str
    provenance_sha256: str
    values: dict[int, dict[str, Any]]


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_aware(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise ParallelShadowError(f"{field}_timestamp_missing")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ParallelShadowError(f"{field}_timestamp_invalid") from exc
    if parsed.tzinfo is None:
        raise ParallelShadowError(f"{field}_timestamp_naive")
    return parsed.astimezone(JST)


def _contains_result_token(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            any(token in str(key).lower() for token in RESULT_TOKENS)
            or _contains_result_token(nested)
            for key, nested in value.items()
        )
    if isinstance(value, list):
        return any(_contains_result_token(item) for item in value)
    return False


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ParallelShadowError(f"feature_value_invalid:{field}")
    value = float(value)
    if not math.isfinite(value):
        raise ParallelShadowError(f"feature_value_invalid:{field}")
    return value


def validate_config(config: dict[str, Any]) -> None:
    if config.get("schemaVersion") != 1:
        raise ParallelShadowError("parallel_shadow_config_schema_invalid")
    if config.get("challengerVersion") != "course_start_residual_shadow_v1":
        raise ParallelShadowError("parallel_shadow_version_invalid")
    if config.get("championId") != CHAMPION_ID:
        raise ParallelShadowError("parallel_shadow_champion_invalid")
    if config.get("championModelSha256") != CHAMPION_MODEL_SHA256:
        raise ParallelShadowError("parallel_shadow_model_hash_invalid")
    if config.get("featureSchemaSha256") != FEATURE_SCHEMA_SHA256:
        raise ParallelShadowError("parallel_shadow_feature_schema_invalid")
    if config.get("featureGroup") != FEATURE_GROUP:
        raise ParallelShadowError("parallel_shadow_feature_group_invalid")
    if config.get("productionAdoptionAllowed") is not False:
        raise ParallelShadowError("parallel_shadow_production_adoption_must_be_false")
    if config.get("prospectiveConnectionAllowed") is not False:
        raise ParallelShadowError("parallel_shadow_prospective_connection_must_be_false")
    if config.get("oofExecuted") is not False:
        raise ParallelShadowError("parallel_shadow_oof_must_be_false")
    if type(config.get("seed")) is not int:
        raise ParallelShadowError("parallel_shadow_seed_invalid")
    coefficients = config.get("coefficients")
    normalization = config.get("normalization")
    if not isinstance(coefficients, dict) or not isinstance(normalization, dict):
        raise ParallelShadowError("parallel_shadow_coefficients_invalid")
    for field in FEATURE_COLUMNS:
        if field not in coefficients or field not in normalization:
            raise ParallelShadowError(f"parallel_shadow_feature_missing:{field}")
        _finite_number(coefficients[field], field)
        scale = normalization[field].get("scale") if isinstance(normalization[field], dict) else None
        center = normalization[field].get("center") if isinstance(normalization[field], dict) else None
        if _finite_number(scale, f"{field}.scale") <= 0:
            raise ParallelShadowError(f"parallel_shadow_scale_invalid:{field}")
        _finite_number(center, f"{field}.center")
    residual_scale = _finite_number(config.get("residualScale"), "residualScale")
    residual_clip = _finite_number(config.get("residualClip"), "residualClip")
    if residual_scale < 0 or residual_clip <= 0:
        raise ParallelShadowError("parallel_shadow_residual_limits_invalid")


def load_fixed_config(path: Path) -> tuple[dict[str, Any], str]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ParallelShadowError("parallel_shadow_config_read_failed") from exc
    if not isinstance(config, dict):
        raise ParallelShadowError("parallel_shadow_config_object_required")
    validate_config(config)
    digest = sha256_file(path)
    if digest != EXPECTED_CONFIG_SHA256:
        raise ParallelShadowError("parallel_shadow_config_hash_mismatch")
    return config, digest


def validate_probabilities(values: Iterable[Any], field: str) -> list[float]:
    result = [float(value) for value in values]
    if len(result) != 6 or any(not math.isfinite(value) or value < 0 or value > 1 for value in result):
        raise ParallelShadowError(f"{field}_probability_contract_invalid")
    if not math.isclose(sum(result), 1.0, abs_tol=1e-8):
        raise ParallelShadowError(f"{field}_probability_sum_invalid")
    return result


def _validate_prediction_payload(payload: dict[str, Any], path: Path, now_jst: datetime) -> dict[str, Any] | None:
    body = dict(payload)
    saved_hash = body.pop("predictionSha256", None)
    if not isinstance(saved_hash, str) or sha256_json(body) != saved_hash:
        raise ParallelShadowError(f"prediction_hash_mismatch:{path.name}")
    for key in ("result", "winner", "finishPosition", "payout", "odds"):
        if key in body:
            raise ParallelShadowError(f"prediction_result_field_present:{path.name}:{key}")
    generated = _parse_aware(body.get("generatedAtJst"), "generatedAtJst")
    deadline = _parse_aware(body.get("deadlineJst"), "deadlineJst")
    if generated >= deadline:
        raise ParallelShadowError(f"prediction_not_pre_deadline:{path.name}")
    if deadline <= now_jst:
        return None
    if body.get("modelVersion") != CHAMPION_ID or body.get("modelSha256") != CHAMPION_MODEL_SHA256:
        raise ParallelShadowError(f"prediction_model_hash_mismatch:{path.name}")
    if body.get("featureSchemaVersion") != FEATURE_SCHEMA_SHA256:
        raise ParallelShadowError(f"prediction_feature_schema_mismatch:{path.name}")
    probabilities = body.get("probabilities")
    if not isinstance(probabilities, list) or len(probabilities) != 6:
        raise ParallelShadowError(f"prediction_probability_contract_invalid:{path.name}")
    by_boat: dict[int, float] = {}
    for item in probabilities:
        if not isinstance(item, dict) or type(item.get("boatNo")) is not int:
            raise ParallelShadowError(f"prediction_identity_invalid:{path.name}")
        boat_no = int(item["boatNo"])
        if boat_no in by_boat or not 1 <= boat_no <= 6:
            raise ParallelShadowError(f"prediction_identity_invalid:{path.name}")
        by_boat[boat_no] = float(item.get("probability"))
    normalized = validate_probabilities([by_boat[boat] for boat in range(1, 7)], "baseline")
    race_id = str(body.get("raceId") or "")
    if not race_id or not isinstance(body.get("raceDate"), str):
        raise ParallelShadowError(f"prediction_identity_invalid:{path.name}")
    return {
        "raceId": race_id,
        "raceDate": body["raceDate"],
        "venue": str(body.get("venue") or ""),
        "raceNo": int(body.get("raceNo")),
        "predictedAt": generated.isoformat(),
        "deadlineJst": deadline.isoformat(),
        "baselineProbabilities": normalized,
        "predictionSha256": saved_hash,
        "inputSha256": str(body.get("inputSha256") or ""),
    }


def load_future_predictions(prediction_root: Path, now: datetime) -> list[dict[str, Any]]:
    now_jst = now.astimezone(JST)
    predictions: dict[str, dict[str, Any]] = {}
    for path in sorted(prediction_root.rglob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ParallelShadowError(f"prediction_object_required:{path.name}")
        record = _validate_prediction_payload(payload, path, now_jst)
        if record is None:
            continue
        if record["raceId"] in predictions:
            raise ParallelShadowError(f"prediction_duplicate:{record['raceId']}")
        predictions[record["raceId"]] = record
    return [predictions[key] for key in sorted(predictions)]


def load_shadow_candidates(
    prediction_root: Path, now: datetime,
) -> tuple[list[dict[str, Any]], int]:
    """Return predictions whose pre-race feature capture window has closed.

    A missing snapshot is a valid fallback only after the collector had the
    full T-8 to T-6 minute window to produce it. Before then, leaving the
    race unrecorded preserves the chance to compare the challenger without
    rewriting an append-only fallback later.
    """
    future = load_future_predictions(prediction_root, now)
    now_jst = now.astimezone(JST)
    candidates: list[dict[str, Any]] = []
    skipped = 0
    for prediction in future:
        deadline = _parse_aware(prediction["deadlineJst"], "deadlineJst")
        seconds_to_deadline = (deadline - now_jst).total_seconds()
        if seconds_to_deadline > SHADOW_CAPTURE_READY_SECONDS:
            skipped += 1
            continue
        candidates.append(prediction)
    return candidates, skipped


def _snapshot_value_valid(values: dict[str, Any]) -> bool:
    if _contains_result_token(values) or set(values) != set(FEATURE_COLUMNS):
        return False
    try:
        course = _finite_number(values["courseEntry"], "courseEntry")
        start = _finite_number(values["startExhibition"], "startExhibition")
        tilt = _finite_number(values["tilt"], "tilt")
        weight = _finite_number(values["bodyWeight"], "bodyWeight")
    except ParallelShadowError:
        return False
    return (
        course.is_integer() and 1 <= course <= 6
        and -1 <= start <= 1
        and -1 <= tilt <= 3
        and 30 <= weight <= 100
    )


def load_feature_snapshot_index(feature_store: Path, now: datetime) -> dict[tuple[str, str, int], FeatureSnapshot]:
    """Read and integrity-check the feature store without writing to it."""
    from scripts.build_feature_value_evaluation_v1 import load_records_read_only

    # This validates raw hashes, feature hashes, and the source ledger chain.
    load_records_read_only(feature_store)
    database = feature_store / "feature_forward.sqlite3"
    if not database.is_file():
        return {}
    connection = sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT s.*, f.boat_no, f.feature_group, f.payload_json, f.parse_status, f.missing_reason "
            "FROM snapshots s JOIN feature_records f ON f.snapshot_id=s.snapshot_id "
            "WHERE f.feature_group=? ORDER BY s.race_date,s.jcd,s.race_no,s.snapshot_id,f.boat_no",
            (FEATURE_GROUP,),
        ).fetchall()
    finally:
        connection.close()
    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(str(row["snapshot_id"]), []).append(row)
    index: dict[tuple[str, str, int], FeatureSnapshot] = {}
    now_jst = now.astimezone(JST)
    for snapshot_id, snapshot_rows in grouped.items():
        first = snapshot_rows[0]
        if len(snapshot_rows) != 6 or {int(row["boat_no"]) for row in snapshot_rows} != set(range(1, 7)):
            continue
        if not bool(first["research_eligible"]) or not bool(first["capture_timestamp_verified"]):
            continue
        captured = _parse_aware(first["fetched_at_jst"], "featureCapturedAt")
        deadline = _parse_aware(first["deadline_jst"], "featureDeadline")
        if captured > now_jst or captured >= deadline or float(first["seconds_before_deadline"] or 0) <= 0:
            continue
        values: dict[int, dict[str, Any]] = {}
        valid = True
        for row in snapshot_rows:
            if row["parse_status"] != "ok" or row["missing_reason"]:
                valid = False
                break
            payload = json.loads(row["payload_json"])
            if not _snapshot_value_valid(payload):
                valid = False
                break
            values[int(row["boat_no"])] = payload
        if not valid:
            continue
        key = (str(first["race_date"]), str(first["jcd"]).zfill(2), int(first["race_no"]))
        if key in index:
            raise ParallelShadowError(f"feature_snapshot_ambiguous:{key[0]}-{key[1]}-{key[2]:02d}")
        index[key] = FeatureSnapshot(
            snapshot_id=snapshot_id,
            captured_at_jst=captured.isoformat(),
            deadline_jst=deadline.isoformat(),
            raw_sha256=str(first["raw_sha256"]),
            schema_sha256=str(first["schema_sha256"]),
            provenance_sha256=str(first["provenance_sha256"]),
            values=values,
        )
    return index


def _softmax(values: list[float]) -> list[float]:
    maximum = max(values)
    exponentials = [math.exp(value - maximum) for value in values]
    total = sum(exponentials)
    return [value / total for value in exponentials]


def challenger_probabilities(
    baseline: Iterable[Any], values: dict[int, dict[str, Any]], config: dict[str, Any],
) -> list[float]:
    baseline_values = validate_probabilities(baseline, "baseline")
    deltas: list[float] = []
    coefficients = config["coefficients"]
    normalization = config["normalization"]
    for boat_no in range(1, 7):
        feature = values.get(boat_no)
        if not isinstance(feature, dict) or not _snapshot_value_valid(feature):
            raise ParallelShadowError("challenger_feature_values_invalid")
        residual = 0.0
        for field in FEATURE_COLUMNS:
            spec = normalization[field]
            z = (float(feature[field]) - float(spec["center"])) / float(spec["scale"])
            residual += float(coefficients[field]) * z
        residual = max(-float(config["residualClip"]), min(float(config["residualClip"]), residual))
        deltas.append(float(config["residualScale"]) * residual)
    logits = [math.log(max(probability, 1e-15)) + delta for probability, delta in zip(baseline_values, deltas)]
    return validate_probabilities(_softmax(logits), "challenger")


def build_shadow_record(
    prediction: dict[str, Any], snapshot: FeatureSnapshot | None, *, config: dict[str, Any],
    config_sha256: str, code_commit: str, code_source_sha256: str = "UNAVAILABLE",
) -> dict[str, Any]:
    baseline = validate_probabilities(prediction["baselineProbabilities"], "baseline")
    fallback_reason = ""
    challenger = baseline
    feature_metadata: dict[str, Any] = {
        "featureSnapshotId": None,
        "featureCapturedAt": None,
        "featureRawSha256": None,
        "captureSchemaSha256": None,
        "provenanceSha256": None,
    }
    if snapshot is None:
        fallback_reason = "FEATURE_SNAPSHOT_UNAVAILABLE"
    else:
        try:
            challenger = challenger_probabilities(baseline, snapshot.values, config)
            feature_metadata = {
                "featureSnapshotId": snapshot.snapshot_id,
                "featureCapturedAt": snapshot.captured_at_jst,
                "featureRawSha256": snapshot.raw_sha256,
                "captureSchemaSha256": snapshot.schema_sha256,
                "provenanceSha256": snapshot.provenance_sha256,
            }
        except ParallelShadowError:
            fallback_reason = "FEATURE_VALUES_INVALID"
    record = {
        "schemaVersion": 1,
        "shadowMode": SHADOW_MODE,
        "raceId": prediction["raceId"],
        "raceDate": prediction["raceDate"],
        "venue": prediction["venue"],
        "raceNo": prediction["raceNo"],
        "predictedAt": prediction["predictedAt"],
        "deadlineJst": prediction["deadlineJst"],
        "featureGroup": FEATURE_GROUP,
        "featureSchemaSha256": FEATURE_SCHEMA_SHA256,
        "modelSha256": CHAMPION_MODEL_SHA256,
        "challengerVersion": config["challengerVersion"],
        "configSha256": config_sha256,
        "codeCommit": code_commit,
        "codeSourceSha256": code_source_sha256,
        "inputSha256": prediction["inputSha256"],
        "predictionSha256": prediction["predictionSha256"],
        "baselineProbabilities": baseline,
        "challengerProbabilities": challenger,
        "baselineTop1": int(max(range(6), key=lambda index: baseline[index])) + 1,
        "challengerTop1": int(max(range(6), key=lambda index: challenger[index])) + 1,
        **feature_metadata,
        "fallbackReason": fallback_reason,
        "leakageCheck": {"status": "PASS", "fields": 0},
        "productionAdoptionAllowed": False,
        "prospectiveConnectionAllowed": False,
        "oofExecuted": False,
    }
    if _contains_result_token(record["baselineProbabilities"]) or _contains_result_token(record["challengerProbabilities"]):
        raise ParallelShadowError("shadow_result_leakage")
    record["recordHash"] = sha256_json(record)
    return record


class ShadowLedger:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS shadow_predictions(
              race_id TEXT PRIMARY KEY,
              payload_json TEXT NOT NULL,
              record_hash TEXT NOT NULL UNIQUE,
              previous_hash TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ledger_chain(
              sequence INTEGER PRIMARY KEY AUTOINCREMENT,
              record_type TEXT NOT NULL,
              record_id TEXT NOT NULL UNIQUE,
              previous_hash TEXT NOT NULL,
              record_hash TEXT NOT NULL UNIQUE
            );
            CREATE TRIGGER IF NOT EXISTS shadow_no_update
              BEFORE UPDATE ON shadow_predictions BEGIN SELECT RAISE(ABORT,'append_only_update_prohibited'); END;
            CREATE TRIGGER IF NOT EXISTS shadow_no_delete
              BEFORE DELETE ON shadow_predictions BEGIN SELECT RAISE(ABORT,'append_only_delete_prohibited'); END;
            CREATE TRIGGER IF NOT EXISTS shadow_chain_no_update
              BEFORE UPDATE ON ledger_chain BEGIN SELECT RAISE(ABORT,'append_only_update_prohibited'); END;
            CREATE TRIGGER IF NOT EXISTS shadow_chain_no_delete
              BEFORE DELETE ON ledger_chain BEGIN SELECT RAISE(ABORT,'append_only_delete_prohibited'); END;
            """
        )
        self.connection.commit()

    def append_many(self, records: list[dict[str, Any]]) -> dict[str, int]:
        def compatible(saved: dict[str, Any], current: dict[str, Any]) -> bool:
            if saved == current:
                return True
            # The first live run predates codeSourceSha256. Accept that exact
            # legacy payload without rewriting it; all new rows carry the hash.
            if "codeSourceSha256" not in saved and "codeSourceSha256" in current:
                saved_unsigned = {
                    key: value for key, value in saved.items() if key != "recordHash"
                }
                current_unsigned = {
                    key: value for key, value in current.items()
                    if key not in {"codeSourceSha256", "recordHash"}
                }
                return (
                    saved_unsigned == current_unsigned
                    and saved.get("recordHash") == sha256_json(saved_unsigned)
                )
            return False

        existing: dict[str, sqlite3.Row] = {}
        for record in records:
            row = self.connection.execute(
                "SELECT payload_json,record_hash FROM shadow_predictions WHERE race_id=?",
                (record["raceId"],),
            ).fetchone()
            if row:
                existing[record["raceId"]] = row
                saved = json.loads(row["payload_json"])
                if not compatible(saved, record) or row["record_hash"] != saved.get("recordHash"):
                    raise ParallelShadowError(f"shadow_record_conflict:{record['raceId']}")
        created = 0
        idempotent = len(existing)
        with self.connection:
            row = self.connection.execute(
                "SELECT record_hash FROM ledger_chain ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            previous = str(row[0]) if row else "0" * 64
            for record in records:
                if record["raceId"] in existing:
                    continue
                chain_hash = sha256_json({
                    "type": "shadow_prediction",
                    "id": record["raceId"],
                    "payloadHash": record["recordHash"],
                    "previousHash": previous,
                })
                self.connection.execute(
                    "INSERT INTO shadow_predictions(race_id,payload_json,record_hash,previous_hash) VALUES(?,?,?,?)",
                    (record["raceId"], canonical_json(record), record["recordHash"], previous),
                )
                self.connection.execute(
                    "INSERT INTO ledger_chain(record_type,record_id,previous_hash,record_hash) VALUES(?,?,?,?)",
                    ("shadow_prediction", record["raceId"], previous, chain_hash),
                )
                previous = chain_hash
                created += 1
        return {"created": created, "idempotent": idempotent}

    def verify_integrity(self) -> dict[str, Any]:
        previous = "0" * 64
        count = 0
        for row in self.connection.execute("SELECT * FROM ledger_chain ORDER BY sequence"):
            if row["record_type"] != "shadow_prediction" or row["previous_hash"] != previous:
                return {"valid": False, "reason": "chain_link"}
            source = self.connection.execute(
                "SELECT payload_json,record_hash,previous_hash FROM shadow_predictions WHERE race_id=?",
                (row["record_id"],),
            ).fetchone()
            if not source or source["record_hash"] != row["record_hash"]:
                # The chain hash is deliberately separate from the record hash.
                expected = sha256_json({
                    "type": "shadow_prediction",
                    "id": row["record_id"],
                    "payloadHash": source["record_hash"] if source else None,
                    "previousHash": previous,
                }) if source else None
                if expected != row["record_hash"]:
                    return {"valid": False, "reason": "chain_record_missing"}
            payload = json.loads(source["payload_json"])
            saved_record_hash = payload.pop("recordHash", None)
            if saved_record_hash != source["record_hash"] or sha256_json(payload) != saved_record_hash:
                return {"valid": False, "reason": "payload_hash"}
            chain_hash = sha256_json({
                "type": "shadow_prediction",
                "id": row["record_id"],
                "payloadHash": source["record_hash"],
                "previousHash": previous,
            })
            if chain_hash != row["record_hash"]:
                return {"valid": False, "reason": "chain_hash"}
            previous = row["record_hash"]
            count += 1
        shadow_count = int(self.connection.execute("SELECT COUNT(*) FROM shadow_predictions").fetchone()[0])
        return {"valid": count == shadow_count, "recordCount": count, "tailHash": previous}

    def close(self) -> None:
        self.connection.close()


def run_parallel_shadow(
    *, prediction_root: Path, feature_store: Path, shadow_root: Path, model_artifact: Path,
    config_path: Path, code_commit: str, now: datetime, code_source_sha256: str = "UNAVAILABLE",
) -> dict[str, Any]:
    prediction_root = prediction_root.resolve()
    feature_store = feature_store.resolve()
    shadow_root = shadow_root.resolve()
    paths = (prediction_root, feature_store, shadow_root)
    for left in paths:
        for right in paths:
            if left != right and (left in right.parents or right in left.parents):
                raise ParallelShadowError("parallel_shadow_output_not_separate")
    config, config_sha256 = load_fixed_config(config_path.resolve())
    if sha256_file(model_artifact.resolve()) != CHAMPION_MODEL_SHA256:
        raise ParallelShadowError("champion_model_hash_mismatch")
    predictions, skipped_before_capture_window = load_shadow_candidates(prediction_root, now)
    snapshot_index = load_feature_snapshot_index(feature_store, now)
    records: list[dict[str, Any]] = []
    fallback_count = 0
    for prediction in predictions:
        key = (prediction["raceDate"], prediction["venue"].zfill(2), prediction["raceNo"])
        record = build_shadow_record(
            prediction,
            snapshot_index.get(key),
            config=config,
            config_sha256=config_sha256,
            code_commit=code_commit,
            code_source_sha256=code_source_sha256,
        )
        fallback_count += int(bool(record["fallbackReason"]))
        records.append(record)
    ledger = ShadowLedger(shadow_root / "parallel_shadow.sqlite3")
    try:
        write_result = ledger.append_many(records)
        integrity = ledger.verify_integrity()
    finally:
        ledger.close()
    if not integrity.get("valid"):
        raise ParallelShadowError("parallel_shadow_ledger_integrity_failed")
    return {
        "status": "PARALLEL_SHADOW_READY" if write_result["created"] or write_result["idempotent"] else "WAITING_FOR_NEXT_PRE_RACE",
        "shadowMode": SHADOW_MODE,
        "challengerVersion": config["challengerVersion"],
        "configSha256": config_sha256,
        "codeCommit": code_commit,
        "codeSourceSha256": code_source_sha256,
        "modelSha256": CHAMPION_MODEL_SHA256,
        "featureSchemaSha256": FEATURE_SCHEMA_SHA256,
        "created": write_result["created"],
        "idempotent": write_result["idempotent"],
        "fallbackCount": fallback_count,
        "predictionCandidates": len(predictions),
        "skippedBeforeCaptureWindow": skipped_before_capture_window,
        "productionWrites": 0,
        "prospectiveWrites": 0,
        "bettingActions": 0,
        "paymentActions": 0,
        "oofExecuted": False,
        "productionAdoptionAllowed": False,
        "ledgerIntegrity": integrity,
    }
