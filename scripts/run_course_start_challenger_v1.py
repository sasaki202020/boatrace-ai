from __future__ import annotations

import argparse
import hashlib
import json
import sys
import sqlite3
from datetime import date, datetime, timedelta
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.feature_forward_v1.course_start_challenger import (
    CHAMPION_MODEL_SHA256,
    FEATURE_GROUP,
    build_course_start_race_rows,
    build_readiness_report,
    candidate_prediction_digest,
    evaluate_course_start_challenger,
    result_digest,
)
from src.feature_forward_v1.store import stable_hash
from src.feature_forward_v1.value_evaluation import (
    build_collection_quality,
    complete_verified_race_keys,
)
from src.feature_forward_v1.oof_readiness import (
    build_fold_preflight,
    build_oof_preflight,
    load_oof_spec,
    oof_execution_allowed,
)
from src.feature_forward_v1.course_start_contract import build_course_start_contract_audit
from src.commercialization_v2.day1_readiness import validate_runtime_bfile
from scripts.run_local_prediction_settlement_v1 import (
    JST,
    build_runtime_input_contract,
)
from scripts.build_feature_value_evaluation_v1 import (
    load_records_read_only,
    write_collection_quality_reports,
)
from scripts.build_oof_reproducibility_manifest_v1 import (
    write_reproducibility_manifest,
)

FEATURE_SCHEMA_SHA256 = "a3853bdbdb75d13d4a596928c13eaa034307b14a5a8c534d31fca9acdab623dd"
DEFAULT_OOF_SPEC = ROOT / "config" / "feature_forward_v1" / "oof_evaluation_spec.json"
DEFAULT_RUNTIME_ROOT = ROOT.parents[1] / "競艇" / "boatrace-ai-mvp"
CAPTURE_WINDOW_CLOSES_SECONDS_BEFORE_DEADLINE = 360
COVERAGE_DEFINITION = "mature_selected_capture_window_passed"
EVALUATION_ARTIFACT_SCHEMA_VERSION = 1


def _as_date(value: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid_race_date:{value}") from exc


def derive_assessment_dates(
    feature_keys: list[tuple[str, str, int]] | set[tuple[str, str, int]],
    scope_dates: set[str],
) -> list[str]:
    """Return the trailing consecutive calendar dates in the fixed scope."""
    feature_dates = {str(key[0]) for key in feature_keys}
    candidates = feature_dates | {str(value) for value in scope_dates}
    if not candidates:
        return []
    end = max((_as_date(value) for value in candidates))
    candidate_dates = {_as_date(value) for value in candidates}
    start = end
    while start - timedelta(days=1) in candidate_dates:
        start -= timedelta(days=1)
    return [
        (start + timedelta(days=offset)).isoformat()
        for offset in range((end - start).days + 1)
    ]


def build_evaluation_cohort_manifest(
    *,
    assessment_dates: list[str],
    joined_race_keys: list[str],
    source_files: list[dict[str, Any]],
    model_sha256: str,
    feature_schema_sha256: str,
) -> dict[str, Any]:
    """Build the immutable input digest used to decide whether OOF may run."""
    normalized_dates = sorted({_as_date(value).isoformat() for value in assessment_dates})
    if not normalized_dates:
        raise ValueError("evaluation_cohort_empty")
    start = _as_date(normalized_dates[0])
    end = _as_date(normalized_dates[-1])
    expected_dates = [
        (start + timedelta(days=offset)).isoformat()
        for offset in range((end - start).days + 1)
    ]
    payload: dict[str, Any] = {
        "schemaVersion": 1,
        "featureGroup": FEATURE_GROUP,
        "cohortStart": normalized_dates[0],
        "cohortEnd": normalized_dates[-1],
        "assessmentDates": normalized_dates,
        "missingAssessmentDates": sorted(set(expected_dates) - set(normalized_dates)),
        "scheduleSourceFiles": sorted(source_files, key=lambda item: json.dumps(item, sort_keys=True)),
        "joinedRaceCount": len(set(joined_race_keys)),
        "joinedRaceDigest": stable_hash(sorted(set(joined_race_keys))),
        "modelSha256": model_sha256,
        "featureSchemaSha256": feature_schema_sha256,
    }
    payload["cohortDigest"] = stable_hash(payload)
    return payload


def cohort_manifest_matches(existing: dict[str, Any], current: dict[str, Any]) -> bool:
    """Validate both manifests and require the exact same immutable digest."""
    def valid_digest(manifest: dict[str, Any]) -> bool:
        digest = manifest.get("cohortDigest")
        body = {key: value for key, value in manifest.items() if key != "cohortDigest"}
        return isinstance(digest, str) and digest == stable_hash(body)

    return valid_digest(existing) and valid_digest(current) and (
        existing.get("cohortDigest") == current.get("cohortDigest")
    )


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"json_read_failed:{path.name}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"json_object_required:{path.name}")
    return value


def _verify_prediction(path: Path) -> dict[str, Any]:
    payload = _json(path)
    saved_hash = payload.pop("predictionSha256", None)
    if not isinstance(saved_hash, str) or stable_hash(payload) != saved_hash:
        raise ValueError(f"prediction_hash_mismatch:{path.name}")
    generated_at = payload.get("generatedAtJst")
    deadline_at = payload.get("deadlineJst")
    if not isinstance(generated_at, str) or not isinstance(deadline_at, str):
        raise ValueError(f"prediction_timing_invalid:{path.name}")
    try:
        generated_at_value = datetime.fromisoformat(generated_at)
        deadline_at_value = datetime.fromisoformat(deadline_at)
    except ValueError as exc:
        raise ValueError(f"prediction_timing_invalid:{path.name}") from exc
    if (
        generated_at_value.tzinfo is None
        or deadline_at_value.tzinfo is None
        or generated_at_value >= deadline_at_value
    ):
        raise ValueError(f"prediction_timing_invalid:{path.name}")
    if payload.get("modelVersion") != "tree_15" or payload.get("modelSha256") != CHAMPION_MODEL_SHA256:
        raise ValueError(f"prediction_model_hash_mismatch:{path.name}")
    if payload.get("featureSchemaVersion") != FEATURE_SCHEMA_SHA256:
        raise ValueError(f"prediction_schema_hash_mismatch:{path.name}")
    probabilities = payload.get("probabilities")
    if not isinstance(probabilities, list) or len(probabilities) != 6:
        raise ValueError(f"prediction_probability_contract_invalid:{path.name}")
    by_boat: dict[int, float] = {}
    ranks: set[int] = set()
    for item in probabilities:
        if not isinstance(item, dict) or type(item.get("boatNo")) is not int or type(item.get("rank")) is not int:
            raise ValueError(f"prediction_probability_contract_invalid:{path.name}")
        boat_no = int(item["boatNo"])
        rank = int(item["rank"])
        probability = float(item.get("probability"))
        if boat_no in by_boat or not 1 <= boat_no <= 6 or rank in ranks or not 1 <= rank <= 6:
            raise ValueError(f"prediction_probability_contract_invalid:{path.name}")
        if probability < 0 or probability > 1 or not __import__("math").isfinite(probability):
            raise ValueError(f"prediction_probability_contract_invalid:{path.name}")
        by_boat[boat_no] = probability
        ranks.add(rank)
    if set(by_boat) != set(range(1, 7)) or not __import__("math").isclose(sum(by_boat.values()), 1.0, abs_tol=1e-8):
        raise ValueError(f"prediction_probability_contract_invalid:{path.name}")
    race_id = str(payload.get("raceId") or "")
    if not race_id or not isinstance(payload.get("raceDate"), str):
        raise ValueError(f"prediction_identity_invalid:{path.name}")
    return {
        "raceKey": race_id,
        "raceDate": payload["raceDate"],
        "venue": str(payload.get("venue") or ""),
        "raceNo": int(payload.get("raceNo")),
        "generatedAtJst": generated_at,
        "deadlineJst": deadline_at,
        "predictionSha256": saved_hash,
        "probabilities": [by_boat[boat_no] for boat_no in range(1, 7)],
    }


def _verify_settlement(path: Path) -> dict[str, Any]:
    payload = _json(path)
    saved_hash = payload.pop("settlementSha256", None)
    if not isinstance(saved_hash, str) or stable_hash(payload) != saved_hash:
        raise ValueError(f"settlement_hash_mismatch:{path.name}")
    race_id = str(payload.get("raceId") or "")
    if not race_id or not isinstance(payload.get("predictionSha256"), str):
        raise ValueError(f"settlement_identity_invalid:{path.name}")
    if str(payload.get("settlementStatus") or "settled").lower() == "void":
        return {"raceKey": race_id, "void": True, "settlementSha256": saved_hash}
    winner = payload.get("winnerBoat")
    if type(winner) is not int or not 1 <= winner <= 6:
        raise ValueError(f"settlement_winner_invalid:{path.name}")
    return {
        "raceKey": race_id,
        "raceDate": payload.get("raceDate"),
        "winnerBoat": winner,
        "predictionSha256": payload["predictionSha256"],
        "void": False,
        "settlementSha256": saved_hash,
    }


def load_prediction_settlement_records(
    prediction_root: Path, settlement_root: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    predictions: dict[str, dict[str, Any]] = {}
    for path in sorted(prediction_root.rglob("*.json")):
        record = _verify_prediction(path)
        if record["raceKey"] in predictions:
            raise ValueError(f"prediction_duplicate:{record['raceKey']}")
        predictions[record["raceKey"]] = record
    settlements: dict[str, dict[str, Any]] = {}
    for path in sorted(settlement_root.rglob("*.json")):
        record = _verify_settlement(path)
        if record["raceKey"] in settlements:
            raise ValueError(f"settlement_duplicate:{record['raceKey']}")
        settlements[record["raceKey"]] = record
    for race_key, settlement in settlements.items():
        prediction = predictions.get(race_key)
        if prediction is None or settlement.get("void"):
            continue
        if settlement.get("predictionSha256") != prediction["predictionSha256"]:
            raise ValueError(f"settlement_prediction_hash_mismatch:{race_key}")
    return predictions, settlements


def _feature_rows(records: list[dict[str, Any]]) -> dict[tuple[str, str, int], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        if row.get("featureGroup") != FEATURE_GROUP:
            continue
        grouped[(str(row.get("raceDate")), str(row.get("jcd")), int(row.get("raceNo")))].append({
            "boatNo": int(row["boatNo"]),
            "values": dict(row.get("values", {})),
            "secondsBeforeDeadline": row.get("secondsBeforeDeadline"),
            "captureTimestampVerified": row.get("captureTimestampVerified"),
            "provenanceVerified": row.get("provenanceVerified"),
            "schemaVerified": row.get("schemaVerified"),
            "researchEligible": row.get("researchEligible"),
        })
    return grouped


def build_joined_race_rows(
    predictions: dict[str, dict[str, Any]],
    settlements: dict[str, dict[str, Any]],
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    feature_by_key = _feature_rows(records)
    joined: list[dict[str, Any]] = []
    for race_key, prediction in predictions.items():
        settlement = settlements.get(race_key)
        if not settlement or settlement.get("void"):
            continue
        key = (prediction["raceDate"], prediction["venue"], prediction["raceNo"])
        feature_rows = feature_by_key.get(key, [])
        if len(feature_rows) != 6:
            continue
        # Rejected snapshots remain in the append-only store for audit, but
        # cannot enter a settled-feature evaluation cohort.
        if any(row.get("researchEligible") is not True for row in feature_rows):
            continue
        metadata = feature_rows[0]
        for row in feature_rows[1:]:
            for field in (
                "secondsBeforeDeadline", "captureTimestampVerified",
                "provenanceVerified", "schemaVerified", "researchEligible",
            ):
                if row.get(field) != metadata.get(field):
                    raise ValueError(f"feature_metadata_mismatch:{race_key}")
        joined.append({
            "raceKey": race_key,
            "raceDate": prediction["raceDate"],
            "venue": prediction["venue"],
            "raceNo": prediction["raceNo"],
            "winnerBoat": settlement["winnerBoat"],
            "baselineProbabilities": prediction["probabilities"],
            "features": [
                {"boatNo": row["boatNo"], "values": row["values"]}
                for row in feature_rows
            ],
            "featureGroup": FEATURE_GROUP,
            "researchEligible": metadata["researchEligible"],
            "captureTimestampVerified": metadata["captureTimestampVerified"],
            "secondsBeforeDeadline": metadata["secondsBeforeDeadline"],
            "provenanceVerified": metadata["provenanceVerified"],
            "schemaVerified": metadata["schemaVerified"],
        })
    return build_course_start_race_rows(joined)


def _selected_scope_by_date(request_ledger: Path) -> dict[str, list[str]]:
    if not request_ledger.is_file():
        return {}
    connection = sqlite3.connect(f"file:{request_ledger.resolve().as_posix()}?mode=ro", uri=True)
    try:
        values = {
            str(row[0]): [value for value in str(row[1]).split(",") if value]
            for row in connection.execute(
                "SELECT key,value FROM state WHERE key LIKE 'venues:%'"
            )
        }
        legacy = {
            str(row[0]).removeprefix("venue:"): [str(row[1])]
            for row in connection.execute(
                "SELECT key,value FROM state WHERE key LIKE 'venue:%'"
            )
        }
        for date, venues in legacy.items():
            key = f"venues:{date}"
            current = values.setdefault(key, [])
            values[key] = (venues + [venue for venue in current if venue not in venues])[:5]
        return {
            key.removeprefix("venues:"): venues
            for key, venues in values.items()
            if venues
        }
    finally:
        connection.close()


def load_selected_scope_schedule(
    b_root: Path, request_ledger: Path, dates: set[str],
) -> tuple[list[dict[str, Any]] | None, dict[str, Any]]:
    selected_by_date = _selected_scope_by_date(request_ledger)
    schedule: list[dict[str, Any]] = []
    source_files: list[dict[str, Any]] = []
    missing: list[str] = []
    for race_date in sorted(dates):
        b_file = b_root / f"B{race_date[2:].replace('-', '')}.TXT"
        venues = selected_by_date.get(race_date)
        if not b_file.is_file() or not venues:
            missing.append(race_date)
            continue
        entries = validate_runtime_bfile(b_file)
        rows = entries[["date", "jcd", "race_no", "deadline"]].drop_duplicates()
        for row in rows.itertuples(index=False):
            if str(row.date) == race_date and str(row.jcd).zfill(2) in venues:
                schedule.append({
                    "raceDate": race_date,
                    "jcd": str(row.jcd).zfill(2),
                    "raceNo": int(row.race_no),
                    "deadlineJst": datetime.fromisoformat(
                        f"{row.date}T{row.deadline}:00"
                    ).replace(tzinfo=JST).isoformat(),
                    "timeBand": "unknown",
                })
        source_files.append({
            "name": b_file.name,
            "sha256": hashlib.sha256(b_file.read_bytes()).hexdigest(),
            "selectedVenues": venues,
        })
    metadata = {
        "status": "VERIFIED_LOCAL_SELECTED_SCOPE" if not missing and schedule else "UNAVAILABLE",
        "scope": "collector_selected_venues",
        "requestedDates": sorted(dates),
        "availableDates": sorted({str(row["raceDate"]) for row in schedule}),
        "scheduledRaceCount": len({
            (row["raceDate"], row["jcd"], row["raceNo"]) for row in schedule
        }),
        "sourceFiles": source_files,
        "missingDates": missing,
    }
    return (schedule if metadata["status"] != "UNAVAILABLE" else None), metadata


def _scheduled_race_count(schedule: list[dict[str, Any]] | None) -> int | None:
    if schedule is None:
        return None
    return len({
        (str(row.get("raceDate")), str(row.get("jcd")), int(row.get("raceNo")))
        for row in schedule
    })


def _schedule_deadline_jst(row: dict[str, Any]) -> datetime:
    deadline = row.get("deadlineJst")
    if not isinstance(deadline, str):
        raise ValueError("schedule_deadline_missing")
    try:
        parsed = datetime.fromisoformat(deadline)
    except ValueError as exc:
        raise ValueError("schedule_deadline_invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError("schedule_deadline_timezone_required")
    return parsed.astimezone(JST)


def mature_selected_schedule(
    schedule: list[dict[str, Any]], *, as_of: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split selected races after the collector's T-8 to T-6 window has closed."""
    if as_of.tzinfo is None:
        raise ValueError("maturity_as_of_timezone_required")
    as_of_jst = as_of.astimezone(JST)
    mature: list[dict[str, Any]] = []
    not_due: list[dict[str, Any]] = []
    for row in schedule:
        deadline = _schedule_deadline_jst(row)
        # At exactly T-6 the existing collector can still make one valid request.
        if (deadline - as_of_jst).total_seconds() < CAPTURE_WINDOW_CLOSES_SECONDS_BEFORE_DEADLINE:
            mature.append(row)
        else:
            not_due.append(row)
    return mature, not_due


def attach_capture_coverage_views(
    raw_quality: dict[str, dict[str, Any]],
    mature_quality: dict[str, dict[str, Any]],
    *,
    raw_selected_race_count: int | None,
    mature_selected_race_count: int | None,
    capture_window_not_due_race_count: int,
) -> dict[str, dict[str, Any]]:
    """Keep raw diagnostics while making mature coverage the gate value."""
    if set(raw_quality) != set(mature_quality):
        raise ValueError("coverage_feature_group_mismatch")
    if raw_selected_race_count is None:
        if mature_selected_race_count is not None or capture_window_not_due_race_count:
            raise ValueError("coverage_schedule_accounting_invalid")
    elif (
        mature_selected_race_count is None
        or raw_selected_race_count
        != mature_selected_race_count + capture_window_not_due_race_count
    ):
        raise ValueError("coverage_schedule_accounting_invalid")

    output: dict[str, dict[str, Any]] = {}
    for group, mature_entry in mature_quality.items():
        raw_entry = raw_quality[group]
        entry = dict(mature_entry)
        entry.update({
            "rawCaptureCoverage": raw_entry.get("coverage"),
            "rawSelectedRaceCount": raw_selected_race_count,
            "matureCaptureCoverage": mature_entry.get("coverage"),
            "matureSelectedRaceCount": mature_selected_race_count,
            "captureWindowNotDueRaceCount": capture_window_not_due_race_count,
            "coverageDefinition": COVERAGE_DEFINITION,
        })
        output[group] = entry
    return output


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")


def _reproducibility_manifest_is_valid(
    manifest: Any, *, spec_hash: str,
) -> bool:
    required = {
        "schemaVersion",
        "artifactType",
        "gitHead",
        "gitStatusPorcelain",
        "dirtyWorktree",
        "trackedDiffPath",
        "trackedDiffSha256",
        "untrackedFiles",
        "untrackedManifestSha256",
        "configPath",
        "configSha256",
        "oofSpecPath",
        "oofSpecSha256",
        "productionAdoptionAllowed",
        "oofExecuted",
    }
    return bool(
        isinstance(manifest, dict)
        and required.issubset(manifest)
        and manifest.get("schemaVersion") == 1
        and manifest.get("artifactType") == "OOF_REPRODUCIBILITY_MANIFEST"
        and isinstance(manifest.get("gitHead"), str)
        and bool(manifest.get("gitHead"))
        and isinstance(manifest.get("gitStatusPorcelain"), list)
        and all(isinstance(value, str) for value in manifest["gitStatusPorcelain"])
        and isinstance(manifest.get("dirtyWorktree"), bool)
        and all(
            isinstance(manifest.get(key), str) and bool(manifest[key])
            for key in (
                "trackedDiffPath",
                "trackedDiffSha256",
                "untrackedManifestSha256",
                "configPath",
                "configSha256",
                "oofSpecPath",
                "oofSpecSha256",
            )
        )
        and isinstance(manifest.get("untrackedFiles"), list)
        and all(isinstance(value, str) for value in manifest["untrackedFiles"])
        and manifest.get("configSha256") == spec_hash
        and manifest.get("oofSpecSha256") == spec_hash
        and manifest.get("productionAdoptionAllowed") is False
        and manifest.get("oofExecuted") is False
    )


def build_evaluation_artifact(
    evaluation: dict[str, Any], *, cohort_digest: str, spec_hash: str,
    reproducibility_manifest: dict[str, Any],
) -> dict[str, Any]:
    """Bind an offline evaluation to its immutable cohort and frozen protocol."""
    if evaluation.get("modelSha256") != CHAMPION_MODEL_SHA256:
        raise ValueError("evaluation_model_hash_mismatch")
    if evaluation.get("productionAdoptionAllowed") is not False:
        raise ValueError("evaluation_production_adoption_not_disabled")
    if not isinstance(cohort_digest, str) or not cohort_digest:
        raise ValueError("evaluation_cohort_digest_missing")
    if not isinstance(spec_hash, str) or not spec_hash:
        raise ValueError("evaluation_spec_hash_missing")
    if not _reproducibility_manifest_is_valid(
        reproducibility_manifest,
        spec_hash=spec_hash,
    ):
        raise ValueError("evaluation_reproducibility_manifest_invalid")
    payload = {
        key: value
        for key, value in evaluation.items()
        if key not in {"candidate", "candidatePredictionDigest", "evaluationArtifactSha256"}
    }
    payload.update({
        "evaluationArtifactSchemaVersion": EVALUATION_ARTIFACT_SCHEMA_VERSION,
        "featureSchemaSha256": FEATURE_SCHEMA_SHA256,
        "cohortDigest": cohort_digest,
        "specHash": spec_hash,
        "personalAdoptionAllowed": False,
        "candidatePredictionDigest": candidate_prediction_digest(evaluation),
        "reproducibilityManifest": reproducibility_manifest,
        "reproducibilityManifestSha256": stable_hash(reproducibility_manifest),
    })
    payload["evaluationArtifactSha256"] = stable_hash(payload)
    return payload


def _evaluation_artifact_is_current(
    payload: dict[str, Any], *, cohort_digest: str, spec_hash: str,
) -> bool:
    required = {
        "evaluationArtifactSchemaVersion",
        "evaluationArtifactSha256",
        "oofValidationRaceCount",
        "oofValidationDateCount",
        "candidatePredictionDigest",
        "deterministicRerunPassed",
        "productionAdoptionAllowed",
        "personalAdoptionAllowed",
        "cohortDigest",
        "specHash",
        "modelSha256",
        "featureSchemaSha256",
        "reproducibilityManifest",
        "reproducibilityManifestSha256",
    }
    if not required.issubset(payload):
        return False
    saved_hash = payload.get("evaluationArtifactSha256")
    body = {key: value for key, value in payload.items() if key != "evaluationArtifactSha256"}
    return bool(
        saved_hash == stable_hash(body)
        and payload.get("evaluationArtifactSchemaVersion") == EVALUATION_ARTIFACT_SCHEMA_VERSION
        and payload.get("cohortDigest") == cohort_digest
        and payload.get("specHash") == spec_hash
        and payload.get("modelSha256") == CHAMPION_MODEL_SHA256
        and payload.get("featureSchemaSha256") == FEATURE_SCHEMA_SHA256
        and payload.get("reproducibilityManifestSha256")
        == stable_hash(payload.get("reproducibilityManifest"))
        and _reproducibility_manifest_is_valid(
            payload.get("reproducibilityManifest"),
            spec_hash=spec_hash,
        )
        and payload.get("deterministicRerunPassed") is True
        and payload.get("productionAdoptionAllowed") is False
        and payload.get("personalAdoptionAllowed") is False
    )


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Course/Start Challenger Readiness",
        "",
        f"- Status: {report['status']}",
        f"- Evaluation executed: {str(report['evaluationExecuted']).lower()}",
        f"- Eligible settled feature races: {report['settledRaces']} / {report['minimumSettledRaces']}",
        f"- All settled prediction races: {report.get('overallSettledRaces', report['settledRaces'])}",
        f"- Verified feature races: {report['verifiedFeatureRaces']}",
        f"- Forward days: {report['observedForwardDays']} / {report['minimumForwardDays']}",
        f"- Raw capture coverage: {report.get('rawCaptureCoverage') if report.get('rawCaptureCoverage') is not None else 'UNKNOWN'}",
        f"- Mature capture coverage: {report.get('matureCaptureCoverage', report['coverage']) if report.get('matureCaptureCoverage', report['coverage']) is not None else 'UNKNOWN'}",
        f"- Coverage definition: {report.get('coverageDefinition', 'UNKNOWN')}",
        f"- Mature selected races: {report.get('matureSelectedRaceCount')}",
        f"- Capture-window not due races: {report.get('captureWindowNotDueRaceCount')}",
        f"- Joined settled feature races: {report.get('joinedSettledFeatureRaces', 0)}",
        f"- Assessment window: {report.get('assessmentWindowStart') or 'UNKNOWN'} to {report.get('assessmentWindowEnd') or 'UNKNOWN'}",
        f"- Evaluation cohort: {report.get('cohortStart') or 'UNKNOWN'} to {report.get('cohortEnd') or 'UNKNOWN'}",
        f"- OOF validation races/dates: {report.get('oofValidationRaceCount', 0)} / {report.get('oofValidationDateCount', 0)}",
        f"- Evaluation locked: {str(report.get('evaluationLocked', False)).lower()}",
        "",
        "## Blocked Reasons",
        "",
    ]
    lines.extend(f"- {reason}" for reason in report.get("blockedReasons", []))
    lines.extend([
        "",
        "このレポートは準備状況だけを示す。閾値未達の間は学習・モデル接続・production書込みを行わない。",
        "オフラインscreeningの合格は、production採用や個人用prospective採用の証拠ではない。",
        "",
        f"- productionAdoptionAllowed: {str(report['productionAdoptionAllowed']).lower()}",
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _runtime_status(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return _json(path)
    except ValueError:
        return {}


def _runtime_snapshot(feature_store: Path, status: dict[str, Any]) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "snapshotId": None,
        "asOfLedgerId": None,
        "asOfLedgerRecordId": None,
        "asOfLedgerRecordHash": None,
        "runtimeStatusRunId": status.get("runId"),
        "runtimeStatusGeneratedAt": status.get("generatedAtUtc") or status.get("generatedAtJst"),
        "sourcePolicyStatus": status.get("sourcePolicyStatus"),
        "sourcePolicyHash": status.get("policyHash"),
        "configHash": status.get("configHash"),
        "codeCommit": status.get("codeCommit"),
    }
    database = feature_store / "feature_forward.sqlite3" if feature_store.is_dir() else feature_store
    if not database.is_file():
        return snapshot
    connection = sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True)
    try:
        row = connection.execute(
            "SELECT snapshot_id, fetched_at_jst FROM snapshots "
            "ORDER BY fetched_at_jst DESC, snapshot_id DESC LIMIT 1"
        ).fetchone()
        if row:
            snapshot["snapshotId"] = row[0]
            snapshot["maxFetchedAtJst"] = row[1]
        row = connection.execute(
            "SELECT sequence, record_id, record_hash FROM ledger_chain "
            "ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        if row:
            snapshot["asOfLedgerId"] = row[0]
            snapshot["asOfLedgerRecordId"] = row[1]
            snapshot["asOfLedgerRecordHash"] = row[2]
    finally:
        connection.close()
    return snapshot


def _write_oof_preflight_markdown(path: Path, preflight: dict[str, Any]) -> None:
    lines = [
        "# OOF Preflight",
        "",
        f"- status: `{preflight['status']}`",
        f"- dataGateEligible: `{str(preflight['dataGateEligible']).lower()}`",
        f"- executionAllowed: `{str(preflight['executionAllowed']).lower()}`",
        f"- executionRequested: `{str(preflight['executionRequested']).lower()}`",
        f"- forwardDays: `{preflight['forwardDays']}`",
        f"- selectedRaceCount: `{preflight.get('selectedRaceCount')}`",
        f"- validCaptureCount: `{preflight.get('validCaptureCount')}`",
        f"- featureSettledRaceCount: `{preflight.get('featureSettledRaceCount')}`",
        f"- coverage: `{preflight['coverage']}`",
        f"- coverageDefinition: `{preflight.get('coverageDefinition')}`",
        f"- rawCaptureCoverage: `{preflight.get('rawCaptureCoverage')}`",
        f"- matureCaptureCoverage: `{preflight.get('matureCaptureCoverage')}`",
        f"- rawSelectedRaceCount: `{preflight.get('rawSelectedRaceCount')}`",
        f"- matureSelectedRaceCount: `{preflight.get('matureSelectedRaceCount')}`",
        f"- captureWindowNotDueRaceCount: `{preflight.get('captureWindowNotDueRaceCount')}`",
        f"- featureSettledRaces: `{preflight['featureSettledRaces']}`",
        f"- productionRelevantFailureCount: `{preflight['productionRelevantFailureCount']}`",
        f"- newUnknownCount: `{preflight['newUnknownCount']}`",
        f"- terminalConflictCount: `{preflight['terminalConflictCount']}`",
        f"- leakageCount: `{preflight['leakageCount']}`",
        f"- hashChainValid: `{str(preflight['hashChainValid']).lower()}`",
        "",
        "## Fold Preflight",
        "",
        f"- method: `{preflight['foldPreflight']['method']}`",
        f"- foldCount: `{preflight['foldPreflight']['foldCount']}`",
        f"- minimumsMet: `{str(preflight['foldPreflight']['minimumsMet']).lower()}`",
        f"- accounting: `{str(preflight['foldPreflight']['accounting']['accountingPass']).lower()}`",
        "",
        "| Fold | Train races | Validation races | Train dates | Validation dates | Overlap |",
        "|---:|---:|---:|---|---|---:|",
    ]
    for fold in preflight["foldPreflight"].get("folds", []):
        lines.append(
            f"| {fold['fold']} | {fold['trainRaceCount']} | {fold['validationRaceCount']} | "
            f"{fold['trainStart']} to {fold['trainEnd']} | "
            f"{fold['validationStart']} to {fold['validationEnd']} | {fold['raceOverlap']} |"
        )
    lines.extend(["", "## Blocked Reasons", ""])
    lines.extend(f"- {reason}" for reason in preflight.get("blockedReasons", []))
    lines.extend([
        "",
        f"- diagnosticGate: `{str(preflight['diagnosticGate']['eligible']).lower()}`",
        f"- decisionGate: `{str(preflight['decisionGate']['eligible']).lower()}`",
        "",
        "このpreflightは件数・時系列境界だけを確認し、OOF学習・評価・モデル変更を実行しない。",
        "明示的な実行フラグがない限り、評価artifactは作成・更新しない。",
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _readiness_forward_days(feature_quality: dict[str, Any]) -> int:
    """Use the trailing consecutive verified collection run, not unique dates."""
    return int(feature_quality.get("consecutiveCollectionDays") or 0)


def _apply_coverage_gate(report: dict[str, Any], coverage_metadata: dict[str, Any]) -> None:
    """Keep an unverified schedule denominator fail-closed."""
    if coverage_metadata.get("status") != "VERIFIED_LOCAL_SELECTED_SCOPE":
        report["blockedReasons"] = sorted(
            set(report.get("blockedReasons", [])) | {"coverage_denominator_unavailable"}
        )
        report["status"] = "CHALLENGER_EVALUATION_BLOCKED"
        report["evaluationExecuted"] = False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only course/start challenger readiness and OOF evaluation.")
    parser.add_argument("--prediction-root", type=Path, required=True)
    parser.add_argument("--settlement-root", type=Path, required=True)
    parser.add_argument("--feature-store", type=Path, required=True)
    parser.add_argument("--report-root", type=Path, required=True)
    parser.add_argument("--model-artifact", type=Path, required=True)
    parser.add_argument("--b-root", type=Path)
    parser.add_argument("--request-ledger", type=Path)
    parser.add_argument("--oof-spec", type=Path, default=DEFAULT_OOF_SPEC)
    parser.add_argument(
        "--runtime-status",
        type=Path,
        default=DEFAULT_RUNTIME_ROOT / "reports" / "feature_forward_v1" / "latest_status.json",
    )
    parser.add_argument(
        "--run-started-at",
        help="Optional timezone-aware ISO timestamp used to freeze the business date.",
    )
    parser.add_argument(
        "--execute-oof",
        action="store_true",
        help="Explicitly request OOF evaluation after all fixed gates pass.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    report_root = args.report_root.resolve()
    allowed = (ROOT / "reports" / "feature_forward").resolve()
    if report_root != allowed:
        raise ValueError("report_root_not_allowlisted")
    if args.model_artifact:
        actual_hash = hashlib.sha256(args.model_artifact.read_bytes()).hexdigest()
        if actual_hash != CHAMPION_MODEL_SHA256:
            raise ValueError("champion_model_hash_mismatch")
    if args.run_started_at:
        run_started_at = datetime.fromisoformat(args.run_started_at).astimezone(JST)
    else:
        run_started_at = datetime.now(JST)
    input_contract = build_runtime_input_contract(
        runtime_root=DEFAULT_RUNTIME_ROOT,
        run_started_at=run_started_at,
    )
    predictions, settlements = load_prediction_settlement_records(
        args.prediction_root.resolve(), args.settlement_root.resolve()
    )
    records = load_records_read_only(args.feature_store.resolve())
    feature_keys = complete_verified_race_keys(records, FEATURE_GROUP)
    course_start_contract = build_course_start_contract_audit(records)
    _write_json(report_root / "course_start_feature_contract_audit.json", course_start_contract)
    schedule = None
    coverage_metadata = {
        "status": "UNAVAILABLE",
        "scope": "collector_selected_venues",
        "requestedDates": [],
        "availableDates": [],
        "scheduledRaceCount": None,
        "sourceFiles": [],
        "missingDates": sorted({str(key[0]) for key in feature_keys}),
    }
    scope_dates = (
        set(_selected_scope_by_date(args.request_ledger.resolve()))
        if args.request_ledger
        else set()
    )
    assessment_dates = derive_assessment_dates(feature_keys, scope_dates)
    if args.b_root and args.request_ledger:
        schedule, coverage_metadata = load_selected_scope_schedule(
            args.b_root.resolve(), args.request_ledger.resolve(),
            set(assessment_dates),
        )
    raw_quality = build_collection_quality(records, scheduled_races=schedule)
    mature_schedule: list[dict[str, Any]] | None = None
    not_due_schedule: list[dict[str, Any]] = []
    if schedule is None:
        mature_quality = raw_quality
    else:
        mature_schedule, not_due_schedule = mature_selected_schedule(
            schedule,
            as_of=run_started_at,
        )
        mature_quality = build_collection_quality(records, scheduled_races=mature_schedule)
        if not mature_schedule:
            for entry in mature_quality.values():
                # No race has completed its capture window, so coverage is undefined.
                entry["coverage"] = None
    raw_selected_race_count = _scheduled_race_count(schedule)
    mature_selected_race_count = _scheduled_race_count(mature_schedule)
    coverage_metadata.update({
        "rawScheduledRaceCount": raw_selected_race_count,
        "matureScheduledRaceCount": mature_selected_race_count,
        "captureWindowNotDueRaceCount": _scheduled_race_count(not_due_schedule) or 0,
        "coverageAsOfJst": run_started_at.isoformat(),
        "coverageDefinition": COVERAGE_DEFINITION,
    })
    quality = attach_capture_coverage_views(
        raw_quality,
        mature_quality,
        raw_selected_race_count=raw_selected_race_count,
        mature_selected_race_count=mature_selected_race_count,
        capture_window_not_due_race_count=coverage_metadata["captureWindowNotDueRaceCount"],
    )
    feature_quality = quality[FEATURE_GROUP]
    collection_dates = sorted({str(record["raceDate"]) for record in records if record.get("raceDate")})
    if collection_dates:
        write_collection_quality_reports(
            report_root=report_root,
            as_of_date=collection_dates[-1],
            quality=quality,
        )
    joined = build_joined_race_rows(predictions, settlements, records)
    eligible_settled_count = len(joined)
    oof_spec = load_oof_spec(args.oof_spec.resolve())
    failure_classification_path = report_root / "oof_failure_classification.json"
    failure_classification = _json(failure_classification_path) if failure_classification_path.is_file() else {}
    failure_counts = failure_classification.get("counts") if isinstance(failure_classification.get("counts"), dict) else {}
    production_relevant_failure_count = int(failure_counts.get("PRODUCTION_RELEVANT") or 0)
    runtime_status = _runtime_status(args.runtime_status.resolve())
    runtime_snapshot = _runtime_snapshot(args.feature_store.resolve(), runtime_status)
    runtime_lifecycle = runtime_status.get("lifecycle") if isinstance(runtime_status.get("lifecycle"), dict) else {}
    runtime_cumulative = runtime_status.get("cumulative") if isinstance(runtime_status.get("cumulative"), dict) else {}
    integrity = runtime_cumulative.get("integrity") if isinstance(runtime_cumulative.get("integrity"), dict) else {}
    lifecycle_integrity = runtime_lifecycle.get("integrity") if isinstance(runtime_lifecycle.get("integrity"), dict) else {}
    hash_chain_valid = bool(integrity.get("valid") is True and lifecycle_integrity.get("valid") is True)
    fold_preflight = build_fold_preflight(
        joined,
        minimum_validation_races_per_fold=int(oof_spec["diagnosticGate"]["minimumValidationRacesPerFold"]),
    )
    contract_leakage_count = int(course_start_contract.get("resultLeakageCount") or 0)
    leakage_count = int(feature_quality.get("resultLeakageCount") or 0) + contract_leakage_count
    oof_preflight = build_oof_preflight(
        spec=oof_spec,
        forward_days=_readiness_forward_days(feature_quality),
        coverage=feature_quality.get("coverage"),
        feature_settled_races=eligible_settled_count,
        new_unknown_count=int(runtime_lifecycle.get("newUnknownCount") or 0),
        terminal_conflict_count=int(runtime_lifecycle.get("terminalStatusConflictCount") or 0),
        leakage_count=leakage_count,
        hash_chain_valid=hash_chain_valid,
        production_relevant_failure_count=production_relevant_failure_count,
        fold_preflight=fold_preflight,
        snapshot=runtime_snapshot,
    )
    oof_preflight["executionRequested"] = bool(args.execute_oof)
    oof_preflight["executionAllowed"] = oof_execution_allowed(
        args.execute_oof,
        oof_preflight,
    )
    oof_preflight.update({
        "selectedRaceCount": coverage_metadata.get("rawScheduledRaceCount"),
        "validCaptureCount": len(feature_keys),
        "featureSettledRaceCount": eligible_settled_count,
        "coverageDefinition": feature_quality["coverageDefinition"],
        "rawCaptureCoverage": feature_quality["rawCaptureCoverage"],
        "matureCaptureCoverage": feature_quality["matureCaptureCoverage"],
        "rawSelectedRaceCount": feature_quality["rawSelectedRaceCount"],
        "matureSelectedRaceCount": feature_quality["matureSelectedRaceCount"],
        "captureWindowNotDueRaceCount": feature_quality["captureWindowNotDueRaceCount"],
    })
    if not runtime_status:
        oof_preflight["dataGateEligible"] = False
        oof_preflight["executionAllowed"] = False
        oof_preflight["blockedReasons"] = sorted(
            set(oof_preflight["blockedReasons"]) | {"runtime_status_unavailable"}
        )
    oof_preflight["specPath"] = str(args.oof_spec.resolve())
    oof_preflight["specHash"] = hashlib.sha256(args.oof_spec.resolve().read_bytes()).hexdigest()
    report = build_readiness_report(
        settled_races=eligible_settled_count,
        feature_races=len(feature_keys),
        quality=feature_quality,
        model_sha256=CHAMPION_MODEL_SHA256,
        observed_forward_days=_readiness_forward_days(feature_quality),
    )
    observed_dates = (
        [str(record.get("raceDate")) for record in records if record.get("raceDate")]
        + [str(record.get("raceDate")) for record in predictions.values() if record.get("raceDate")]
    )
    report.update({
        "asOfDate": max(observed_dates) if observed_dates else "UNKNOWN",
        "predictionCount": len(predictions),
        "settlementRecordCount": len(settlements),
        "overallSettledRaces": sum(not settlement.get("void") for settlement in settlements.values()),
        "joinedSettledFeatureRaces": len(joined),
        "featureStoreIntegrityChecked": True,
        "predictionSettlementHashesChecked": True,
        "tree15Changed": False,
        "productionWrites": 0,
        "prospectiveWrites": 0,
        "coverageScope": coverage_metadata["scope"],
        "coverageEvidenceStatus": coverage_metadata["status"],
        "coverageDenominatorRaceCount": coverage_metadata["matureScheduledRaceCount"],
        "rawCoverageDenominatorRaceCount": coverage_metadata["rawScheduledRaceCount"],
        "rawCaptureCoverage": feature_quality["rawCaptureCoverage"],
        "matureCaptureCoverage": feature_quality["matureCaptureCoverage"],
        "rawSelectedRaceCount": feature_quality["rawSelectedRaceCount"],
        "matureSelectedRaceCount": feature_quality["matureSelectedRaceCount"],
        "captureWindowNotDueRaceCount": feature_quality["captureWindowNotDueRaceCount"],
        "coverageDefinition": feature_quality["coverageDefinition"],
        "coverageAsOfJst": coverage_metadata["coverageAsOfJst"],
        "coverageSourceFiles": coverage_metadata["sourceFiles"],
        "coverageMissingDates": coverage_metadata["missingDates"],
        "assessmentWindowStart": assessment_dates[0] if assessment_dates else None,
        "assessmentWindowEnd": assessment_dates[-1] if assessment_dates else None,
        "cohortStart": None,
        "cohortEnd": None,
        "cohortDigest": None,
        "evaluationLocked": False,
        "oofValidationRaceCount": 0,
        "oofValidationDateCount": 0,
        "snapshotId": runtime_snapshot.get("snapshotId"),
        "asOfLedgerId": runtime_snapshot.get("asOfLedgerId"),
        "asOfLedgerRecordId": runtime_snapshot.get("asOfLedgerRecordId"),
        "newUnknownCount": oof_preflight["newUnknownCount"],
        "terminalConflictCount": oof_preflight["terminalConflictCount"],
        "leakageCount": oof_preflight["leakageCount"],
        "courseStartContractPass": course_start_contract["contractPass"],
        "hashChainValid": oof_preflight["hashChainValid"],
        "selectedRaceCount": oof_preflight["selectedRaceCount"],
        "validCaptureCount": oof_preflight["validCaptureCount"],
        "featureSettledRaceCount": oof_preflight["featureSettledRaceCount"],
        "oofPreflight": oof_preflight,
        "captureBusinessDate": input_contract["captureBusinessDate"],
        "requiredBFile": input_contract["requiredBFile"],
        "settlementTargetDates": input_contract["settlementTargetDates"],
        "requiredKFiles": input_contract["requiredKFiles"],
        "optionalPrefetchBFile": input_contract["optionalPrefetchBFile"],
        "notDueFiles": input_contract["notDueFiles"],
        "officialAvailable": input_contract["officialAvailable"],
        "canonicalAvailable": input_contract["canonicalAvailable"],
        "dueAtJst": input_contract["dueAtJst"],
        "graceDeadlineAtJst": input_contract["graceDeadlineAtJst"],
        "inputState": input_contract["inputState"],
        "blockedReason": input_contract["blockedReason"],
        "inputContractRunStartedAtJst": input_contract["runStartedAtJst"],
    })
    _apply_coverage_gate(report, coverage_metadata)
    if report["status"] == "COURSE_START_CHALLENGER_READY" and oof_preflight["executionAllowed"]:
        cohort_manifest = build_evaluation_cohort_manifest(
            assessment_dates=assessment_dates,
            joined_race_keys=[str(row["raceKey"]) for row in joined],
            source_files=coverage_metadata["sourceFiles"],
            model_sha256=CHAMPION_MODEL_SHA256,
            feature_schema_sha256=FEATURE_SCHEMA_SHA256,
        )
        cohort_path = report_root / "course_start_evaluation_cohort.json"
        existing_cohort = _json(cohort_path) if cohort_path.is_file() else None
        if existing_cohort is not None and not cohort_manifest_matches(existing_cohort, cohort_manifest):
            report["blockedReasons"] = sorted(
                set(report.get("blockedReasons", []))
                | {"evaluation_cohort_changed_requires_review"}
            )
            report["status"] = "CHALLENGER_EVALUATION_BLOCKED"
            report["evaluationExecuted"] = False
        else:
            if existing_cohort is None:
                _write_json(cohort_path, cohort_manifest)
                existing_cohort = cohort_manifest
            report["cohortStart"] = existing_cohort.get("cohortStart")
            report["cohortEnd"] = existing_cohort.get("cohortEnd")
            report["cohortDigest"] = existing_cohort.get("cohortDigest")
            report["evaluationLocked"] = True
            evaluation_path = report_root / "course_start_challenger_evaluation.json"
            if evaluation_path.is_file():
                evaluation_for_report = _json(evaluation_path)
                if not _evaluation_artifact_is_current(
                    evaluation_for_report,
                    cohort_digest=str(existing_cohort["cohortDigest"]),
                    spec_hash=str(oof_preflight["specHash"]),
                ):
                    report["blockedReasons"] = sorted(
                        set(report.get("blockedReasons", []))
                        | {"evaluation_artifact_incompatible_requires_review"}
                    )
                    report["status"] = "CHALLENGER_EVALUATION_BLOCKED"
                    report["evaluationExecuted"] = False
                    evaluation_for_report = None
            else:
                reproducibility_manifest = write_reproducibility_manifest(
                    root=ROOT,
                    report_root=report_root,
                    spec_path=args.oof_spec.resolve(),
                )
                evaluation = evaluate_course_start_challenger(joined)
                rerun = evaluate_course_start_challenger(joined)
                deterministic = (
                    result_digest(evaluation) == result_digest(rerun)
                    and candidate_prediction_digest(evaluation)
                    == candidate_prediction_digest(rerun)
                )
                evaluation["deterministicRerunPassed"] = deterministic
                if not deterministic:
                    evaluation["status"] = "NO_CHALLENGER_FOUND"
                    evaluation.setdefault("adoptionReasons", []).append("deterministic_rerun_mismatch")
                evaluation_for_report = build_evaluation_artifact(
                    evaluation,
                    cohort_digest=str(existing_cohort["cohortDigest"]),
                    spec_hash=str(oof_preflight["specHash"]),
                    reproducibility_manifest=reproducibility_manifest,
                )
                _write_json(evaluation_path, evaluation_for_report)
            if evaluation_for_report is not None:
                report["evaluationExecuted"] = True
                report["evaluation"] = evaluation_for_report
                report["oofValidationRaceCount"] = int(evaluation_for_report.get("oofValidationRaceCount") or 0)
                report["oofValidationDateCount"] = int(evaluation_for_report.get("oofValidationDateCount") or 0)
    else:
        report["evaluationExecuted"] = False
        report["evaluationLocked"] = False
        report["cohortStart"] = None
        report["cohortEnd"] = None
        report["cohortDigest"] = None
        report["blockedReasons"] = sorted(
            set(report.get("blockedReasons", []))
            | {"explicit_oof_execution_approval_required"}
        )
    _write_json(report_root / "oof_preflight.json", oof_preflight)
    _write_oof_preflight_markdown(report_root / "oof_preflight.md", oof_preflight)
    _write_json(report_root / "course_start_challenger_readiness.json", report)
    _write_markdown(report_root / "course_start_challenger_readiness.md", report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
