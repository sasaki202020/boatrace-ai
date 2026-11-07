from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import tempfile
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.models.win_baseline_common import (
    augment_features_for_relative_comparison,
    load_feature_set_config,
    load_trainable_frame,
    train_single_feature_set,
)


DEFAULT_TRAINABLE_PATH = ROOT / "data" / "processed" / "trainable_win_training_data.csv"
DEFAULT_CORE_FEATURE_SET = ROOT / "config" / "feature_sets" / "win_baseline_core.json"
DEFAULT_CHALLENGER_FEATURE_SET = ROOT / "config" / "feature_sets" / "win_baseline_core_relative.json"
DEFAULT_TRACE_PATH = ROOT / "reports" / "monitoring" / "candidate_trace_audit.json"
OUT_ROOT = ROOT / "reports" / "model_eval"
OUT_JSON = OUT_ROOT / "architecture_v2_walk_forward_validation.json"
OUT_CSV = OUT_ROOT / "architecture_v2_walk_forward_validation.csv"
OUT_MD = OUT_ROOT / "architecture_v2_walk_forward_validation.md"

LEAKAGE_TOKENS = ("finish", "result", "payout", "refund", "actual", "target", "label", "hit")
METRIC_NAMES = ("log_loss", "brier_score", "calibration_error", "top1_accuracy", "top1_win_rate")


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _parse_iso(value: Any) -> date | None:
    token = str(value or "").strip()
    if not token:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(token, fmt).date()
        except ValueError:
            continue
    return None


def _trace_range(path: Path) -> tuple[date | None, date | None]:
    payload = _load_json(path)
    start = _parse_iso(payload.get("startDate"))
    end = _parse_iso(payload.get("endDate"))
    if start and end:
        return start, end
    raw = payload.get("dateRange")
    if isinstance(raw, dict):
        return _parse_iso(raw.get("start")), _parse_iso(raw.get("end"))
    if isinstance(raw, str) and "_" in raw:
        left, right = raw.split("_", 1)
        return _parse_iso(left), _parse_iso(right)
    return None, None


def _overlap_days(start_a: date | None, end_a: date | None, start_b: date | None, end_b: date | None) -> int:
    if not all((start_a, end_a, start_b, end_b)):
        return 0
    start = max(start_a, start_b)  # type: ignore[arg-type]
    end = min(end_a, end_b)  # type: ignore[arg-type]
    return max(0, (end - start).days + 1)


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if pd.notna(number) else None


def _mean_metric(folds: list[dict[str, Any]], side: str, metric: str) -> float | None:
    values = [
        value
        for fold in folds
        if (value := _safe_float(fold[side]["metrics"]["test"].get(metric))) is not None
    ]
    return float(mean(values)) if values else None


def _leakage_suspects(feature_names: list[str]) -> list[str]:
    return sorted({name for name in feature_names if any(token in name.lower() for token in LEAKAGE_TOKENS)})


def build_walk_forward_validation(
    *,
    trainable_path: Path = DEFAULT_TRAINABLE_PATH,
    core_feature_set_path: Path = DEFAULT_CORE_FEATURE_SET,
    challenger_feature_set_path: Path = DEFAULT_CHALLENGER_FEATURE_SET,
    candidate_trace_path: Path = DEFAULT_TRACE_PATH,
    fold_count: int = 3,
    valid_days: int = 30,
    test_days: int = 30,
    random_state: int = 42,
    model_work_dir: Path | None = None,
) -> dict[str, Any]:
    if fold_count < 1:
        raise ValueError("fold_count must be at least 1")
    if valid_days < 1 or test_days < 1:
        raise ValueError("valid_days and test_days must be at least 1")

    frame = load_trainable_frame(trainable_path)
    relative_frame = augment_features_for_relative_comparison(frame)
    core_config = load_feature_set_config(core_feature_set_path)
    challenger_config = load_feature_set_config(challenger_feature_set_path)
    all_features = list(core_config.get("features", [])) + list(challenger_config.get("features", []))
    leakage_suspects = _leakage_suspects([str(value) for value in all_features])

    unique_dates = [pd.Timestamp(value) for value in sorted(frame["date"].dropna().dt.normalize().unique())]
    minimum_days = valid_days + test_days + 1
    available_fold_count = max(0, (len(unique_dates) - minimum_days) // test_days + 1)
    actual_fold_count = min(fold_count, available_fold_count)
    if actual_fold_count < 1:
        raise ValueError("not enough unique dates for requested walk-forward split")

    cleanup_dir = model_work_dir is None
    if model_work_dir is None:
        temp_parent = ROOT / "_tmp"
        temp_parent.mkdir(parents=True, exist_ok=True)
        model_work_dir = Path(tempfile.mkdtemp(prefix="architecture_v2_walk_forward_", dir=temp_parent))
    else:
        model_work_dir.mkdir(parents=True, exist_ok=True)

    folds: list[dict[str, Any]] = []
    try:
        offsets = list(reversed(range(actual_fold_count)))
        for fold_index, offset in enumerate(offsets, start=1):
            end_index = len(unique_dates) - 1 - offset * test_days
            fold_end = unique_dates[end_index]
            core_subset = frame.loc[frame["date"] <= fold_end].copy()
            challenger_subset = relative_frame.loc[relative_frame["date"] <= fold_end].copy()
            core_run, _, _, _ = train_single_feature_set(
                trainable_frame=core_subset,
                feature_set_config=core_config,
                feature_set_path=core_feature_set_path,
                model_path=model_work_dir / f"fold_{fold_index}_core.joblib",
                valid_days=valid_days,
                test_days=test_days,
                random_state=random_state,
            )
            challenger_run, _, _, _ = train_single_feature_set(
                trainable_frame=challenger_subset,
                feature_set_config=challenger_config,
                feature_set_path=challenger_feature_set_path,
                model_path=model_work_dir / f"fold_{fold_index}_challenger.joblib",
                relative_features_used=list(challenger_config.get("relative_features", [])),
                valid_days=valid_days,
                test_days=test_days,
                random_state=random_state,
            )
            core_payload = asdict(core_run)
            challenger_payload = asdict(challenger_run)
            split_parity = core_payload["split_period"] == challenger_payload["split_period"]
            deltas = {
                metric: (
                    float(challenger_payload["metrics"]["test"][metric])
                    - float(core_payload["metrics"]["test"][metric])
                )
                for metric in METRIC_NAMES
            }
            folds.append(
                {
                    "fold": fold_index,
                    "foldDataEnd": fold_end.strftime("%Y-%m-%d"),
                    "splitParity": split_parity,
                    "core": core_payload,
                    "challenger": challenger_payload,
                    "testMetricDeltaChallengerMinusCore": deltas,
                }
            )
    finally:
        if cleanup_dir:
            shutil.rmtree(model_work_dir, ignore_errors=True)

    same_period = all(fold["splitParity"] for fold in folds)
    earliest_test = min(_parse_iso(fold["core"]["split_period"]["test_start"]) for fold in folds)
    latest_test = max(_parse_iso(fold["core"]["split_period"]["test_end"]) for fold in folds)
    trace_start, trace_end = _trace_range(candidate_trace_path)
    overlap_days = _overlap_days(earliest_test, latest_test, trace_start, trace_end)
    cross_layer_ready = overlap_days > 0
    future_leakage = bool(leakage_suspects) or not same_period

    if future_leakage or not same_period:
        classification = "validation_blocked"
    elif not cross_layer_ready:
        classification = "validation_warning"
    else:
        classification = "validation_ready"

    aggregate: dict[str, Any] = {"core": {}, "challenger": {}, "deltaChallengerMinusCore": {}}
    for metric in METRIC_NAMES:
        core_mean = _mean_metric(folds, "core", metric)
        challenger_mean = _mean_metric(folds, "challenger", metric)
        aggregate["core"][metric] = core_mean
        aggregate["challenger"][metric] = challenger_mean
        aggregate["deltaChallengerMinusCore"][metric] = (
            challenger_mean - core_mean if challenger_mean is not None and core_mean is not None else None
        )

    return {
        "reportType": "architecture_v2_walk_forward_validation",
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "inputDataset": str(trainable_path),
        "method": "expanding_window_train_valid_holdout",
        "parameters": {
            "requestedFoldCount": fold_count,
            "validDays": valid_days,
            "testDays": test_days,
            "randomState": random_state,
        },
        "counts": {
            "inputRows": int(len(frame)),
            "uniqueDateCount": len(unique_dates),
            "foldCount": len(folds),
        },
        "modelValidationPeriod": {
            "start": earliest_test.isoformat() if earliest_test else None,
            "end": latest_test.isoformat() if latest_test else None,
        },
        "crossLayer": {
            "candidateTraceStart": trace_start.isoformat() if trace_start else None,
            "candidateTraceEnd": trace_end.isoformat() if trace_end else None,
            "policyPeriodOverlapDays": overlap_days,
            "samePeriodCrossLayerValidation": cross_layer_ready,
        },
        "quality": {
            "classification": classification,
            "samePeriodModelComparison": same_period,
            "futureLeakageDetected": future_leakage,
            "leakageSuspectColumns": leakage_suspects,
            "productionModelArtifactsChanged": False,
        },
        "aggregateTestMetrics": aggregate,
        "folds": folds,
    }


def _write_outputs(payload: dict[str, Any]) -> None:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    fieldnames = [
        "fold",
        "model",
        "train_start",
        "train_end",
        "valid_start",
        "valid_end",
        "test_start",
        "test_end",
        *METRIC_NAMES,
        "n_rows",
        "n_races",
    ]
    with OUT_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for fold in payload["folds"]:
            for side in ("core", "challenger"):
                split = fold[side]["split_period"]
                metrics = fold[side]["metrics"]["test"]
                writer.writerow(
                    {
                        "fold": fold["fold"],
                        "model": fold[side]["feature_set_name"],
                        **{key: split[key] for key in ("train_start", "train_end", "valid_start", "valid_end", "test_start", "test_end")},
                        **{key: metrics.get(key) for key in METRIC_NAMES},
                        "n_rows": metrics.get("n_rows"),
                        "n_races": metrics.get("n_races"),
                    }
                )
    lines = [
        "# Architecture V2 Walk-Forward Validation",
        "",
        f"- classification: {payload['quality']['classification']}",
        f"- method: {payload['method']}",
        f"- foldCount: {payload['counts']['foldCount']}",
        f"- inputRows: {payload['counts']['inputRows']}",
        f"- modelValidationPeriod: {payload['modelValidationPeriod']['start']} to {payload['modelValidationPeriod']['end']}",
        f"- samePeriodModelComparison: {payload['quality']['samePeriodModelComparison']}",
        f"- futureLeakageDetected: {payload['quality']['futureLeakageDetected']}",
        f"- policyPeriodOverlapDays: {payload['crossLayer']['policyPeriodOverlapDays']}",
        f"- samePeriodCrossLayerValidation: {payload['crossLayer']['samePeriodCrossLayerValidation']}",
        f"- productionModelArtifactsChanged: {payload['quality']['productionModelArtifactsChanged']}",
        "",
        "## Aggregate test metrics",
    ]
    for side in ("core", "challenger", "deltaChallengerMinusCore"):
        metrics = payload["aggregateTestMetrics"][side]
        lines.append(f"- {side}: " + ", ".join(f"{key}={value}" for key, value in metrics.items()))
    lines.extend(["", "## Folds"])
    for fold in payload["folds"]:
        split = fold["core"]["split_period"]
        lines.append(
            f"- fold {fold['fold']}: train={split['train_start']}..{split['train_end']}, "
            f"valid={split['valid_start']}..{split['valid_end']}, test={split['test_start']}..{split['test_end']}, "
            f"splitParity={fold['splitParity']}"
        )
    lines.extend(
        [
            "",
            "## Safety",
            "- Model artifacts are written only to a temporary work directory and removed after evaluation.",
            "- Policy thresholds, BUY/EV logic, frozen_bets, and settlement are unchanged.",
            "- Cross-layer period overlap is reported separately from model metric quality.",
        ]
    )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run architecture v2 same-window walk-forward validation")
    parser.add_argument("--trainable-path", type=Path, default=DEFAULT_TRAINABLE_PATH)
    parser.add_argument("--core-feature-set", type=Path, default=DEFAULT_CORE_FEATURE_SET)
    parser.add_argument("--challenger-feature-set", type=Path, default=DEFAULT_CHALLENGER_FEATURE_SET)
    parser.add_argument("--candidate-trace", type=Path, default=DEFAULT_TRACE_PATH)
    parser.add_argument("--fold-count", type=int, default=3)
    parser.add_argument("--valid-days", type=int, default=30)
    parser.add_argument("--test-days", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    payload = build_walk_forward_validation(
        trainable_path=args.trainable_path,
        core_feature_set_path=args.core_feature_set,
        challenger_feature_set_path=args.challenger_feature_set,
        candidate_trace_path=args.candidate_trace,
        fold_count=args.fold_count,
        valid_days=args.valid_days,
        test_days=args.test_days,
    )
    if not args.dry_run:
        _write_outputs(payload)
    print(json.dumps({key: payload[key] for key in ("counts", "modelValidationPeriod", "crossLayer", "quality", "aggregateTestMetrics")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
