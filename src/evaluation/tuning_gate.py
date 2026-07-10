from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

from src.evaluation.live_operation_summary import live_operation_summary


ROOT = Path(__file__).resolve().parents[2]
REPORT_MONITORING_ROOT = ROOT / "reports" / "monitoring"


def _normalize_date(value: str) -> str:
    token = str(value).strip().lower()
    if token == "today":
        return date.today().strftime("%Y%m%d")
    digits = "".join(ch for ch in token if ch.isdigit())
    if len(digits) != 8:
        raise ValueError(f"invalid date: {value!r}")
    return digits


def _load_latest_backfill_readiness() -> dict[str, Any]:
    candidates = sorted((ROOT / "reports" / "backtest").glob("*_backfill_tuning_readiness.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in candidates:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def tuning_gate(*, start_date: str, end_date: str) -> dict[str, Any]:
    start8 = _normalize_date(start_date)
    end8 = _normalize_date(end_date)
    live_summary = live_operation_summary(start_date=start8, end_date=end8)
    summary = live_summary.get("summary") or {}
    live_settled_bet_count = int(summary.get("liveSettledBetCount") or 0)
    live_settlement_coverage = float(summary.get("liveSettlementCoverage") or 0.0) if summary.get("liveSettlementCoverage") is not None else 0.0
    result_parse_error_rate = float(summary.get("resultParseErrorRate") or 0.0)
    frozen_bets_missing_days = int(summary.get("frozenBetsMissingDays") or 0)
    prediction_hash_missing_days = int(summary.get("predictionHashMissingDays") or 0)
    can_tune_with_live_only = bool(summary.get("canTuneWithLiveOnly"))
    backfill_readiness = _load_latest_backfill_readiness()

    reasons: list[str] = []
    if live_settled_bet_count < 100:
        reasons.append("liveSettledBetCount_below_100")
    if live_settlement_coverage < 0.5:
        reasons.append("liveSettlementCoverage_below_0.5")
    if result_parse_error_rate > 0.1:
        reasons.append("resultParseErrorRate_too_high")
    if frozen_bets_missing_days > 0:
        reasons.append("frozen_bets_missing")
    if prediction_hash_missing_days > 0:
        reasons.append("predictionHash_missing")
    if not can_tune_with_live_only:
        reasons.append("canTuneWithLiveOnly_false")

    can_start_tuning = not reasons
    next_required_action = "start_tuning_review" if can_start_tuning else "continue_live_operation_until_gate_passes"
    report = {
        "dateRange": f"{start8}_{end8}",
        "canStartTuning": can_start_tuning,
        "reasons": reasons,
        "nextRequiredAction": next_required_action,
        "liveSettledBetCount": live_settled_bet_count,
        "liveSettlementCoverage": live_settlement_coverage,
        "resultParseErrorRate": result_parse_error_rate,
        "frozenBetsMissingDays": frozen_bets_missing_days,
        "predictionHashMissingDays": prediction_hash_missing_days,
        "canTuneWithLiveOnly": can_tune_with_live_only,
        "canTuneWithBackfill": bool(backfill_readiness.get("canTuneWithBackfill")),
        "minimumLiveSettledBetCount": 100,
        "minimumLiveSettlementCoverage": 0.5,
        "resultParseErrorRateThreshold": 0.1,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
    }
    REPORT_MONITORING_ROOT.mkdir(parents=True, exist_ok=True)
    json_path = REPORT_MONITORING_ROOT / "tuning_gate.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"summary": report, "files": {"json": str(json_path)}}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Evaluate whether live tuning can start.")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    args = parser.parse_args()
    result = tuning_gate(start_date=args.start_date, end_date=args.end_date)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
