from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from src.evaluation.backtest_day import build_backtest_summary
from src.evaluation.settle_results import inspect_prediction_sources, settle_daily_predictions


ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = ROOT / "reports" / "backtest"
DAILY_ROOT = ROOT / "reports" / "daily"


def _normalize_date(value: str) -> str:
    token = str(value).strip().lower()
    if token == "today":
        return date.today().strftime("%Y-%m-%d")
    digits = "".join(ch for ch in token if ch.isdigit())
    if len(digits) != 8:
        raise ValueError(f"invalid date: {value!r}")
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"


def _daterange(start_date: str, end_date: str) -> list[str]:
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    days: list[str] = []
    current = start
    while current <= end:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def _numeric(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except Exception:
        return None


def _bucket_ev(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value < 0.9:
        return "ev_lt_0_9"
    if value < 1.0:
        return "ev_0_9_1_0"
    if value < 1.05:
        return "ev_1_0_1_05"
    if value < 1.15:
        return "ev_1_05_1_15"
    if value < 1.30:
        return "ev_1_15_1_30"
    return "ev_gte_1_30"


def _bucket_odds(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value < 5:
        return "odds_lt_5"
    if value < 10:
        return "odds_5_10"
    if value < 20:
        return "odds_10_20"
    if value < 50:
        return "odds_20_50"
    if value < 100:
        return "odds_50_100"
    return "odds_gte_100"


def _settled_flag(status: str) -> bool:
    return status in {"hit", "miss"}


def _bucket_can_evaluate(settled_bet_count: int) -> bool:
    return settled_bet_count >= 30


def _normalize_prediction_source_mode(value: str | None) -> str:
    token = str(value or "auto").strip().lower()
    if token in {"auto", "live", "ui_recovered", "backfill", "all"}:
        return token
    return "auto"


def _output_prefix_for_source(source: str) -> str:
    source = _normalize_prediction_source_mode(source)
    if source == "auto":
        return "auto"
    if source in {"live", "ui_recovered", "backfill"}:
        return source
    return "auto"


@dataclass
class BucketAgg:
    bet_count: int = 0
    settled_bet_count: int = 0
    unresolved_bet_count: int = 0
    void_bet_count: int = 0
    hit_count: int = 0
    stake_amount: float = 0.0
    settled_stake_amount: float = 0.0
    unresolved_stake_amount: float = 0.0
    void_stake_amount: float = 0.0
    payout_amount: float = 0.0
    sum_ev: float = 0.0
    ev_count: int = 0
    sum_prob: float = 0.0
    prob_count: int = 0
    sum_odds: float = 0.0
    odds_count: int = 0

    def add(self, row: dict[str, Any]) -> None:
        self.bet_count += 1
        status = str(row.get("settleStatus") or "").lower()
        stake = _numeric(row.get("stake")) or 0.0
        payout = _numeric(row.get("payout"))
        ev = _numeric(row.get("expectedValue"))
        prob = _numeric(row.get("prob"))
        odds = _numeric(row.get("odds"))
        self.stake_amount += stake
        if payout is not None:
            self.payout_amount += payout
        if _settled_flag(status):
            self.settled_bet_count += 1
            self.settled_stake_amount += stake
            if status == "hit":
                self.hit_count += 1
        elif status == "void":
            self.void_bet_count += 1
            self.void_stake_amount += stake
        elif status in {"pending", "parse_error", "no_result"}:
            self.unresolved_bet_count += 1
            self.unresolved_stake_amount += stake
        if ev is not None:
            self.sum_ev += ev
            self.ev_count += 1
        if prob is not None:
            self.sum_prob += prob
            self.prob_count += 1
        if odds is not None:
            self.sum_odds += odds
            self.odds_count += 1

    def to_row(self, label: str, *, extra_cols: dict[str, Any] | None = None) -> dict[str, Any]:
        extra_cols = extra_cols or {}
        return {
            "bucket": label,
            "betCount": self.bet_count,
            "settledBetCount": self.settled_bet_count,
            "unresolvedBetCount": self.unresolved_bet_count,
            "voidBetCount": self.void_bet_count,
            "hitCount": self.hit_count,
            "stakeAmount": round(self.stake_amount, 2),
            "settledStakeAmount": round(self.settled_stake_amount, 2),
            "unresolvedStakeAmount": round(self.unresolved_stake_amount, 2),
            "voidStakeAmount": round(self.void_stake_amount, 2),
            "payoutAmount": round(self.payout_amount, 2),
            "settledRoi": round(self.payout_amount / self.settled_stake_amount, 4) if self.settled_stake_amount > 0 else None,
            "hitRate": round(self.hit_count / self.settled_bet_count, 4) if self.settled_bet_count else None,
            "avgExpectedValue": round(self.sum_ev / self.ev_count, 4) if self.ev_count else None,
            "avgProb": round(self.sum_prob / self.prob_count, 4) if self.prob_count else None,
            "avgOdds": round(self.sum_odds / self.odds_count, 4) if self.odds_count else None,
            "settlementCoverage": round(self.settled_bet_count / self.bet_count, 4) if self.bet_count else None,
            "canEvaluateBucket": _bucket_can_evaluate(self.settled_bet_count),
            **extra_cols,
        }


def _warns(summary: dict[str, Any]) -> list[str]:
    warnings = list(summary.get("warnings") or [])
    buy_count = int(summary.get("buyCount") or 0)
    race_count = int(summary.get("raceCount") or 0)
    settled_bet_count = int(summary.get("settledBetCount") or 0)
    parse_error_count = int(summary.get("resultParseErrorCount") or 0)
    if buy_count > 50:
        warnings.append("high_daily_buy_count")
    if race_count > 0 and buy_count / max(race_count, 1) > 3:
        warnings.append("high_buy_per_race")
    if buy_count > 0 and settled_bet_count / buy_count < 0.5:
        warnings.append("low_settlement_coverage")
    if parse_error_count >= max(5, race_count // 3):
        warnings.append("high_result_parse_error")
    if settled_bet_count < 30:
        warnings.append("insufficient_settled_sample_do_not_tune")
    if settled_bet_count < 100:
        warnings.append("low_settled_sample_tuning_unreliable")
    return sorted(dict.fromkeys(warnings))


def _prediction_reason_from_source(info: dict[str, Any]) -> tuple[str, str]:
    source = str(info.get("source") or "missing")
    warnings = set(str(item) for item in (info.get("warnings") or []))
    if source == "missing":
        frozen_state = str(info.get("frozenState") or "missing")
        ui_state = str(info.get("uiState") or "missing")
        if frozen_state == "invalid":
            return "frozen_bets_invalid", "recreate frozen_bets_all.json from pre-result predictions"
        if frozen_state == "empty":
            return "frozen_bets_empty", "ensure frozen_bets contains prediction rows before result stage"
        if frozen_state == "missing" and ui_state == "present":
            return "frozen_bets_missing_but_ui_available", "freeze bets before result stage; ui recovery is reference only"
        return "frozen_bets_missing_and_ui_missing", "generate frozen_bets_all.json or keep pre-result UI snapshots"
    if source == "ui_recovered":
        if "prediction_hash_changed" in warnings:
            return "prediction_hash_changed", "verify frozen predictions were not edited after result stage"
        if "prediction_hash_missing" in warnings:
            return "prediction_hash_missing", "write predictionHash when freezing bets"
        if str(info.get("uiStage") or "") == "result":
            return "ui_recovered_predictions_used", "use frozen_bets_all.json for tuning; UI was recovered from result-stage output"
        return "ui_recovered_predictions_used", "prefer frozen_bets_all.json for backtests"
    if "prediction_hash_changed" in warnings:
        return "prediction_hash_changed", "verify frozen predictions were not edited"
    if "prediction_hash_missing" in warnings:
        return "prediction_hash_missing", "write predictionHash to frozen_bets before running backtest"
    return "prediction_missing", "freeze predictions before result stage"


def run_backtest_range(*, start_date: str, end_date: str, jcd: str = "all", stake_per_buy: int = 100, prediction_source: str = "auto") -> dict[str, Any]:
    start = _normalize_date(start_date)
    end = _normalize_date(end_date)
    days = _daterange(start, end)
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    source_mode = _normalize_prediction_source_mode(prediction_source)
    if source_mode == "all":
        source_results: dict[str, dict[str, Any]] = {}
        compare_rows: list[dict[str, Any]] = []
        for sub_source in ["live", "ui_recovered", "backfill"]:
            result = run_backtest_range(
                start_date=start_date,
                end_date=end_date,
                jcd=jcd,
                stake_per_buy=stake_per_buy,
                prediction_source=sub_source,
            )
            source_results[sub_source] = result
            summary = result.get("summary") or {}
            compare_rows.append(
                {
                    "sourceType": sub_source,
                    "days": summary.get("days", 0),
                    "raceCount": summary.get("raceCount", 0),
                    "betCount": summary.get("betCount", 0),
                    "settledBetCount": summary.get("settledBetCount", 0),
                    "unresolvedBetCount": summary.get("unresolvedBetCount", 0),
                    "voidBetCount": summary.get("voidBetCount", 0),
                    "resultOkCount": summary.get("resultOkCount", 0),
                    "resultMissingCount": summary.get("resultMissingCount", 0),
                    "settlementCoverage": summary.get("settlementCoverage"),
                    "frozenStakeAmount": summary.get("frozenStakeAmount", 0.0),
                    "settledStakeAmount": summary.get("settledStakeAmount", 0.0),
                    "payoutAmount": summary.get("payoutAmount", 0.0),
                    "profit": summary.get("profit", 0.0),
                    "settledRoi": summary.get("settledRoi"),
                    "hitRate": summary.get("hitRate"),
                    "avgExpectedValue": summary.get("avgExpectedValue"),
                    "avgOdds": summary.get("avgOdds"),
                    "canTune": bool(summary.get("canTuneBuyThreshold")),
                    "warningCount": len(summary.get("warnings") or []),
                    "warnings": "|".join(summary.get("warnings") or []),
                }
            )
        date_tag = f"{start.replace('-', '')}_{end.replace('-', '')}"
        comparison_json = REPORT_ROOT / f"{date_tag}_source_comparison.json"
        comparison_csv = REPORT_ROOT / f"{date_tag}_source_comparison.csv"
        with comparison_csv.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "sourceType",
                    "days",
                    "raceCount",
                    "betCount",
                    "settledBetCount",
                    "unresolvedBetCount",
                    "voidBetCount",
                    "resultOkCount",
                    "resultMissingCount",
                    "settlementCoverage",
                    "frozenStakeAmount",
                    "settledStakeAmount",
                    "payoutAmount",
                    "profit",
                    "settledRoi",
                    "hitRate",
                    "avgExpectedValue",
                    "avgOdds",
                    "canTune",
                    "warningCount",
                    "warnings",
                ],
            )
            writer.writeheader()
            for row in compare_rows:
                writer.writerow(row)
        comparison_payload = {
            "dateRange": f"{start.replace('-', '')}_{end.replace('-', '')}",
            "sources": compare_rows,
            "generatedAt": datetime.now().isoformat(timespec="seconds"),
        }
        comparison_json.write_text(json.dumps(comparison_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "predictionSource": "all",
            "sources": source_results,
            "comparison": comparison_payload,
            "files": {"comparisonJson": str(comparison_json), "comparisonCsv": str(comparison_csv)},
        }

    by_day_rows: list[dict[str, Any]] = []
    diagnostics_rows: list[dict[str, Any]] = []
    by_venue: dict[str, BucketAgg] = defaultdict(BucketAgg)
    by_ev_bucket: dict[str, BucketAgg] = defaultdict(BucketAgg)
    by_odds_bucket: dict[str, BucketAgg] = defaultdict(BucketAgg)
    all_bets: list[dict[str, Any]] = []
    warnings: list[str] = []
    venue_names: set[str] = set()
    days_with_frozen_bets = 0
    days_with_ui_recovered = 0
    days_with_backfilled_bets = 0
    days_missing_predictions = 0
    days_missing_any_prediction = 0
    days_with_results = 0
    days_with_predictions = 0
    result_source_breakdown: dict[str, int] = defaultdict(int)

    for day in days:
        date_key = day.replace("-", "")
        source_info = inspect_prediction_sources(date_key=date_key, jcd=jcd)
        if source_info.get("hasFrozenBets"):
            days_with_frozen_bets += 1
        if source_info.get("hasAiPredictions"):
            days_with_predictions += 1
        else:
            days_missing_predictions += 1

        settlement = settle_daily_predictions(
            date=day,
            jcd=jcd,
            stake_per_buy=stake_per_buy,
            allow_live_fallback=False,
            allow_backfill_fallback=False,
            prediction_source=source_mode,
            persist_ui_recovered=False,
        )
        summary = build_backtest_summary(settlement)
        day_warnings = _warns(settlement)
        day_warnings.extend(source_info.get("warnings") or [])
        summary["warnings"] = sorted(dict.fromkeys(day_warnings))
        for key, value in (settlement.get("resultSourceBreakdown") or {}).items():
            result_source_breakdown[str(key)] += int(value or 0)
        if int(settlement.get("resultReadyCount") or 0) > 0:
            days_with_results += 1
        live_bets = int(settlement.get("liveBetCount") or 0)
        ui_recovered_bets = int(settlement.get("uiRecoveredBetCount") or 0)
        backfill_bets = int(settlement.get("backfillBetCount") or 0)
        if live_bets > 0:
            days_with_frozen_bets += 1
        if ui_recovered_bets > 0:
            days_with_ui_recovered += 1
        if backfill_bets > 0:
            days_with_backfilled_bets += 1
        if live_bets + ui_recovered_bets + backfill_bets == 0:
            days_missing_any_prediction += 1

        by_day_rows.append(
            {
                "date": date_key,
                "raceCount": summary.get("raceCount", 0),
                "betCount": settlement.get("betCount", 0),
                "settledBetCount": settlement.get("settledBetCount", 0),
                "unresolvedBetCount": settlement.get("unresolvedBetCount", 0),
                "voidBetCount": settlement.get("voidBetCount", 0),
                "hitCount": settlement.get("hitCount", 0),
                "missCount": settlement.get("missCount", 0),
                "frozenStakeAmount": settlement.get("frozenStakeAmount", 0.0),
                "settledStakeAmount": settlement.get("settledStakeAmount", 0.0),
                "unresolvedStakeAmount": settlement.get("unresolvedStakeAmount", 0.0),
                "voidStakeAmount": settlement.get("voidStakeAmount", 0.0),
                "payoutAmount": settlement.get("payoutAmount", 0.0),
                "profit": settlement.get("profit", 0.0),
                "settledRoi": settlement.get("settledRoi"),
                "hitRate": settlement.get("hitRate"),
                "resultOkCount": settlement.get("resultOkCount", 0),
                "resultHtmlOkCount": settlement.get("resultHtmlOkCount", 0),
                "resultTxtOkCount": settlement.get("resultTxtOkCount", 0),
                "resultParseErrorCount": settlement.get("resultParseErrorCount", 0),
                "resultPendingCount": settlement.get("resultPendingCount", 0),
                "resultMissingCount": settlement.get("resultMissingCount", 0),
                "settlementCoverage": settlement.get("settledBetCount", 0) / settlement.get("betCount", 1) if settlement.get("betCount") else None,
                "predictionCoverage": 1.0 if source_info.get("hasAiPredictions") else 0.0,
                "resultCoverage": (int(settlement.get("resultOkCount") or 0) / max(1, int(settlement.get("raceCount") or 0))),
                "daysWithFrozenBets": 1 if live_bets > 0 else 0,
                "daysWithUiRecovered": 1 if ui_recovered_bets > 0 else 0,
                "daysWithBackfilledBets": 1 if backfill_bets > 0 else 0,
                "daysMissingPredictions": 1 if (live_bets + ui_recovered_bets + backfill_bets) == 0 else 0,
                "daysMissingAnyPrediction": 1 if (live_bets + ui_recovered_bets + backfill_bets) == 0 else 0,
                "daysWithResults": 1 if int(settlement.get("resultReadyCount") or 0) > 0 else 0,
                "canTuneBuyThreshold": False,
                "resultsStatus": settlement.get("resultsStatus"),
                "warnings": "|".join(sorted(dict.fromkeys(day_warnings))),
                "liveSettledBetCount": settlement.get("liveSettledBetCount", 0),
                "uiRecoveredSettledBetCount": settlement.get("uiRecoveredSettledBetCount", 0),
                "backfillSettledBetCount": settlement.get("backfillSettledBetCount", 0),
            }
        )
        warnings.extend(day_warnings)

        reason, fix = _prediction_reason_from_source(source_info)
        if reason != "prediction_missing" or not source_info.get("hasAiPredictions"):
            diagnostics_rows.append(
                {
                    "date": date_key,
                    "jcd": source_info.get("jcd", jcd),
                    "rno": "",
                    "reason": reason,
                    "hasFrozenBets": bool(source_info.get("hasFrozenBets")),
                    "hasUiJson": bool(source_info.get("hasUiJson")),
                    "hasAiPredictions": bool(source_info.get("hasAiPredictions")),
                    "buyCount": int(settlement.get("betCount") or 0),
                    "uiPath": ";".join(source_info.get("uiPaths") or []),
                    "frozenPath": ";".join(source_info.get("frozenPaths") or []),
                    "suggestedFix": fix,
                }
            )

        for row in settlement.get("bets") or []:
            if not isinstance(row, dict):
                continue
            all_bets.append(row)
            venue = str(row.get("venue") or row.get("jcd") or "")
            if venue:
                venue_names.add(venue)
            by_venue[venue].add(row)
            ev_bucket = _bucket_ev(_numeric(row.get("expectedValue")))
            odds_bucket = _bucket_odds(_numeric(row.get("odds")))
            by_ev_bucket[ev_bucket].add(row)
            by_odds_bucket[odds_bucket].add(row)

    bet_count = sum(int(row.get("betCount") or 0) for row in by_day_rows)
    settled_bet_count = sum(int(row.get("settledBetCount") or 0) for row in by_day_rows)
    unresolved_bet_count = sum(int(row.get("unresolvedBetCount") or 0) for row in by_day_rows)
    void_bet_count = sum(int(row.get("voidBetCount") or 0) for row in by_day_rows)
    hit_count = sum(int(row.get("hitCount") or 0) for row in by_day_rows)
    miss_count = sum(int(row.get("missCount") or 0) for row in by_day_rows)
    frozen_stake_amount = sum(float(row.get("frozenStakeAmount") or 0.0) for row in by_day_rows)
    settled_stake_amount = sum(float(row.get("settledStakeAmount") or 0.0) for row in by_day_rows)
    unresolved_stake_amount = sum(float(row.get("unresolvedStakeAmount") or 0.0) for row in by_day_rows)
    void_stake_amount = sum(float(row.get("voidStakeAmount") or 0.0) for row in by_day_rows)
    payout_amount = sum(float(row.get("payoutAmount") or 0.0) for row in by_day_rows)
    profit = payout_amount - settled_stake_amount
    settled_roi = (payout_amount / settled_stake_amount) if settled_stake_amount > 0 else None
    hit_rate = (hit_count / settled_bet_count) if settled_bet_count > 0 else None
    live_settled_bet_count = sum(int(row.get("liveSettledBetCount") or 0) for row in by_day_rows)
    ui_recovered_settled_bet_count = sum(int(row.get("uiRecoveredSettledBetCount") or 0) for row in by_day_rows)
    backfill_settled_bet_count = sum(int(row.get("backfillSettledBetCount") or 0) for row in by_day_rows)
    days_with_frozen_bets = sum(int(row.get("daysWithFrozenBets") or 0) for row in by_day_rows)
    days_with_ui_recovered = sum(int(row.get("daysWithUiRecovered") or 0) for row in by_day_rows)
    days_with_backfilled_bets = sum(int(row.get("daysWithBackfilledBets") or 0) for row in by_day_rows)
    days_missing_predictions = sum(int(row.get("daysMissingPredictions") or 0) for row in by_day_rows)
    days_missing_any_prediction = sum(int(row.get("daysMissingAnyPrediction") or 0) for row in by_day_rows)
    days_with_results = sum(int(row.get("daysWithResults") or 0) for row in by_day_rows)
    days_with_k_result = sum(1 for row in by_day_rows if int(row.get("resultTxtOkCount") or 0) > 0)
    ev_values = [_numeric(row.get("expectedValue")) for row in all_bets if _numeric(row.get("expectedValue")) is not None]
    odds_values = [_numeric(row.get("odds")) for row in all_bets if _numeric(row.get("odds")) is not None]

    def _bucket_rows_to_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: row.get(key) for key in fieldnames})

    date_tag = f"{start.replace('-', '')}_{end.replace('-', '')}"
    output_prefix = _output_prefix_for_source(source_mode)
    day_path = REPORT_ROOT / f"{date_tag}_{output_prefix}_summary.json"
    day_csv = REPORT_ROOT / f"{date_tag}_{output_prefix}_by_day.csv"
    venue_csv = REPORT_ROOT / f"{date_tag}_{output_prefix}_by_venue.csv"
    ev_csv = REPORT_ROOT / f"{date_tag}_{output_prefix}_by_ev_bucket.csv"
    odds_csv = REPORT_ROOT / f"{date_tag}_{output_prefix}_by_odds_bucket.csv"
    coverage_path = REPORT_ROOT / f"{date_tag}_{output_prefix}_coverage.json"
    diagnostics_path = REPORT_ROOT / f"{date_tag}_{output_prefix}_prediction_missing_diagnostics.csv"
    tuning_path = REPORT_ROOT / f"{date_tag}_{output_prefix}_tuning_readiness.json"

    venue_rows = [agg.to_row(venue, extra_cols={"venue": venue}) for venue, agg in sorted(by_venue.items(), key=lambda item: item[0])]
    ev_rows = [by_ev_bucket[label].to_row(label) for label in ["ev_lt_0_9", "ev_0_9_1_0", "ev_1_0_1_05", "ev_1_05_1_15", "ev_1_15_1_30", "ev_gte_1_30"] if label in by_ev_bucket]
    odds_rows = [by_odds_bucket[label].to_row(label) for label in ["odds_lt_5", "odds_5_10", "odds_10_20", "odds_20_50", "odds_50_100", "odds_gte_100"] if label in by_odds_bucket]

    settlement_coverage = round(settled_bet_count / bet_count, 4) if bet_count else None
    live_settlement_coverage = round(live_settled_bet_count / max(1, sum(int(row.get("liveBetCount") or 0) for row in by_day_rows)), 4) if sum(int(row.get("liveBetCount") or 0) for row in by_day_rows) else None
    backfill_settlement_coverage = round(backfill_settled_bet_count / max(1, sum(int(row.get("backfillBetCount") or 0) for row in by_day_rows)), 4) if sum(int(row.get("backfillBetCount") or 0) for row in by_day_rows) else None
    prediction_coverage = round(days_with_predictions / len(days), 4) if days else None
    live_prediction_coverage = round(days_with_frozen_bets / len(days), 4) if days else None
    backfill_prediction_coverage = round(days_with_backfilled_bets / len(days), 4) if days else None
    result_coverage = round(sum(int(r.get("resultOkCount") or 0) for r in by_day_rows) / max(1, sum(int(r.get("raceCount") or 0) for r in by_day_rows)), 4)

    summary = {
        "dateRange": date_tag,
        "predictionSource": source_mode,
        "sourceType": source_mode,
        "days": len(days),
        "venueCount": len(venue_names),
        "raceCount": sum(int(r.get("raceCount") or 0) for r in by_day_rows),
        "betCount": bet_count,
        "settledBetCount": settled_bet_count,
        "unresolvedBetCount": unresolved_bet_count,
        "voidBetCount": void_bet_count,
        "hitCount": hit_count,
        "missCount": miss_count,
        "frozenStakeAmount": round(frozen_stake_amount, 2),
        "settledStakeAmount": round(settled_stake_amount, 2),
        "unresolvedStakeAmount": round(unresolved_stake_amount, 2),
        "voidStakeAmount": round(void_stake_amount, 2),
        "payoutAmount": round(payout_amount, 2),
        "profit": round(profit, 2),
        "settledRoi": round(settled_roi, 4) if settled_roi is not None else None,
        "hitRate": round(hit_rate, 4) if hit_rate is not None else None,
        "avgExpectedValue": round(sum(ev_values) / len(ev_values), 4) if ev_values else None,
        "avgOdds": round(sum(odds_values) / len(odds_values), 4) if odds_values else None,
        "medianOdds": round(statistics.median(odds_values), 4) if odds_values else None,
        "resultOkCount": sum(int(r.get("resultOkCount") or 0) for r in by_day_rows),
        "resultHtmlOkCount": sum(int(r.get("resultHtmlOkCount") or 0) for r in by_day_rows),
        "resultTxtOkCount": sum(int(r.get("resultTxtOkCount") or 0) for r in by_day_rows),
        "resultParseErrorCount": sum(int(r.get("resultParseErrorCount") or 0) for r in by_day_rows),
        "resultPendingCount": sum(int(r.get("resultPendingCount") or 0) for r in by_day_rows),
        "resultMissingCount": sum(int(r.get("resultMissingCount") or 0) for r in by_day_rows),
        "resultSourceBreakdown": dict(result_source_breakdown),
        "settlementCoverage": settlement_coverage,
        "liveSettlementCoverage": live_settlement_coverage,
        "backfillSettlementCoverage": backfill_settlement_coverage,
        "predictionCoverage": prediction_coverage,
        "livePredictionCoverage": live_prediction_coverage,
        "backfillPredictionCoverage": backfill_prediction_coverage,
        "resultCoverage": result_coverage,
        "daysWithFrozenBets": days_with_frozen_bets,
        "daysWithLiveFrozenBets": days_with_frozen_bets,
        "daysWithUiRecovered": days_with_ui_recovered,
        "daysWithUiRecoveredBets": days_with_ui_recovered,
        "daysWithBackfilledBets": days_with_backfilled_bets,
        "kResultDays": days_with_k_result,
        "kResultMissingDays": max(0, len(days) - days_with_k_result),
        "daysMissingPredictions": days_missing_predictions,
        "daysMissingAnyPrediction": days_missing_any_prediction,
        "daysWithResults": days_with_results,
        "liveSettledBetCount": live_settled_bet_count,
        "uiRecoveredSettledBetCount": ui_recovered_settled_bet_count,
        "backfillSettledBetCount": backfill_settled_bet_count,
        "currentBackfillSettledBetCount": backfill_settled_bet_count,
        "remainingSettledBetCountNeeded": max(0, 300 - backfill_settled_bet_count),
        "warnings": sorted(dict.fromkeys(warnings)),
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
    }

    if settled_bet_count < 30 and "insufficient_settled_sample_do_not_tune" not in summary["warnings"]:
        summary["warnings"].append("insufficient_settled_sample_do_not_tune")
    if settled_bet_count < 100 and "low_settled_sample_tuning_unreliable" not in summary["warnings"]:
        summary["warnings"].append("low_settled_sample_tuning_unreliable")
    if settlement_coverage is not None and settlement_coverage < 0.5 and "low_settlement_coverage" not in summary["warnings"]:
        summary["warnings"].append("low_settlement_coverage")
    summary["warnings"] = sorted(dict.fromkeys(summary["warnings"]))
    backfill_leakage_guard_status = "ok" if backfill_settled_bet_count >= 0 else "warning"
    can_tune_with_live_only = bool(live_settled_bet_count >= 100 and (live_settlement_coverage is None or live_settlement_coverage >= 0.5))
    result_source_coverage = round((summary["resultHtmlOkCount"] + summary["resultTxtOkCount"]) / max(1, summary["resultOkCount"]), 4) if summary["resultOkCount"] else 0.0
    can_tune_with_backfill = bool(
        backfill_settled_bet_count >= 300
        and backfill_leakage_guard_status == "ok"
        and (backfill_settlement_coverage is None or backfill_settlement_coverage >= 0.5)
        and result_source_coverage >= 0.9
    )
    can_tune_buy_threshold = bool(can_tune_with_live_only and can_tune_with_backfill and (settlement_coverage is None or settlement_coverage >= 0.5))
    summary["canTuneBuyThreshold"] = can_tune_buy_threshold
    summary["canTuneWithLiveOnly"] = can_tune_with_live_only
    summary["canTuneWithBackfill"] = can_tune_with_backfill
    summary["tuningDataSourceRecommendation"] = (
        "continue_forward_collection"
        if not can_tune_with_live_only and not can_tune_with_backfill
        else "analyze_backfill_but_do_not_deploy_without_live_confirmation"
        if can_tune_with_backfill and not can_tune_with_live_only
        else "live_only_ready"
        if can_tune_with_live_only
        else "continue_forward_collection"
    )

    _bucket_rows_to_csv(
        day_csv,
        by_day_rows,
        [
            "date",
            "raceCount",
            "betCount",
            "settledBetCount",
            "unresolvedBetCount",
            "voidBetCount",
            "hitCount",
            "missCount",
            "frozenStakeAmount",
            "settledStakeAmount",
            "unresolvedStakeAmount",
            "voidStakeAmount",
            "payoutAmount",
            "profit",
            "settledRoi",
            "hitRate",
            "resultOkCount",
            "resultParseErrorCount",
            "resultPendingCount",
            "resultMissingCount",
            "settlementCoverage",
            "predictionCoverage",
            "resultCoverage",
            "daysWithFrozenBets",
            "daysWithUiRecovered",
            "daysWithBackfilledBets",
            "daysMissingPredictions",
            "daysMissingAnyPrediction",
            "daysWithResults",
            "canTuneBuyThreshold",
            "resultsStatus",
            "warnings",
            "liveSettledBetCount",
            "uiRecoveredSettledBetCount",
            "backfillSettledBetCount",
        ],
    )
    _bucket_rows_to_csv(
        venue_csv,
        venue_rows,
        [
            "bucket",
            "venue",
            "betCount",
            "settledBetCount",
            "unresolvedBetCount",
            "voidBetCount",
            "hitCount",
            "stakeAmount",
            "settledStakeAmount",
            "unresolvedStakeAmount",
            "voidStakeAmount",
            "payoutAmount",
            "settledRoi",
            "hitRate",
            "avgExpectedValue",
            "avgProb",
            "avgOdds",
            "settlementCoverage",
            "canEvaluateBucket",
        ],
    )
    _bucket_rows_to_csv(
        ev_csv,
        ev_rows,
        [
            "bucket",
            "betCount",
            "settledBetCount",
            "unresolvedBetCount",
            "voidBetCount",
            "hitCount",
            "stakeAmount",
            "settledStakeAmount",
            "unresolvedStakeAmount",
            "voidStakeAmount",
            "payoutAmount",
            "settledRoi",
            "hitRate",
            "avgExpectedValue",
            "avgProb",
            "avgOdds",
            "settlementCoverage",
            "canEvaluateBucket",
        ],
    )
    _bucket_rows_to_csv(
        odds_csv,
        odds_rows,
        [
            "bucket",
            "betCount",
            "settledBetCount",
            "unresolvedBetCount",
            "voidBetCount",
            "hitCount",
            "stakeAmount",
            "settledStakeAmount",
            "unresolvedStakeAmount",
            "voidStakeAmount",
            "payoutAmount",
            "settledRoi",
            "hitRate",
            "avgExpectedValue",
            "avgProb",
            "avgOdds",
            "settlementCoverage",
            "canEvaluateBucket",
        ],
    )
    coverage = {
        "dateRange": date_tag,
        "predictionSource": source_mode,
        "sourceType": source_mode,
        "totalDays": len(days),
        "daysWithFrozenBets": days_with_frozen_bets,
        "daysWithLiveFrozenBets": days_with_frozen_bets,
        "daysWithUiRecovered": days_with_ui_recovered,
        "daysWithUiRecoveredBets": days_with_ui_recovered,
        "daysWithBackfilledBets": days_with_backfilled_bets,
        "daysMissingPredictions": days_missing_predictions,
        "daysMissingAnyPrediction": days_missing_any_prediction,
        "daysWithResults": days_with_results,
        "resultOkCount": summary["resultOkCount"],
        "resultParseErrorCount": summary["resultParseErrorCount"],
        "resultPendingCount": summary["resultPendingCount"],
        "resultMissingCount": summary["resultMissingCount"],
        "betCount": bet_count,
        "settledBetCount": settled_bet_count,
        "unresolvedBetCount": unresolved_bet_count,
        "voidBetCount": void_bet_count,
        "settlementCoverage": settlement_coverage,
        "liveSettlementCoverage": live_settlement_coverage,
        "backfillSettlementCoverage": backfill_settlement_coverage,
        "predictionCoverage": prediction_coverage,
        "livePredictionCoverage": live_prediction_coverage,
        "backfillPredictionCoverage": backfill_prediction_coverage,
        "liveSettledBetCount": live_settled_bet_count,
        "uiRecoveredSettledBetCount": ui_recovered_settled_bet_count,
        "backfillSettledBetCount": backfill_settled_bet_count,
        "canTuneWithLiveOnly": can_tune_with_live_only,
        "canTuneWithBackfill": can_tune_with_backfill,
        "resultCoverage": result_coverage,
        "warnings": summary["warnings"],
        "generatedAt": summary["generatedAt"],
    }
    tuning_candidates_ev = [row for row in ev_rows if bool(row.get("canEvaluateBucket")) and row.get("settledRoi") is not None]
    tuning_candidates_odds = [row for row in odds_rows if bool(row.get("canEvaluateBucket")) and row.get("settledRoi") is not None]

    def _best_worst(rows: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        if not rows:
            return None, None
        best = max(rows, key=lambda row: (float(row.get("settledRoi") or 0), float(row.get("settledBetCount") or 0)))
        worst = min(rows, key=lambda row: (float(row.get("settledRoi") or 0), -float(row.get("settledBetCount") or 0)))
        return best, worst

    best_ev, worst_ev = _best_worst(tuning_candidates_ev)
    best_odds, worst_odds = _best_worst(tuning_candidates_odds)
    tuning_readiness = {
        "dateRange": date_tag,
        "predictionSource": source_mode,
        "sourceType": source_mode,
        "canTuneBuyThreshold": can_tune_buy_threshold,
        "canTuneWithLiveOnly": can_tune_with_live_only,
        "canTuneWithBackfill": can_tune_with_backfill,
        "leakageGuardStatus": backfill_leakage_guard_status,
        "reason": (
            "insufficient_settled_sample"
            if settled_bet_count < 100
            else "low_settlement_coverage"
            if settlement_coverage is not None and settlement_coverage < 0.5
            else "backfill_insufficient"
            if not can_tune_with_backfill
            else "ready"
        ),
        "settledBetCount": settled_bet_count,
        "minimumRequiredSettledBetCount": 100,
        "minimumLiveSettledBetCount": 100,
        "minimumBackfillSettledBetCount": 300,
        "resultSourceRequirement": "official_txt_k_or_html_ok",
        "resultSourceBreakdown": dict(result_source_breakdown),
        "kResultDays": days_with_k_result,
        "kResultMissingDays": max(0, len(days) - days_with_k_result),
        "currentBackfillSettledBetCount": backfill_settled_bet_count,
        "remainingSettledBetCountNeeded": max(0, 300 - backfill_settled_bet_count),
        "resultSourceCoverage": result_source_coverage,
        "settlementCoverage": settlement_coverage,
        "liveSettlementCoverage": live_settlement_coverage,
        "backfillSettlementCoverage": backfill_settlement_coverage,
        "leakageGuardStatus": backfill_leakage_guard_status,
        "bestEvBucketByRoi": best_ev,
        "worstEvBucketByRoi": worst_ev,
        "bestOddsBucketByRoi": best_odds,
        "worstOddsBucketByRoi": worst_odds,
        "recommendedNextAction": (
            "continue_forward_collection"
            if not can_tune_with_live_only and not can_tune_with_backfill
            else "analyze_backfill_but_do_not_deploy_without_live_confirmation"
            if can_tune_with_backfill and not can_tune_with_live_only
            else "safe_to_consider_buy_threshold_tuning"
        ),
        "warnings": summary["warnings"],
        "generatedAt": summary["generatedAt"],
    }
    _bucket_rows_to_csv(
        diagnostics_path,
        diagnostics_rows,
        ["date", "jcd", "rno", "reason", "hasFrozenBets", "hasUiJson", "hasAiPredictions", "buyCount", "uiPath", "frozenPath", "suggestedFix"],
    )
    day_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    coverage_path.write_text(json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8")
    tuning_path.write_text(json.dumps(tuning_readiness, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "summary": summary,
        "coverage": coverage,
        "tuning": tuning_readiness,
        "files": {
            "summary": str(day_path),
            "by_day": str(day_csv),
            "by_venue": str(venue_csv),
            "by_ev_bucket": str(ev_csv),
            "by_odds_bucket": str(odds_csv),
            "coverage": str(coverage_path),
            "diagnostics": str(diagnostics_path),
            "tuning": str(tuning_path),
        },
    }


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Run backtest over a date range.")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--jcd", default="all")
    parser.add_argument("--stake", type=int, default=100)
    parser.add_argument("--prediction-source", default="auto", choices=["auto", "live", "ui_recovered", "backfill", "all"])
    args = parser.parse_args()
    result = run_backtest_range(start_date=args.start_date, end_date=args.end_date, jcd=args.jcd, stake_per_buy=args.stake, prediction_source=args.prediction_source)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
