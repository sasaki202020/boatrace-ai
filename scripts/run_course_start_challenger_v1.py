from __future__ import annotations

import argparse
import hashlib
import json
import sys
import sqlite3
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
    evaluate_course_start_challenger,
    result_digest,
)
from src.feature_forward_v1.store import stable_hash
from src.feature_forward_v1.value_evaluation import (
    build_collection_quality,
    complete_verified_race_keys,
)
from src.commercialization_v2.day1_readiness import validate_runtime_bfile
from scripts.build_feature_value_evaluation_v1 import load_records_read_only

FEATURE_SCHEMA_SHA256 = "a3853bdbdb75d13d4a596928c13eaa034307b14a5a8c534d31fca9acdab623dd"


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
        "scheduledRaceCount": len({
            (row["raceDate"], row["jcd"], row["raceNo"]) for row in schedule
        }),
        "sourceFiles": source_files,
        "missingDates": missing,
    }
    return (schedule if metadata["status"] != "UNAVAILABLE" else None), metadata


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")


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
        f"- Coverage: {report['coverage'] if report['coverage'] is not None else 'UNKNOWN'}",
        f"- Joined settled feature races: {report.get('joinedSettledFeatureRaces', 0)}",
        "",
        "## Blocked Reasons",
        "",
    ]
    lines.extend(f"- {reason}" for reason in report.get("blockedReasons", []))
    lines.extend([
        "",
        "このレポートは準備状況だけを示す。閾値未達の間は学習・モデル接続・production書込みを行わない。",
        "",
        f"- productionAdoptionAllowed: {str(report['productionAdoptionAllowed']).lower()}",
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _readiness_forward_days(feature_quality: dict[str, Any]) -> int:
    """Use the trailing consecutive verified collection run, not unique dates."""
    return int(feature_quality.get("consecutiveCollectionDays") or 0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only course/start challenger readiness and OOF evaluation.")
    parser.add_argument("--prediction-root", type=Path, required=True)
    parser.add_argument("--settlement-root", type=Path, required=True)
    parser.add_argument("--feature-store", type=Path, required=True)
    parser.add_argument("--report-root", type=Path, required=True)
    parser.add_argument("--model-artifact", type=Path)
    parser.add_argument("--b-root", type=Path)
    parser.add_argument("--request-ledger", type=Path)
    args = parser.parse_args(argv)
    report_root = args.report_root.resolve()
    allowed = (ROOT / "reports" / "feature_forward").resolve()
    if report_root != allowed:
        raise ValueError("report_root_not_allowlisted")
    if args.model_artifact:
        actual_hash = hashlib.sha256(args.model_artifact.read_bytes()).hexdigest()
        if actual_hash != CHAMPION_MODEL_SHA256:
            raise ValueError("champion_model_hash_mismatch")
    predictions, settlements = load_prediction_settlement_records(
        args.prediction_root.resolve(), args.settlement_root.resolve()
    )
    records = load_records_read_only(args.feature_store.resolve())
    feature_keys = complete_verified_race_keys(records, FEATURE_GROUP)
    schedule = None
    coverage_metadata = {
        "status": "UNAVAILABLE",
        "scope": "collector_selected_venues",
        "scheduledRaceCount": None,
        "sourceFiles": [],
        "missingDates": sorted({str(key[0]) for key in feature_keys}),
    }
    if args.b_root and args.request_ledger:
        schedule, coverage_metadata = load_selected_scope_schedule(
            args.b_root.resolve(), args.request_ledger.resolve(),
            {str(key[0]) for key in feature_keys},
        )
    quality = build_collection_quality(records, scheduled_races=schedule)
    feature_quality = quality[FEATURE_GROUP]
    joined = build_joined_race_rows(predictions, settlements, records)
    eligible_settled_count = len(joined)
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
        "coverageDenominatorRaceCount": coverage_metadata["scheduledRaceCount"],
        "coverageSourceFiles": coverage_metadata["sourceFiles"],
        "coverageMissingDates": coverage_metadata["missingDates"],
    })
    if coverage_metadata["status"] != "VERIFIED_LOCAL_SELECTED_SCOPE":
        report["blockedReasons"] = sorted(set(report["blockedReasons"] + ["coverage_denominator_unavailable"]))
    if report["status"] == "COURSE_START_CHALLENGER_READY":
        evaluation = evaluate_course_start_challenger(joined)
        rerun = evaluate_course_start_challenger(joined)
        deterministic = result_digest(evaluation) == result_digest(rerun)
        evaluation["deterministicRerunPassed"] = deterministic
        if not deterministic:
            evaluation["status"] = "NO_CHALLENGER_FOUND"
            evaluation.setdefault("adoptionReasons", []).append("deterministic_rerun_mismatch")
        evaluation_for_report = {key: value for key, value in evaluation.items() if key != "candidate"}
        evaluation_for_report["candidatePredictionDigest"] = result_digest(evaluation)
        report["evaluationExecuted"] = True
        report["evaluation"] = evaluation_for_report
        _write_json(report_root / "course_start_challenger_evaluation.json", evaluation_for_report)
    _write_json(report_root / "course_start_challenger_readiness.json", report)
    _write_markdown(report_root / "course_start_challenger_readiness.md", report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
