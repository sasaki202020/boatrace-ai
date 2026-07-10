from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
REPORT_DAILY_ROOT = ROOT / "reports" / "daily"
REPORT_MONITORING_ROOT = ROOT / "reports" / "monitoring"
REPORT_BACKTEST_ROOT = ROOT / "reports" / "backtest"
PRED_ROOT = ROOT / "data" / "predictions"


def _normalize_date(value: str) -> str:
    token = str(value).strip().lower()
    if token == "today":
        return date.today().strftime("%Y%m%d")
    digits = "".join(ch for ch in token if ch.isdigit())
    if len(digits) != 8:
        raise ValueError(f"invalid date: {value!r}")
    return digits


def _daterange(start8: str, end8: str) -> list[str]:
    start = datetime.strptime(start8, "%Y%m%d").date()
    end = datetime.strptime(end8, "%Y%m%d").date()
    days: list[str] = []
    current = start
    while current <= end:
        days.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return days


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_errors(date_key: str) -> list[dict[str, Any]]:
    path = ROOT / "reports" / "errors" / f"{date_key}_errors.jsonl"
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def _load_frozen_payload(date_key: str) -> dict[str, Any]:
    path = PRED_ROOT / date_key / "frozen_bets_all.json"
    return _load_json(path)


def _has_prediction_hash(payload: dict[str, Any]) -> bool:
    if not payload:
        return False
    if payload.get("predictionHash") or payload.get("predictionHashComputed"):
        return True
    for key in ("bets", "candidates", "rows"):
        rows = payload.get(key)
        if isinstance(rows, list) and any(isinstance(row, dict) and (row.get("predictionHash") or row.get("predictionHashComputed")) for row in rows):
            return True
    races = payload.get("races")
    if isinstance(races, list):
        for race in races:
            if not isinstance(race, dict):
                continue
            bets = race.get("bets")
            if isinstance(bets, list) and any(isinstance(bet, dict) and (bet.get("predictionHash") or bet.get("predictionHashComputed")) for bet in bets):
                return True
    return False


def _load_daily_payload(date_key: str) -> dict[str, Any]:
    for candidate in [
        REPORT_DAILY_ROOT / f"{date_key}_summary.json",
        REPORT_DAILY_ROOT / f"{date_key}_settlement.json",
        REPORT_DAILY_ROOT / f"{date_key}_report.json",
    ]:
        payload = _load_json(candidate)
        if payload:
            return payload
    return {}


def _load_backfill_tuning_readiness() -> dict[str, Any]:
    candidates = sorted(REPORT_BACKTEST_ROOT.glob("*_backfill_tuning_readiness.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in candidates:
        payload = _load_json(path)
        if payload:
            return payload
    return {}


def _live_metric(payload: dict[str, Any], key: str, default: Any, caster: Any) -> Any:
    if not isinstance(payload, dict) or key not in payload:
        return default
    value = payload.get(key)
    if value is None:
        return default
    try:
        return caster(value)
    except Exception:
        return default


def _ready_result_status(status: str) -> bool:
    return status in {"ok", "refund", "canceled", "no_contest", "available_without_trifecta"}


def _count_ready_statuses(root: Path) -> dict[str, int]:
    counts = {
        "odds3tOkCount": 0,
        "beforeinfoOkCount": 0,
        "resultReadyCount": 0,
        "resultMissingCount": 0,
    }
    if not root.exists():
        return counts
    for path in sorted(root.rglob("race_*.json")):
        payload = _load_json(path)
        if not payload:
            continue
        data_status = payload.get("data_status") or payload.get("dataStatus") or {}
        if not isinstance(data_status, dict):
            data_status = {}
        odds_status = str(data_status.get("odds3t") or payload.get("odds3tStatus") or "").lower()
        before_status = str(data_status.get("beforeinfo") or payload.get("beforeinfoStatus") or "").lower()
        result_payload = payload.get("result") or {}
        if not isinstance(result_payload, dict):
            result_payload = {}
        result_status = str(result_payload.get("raceStatus") or result_payload.get("dataStatus") or data_status.get("result") or "").lower()
        if odds_status in {"ok", "available", "ready"}:
            counts["odds3tOkCount"] += 1
        if before_status in {"ok", "available", "ready"}:
            counts["beforeinfoOkCount"] += 1
        if _ready_result_status(result_status):
            counts["resultReadyCount"] += 1
        else:
            counts["resultMissingCount"] += 1
    return counts


def live_operation_summary(*, start_date: str, end_date: str) -> dict[str, Any]:
    start8 = _normalize_date(start_date)
    end8 = _normalize_date(end_date)
    days = _daterange(start8, end8)

    rows: list[dict[str, Any]] = []
    warning_set: set[str] = set()
    error_days: list[str] = []
    total = {
        "daysWithFrozenBets": 0,
        "daysWithSettlement": 0,
        "liveBetCount": 0,
        "liveSettledBetCount": 0,
        "liveUnresolvedBetCount": 0,
        "liveVoidBetCount": 0,
        "liveSettledStakeAmount": 0.0,
        "livePayoutAmount": 0.0,
        "hitCount": 0,
        "resultParseErrorCount": 0,
        "resultReadyCount": 0,
        "resultMissingCount": 0,
        "predictionHashMissingDays": 0,
        "frozenBetsMissingDays": 0,
    }

    for date8 in days:
        daily = _load_daily_payload(date8)
        errors = _load_errors(date8)
        frozen = _load_frozen_payload(date8)
        normalized_counts = _count_ready_statuses(ROOT / "data" / "normalized" / date8)
        frozen_present = bool(frozen)
        daily_present = bool(daily)
        prediction_hash_present = _has_prediction_hash(frozen) if frozen_present else False
        live_bet_count = _live_metric(daily, "liveBetCount", 0, int)
        live_settled_bet_count = _live_metric(daily, "liveSettledBetCount", 0, int)
        live_unresolved_bet_count = _live_metric(daily, "liveUnresolvedBetCount", 0, int)
        live_void_bet_count = _live_metric(daily, "liveVoidBetCount", 0, int)
        live_settled_stake_amount = _live_metric(daily, "liveSettledStakeAmount", 0.0, float)
        live_payout_amount = _live_metric(daily, "livePayoutAmount", 0.0, float)
        hit_count = _live_metric(daily, "hitCount", 0, int)
        result_parse_error_count = _live_metric(daily, "resultParseErrorCount", 0, int)
        result_ready_count = _live_metric(daily, "resultReadyCount", normalized_counts["resultReadyCount"], int)
        result_missing_count = _live_metric(daily, "resultMissingCount", normalized_counts["resultMissingCount"], int)
        live_settlement_coverage = daily.get("liveSettlementCoverage") if "liveSettlementCoverage" in daily else None
        if live_settlement_coverage is None and live_bet_count > 0:
            live_settlement_coverage = round(live_settled_bet_count / live_bet_count, 4)
        live_settled_roi = daily.get("liveSettledRoi") if "liveSettledRoi" in daily else None
        if live_settled_roi is None and live_settled_stake_amount > 0:
            live_settled_roi = round(live_payout_amount / live_settled_stake_amount, 4)
        live_hit_rate = daily.get("liveHitRate") if "liveHitRate" in daily else None
        can_tune_with_live_only = bool(
            frozen_present
            and daily_present
            and prediction_hash_present
            and live_settled_bet_count >= 100
            and float(live_settlement_coverage or 0) >= 0.5
            and result_parse_error_count == 0
        )
        row_warnings = list(dict.fromkeys((daily.get("warnings") or []) + (["missing_frozen_bets"] if not frozen_present else []) + (["missing_daily_summary"] if not daily_present else []) + (["prediction_hash_missing"] if not prediction_hash_present else []) + (["errors_present"] if errors else [])))
        if prediction_hash_present:
            row_warnings = [warning for warning in row_warnings if warning != "prediction_hash_missing"]
        if frozen_present:
            row_warnings = [warning for warning in row_warnings if warning != "missing_frozen_bets"]
        row_warnings = sorted(row_warnings)
        rows.append(
            {
                "date": date8,
                "hasFrozenBets": frozen_present,
                "hasSettlement": daily_present,
                "liveBetCount": live_bet_count,
                "liveSettledBetCount": live_settled_bet_count,
                "liveUnresolvedBetCount": live_unresolved_bet_count,
                "liveVoidBetCount": live_void_bet_count,
                "liveSettlementCoverage": live_settlement_coverage,
                "liveSettledRoi": live_settled_roi,
                "liveHitRate": live_hit_rate,
                "resultReadyCount": result_ready_count,
                "resultMissingCount": result_missing_count,
                "resultParseErrorCount": result_parse_error_count,
                "errorCount": len(errors),
                "warnings": row_warnings,
                "canTuneWithLiveOnly": can_tune_with_live_only,
            }
        )

        if frozen_present:
            total["daysWithFrozenBets"] += 1
        else:
            total["frozenBetsMissingDays"] += 1
        if daily_present:
            total["daysWithSettlement"] += 1
        if errors:
            error_days.append(date8)
        total["liveBetCount"] += live_bet_count
        total["liveSettledBetCount"] += live_settled_bet_count
        total["liveUnresolvedBetCount"] += live_unresolved_bet_count
        total["liveVoidBetCount"] += live_void_bet_count
        total["liveSettledStakeAmount"] += live_settled_stake_amount
        total["livePayoutAmount"] += live_payout_amount
        total["hitCount"] += hit_count
        total["resultParseErrorCount"] += result_parse_error_count
        total["resultReadyCount"] += result_ready_count
        total["resultMissingCount"] += result_missing_count
        if not prediction_hash_present:
            total["predictionHashMissingDays"] += 1
        warning_set.update(row_warnings)

    live_settlement_coverage = round(total["liveSettledBetCount"] / total["liveBetCount"], 4) if total["liveBetCount"] > 0 else None
    live_settled_roi = round(total["livePayoutAmount"] / total["liveSettledStakeAmount"], 4) if total["liveSettledStakeAmount"] > 0 else None
    live_hit_rate = round(total["hitCount"] / total["liveSettledBetCount"], 4) if total["liveSettledBetCount"] > 0 else None
    result_parse_error_rate = round(total["resultParseErrorCount"] / max(total["resultReadyCount"] + total["resultMissingCount"], 1), 4)
    backfill_readiness = _load_backfill_tuning_readiness()
    can_tune_with_live_only = bool(
        total["liveSettledBetCount"] >= 100
        and (live_settlement_coverage or 0) >= 0.5
        and result_parse_error_rate <= 0.1
        and total["frozenBetsMissingDays"] == 0
        and total["predictionHashMissingDays"] == 0
        and total["daysWithSettlement"] > 0
    )

    summary = {
        "dateRange": f"{start8}_{end8}",
        "days": len(days),
        "daysWithFrozenBets": total["daysWithFrozenBets"],
        "daysWithSettlement": total["daysWithSettlement"],
        "liveBetCount": total["liveBetCount"],
        "liveSettledBetCount": total["liveSettledBetCount"],
        "liveUnresolvedBetCount": total["liveUnresolvedBetCount"],
        "liveVoidBetCount": total["liveVoidBetCount"],
        "liveSettlementCoverage": live_settlement_coverage,
        "liveSettledRoi": live_settled_roi,
        "liveHitRate": live_hit_rate,
        "resultReadyCount": total["resultReadyCount"],
        "resultMissingCount": total["resultMissingCount"],
        "resultParseErrorCount": total["resultParseErrorCount"],
        "resultParseErrorRate": result_parse_error_rate,
        "errorDays": error_days,
        "warnings": sorted(warning_set),
        "canTuneWithLiveOnly": can_tune_with_live_only,
        "canTuneWithBackfill": bool(backfill_readiness.get("canTuneWithBackfill")),
        "predictionHashMissingDays": total["predictionHashMissingDays"],
        "frozenBetsMissingDays": total["frozenBetsMissingDays"],
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
    }

    REPORT_MONITORING_ROOT.mkdir(parents=True, exist_ok=True)
    json_path = REPORT_MONITORING_ROOT / "live_operation_summary.json"
    csv_path = REPORT_MONITORING_ROOT / "live_operation_summary.csv"
    json_path.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        fieldnames = [
            "date",
            "hasFrozenBets",
            "hasSettlement",
            "liveBetCount",
            "liveSettledBetCount",
            "liveUnresolvedBetCount",
            "liveVoidBetCount",
            "liveSettlementCoverage",
            "liveSettledRoi",
            "liveHitRate",
            "resultReadyCount",
            "resultMissingCount",
            "resultParseErrorCount",
            "errorCount",
            "canTuneWithLiveOnly",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})
    return {"summary": summary, "rows": rows, "files": {"json": str(json_path), "csv": str(csv_path)}}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Summarize live operation results across a date range.")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    args = parser.parse_args()
    result = live_operation_summary(start_date=args.start_date, end_date=args.end_date)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
