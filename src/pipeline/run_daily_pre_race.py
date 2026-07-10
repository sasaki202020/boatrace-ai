from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

from src.pipeline.pipeline_utils import (
    append_log,
    ROOT,
    backup_files,
    copy_artifact,
    iso_now,
    log_file_for,
    parse_date,
    report_dir_for,
    restore_backups,
    run_step,
    write_json,
)
from src.pipeline.odds_refresh_policy import (
    ACTIVE_PHASE_STATUS_PATH,
    SUMMARY_PATH as ODDS_REFRESH_SUMMARY_PATH,
    load_policy as load_odds_refresh_policy,
    update_active_phase_status as update_odds_refresh_active_phase_status,
)


def run_monitoring_summary() -> int:
    script_path = ROOT / "scripts" / "monitor_improvement_loop.py"
    if not script_path.exists():
        print(f"[monitor] script not found: {script_path}")
        return 0

    cmd = [
        sys.executable,
        str(script_path),
        "--lookback-days",
        "7",
        "--min-hold-days",
        "2",
    ]
    print(f"[monitor] running: {' '.join(cmd)}")
    result = subprocess.run(cmd, check=False, cwd=str(ROOT))
    print(f"[monitor] exit_code={result.returncode}")
    return int(result.returncode)


def _load_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _classify_source_state(*, target_date: date, venues_payload: dict[str, object], raw_index_html: str) -> str:
    fetch_status = str(venues_payload.get("fetchStatus") or "").lower()
    venue_count = len(venues_payload.get("venues") or []) if isinstance(venues_payload.get("venues"), list) else 0
    missing_reason = venues_payload.get("missingReason") or []
    if not isinstance(missing_reason, list):
        missing_reason = [str(missing_reason)]
    html = raw_index_html or ""
    if fetch_status and fetch_status not in {"ok", "available", "200"}:
        return "official_index_unavailable"
    if not html.strip():
        return "official_index_empty"
    if venue_count > 0:
        return "available"
    if target_date > date.today():
        return "future_date_not_ready"
    no_race_markers = [
        "開催はありません",
        "レースはありません",
        "開催なし",
        "no race",
        "no races scheduled",
    ]
    if any(marker.lower() in html.lower() for marker in no_race_markers):
        return "no_races_scheduled"
    if "index_parse_failed" in {str(item) for item in missing_reason}:
        return "official_index_parse_failed"
    return "unknown"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the daily pre-race pipeline.")
    parser.add_argument("--date", help="Target date (YYYY-MM-DD). Defaults to today.")
    parser.add_argument("--delay", type=float, default=1.0)
    args = parser.parse_args()

    target_date = parse_date(args.date, default=date.today())
    report_dir = report_dir_for(target_date)
    log_path = log_file_for("pre_race", target_date)
    append_log(log_path, f"[run:start] pipeline=pre_race date={target_date.isoformat()}")
    policy = load_odds_refresh_policy()
    try:
        policy_selection = update_odds_refresh_active_phase_status(
            target_date=target_date,
            policy=policy,
            summary_path=ODDS_REFRESH_SUMMARY_PATH,
            status_path=ACTIVE_PHASE_STATUS_PATH,
        )
    except Exception as exc:
        policy_selection = {
            "target_date": target_date.isoformat(),
            "mode": "fixed",
            "default_phase": str(policy.get("default_phase", "final")),
            "lookback_days": int(policy.get("lookback_days", 3) or 3),
            "summary_path": str(ODDS_REFRESH_SUMMARY_PATH),
            "source_rows": 0,
            "complete_dates": [],
            "incomplete_dates": [],
            "eligible_phases": [],
            "phase_scores": [],
            "active_phase": str(policy.get("default_phase", "final")),
            "reason": f"fallback: policy selection failed ({exc})",
            "fallback_reason": f"policy selection failed ({exc})",
            "promotion_reason": "",
            "reevaluation_reason": f"policy selection failed ({exc})",
            "candidate_phase": str(policy.get("default_phase", "final")),
            "candidate_streak": 0,
            "warning_streak": 0,
            "locked_until": "",
            "last_reevaluation_date": "",
            "reevaluation_interval_days": int(policy.get("lookback_days", 3) or 3),
            "reevaluation_due": False,
            "fallback_reason": f"policy selection failed ({exc})",
            "baseline": {
                "avg_available": 0.0,
                "avg_pending": 0.0,
                "avg_missing": 0.0,
                "complete_dates": [],
            },
            "current_metrics": {
                "avg_available": 0.0,
                "avg_pending": 0.0,
                "avg_missing": 0.0,
            },
            "status": "fallback",
        }
    append_log(
        log_path,
        f"[policy] mode={policy_selection.get('mode', 'fixed')} "
        f"active_phase={policy_selection.get('active_phase', 'final')} "
        f"candidate_phase={policy_selection.get('candidate_phase', 'final')} "
        f"candidate_streak={policy_selection.get('candidate_streak', 0)} "
        f"locked_until={policy_selection.get('locked_until', '')} "
        f"reevaluation_due={policy_selection.get('reevaluation_due', False)} "
        f"last_reevaluation_date={policy_selection.get('last_reevaluation_date', '')} "
        f"warning_streak={policy_selection.get('warning_streak', 0)} "
        f"baseline={policy_selection.get('baseline', {})} "
        f"current={policy_selection.get('current_metrics', {})} "
        f"reevaluation_reason={policy_selection.get('reevaluation_reason', '')} "
        f"reason={policy_selection.get('promotion_reason') or policy_selection.get('fallback_reason') or policy_selection.get('reason', '')}",
    )
    os.environ["BOATRACE_AI_ACTIVE_PHASE"] = str(policy_selection.get("active_phase", "final"))
    os.environ["BOATRACE_AI_ACTIVE_PHASE_REASON"] = str(policy_selection.get("reason", ""))
    write_json(report_dir / "odds_refresh_policy.json", policy_selection)

    date8 = target_date.strftime("%Y%m%d")
    today_venues_path = ROOT / "data" / "normalized" / date8 / "today_venues.json"
    raw_index_path = ROOT / "data" / "raw" / "official" / date8 / "index.html"
    today_venues_payload = _load_json(today_venues_path)
    raw_index_html = ""
    if raw_index_path.exists():
        try:
            raw_index_html = raw_index_path.read_text(encoding="utf-8")
        except Exception:
            raw_index_html = ""
    source_classification = _classify_source_state(
        target_date=target_date,
        venues_payload=today_venues_payload,
        raw_index_html=raw_index_html,
    )
    source_venues = today_venues_payload.get("venues") if isinstance(today_venues_payload.get("venues"), list) else []
    source_venues_count = len(source_venues)
    source_http_status = str(today_venues_payload.get("fetchStatus") or "")
    source_missing_reason = list(today_venues_payload.get("missingReason") or [])
    source_body_length = len(raw_index_html or "")
    source_url = str(today_venues_payload.get("sourceUrl") or f"https://www.boatrace.jp/owpc/pc/race/index?hd={date8}")
    source_ready = source_classification == "available"
    model_path = ROOT / "models" / "win_model.joblib"
    calibrator_path = ROOT / "models" / "probability_calibrator.json"
    backup_dir = report_dir / "backups"
    model_backups = backup_files([model_path, calibrator_path], backup_dir)

    if not source_ready:
        report = {
            "generated_at": target_date.isoformat(),
            "pipeline": "pre_race",
            "date": target_date.isoformat(),
            "status": "source_not_ready",
            "failure_step": "discover_today_empty",
            "failure_reason": source_classification,
            "log_path": str(log_path),
            "steps": [],
            "artifacts": {
                "today_venues": str(today_venues_path) if today_venues_path.exists() else "",
                "today_index_html": str(raw_index_path) if raw_index_path.exists() else "",
            },
            "model_backups": [],
            "sourceClassification": source_classification,
            "sourceUrl": source_url,
            "sourceHttpStatus": source_http_status,
            "sourceBodyLength": source_body_length,
            "sourceVenueCount": source_venues_count,
            "sourceMissingReason": source_missing_reason,
            "sourceReady": False,
            "sourceDate": target_date.isoformat(),
            "sourceDateKey": date8,
            "odds_refresh_policy": policy_selection,
            "active_phase": policy_selection.get("active_phase", "final"),
            "selection_reason": policy_selection.get("reason", ""),
            "candidate_phase": policy_selection.get("candidate_phase", "final"),
            "candidate_streak": policy_selection.get("candidate_streak", 0),
            "warning_streak": policy_selection.get("warning_streak", 0),
            "mode": policy_selection.get("mode", "fixed"),
            "locked_until": policy_selection.get("locked_until", ""),
            "reevaluation_due": policy_selection.get("reevaluation_due", False),
            "last_reevaluation_date": policy_selection.get("last_reevaluation_date", ""),
            "reevaluation_reason": policy_selection.get("reevaluation_reason", ""),
            "baseline": policy_selection.get("baseline", {}),
            "current_metrics": policy_selection.get("current_metrics", {}),
            "baseline_metrics": policy_selection.get("baseline_metrics", {}),
            "notes": {
                "source_not_ready": "discover_today returned no venues for target date; later stages were not executed",
            },
        }
        write_json(report_dir / "pre_race_run.json", report)
        append_log(
            log_path,
            f"[run:end] pipeline=pre_race status=source_not_ready failure_step=discover_today_empty source_classification={source_classification}",
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        raise SystemExit(2)

    steps = []
    run_status = "ok"
    failure_step = None

    odds_date_key = target_date.strftime("%Y%m%d")
    py_cmd = sys.executable
    odds_fetch_timeout = int(os.environ.get("BOATRACE_ODDS_FETCH_TIMEOUT_SEC", "180"))
    step_specs = [
        ("fetch_entries", [py_cmd, "src/data_fetch/fetch_official.py", "--type", "entries", "--date", target_date.isoformat(), "--delay", str(args.delay)], False, None),
        ("parse_fixed_width", [py_cmd, "src/data/parse_fixed_width.py", "--target-date", target_date.isoformat()], False, None),
        ("build_features", [py_cmd, "-m", "src.features.build_features"], False, None),
        ("train_model", [py_cmd, "-m", "src.models.train_win_model"], False, None),
        ("train_calibrator", [py_cmd, "-m", "src.eval.train_probability_calibrator"], True, None),
        ("predict_win_proba", [py_cmd, "-m", "src.models.predict_win_proba"], False, None),
        ("generate_trifecta_candidates", [py_cmd, "-m", "src.strategy.generate_trifecta_candidates"], False, None),
        (
            "fetch_today_odds",
            [
                py_cmd,
                "-m",
                "src.odds.fetch_daily_trifecta_odds",
                "--date",
                target_date.isoformat(),
                "--timeout",
                "15",
                "--retries",
                "2",
                "--retry-sleep",
                "1.5",
                "--settle-retry-rounds",
                "0",
                "--settle-retry-sleep",
                "0",
                "--pending-retry-rounds",
                "0",
                "--unpublished-retry-rounds",
                "0",
                "--request-interval",
                str(args.delay),
            ],
            True,
            odds_fetch_timeout,
        ),
        ("evaluate_ev_and_skip", [py_cmd, "-m", "src.strategy.evaluate_ev_and_skip"], False, None),
        ("build_exacta_mode_predictions", [py_cmd, "-m", "src.strategy.build_exacta_mode_predictions"], True, None),
        ("analyze_gate_health", [py_cmd, "-m", "src.eval.analyze_gate_health"], True, None),
    ]

    total_steps = len(step_specs)
    for index, (label, cmd, allow_failure, timeout) in enumerate(step_specs, start=1):
        print(f"[STEP {index}/{total_steps}] {label} started at {iso_now()}")
        step = run_step(label, cmd, allow_failure=allow_failure, timeout=timeout, log_path=log_path)
        if label == "train_calibrator" and step["returncode"] != 0:
            restore_backups([entry for entry in model_backups if Path(entry["src"]).name == "probability_calibrator.json"])
            step["status"] = "allowed_failure"
            step["stderr_tail"] = (
                step["stderr_tail"]
                + "\n[info] calibration failed; restored previous calibrator and continued."
            )[-2500:]
        steps.append(step)
        print(
            f"[STEP {index}/{total_steps}] {label} "
            f"{'OK' if step['returncode'] == 0 else step['status'].upper()} "
            f"({step['duration_sec']}s)"
        )
        if step["returncode"] != 0 and not allow_failure:
            run_status = "failed"
            failure_step = label
            if label in {"train_model", "train_calibrator"}:
                restore_backups(model_backups)
            break

    artifacts = {}
    if run_status == "ok":
        artifact_map = {
            "today_features": ROOT / "data/features/today_features.csv",
            "today_win_proba": ROOT / "data/model_outputs/today_win_proba.csv",
            "trifecta_candidates": ROOT / "data/strategy_outputs/trifecta_candidates.csv",
            "today_odds": ROOT / "data/odds/today_trifecta_odds.csv",
            "today_odds_report": ROOT / "data/odds" / odds_date_key / "fetch_report.json",
            "today_odds_race_status": ROOT / "data/odds" / odds_date_key / "race_status.csv",
            "today_odds_targets": ROOT / "data/odds" / odds_date_key / "race_targets.csv",
            "today_odds_failures": ROOT / "data/odds" / odds_date_key / "failed_races.csv",
            "skip_decisions": ROOT / "data/strategy_outputs/skip_decisions.csv",
            "skip_decisions_exacta_mode": ROOT / "data/strategy_outputs/skip_decisions_exacta_mode.csv",
            "ev_analysis": ROOT / "data/strategy_outputs/ev_analysis.csv",
            "gate_health_summary": ROOT / "reports/gate_health/gate_health_summary.json",
        }
        for key, src in artifact_map.items():
            copied = copy_artifact(src, report_dir)
            if copied:
                artifacts[key] = copied

    report = {
        "generated_at": target_date.isoformat(),
        "pipeline": "pre_race",
        "date": target_date.isoformat(),
        "status": run_status,
        "failure_step": failure_step,
        "log_path": str(log_path),
        "steps": steps,
        "artifacts": artifacts,
        "model_backups": model_backups,
        "odds_refresh_policy": policy_selection,
        "active_phase": policy_selection.get("active_phase", "final"),
        "selection_reason": policy_selection.get("reason", ""),
        "candidate_phase": policy_selection.get("candidate_phase", "final"),
        "candidate_streak": policy_selection.get("candidate_streak", 0),
        "warning_streak": policy_selection.get("warning_streak", 0),
        "mode": policy_selection.get("mode", "fixed"),
        "locked_until": policy_selection.get("locked_until", ""),
        "reevaluation_due": policy_selection.get("reevaluation_due", False),
        "last_reevaluation_date": policy_selection.get("last_reevaluation_date", ""),
        "reevaluation_reason": policy_selection.get("reevaluation_reason", ""),
        "baseline": policy_selection.get("baseline", {}),
        "current_metrics": policy_selection.get("current_metrics", {}),
        "baseline_metrics": policy_selection.get("baseline_metrics", {}),
        "notes": {
            "odds_fetch_failure": "当日オッズ取得に失敗しても skip_decisions は暫定オッズで再生成する",
            "model_failure": "学習失敗時は backup から旧モデルを復元する",
        },
    }
    write_json(report_dir / "pre_race_run.json", report)
    run_monitoring_summary()
    append_log(
        log_path,
        f"[run:end] pipeline=pre_race status={run_status} failure_step={failure_step}",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
