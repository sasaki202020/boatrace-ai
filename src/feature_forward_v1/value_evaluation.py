from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Iterable

FEATURE_GROUPS = {
    "course_and_start_exhibition": ["courseEntry", "startExhibition", "tilt", "bodyWeight"],
    "exhibition_time": ["exhibitionTime"],
    "weather_and_water": ["weather", "airTemp", "waterTemp", "windDirection", "windSpeed", "waveHeight"],
    "racer_recent_condition": ["racerRecentStarts", "racerRecentAvgSt"],
    "motor_boat_recent_condition": ["motorRecentRate", "boatRecentRate", "sampleCount"],
}

CONTRACT: dict[str, Any] = {
    "schemaVersion": 1,
    "featureGroups": FEATURE_GROUPS,
    "sources": ["approved_forward_pre_race_snapshot"],
    "cutoffRule": "fetchedAtJst < raceDeadlineJst and capture timestamp verified",
    "missingRule": "preserve missing with explicit reason; never impute after result",
    "dtypes": {
        "courseEntry": "integer",
        "startExhibition": "number",
        "exhibitionTime": "number",
        "tilt": "number",
        "bodyWeight": "number",
        "weather": "string",
        "airTemp": "number",
        "waterTemp": "number",
        "windDirection": "string",
        "windSpeed": "number",
        "waveHeight": "number",
        "racerRecentStarts": "integer",
        "racerRecentAvgSt": "number",
        "motorRecentRate": "number",
        "boatRecentRate": "number",
        "sampleCount": "integer",
    },
    "aggregationRule": "one feature group at a time; race rows remain grouped",
    "comparisonBaseline": "frozen tree_15 probabilities",
    "comparisonBaselineModelSha256": "a2f11bf69c1b4b7ea47cca847dbe0a46f076f7c08d3361ba9e30b43f12d65da0",
    "chronologicalFolds": {"method": "expanding_window", "foldCount": 5, "randomSplit": False},
    "evaluationMode": "chronological_oof_only",
    "primaryMetrics": ["race_log_loss_difference", "top1_accuracy_difference"],
    "secondaryMetrics": ["brier", "ece", "fold_stability", "venue_stability", "lane_stability"],
    "minimumCoverage": 0.8,
    "minimumForwardDays": 30,
    "minimumSettledRaces": 1500,
    "promotionConditions": [
        "log_loss_improves_in_at_least_4_of_5_folds",
        "top1_does_not_degrade",
        "95_percent_ci_has_no_material_degradation",
        "coverage_at_least_80_percent",
        "not_dependent_on_single_venue_month_or_lane",
        "leakage_audit_pass",
        "deterministic_rerun_pass",
        "timestamp_and_provenance_pass",
    ],
    "rejectionConditions": [
        "in_sample_only",
        "post_deadline_record_present",
        "target_column_present",
        "coverage_below_80_percent",
        "fewer_than_1500_settled_races",
        "fewer_than_30_forward_days",
    ],
    "productionAdoptionAllowed": False,
}

TARGET_TOKENS = ("target", "winner", "finish", "payout", "result", "refund", "着順", "払戻", "結果", "確定")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def contract_sha256(contract: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(contract).encode("utf-8")).hexdigest()


def verify_contract(contract: dict[str, Any], expected_sha256: str) -> None:
    if contract_sha256(contract) != expected_sha256:
        raise ValueError("feature_contract_hash_mismatch")
    folds = contract.get("chronologicalFolds", {})
    if (
        folds.get("method") != "expanding_window"
        or folds.get("foldCount") != 5
        or folds.get("randomSplit") is not False
        or contract.get("evaluationMode") != "chronological_oof_only"
    ):
        raise ValueError("chronological_oof_required")


def validate_evaluation_frame(rows: Iterable[dict[str, Any]]) -> None:
    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for column, nested in value.items():
                lowered = str(column).lower()
                if any(token in lowered for token in TARGET_TOKENS):
                    raise ValueError(f"target_column_prohibited:{column}")
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)
    for row in rows:
        visit(row)


def validate_settlement_manifest(rows: Iterable[dict[str, Any]], base_dir: Any = None) -> set[str]:
    seen: set[str] = set()
    eligible: set[str] = set()
    for row in rows:
        race_key = row.get("raceKey")
        if not isinstance(race_key, str) or not race_key:
            raise ValueError("settlement_race_key_missing")
        if race_key in seen:
            raise ValueError("settlement_duplicate")
        seen.add(race_key)
        if row.get("settled") is not True or row.get("eligible") is not True:
            continue
        if not HEX64.fullmatch(str(row.get("resultProvenanceSha256", ""))):
            raise ValueError("settlement_provenance_invalid")
        source_path = row.get("resultSourcePath")
        source_hash = row.get("resultSourceSha256")
        settled_at = row.get("settledAt")
        if base_dir is None or not isinstance(source_path, str) or not HEX64.fullmatch(str(source_hash or "")) or not isinstance(settled_at, str):
            raise ValueError("settlement_evidence_invalid")
        try:
            if datetime.fromisoformat(settled_at).tzinfo is None:
                raise ValueError
        except ValueError:
            raise ValueError("settlement_timestamp_invalid")
        source = (base_dir / source_path).resolve()
        root = base_dir.resolve()
        if not source.is_relative_to(root) or not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != source_hash:
            raise ValueError("settlement_source_hash_mismatch")
        try:
            result_payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise ValueError("settlement_source_content_invalid")
        if (
            not isinstance(result_payload, dict)
            or result_payload.get("raceKey") != race_key
            or result_payload.get("settled") is not True
            or type(result_payload.get("winnerBoatNo")) is not int
            or not 1 <= result_payload["winnerBoatNo"] <= 6
        ):
            raise ValueError("settlement_race_binding_invalid")
        expected = contract_sha256({"raceKey": race_key, "resultSourceSha256": source_hash, "settledAt": settled_at})
        if expected != row["resultProvenanceSha256"]:
            raise ValueError("settlement_provenance_invalid")
        if row.get("resultConflict") is True:
            raise ValueError("settlement_conflict")
        eligible.add(race_key)
    return eligible


def validate_schedule_manifest(manifest: dict[str, Any], base_dir: Any, trusted_receipt_verifier: Any = None) -> list[dict[str, Any]]:
    races = manifest.get("races")
    if not isinstance(races, list) or manifest.get("externalTimestampVerified") is not True:
        raise ValueError("schedule_provenance_invalid")
    source = (base_dir / str(manifest.get("sourcePath", ""))).resolve()
    receipt = (base_dir / str(manifest.get("anchorReceiptPath", ""))).resolve()
    root = base_dir.resolve()
    if not source.is_relative_to(root) or not receipt.is_relative_to(root) or not source.is_file() or not receipt.is_file():
        raise ValueError("schedule_provenance_invalid")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    receipt_hash = hashlib.sha256(receipt.read_bytes()).hexdigest()
    if source_hash != manifest.get("sourceSha256") or receipt_hash != manifest.get("anchorReceiptSha256"):
        raise ValueError("schedule_source_hash_mismatch")
    if json.loads(source.read_text(encoding="utf-8")) != races:
        raise ValueError("schedule_source_content_mismatch")
    race_set_hash = contract_sha256(races)
    receipt_payload = json.loads(receipt.read_text(encoding="utf-8"))
    if receipt_payload.get("contentSha256") != race_set_hash:
        raise ValueError("schedule_anchor_mismatch")
    if trusted_receipt_verifier is None or trusted_receipt_verifier(receipt_payload) is not True:
        raise ValueError("schedule_external_verifier_unavailable")
    expected = contract_sha256({"sourceSha256": source_hash, "raceSetSha256": race_set_hash, "anchorReceiptSha256": receipt_hash})
    if manifest.get("scheduleProvenanceSha256") != expected:
        raise ValueError("schedule_provenance_invalid")
    keys = set()
    for row in races:
        key = (row.get("raceDate"), row.get("jcd"), row.get("raceNo"))
        if key in keys or not all(key):
            raise ValueError("schedule_race_invalid")
        keys.add(key)
    return races


def validate_chronological_oof(rows: Iterable[dict[str, Any]], artifact_root: Any) -> None:
    by_fold: dict[int, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: {"train": [], "validation": []})
    artifact_identity: tuple[str, str] | None = None
    for row in rows:
        if (
            row.get("predictionMode") != "OOF"
            or row.get("baselineId") != "tree_15"
            or row.get("baselineModelSha256") != CONTRACT["comparisonBaselineModelSha256"]
            or not HEX64.fullmatch(str(row.get("baselinePredictionSha256", "")))
        ):
            raise ValueError("oof_contract_invalid")
        path_value = row.get("baselinePredictionArtifactPath")
        digest = row.get("baselinePredictionSha256")
        if not isinstance(path_value, str):
            raise ValueError("oof_prediction_artifact_invalid")
        artifact = (artifact_root / path_value).resolve()
        root = artifact_root.resolve()
        if not artifact.is_relative_to(root) or not artifact.is_file() or hashlib.sha256(artifact.read_bytes()).hexdigest() != digest:
            raise ValueError("oof_prediction_artifact_invalid")
        identity = (str(artifact), digest)
        if artifact_identity is not None and identity != artifact_identity:
            raise ValueError("oof_prediction_artifact_inconsistent")
        artifact_identity = identity
        split = row.get("split")
        fold = row.get("fold")
        if split not in {"train", "validation"} or type(fold) is not int:
            raise ValueError("oof_contract_invalid")
        try:
            date.fromisoformat(row.get("raceDate"))
        except (TypeError, ValueError):
            raise ValueError("oof_date_invalid")
        by_fold[fold][split].append(row)
    if set(by_fold) != set(range(1, 6)):
        raise ValueError("oof_fold_contract_invalid")
    prior_train_keys: set[Any] = set()
    validation_keys_all: set[Any] = set()
    prior_validation_start: str | None = None
    prior_validation_keys: set[Any] = set()
    for fold in range(1, 6):
        splits = by_fold[fold]
        train = splits["train"]
        validation = splits["validation"]
        train_keys = {row.get("raceKey") for row in train}
        validation_keys = {row.get("raceKey") for row in validation}
        if len(train_keys) != len(train) or len(validation_keys) != len(validation):
            raise ValueError("oof_duplicate_row")
        if train_keys & validation_keys:
            raise ValueError("oof_race_overlap")
        if validation_keys & validation_keys_all:
            raise ValueError("oof_validation_duplicate")
        if not train or not validation or max(row["raceDate"] for row in train) >= min(row["raceDate"] for row in validation):
            raise ValueError("oof_chronology_invalid")
        validation_start = min(row["raceDate"] for row in validation)
        if prior_validation_start is not None and validation_start <= prior_validation_start:
            raise ValueError("oof_chronology_invalid")
        if prior_train_keys and (not prior_train_keys.issubset(train_keys) or len(train_keys) <= len(prior_train_keys)):
            raise ValueError("oof_not_expanding_window")
        if prior_validation_keys and not prior_validation_keys.issubset(train_keys):
            raise ValueError("oof_not_expanding_window")
        prior_train_keys = train_keys
        validation_keys_all.update(validation_keys)
        prior_validation_keys = validation_keys
        prior_validation_start = validation_start


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _distribution(rows: list[dict[str, Any]], key: str) -> str:
    counts = Counter(str(row.get(key, "UNKNOWN")) for row in rows)
    return _canonical(dict(sorted(counts.items())))


def _consecutive_days(values: Iterable[str]) -> int:
    parsed = sorted({date.fromisoformat(value) for value in values})
    if not parsed:
        return 0
    count = 1
    for current, following in zip(reversed(parsed[:-1]), reversed(parsed[1:])):
        if following - current != timedelta(days=1):
            break
        count += 1
    return count


def _segment_coverage(
    schedule: list[dict[str, Any]] | None,
    verified_keys: set[tuple[Any, Any, Any]],
    field: str,
) -> str:
    if schedule is None:
        return _canonical({})
    denominators: Counter[str] = Counter()
    numerators: Counter[str] = Counter()
    for row in schedule:
        segment = str(row.get(field, "UNKNOWN"))
        key = (row.get("raceDate"), row.get("jcd"), row.get("raceNo"))
        denominators[segment] += 1
        if key in verified_keys:
            numerators[segment] += 1
    return _canonical({key: numerators[key] / value for key, value in sorted(denominators.items()) if value})


def _low_variance_columns(rows: list[dict[str, Any]], columns: list[str]) -> tuple[list[str], list[str]]:
    constants: list[str] = []
    low_variance: list[str] = []
    for column in columns:
        values = [row.get("values", {}).get(column) for row in rows if row.get("values", {}).get(column) is not None]
        unique = {json.dumps(value, ensure_ascii=False, sort_keys=True) for value in values}
        if values and len(unique) == 1:
            constants.append(column)
        elif values and len(unique) / len(values) <= 0.01:
            low_variance.append(column)
    return constants, low_variance


def complete_verified_race_keys(records: Iterable[dict[str, Any]], group: str) -> set[tuple[Any, Any, Any]]:
    grouped: dict[tuple[Any, Any, Any], list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        if row.get("featureGroup") == group:
            grouped[(row.get("raceDate"), row.get("jcd"), row.get("raceNo"))].append(row)
    return {
        key
        for key, race_rows in grouped.items()
        if {row.get("boatNo") for row in race_rows} == set(range(1, 7))
        and all(
            row.get("researchEligible") is True
            and row.get("captureTimestampVerified") is True
            and row.get("provenanceVerified") is True
            and row.get("schemaVerified") is True
            and (row.get("secondsBeforeDeadline") or 0) > 0
            and row.get("parseStatus") == "ok"
            for row in race_rows
        )
    }


def canonical_race_key(key: tuple[Any, Any, Any]) -> str:
    race_date, jcd, race_no = key
    return f"{race_date}-{jcd}-{int(race_no):02d}"


def build_collection_quality(
    records: Iterable[dict[str, Any]],
    scheduled_races: Iterable[dict[str, Any]] | None = None,
    external_anchor_count: int = 0,
    existing_missing_patterns: dict[str, set[str]] | None = None,
) -> dict[str, dict[str, Any]]:
    rows = list(records)
    validate_evaluation_frame(rows)
    schedule = list(scheduled_races) if scheduled_races is not None else None
    scheduled_keys = {
        (row.get("raceDate"), row.get("jcd"), row.get("raceNo")) for row in schedule or []
    }
    scheduled_count = len(scheduled_keys) if schedule is not None else None
    output: dict[str, dict[str, Any]] = {}
    for group, columns in FEATURE_GROUPS.items():
        group_rows = [row for row in rows if row.get("featureGroup") == group]
        race_keys = {(row.get("raceDate"), row.get("jcd"), row.get("raceNo")) for row in group_rows}
        verified_keys = complete_verified_race_keys(group_rows, group)
        missing = sum(
            1
            for row in group_rows
            for column in columns
            if row.get("values", {}).get(column) is None
        )
        possible = len(group_rows) * len(columns)
        missing_reasons = Counter(
            str(row.get("missingReason")) for row in group_rows if row.get("missingReason")
        )
        timestamp_verified = sum(row.get("captureTimestampVerified") is True for row in group_rows)
        provenance_verified = sum(row.get("provenanceVerified") is True for row in group_rows)
        schema_verified = sum(row.get("schemaVerified") is True for row in group_rows)
        post_deadline = sum((row.get("secondsBeforeDeadline") or 0) <= 0 for row in group_rows)
        parser_failures = sum(row.get("parseStatus") != "ok" for row in group_rows)
        schema_drift = sum("SCHEMA_DRIFT" in row.get("reasons", []) for row in group_rows)
        duplicates = sum(row.get("duplicate") is True for row in group_rows)
        result_leakage = sum("RESULT_LEAKAGE" in row.get("reasons", []) for row in group_rows)
        dates = sorted({str(key[0]) for key in verified_keys})
        constants, low_variance = _low_variance_columns(group_rows, columns)
        missing_keys = {
            _canonical([row.get("raceDate"), row.get("jcd"), row.get("raceNo"), row.get("boatNo")])
            for row in group_rows
            if any(row.get("values", {}).get(column) is None for column in columns)
        }
        existing = (existing_missing_patterns or {}).get(group)
        overlap = None
        if existing is not None:
            union = missing_keys | existing
            overlap = len(missing_keys & existing) / len(union) if union else 1.0
        output[group] = {
            "scheduledRaceCount": scheduled_count,
            "capturedRaceCount": len(race_keys),
            "verifiedPreDeadlineCount": len(verified_keys),
            "coverage": _ratio(len(verified_keys & scheduled_keys), scheduled_count or 0) if scheduled_count is not None else None,
            "missingRate": _ratio(missing, possible),
            "missingReasons": dict(sorted(missing_reasons.items())),
            "captureTimestampVerifiedRate": _ratio(timestamp_verified, len(group_rows)),
            "provenanceVerifiedRate": _ratio(provenance_verified, len(group_rows)),
            "schemaConsistencyRate": _ratio(schema_verified, len(group_rows)),
            "deterministicParsing": bool(group_rows) and parser_failures == 0 and schema_drift == 0,
            "parserFailureCount": parser_failures,
            "schemaDriftCount": schema_drift,
            "duplicateCount": duplicates,
            "postDeadlineCount": post_deadline,
            "resultLeakageCount": result_leakage,
            "externalAnchorRate": _ratio(external_anchor_count, len(race_keys)),
            "consecutiveCollectionDays": _consecutive_days(dates),
            "existingFeatureMissingPatternOverlap": overlap,
            "constantColumns": constants,
            "lowVarianceColumns": low_variance,
            "coverageByVenue": _segment_coverage(schedule, verified_keys, "jcd"),
            "coverageByRaceNo": _segment_coverage(schedule, verified_keys, "raceNo"),
            "coverageByTimeBand": _segment_coverage(schedule, verified_keys, "timeBand"),
        }
    return output


def _priority(entry: dict[str, Any]) -> tuple[int, str]:
    if entry["capturedRaceCount"] == 0:
        return 1, "現状では研究利用不可"
    if entry["resultLeakageCount"]:
        return 1, "result leakage riskにより研究利用不可"
    if (
        entry["postDeadlineCount"]
        or entry["schemaDriftCount"]
        or entry["parserFailureCount"]
        or entry["captureTimestampVerifiedRate"] != 1.0
        or entry["provenanceVerifiedRate"] != 1.0
        or entry["schemaConsistencyRate"] != 1.0
        or not entry["deterministicParsing"]
    ):
        return 2, "schemaまたはtimestampに問題あり"
    coverage = entry["coverage"]
    if coverage is None or entry["consecutiveCollectionDays"] < 30:
        return 3, "収集中・証拠不足"
    if coverage < 0.8 or (entry["missingRate"] or 0) > 0.2:
        return 4, "取得安定・一部欠損あり"
    if entry["existingFeatureMissingPatternOverlap"] is not None and entry["existingFeatureMissingPatternOverlap"] >= 0.8:
        return 4, "取得安定・既存featureとの欠損重複が高い"
    return 5, "取得安定・高coverage・低重複"


def build_priority_markdown(quality: dict[str, dict[str, Any]]) -> str:
    lines = [
        "# Feature Collection Priority",
        "",
        "この星評価は収集品質と研究優先度を示し、予測精度への効果や勝率改善を示しません。",
        "",
        "| Feature group | Priority | Reason | Coverage | Days |",
        "|---|---|---|---:|---:|",
    ]
    for group in FEATURE_GROUPS:
        stars, reason = _priority(quality[group])
        coverage = quality[group]["coverage"]
        coverage_text = "UNKNOWN" if coverage is None else f"{coverage:.3f}"
        lines.append(f"| {group} | {'★' * stars}{'☆' * (5 - stars)} | {reason} | {coverage_text} | {quality[group]['consecutiveCollectionDays']} |")
    return "\n".join(lines) + "\n"


def predictive_value_gate(
    quality: dict[str, dict[str, Any]],
    settled_races: int,
    oof_rows: Iterable[dict[str, Any]] | None = None,
    oof_artifact_root: Any = None,
    target_groups: Iterable[str] | None = None,
) -> dict[str, Any]:
    reasons: list[str] = []
    groups = list(target_groups) if target_groups is not None else list(FEATURE_GROUPS)
    if not groups or any(group not in FEATURE_GROUPS for group in groups):
        raise ValueError("feature_gate_target_group_invalid")
    observed_days = min((quality[group].get("consecutiveCollectionDays", 0) for group in groups), default=0)
    if settled_races < CONTRACT["minimumSettledRaces"]:
        reasons.append("minimum_settled_races_not_met")
    for group in groups:
        entry = quality[group]
        if entry.get("consecutiveCollectionDays", 0) < CONTRACT["minimumForwardDays"]:
            reasons.append(f"minimum_forward_days_not_met:{group}")
        coverage = entry.get("coverage")
        if coverage is None or coverage < CONTRACT["minimumCoverage"]:
            reasons.append(f"minimum_coverage_not_met:{group}")
        if entry.get("postDeadlineCount", 0):
            reasons.append(f"post_deadline_records_present:{group}")
        if entry.get("captureTimestampVerifiedRate") != 1.0:
            reasons.append(f"timestamp_not_fully_verified:{group}")
        if entry.get("provenanceVerifiedRate") != 1.0:
            reasons.append(f"provenance_not_fully_verified:{group}")
        if entry.get("resultLeakageCount", 0):
            reasons.append(f"result_leakage_present:{group}")
        if entry.get("schemaConsistencyRate") != 1.0 or not entry.get("deterministicParsing", False):
            reasons.append(f"deterministic_parsing_not_verified:{group}")
        if entry.get("duplicateCount", 0):
            reasons.append(f"duplicate_records_present:{group}")
    if not reasons:
        if oof_rows is None:
            reasons.append("chronological_oof_predictions_missing")
        else:
            if oof_artifact_root is None:
                reasons.append("oof_prediction_artifact_missing")
            else:
                validate_chronological_oof(oof_rows, oof_artifact_root)
    return {
        "status": "PREDICTIVE_VALUE_EVALUATION_BLOCKED" if reasons else "PREDICTIVE_VALUE_EVALUATION_READY",
        "predictiveEvidenceRanking": "DATA_SOURCE_NOT_READY" if reasons else "PROMISING_INSUFFICIENT_EVIDENCE",
        "blockedReasons": sorted(reasons),
        "settledRaceCount": settled_races,
        "minimumSettledRaces": CONTRACT["minimumSettledRaces"],
        "remainingSettledRaces": max(0, CONTRACT["minimumSettledRaces"] - settled_races),
        "minimumForwardDays": CONTRACT["minimumForwardDays"],
        "observedForwardDays": observed_days,
        "remainingForwardDays": max(0, CONTRACT["minimumForwardDays"] - observed_days),
        "targetFeatureGroups": groups,
        "targetEvaluationExecuted": False,
        "productionAdoptionAllowed": False,
    }
