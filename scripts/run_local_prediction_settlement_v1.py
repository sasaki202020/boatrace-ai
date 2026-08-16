from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.feature_forward_v1.local_pipeline import (
    DATASET_SHA256,
    MODEL_SHA256,
    generate_daily_predictions,
    settle_available_predictions,
)
from src.feature_forward_v1.runtime_sync import (
    sync_runtime_official_inputs,
)
from src.feature_forward_v1.runtime_lifecycle import (
    RuntimeGateError,
    append_settlement_lifecycle,
    load_runtime_gate,
    new_run_id,
)
from src.feature_forward_v1.date_contract import resolve_input_contract


JST = ZoneInfo("Asia/Tokyo")


def select_business_b_file(b_root: Path, business_date: date) -> Path | None:
    """Select only the B file for the run's fixed business date."""
    expected = b_root / f"B{business_date:%y%m%d}.TXT"
    return expected if expected.is_file() else None


def _date_dirs(root: Path) -> set[date]:
    dates: set[date] = set()
    if not root.is_dir():
        return dates
    for path in root.iterdir():
        if not path.is_dir() or len(path.name) != 8 or not path.name.isdigit():
            continue
        try:
            dates.add(date.fromisoformat(
                f"{path.name[:4]}-{path.name[4:6]}-{path.name[6:]}"
            ))
        except ValueError:
            continue
    return dates


def _fully_settled_dates(
    prediction_root: Path, settlement_root: Path,
) -> set[date]:
    settled: set[date] = set()
    for prediction_date in _date_dirs(prediction_root):
        date8 = prediction_date.strftime("%Y%m%d")
        prediction_names = {
            path.name for path in (prediction_root / date8).glob("*.json")
        }
        settlement_names = {
            path.name for path in (settlement_root / date8).glob("*.json")
        }
        if prediction_names and prediction_names <= settlement_names:
            settled.add(prediction_date)
    return settled


def build_runtime_input_contract(
    *, runtime_root: Path, run_started_at: datetime,
) -> dict[str, object]:
    b_root = runtime_root / "data/raw/official/entries"
    k_root = runtime_root / "data/raw/official/results"
    prediction_root = runtime_root / "data/prospective/predictions"
    settlement_root = runtime_root / "data/prospective/settlements"
    return resolve_input_contract(
        run_started_at=run_started_at,
        available_b_files=(path.name for path in b_root.glob("B*.TXT")),
        available_k_files=(path.name for path in k_root.glob("K*.TXT")),
        settlement_candidate_dates=_date_dirs(prediction_root),
        settled_dates=_fully_settled_dates(prediction_root, settlement_root),
    )


def result_not_due_dates(input_contract: dict[str, object]) -> set[str]:
    """Return K dates that the date contract explicitly marks as not due."""
    dates: set[str] = set()
    for value in input_contract.get("notDueFiles", []):
        name = str(value).upper()
        if not (name.startswith("K") and len(name) == 11 and name.endswith(".TXT")):
            continue
        try:
            dates.add(datetime.strptime(name[1:7], "%y%m%d").date().isoformat())
        except ValueError:
            continue
    return dates


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--entry-source", type=Path, action="append", default=[])
    parser.add_argument("--result-source", type=Path, action="append", default=[])
    parser.add_argument("--minimum-token", default="260721")
    parser.add_argument(
        "--policy",
        type=Path,
        default=ROOT / "config/feature_forward_v1/source_approval.json",
    )
    parser.add_argument(
        "--gate-config",
        type=Path,
        default=ROOT / "config/feature_forward_v1/runtime_gate.json",
    )
    args = parser.parse_args()
    run_id = new_run_id("settlement")
    try:
        gate = load_runtime_gate(
            ROOT,
            gate_config_path=args.gate_config,
            policy_path_override=args.policy,
        )
    except RuntimeGateError as exc:
        payload = {
            "status": "BLOCKED_RUNTIME_GATE",
            "executionStatus": "BLOCKED",
            "blockingGate": "SOURCE_POLICY_RUNTIME",
            "reason": str(exc),
            "runId": run_id,
            "networkRequests": 0,
            "productionWrites": 0,
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 2
    run_started_at = datetime.now(JST)
    now_utc = run_started_at.astimezone(timezone.utc)
    input_sync = sync_runtime_official_inputs(
        runtime_root=args.runtime,
        entry_sources=args.entry_source,
        result_sources=args.result_source,
        minimum_token=args.minimum_token,
    )
    input_contract = build_runtime_input_contract(
        runtime_root=args.runtime,
        run_started_at=run_started_at,
    )
    b_root = args.runtime / "data/raw/official/entries"
    business_date = date.fromisoformat(str(input_contract["captureBusinessDate"]))
    business_b = select_business_b_file(b_root, business_date)
    if business_b is None:
        prediction = {
            "created": 0,
            "existing": 0,
            "skippedLate": 0,
            "status": str(input_contract["inputState"]),
            "requiredBFile": input_contract["requiredBFile"],
            "inputSha256": None,
            "modelSha256": MODEL_SHA256,
            "datasetSha256": DATASET_SHA256,
        }
    else:
        prediction = generate_daily_predictions(
            b_file=business_b,
            prediction_root=args.runtime / "data/prospective/predictions",
            model_path=args.artifact_root / "data/commercialization_v1/frozen_candidate/tree_15.joblib",
            history_path=args.artifact_root / "data/offline_model_v3/canonical_race_results.csv",
            now=run_started_at,
        )
    settlement = settle_available_predictions(
        prediction_root=args.runtime / "data/prospective/predictions",
        settlement_root=args.runtime / "data/prospective/settlements",
        k_root=args.runtime / "data/raw/official/results",
    )
    lifecycle = append_settlement_lifecycle(
        store_root=args.runtime / "data/research/feature_forward_v1/store",
        prediction_root=args.runtime / "data/prospective/predictions",
        settlement_root=args.runtime / "data/prospective/settlements",
        result_root=args.runtime / "data/raw/official/results",
        gate=gate,
        collector_run_id=run_id,
        task_run_id="BOATRACE-Local-Prediction-Settlement-V1",
        now_utc=now_utc,
        result_not_due_dates=result_not_due_dates(input_contract),
    )
    report = {
        **gate.as_dict(),
        "runId": run_id,
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
        "inputContract": input_contract,
        "inputSync": input_sync,
        "latestB": business_b.name if business_b else None,
        "prediction": prediction,
        "settlement": settlement,
        "lifecycle": lifecycle,
        "tree15Changed": False,
        "productionWrites": 0,
    }
    status = args.runtime / "reports/prediction_priority/latest_status.json"
    status.parent.mkdir(parents=True, exist_ok=True)
    status.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))
    if settlement["conflicts"]:
        return 2
    return 3 if input_sync["sourceErrors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
