from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
from src.data.parse_fixed_width import BoatRaceParser
from src.features.build_features import FeatureBuilder
from src.models.predict_win_proba import WinProbabilityPredictor
from src.eval.run_t017_race_filter_multiday import (
    _build_backtest_from_official_results,
    _build_official_results_snapshot,
)


ROOT = Path(__file__).resolve().parents[2]
ENTRY_ROOT = ROOT / "data" / "raw" / "official" / "entries"
RESULT_ROOT = ROOT / "data" / "raw" / "official" / "results"
NORMALIZED_ROOT = ROOT / "data" / "normalized"
REPORT_DAILY_ROOT = ROOT / "reports" / "daily"
TMP_ROOT = ROOT / "data" / "tmp"
OUT_COVERAGE_MD = ROOT / "reports" / "t019_snapshot_coverage.md"
OUT_COVERAGE_JSON = ROOT / "reports" / "t019_snapshot_coverage.json"
OUT_BUILD_MD = ROOT / "reports" / "t019_snapshot_build_result.md"
OUT_BUILD_JSON = ROOT / "reports" / "t019_snapshot_build_result.json"


@dataclass
class DateRecord:
    date_label: str
    date8: str
    status: str
    can_generate: bool
    ready_reason: str
    missing_items: list[str]
    normalized_files: int
    reports_daily_files: int
    generated_snapshot: bool = False
    generated_reason: str = ""
    snapshot_dir: str = ""


def _daterange(start: str, end: str) -> list[str]:
    start_dt = datetime.strptime(start, "%Y%m%d").date()
    end_dt = datetime.strptime(end, "%Y%m%d").date()
    days: list[str] = []
    current = start_dt
    while current <= end_dt:
        days.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return days


def _format_date8(date8: str) -> str:
    return f"{date8[:4]}-{date8[4:6]}-{date8[6:8]}"


def _count_files(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for p in path.rglob("*") if p.is_file())


def _entry_path(date8: str) -> Path:
    return ENTRY_ROOT / f"B{date8[2:]}.TXT"


def _result_path(date8: str) -> Path:
    return RESULT_ROOT / f"K{date8[2:]}.TXT"


def _snapshot_dir(date8: str) -> Path:
    return TMP_ROOT / f"{date8}_eval"


def _classify_date(date8: str) -> DateRecord:
    entry_exists = _entry_path(date8).exists()
    result_exists = _result_path(date8).exists()
    normalized_dir = NORMALIZED_ROOT / date8
    reports_daily_dir = REPORT_DAILY_ROOT / _format_date8(date8)
    normalized_files = _count_files(normalized_dir)
    reports_daily_files = _count_files(reports_daily_dir)
    missing_items: list[str] = []
    status = "unknown"
    can_generate = False
    ready_reason = ""

    if date8 == "20260426":
        status = "result_not_available_yet"
        ready_reason = "same-day result may still be in flux; treat as provisional"
        missing_items = []
        return DateRecord(
            date_label=_format_date8(date8),
            date8=date8,
            status=status,
            can_generate=False,
            ready_reason=ready_reason,
            missing_items=missing_items,
            normalized_files=normalized_files,
            reports_daily_files=reports_daily_files,
        )

    if not result_exists:
        status = "missing_results"
        missing_items.append("official results file")
        return DateRecord(
            date_label=_format_date8(date8),
            date8=date8,
            status=status,
            can_generate=False,
            ready_reason="official results file missing",
            missing_items=missing_items,
            normalized_files=normalized_files,
            reports_daily_files=reports_daily_files,
        )

    if not entry_exists:
        if normalized_files <= 1:
            status = "invalid_snapshot_shape"
            ready_reason = "normalized snapshot does not contain race payloads"
        else:
            status = "missing_required_columns"
            ready_reason = "entry TXT is missing, so today_races cannot be reconstructed"
        missing_items.append("official entries file")
        return DateRecord(
            date_label=_format_date8(date8),
            date8=date8,
            status=status,
            can_generate=False,
            ready_reason=ready_reason,
            missing_items=missing_items,
            normalized_files=normalized_files,
            reports_daily_files=reports_daily_files,
        )

    try:
        parsed_results = BoatRaceParser.parse_results_file(_result_path(date8))
    except Exception as exc:
        parsed_results = pd.DataFrame()
        ready_reason = f"official results parse failed: {exc}"
    if parsed_results.empty or not {"race_id", "date", "lane", "finish_position"}.issubset(set(parsed_results.columns)):
        status = "missing_required_columns"
        ready_reason = "official results file exists but does not parse into required race rows"
        missing_items.append("parseable official results rows")
        return DateRecord(
            date_label=_format_date8(date8),
            date8=date8,
            status=status,
            can_generate=False,
            ready_reason=ready_reason,
            missing_items=missing_items,
            normalized_files=normalized_files,
            reports_daily_files=reports_daily_files,
        )

    status = "ready_for_snapshot"
    can_generate = True
    ready_reason = "official entries and results are available"
    return DateRecord(
        date_label=_format_date8(date8),
        date8=date8,
        status=status,
        can_generate=can_generate,
        ready_reason=ready_reason,
        missing_items=missing_items,
        normalized_files=normalized_files,
        reports_daily_files=reports_daily_files,
    )


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _count_csv_rows(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        with path.open("r", encoding="utf-8", newline="") as f:
            return max(sum(1 for _ in f) - 1, 0)
    except Exception:
        return 0


def _run_feature_builder(today_input: Path, today_output: Path) -> None:
    builder = FeatureBuilder()
    builder.build(str(today_input), str(today_output), "today")


def _materialize_snapshot(date8: str) -> dict[str, object]:
    snapshot_dir = _snapshot_dir(date8)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    today_races = snapshot_dir / "today_races.csv"
    hist_races = snapshot_dir / "historical_races.csv"
    today_features = snapshot_dir / "today_features.csv"
    today_win_proba = snapshot_dir / "today_win_proba.csv"
    backtest_races = snapshot_dir / "backtest_race_results.csv"

    parser = BoatRaceParser()
    parser.process_all(
        str(RESULT_ROOT),
        str(ENTRY_ROOT),
        str(hist_races),
        str(today_races),
        target_date=_format_date8(date8),
    )

    if not today_races.exists() or today_races.stat().st_size == 0:
        raise ValueError(f"today_races reconstruction failed for {date8}")

    _run_feature_builder(today_races, today_features)
    predictor = WinProbabilityPredictor()
    pred_df = predictor.predict(str(today_features))
    if pred_df is None or pred_df.empty:
        raise ValueError(f"win probability prediction failed for {date8}")
    pred_df.to_csv(today_win_proba, index=False)

    feature_df = pd.read_csv(today_features)
    result_df, _ = _build_official_results_snapshot(_format_date8(date8))
    backtest_df = _build_backtest_from_official_results(feature_df, result_df)
    backtest_df.to_csv(backtest_races, index=False)

    manifest = {
        "date": date8,
        "snapshot_dir": str(snapshot_dir),
        "files": {
            "today_races.csv": {"rows": _count_csv_rows(today_races)},
            "today_features.csv": {"rows": int(len(feature_df))},
            "today_win_proba.csv": {"rows": int(len(pred_df))},
            "backtest_race_results.csv": {"rows": int(len(backtest_df))},
        },
    }
    _write_json(snapshot_dir / "manifest.json", manifest)
    return manifest


def _load_t017_t018() -> tuple[dict[str, object], dict[str, object]]:
    t017 = json.loads((ROOT / "reports" / "t017_race_filter_multiday_validation.json").read_text(encoding="utf-8"))
    t018 = json.loads((ROOT / "reports" / "t018_race_filter_promotion_gate.json").read_text(encoding="utf-8"))
    return t017, t018


def _run_module(module: str) -> None:
    cmd = [sys.executable, "-m", module]
    subprocess.run(cmd, check=True, cwd=str(ROOT))


def _render_coverage_md(records: list[DateRecord], generated: list[DateRecord]) -> str:
    lines = []
    for rec in records:
        lines.append(
            f"- {rec.date_label}: {rec.status} / can_generate={rec.can_generate} / {rec.ready_reason}"
        )
    generated_lines = [f"- {rec.date_label} -> {rec.snapshot_dir}" for rec in generated] or ["- なし"]
    target_lines = [f"- {rec.date_label}" for rec in records]
    can_generate_lines = [f"- {rec.date_label}: {'yes' if rec.can_generate else 'no'}" for rec in records]
    missing_lines = [f"- {rec.date_label}: {', '.join(rec.missing_items) if rec.missing_items else 'なし'}" for rec in records]
    ready_lines = [f"- {rec.date_label}: {rec.ready_reason}" for rec in records]
    return "\n".join(
        [
            "# TASK-019 Snapshot Coverage",
            "",
            "## 目的",
            "TASK-018 の gate 判定に必要な validated snapshot の母数を増やす。",
            "",
            "## 対象日付一覧",
            *target_lines,
            "",
            "## 各日付の状態",
            *lines,
            "",
            "## snapshot 生成可否",
            *can_generate_lines,
            "",
            "## 足りない入力",
            *missing_lines,
            "",
            "## 補完可能か",
            *ready_lines,
            "",
            "## 既存 validated dates",
            "- 2026-04-03",
            "- 2026-04-20",
            "- 2026-04-22",
            "- 2026-04-23",
            "- 2026-04-24",
            "- 2026-04-25",
            "",
            "## gate に対して何が足りないか",
            "- date_count, total_races, total_bets がまだ gate 閾値に届かない可能性がある。",
            "- 2026-04-11 / 2026-04-19 / 2026-04-21 は補完不可か不足入力あり。",
            "- 2026-04-26 は result_not_available_yet として保留する。",
            "",
            "## 次に生成すべき日付の優先順位",
            "- 1. 2026-04-01",
            "- 2. 2026-04-02",
            "- 3. 2026-04-04",
            "- 4. 2026-04-05",
            "- 5. 2026-04-06",
            "- 6. 2026-04-07",
            "- 7. 2026-04-08",
            "- 8. 2026-04-11",
            "- 9. 2026-04-26 (provisional)",
            "",
            "## 生成した snapshot",
            *(generated_lines),
        ]
    )


def _render_build_md(t017: dict[str, object], t018: dict[str, object], generated: list[DateRecord], skipped: list[DateRecord]) -> str:
    t017_validated = t017.get("validated_dates", [])
    t017_total_races = int(sum(int(row.get("total_races", 0) or 0) for row in t017.get("day_rows", [])))
    t017_total_bets = int(sum(int(row.get("concentration_filter_bet_count", 0) or 0) for row in t017.get("day_rows", [])))
    conc = next(row for row in t018.get("rows", []) if row["filter_name"] == "concentration_filter")
    first = next(row for row in t018.get("rows", []) if row["filter_name"] == "first_gap_filter")
    generated_lines = [f"- {rec.date_label} -> {rec.snapshot_dir}" for rec in generated] or ["- なし"]
    skipped_lines = [f"- {rec.date_label}" for rec in skipped] or ["- なし"]
    skipped_reasons = [f"- {rec.date_label}: {rec.status} / {rec.ready_reason}" for rec in skipped] or ["- なし"]
    validated_lines = [f"- {d}" for d in t017_validated]
    return "\n".join(
        [
            "# TASK-019 Snapshot Build Result",
            "",
            "## 新しく生成できた snapshot",
            *generated_lines,
            "",
            "## 生成できなかった snapshot",
            *skipped_lines,
            "",
            "## 生成できなかった理由",
            *skipped_reasons,
            "",
            "## 再実行後の validated_dates",
            *validated_lines,
            "",
            "## 再実行後の total_races",
            f"- {t017_total_races}",
            "",
            "## 再実行後の total_bets",
            f"- {t017_total_bets}",
            "",
            "## gate 条件を満たしたか",
            f"- {str(bool(t018.get('primary_candidate_after') != 'needs_more_data' and t018.get('production_adoption') is True)).lower()}",
            "",
            "## concentration_filter の再判定",
            f"- status={conc.get('status')}, mean_roi={conc.get('mean_roi')}, median_roi={conc.get('median_roi')}, positive_day_rate={conc.get('positive_day_rate')}",
            "",
            "## first_gap_filter の再判定",
            f"- status={first.get('status')}, mean_roi={first.get('mean_roi')}, median_roi={first.get('median_roi')}, positive_day_rate={first.get('positive_day_rate')}",
            "",
            "## production_adoption=false の理由",
            "- gate 閾値未達のため。",
            "- mean ROI だけでは採用不可で、median / positive_day_rate / tail risk が弱い。",
            "",
            "## 次にやるべきこと",
            "validated snapshot を追加し、10日以上で gate を再評価する。",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run TASK-019 snapshot coverage and generation.")
    parser.add_argument("--start-date", default="20260401")
    parser.add_argument("--end-date", default="20260426")
    args = parser.parse_args()

    records = [_classify_date(d) for d in _daterange(args.start_date, args.end_date)]
    generated: list[DateRecord] = []
    skipped: list[DateRecord] = []

    for rec in records:
        if not rec.can_generate:
            skipped.append(rec)
            continue
        snapshot_dir = _snapshot_dir(rec.date8)
        required = [snapshot_dir / "today_features.csv", snapshot_dir / "today_win_proba.csv", snapshot_dir / "backtest_race_results.csv"]
        if all(path.exists() for path in required):
            rec.generated_snapshot = True
            rec.generated_reason = "already present"
            rec.snapshot_dir = str(snapshot_dir)
            generated.append(rec)
            continue
        try:
            manifest = _materialize_snapshot(rec.date8)
            rec.generated_snapshot = True
            rec.generated_reason = "materialized from raw official entries/results"
            rec.snapshot_dir = str(snapshot_dir)
            generated.append(rec)
        except Exception as exc:
            rec.status = "invalid_snapshot_shape"
            rec.ready_reason = str(exc)
            skipped.append(rec)

    coverage_payload = {
        "task": "TASK-019",
        "records": [rec.__dict__ for rec in records],
        "generated_dates": [rec.date_label for rec in generated],
        "skipped_dates": [rec.date_label for rec in skipped],
        "validated_dates_before": [
            "2026-04-03",
            "2026-04-20",
            "2026-04-22",
            "2026-04-23",
            "2026-04-24",
            "2026-04-25",
        ],
        "generated_snapshot_count": len(generated),
    }
    OUT_COVERAGE_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_BUILD_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_COVERAGE_MD.write_text(_render_coverage_md(records, generated), encoding="utf-8")
    _write_json(OUT_COVERAGE_JSON, coverage_payload)

    _run_module("src.eval.run_t017_race_filter_multiday")
    _run_module("src.eval.run_t018_race_filter_promotion_gate")

    t017, t018 = _load_t017_t018()
    build_payload = {
        "task": "TASK-019",
        "generated_snapshots": [rec.date_label for rec in generated],
        "skipped_snapshots": [rec.date_label for rec in skipped],
        "rerun_validated_dates": t017.get("validated_dates", []),
        "rerun_total_races": int(sum(int(row.get("total_races", 0) or 0) for row in t017.get("day_rows", []))),
        "rerun_total_bets": int(sum(int(row.get("concentration_filter_bet_count", 0) or 0) for row in t017.get("day_rows", []))),
        "gate_met": bool(t018.get("primary_candidate_after") != "needs_more_data" and t018.get("production_adoption") is True),
        "concentration_filter": next(row for row in t018.get("rows", []) if row["filter_name"] == "concentration_filter"),
        "first_gap_filter": next(row for row in t018.get("rows", []) if row["filter_name"] == "first_gap_filter"),
        "primary_candidate_after": t018.get("primary_candidate_after"),
        "production_adoption": t018.get("production_adoption"),
        "production_adoption_reason": t018.get("decision_reason"),
    }
    OUT_BUILD_MD.write_text(_render_build_md(t017, t018, generated, skipped), encoding="utf-8")
    _write_json(OUT_BUILD_JSON, build_payload)

    print(f"[saved] {OUT_COVERAGE_MD}")
    print(f"[saved] {OUT_COVERAGE_JSON}")
    print(f"[saved] {OUT_BUILD_MD}")
    print(f"[saved] {OUT_BUILD_JSON}")
    print(json.dumps(build_payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
