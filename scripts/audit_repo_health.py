from __future__ import annotations

import csv
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.utils.date_paths import (
    compact_date_str,
    find_existing_daily_report_dir,
    get_daily_report_dir,
    list_legacy_daily_dirs,
    normalize_date_str,
    parse_daily_dir_date,
)


REPORT_DIR = ROOT / "reports" / "repo_audit"
OUT_JSON = REPORT_DIR / "health_check.json"
OUT_MD = REPORT_DIR / "health_check.md"
SAMPLE_AUDIT_MD = REPORT_DIR / "sample_symbol_audit.md"
SKIP_SCHEMA_AUDIT_CSV = REPORT_DIR / "skip_decisions_schema_audit.csv"
SKIP_SCHEMA_AUDIT_MD = REPORT_DIR / "skip_decisions_schema_audit.md"
LATEST_OPS_GAP_AUDIT_MD = REPORT_DIR / "latest_ops_gap_audit.md"
LATEST_COMPLETE_OPS_AUDIT_MD = REPORT_DIR / "latest_complete_ops_audit.md"
TODAY_OPS_STATUS_MD = REPORT_DIR / "today_ops_status.md"

DATE_DIR_HYPHEN_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
DATE_DIR_COMPACT_RE = re.compile(r"^(\d{8})$")
SUMMARY_FILE_RE = re.compile(r"^(\d{8})_summary\.json$")


@dataclass
class HealthCheckResult:
    latest_daily_date: str
    latest_ready_daily_date: str
    latest_daily_dir: str
    latest_pre_race_date: str
    latest_odds_refresh_date: str
    latest_post_race_date: str
    latest_complete_ops_date: str
    latest_source_not_ready_date: str
    latest_incomplete_daily_date: str
    latest_daily_missing_items: list[str]
    latest_daily_issue_classification: str
    legacy_daily_dirs: list[str]
    daily_date_format_mixed: bool
    pre_race_run_exists: bool
    odds_refresh_run_exists: bool
    post_race_run_exists: bool
    daily_summary_exists: bool
    skip_decisions_exists: bool
    skip_decisions_required_columns: bool
    skip_decisions_alias_compatible: bool
    skip_decisions_missing_columns: list[str]
    skip_decisions_alias_columns: dict[str, str]
    raw_official_exists: bool
    models_exists: bool
    ui_json_exists_for_latest_date: bool
    sample_symbol_in_src: bool
    sample_symbol_true_positive_count: int
    sample_symbol_ignored_false_positive_count: int
    ev_uses_today_win_proba_lane_only: bool
    ev_uses_trifecta_candidates_and_approx_prob: bool
    demo_diagnostics_used_by_production_summary: bool
    repo_audit_files_ready: bool
    warnings: list[str]


def _detect_daily_date_formats(daily_root: Path) -> tuple[bool, bool]:
    has_compact = False
    has_hyphen = False
    if not daily_root.exists():
        return has_compact, has_hyphen
    for p in daily_root.iterdir():
        if p.is_dir():
            if DATE_DIR_COMPACT_RE.match(p.name):
                has_compact = True
            if DATE_DIR_HYPHEN_RE.match(p.name):
                has_hyphen = True
        elif p.is_file():
            if SUMMARY_FILE_RE.match(p.name):
                has_compact = True
    return has_compact, has_hyphen


def _find_daily_dir(daily_root: Path, compact_date: str) -> Path | None:
    if not compact_date:
        return None
    return find_existing_daily_report_dir(compact_date, daily_root)


def _collect_known_daily_dates(daily_root: Path) -> set[str]:
    out: set[str] = set()
    if not daily_root.exists():
        return out
    for p in daily_root.iterdir():
        if p.is_file():
            m = SUMMARY_FILE_RE.match(p.name)
            if m:
                out.add(normalize_date_str(m.group(1)))
        elif p.is_dir():
            normalized = parse_daily_dir_date(p.name)
            if normalized:
                out.add(normalized)
    return out


def _parse_latest_daily_date(daily_root: Path) -> tuple[str, Path | None]:
    if not daily_root.exists():
        return "", None

    dates = sorted(_collect_known_daily_dates(daily_root))
    if not dates:
        return "", None
    latest = dates[-1]
    return latest, _find_daily_dir(daily_root, latest)


def _safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return path.read_text(encoding="cp932")
        except Exception:
            return ""
    except Exception:
        return ""


def _check_skip_columns(skip_path: Path) -> tuple[bool, bool, list[str], list[str], dict[str, str]]:
    if not skip_path.exists():
        return False, False, [], [], {}
    try:
        df = pd.read_csv(skip_path, nrows=0)
    except Exception:
        return False, False, [], [], {}
    columns = [str(c) for c in df.columns]
    col_set = set(columns)
    required = {"final_decision", "stop_reason", "odds_status"}
    alias_map = {
        "final_decision": ["final_decision", "decision"],
        "stop_reason": ["stop_reason", "skip_reason", "reason"],
        "odds_status": ["odds_status"],
    }
    has_required = required.issubset(col_set)
    has_alias_compatible = all(any(alias in col_set for alias in aliases) for aliases in alias_map.values())
    missing_required = sorted(required - col_set)
    alias_columns: dict[str, str] = {}
    for required_col, aliases in alias_map.items():
        if required_col in col_set:
            continue
        for alias in aliases:
            if alias != required_col and alias in col_set:
                alias_columns[required_col] = alias
                break
    return has_required, has_alias_compatible, columns, missing_required, alias_columns


def _latest_date_with_file(daily_root: Path, file_name: str) -> str:
    candidates: list[str] = []
    for d in _collect_known_daily_dates(daily_root):
        daily_dir = _find_daily_dir(daily_root, d)
        if daily_dir and (daily_dir / file_name).exists():
            candidates.append(d)
    return max(candidates) if candidates else ""


def _sample_symbol_audit() -> tuple[bool, int, int, list[dict[str, str]], list[dict[str, str]]]:
    src = ROOT / "src"
    if not src.exists():
        return False, 0, 0, [], []

    dangerous_patterns = [
        re.compile(r"\bSAMPLE_RACES\b"),
        re.compile(r"\bSAMPLE_DATE\b"),
        re.compile(r"\bSAMPLE_VENUE\b"),
        re.compile(r"\bSAMPLE_EVENT\b"),
        re.compile(r"hardcoded sample race data", re.IGNORECASE),
        re.compile(r"dummy race data", re.IGNORECASE),
    ]
    ignore_patterns = [
        re.compile(r"\bLOW_SAMPLE_MODEL\b"),
        re.compile(r"\bsample_weight\b", re.IGNORECASE),
    ]

    true_hits: list[dict[str, str]] = []
    ignored_hits: list[dict[str, str]] = []

    for p in src.rglob("*"):
        if not p.is_file() or p.suffix not in {".py", ".js", ".ts", ".tsx", ".jsx"}:
            continue
        text = _safe_read_text(p)
        if not text:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            is_comment_only = stripped.startswith(("#", "//", "/*", "*"))
            if any(ip.search(line) for ip in ignore_patterns):
                ignored_hits.append(
                    {
                        "path": str(p.relative_to(ROOT)).replace("\\", "/"),
                        "line": str(lineno),
                        "value": stripped[:200],
                        "reason": "known_non_production_sample_term",
                    }
                )
                continue
            matched = [pat.pattern for pat in dangerous_patterns if pat.search(line)]
            if not matched:
                continue
            entry = {
                "path": str(p.relative_to(ROOT)).replace("\\", "/"),
                "line": str(lineno),
                "value": stripped[:200],
                "pattern": ";".join(matched),
            }
            if is_comment_only:
                entry["reason"] = "comment_only_reference"
                ignored_hits.append(entry)
                continue
            true_hits.append(entry)

    return bool(true_hits), len(true_hits), len(ignored_hits), true_hits, ignored_hits


def _write_sample_symbol_audit(
    true_positive_count: int,
    ignored_false_positive_count: int,
    true_hits: list[dict[str, str]],
    ignored_hits: list[dict[str, str]],
) -> None:
    lines = [
        "# Sample Symbol Audit",
        "",
        f"- detected_count: {true_positive_count + ignored_false_positive_count}",
        f"- true_positive: {true_positive_count}",
        f"- ignored_false_positive: {ignored_false_positive_count}",
        "",
        "## true_positive_hits",
    ]
    if not true_hits:
        lines.append("- none")
    else:
        lines.extend(f"- {h['path']}:{h['line']} :: {h.get('value', '')}" for h in true_hits[:100])
    lines.extend(["", "## ignored_false_positive_hits"])
    if not ignored_hits:
        lines.append("- none")
    else:
        lines.extend(
            f"- {h['path']}:{h['line']} :: {h.get('reason', '')} :: {h.get('value', '')}" for h in ignored_hits[:100]
        )
    lines.extend(["", "## remaining_warnings"])
    if true_positive_count == 0:
        lines.append("- none")
    else:
        lines.append("- sample_symbol_detected_in_src")
    SAMPLE_AUDIT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _detect_skip_generators() -> list[str]:
    hits: list[str] = []
    search_roots = [ROOT / "src", ROOT / "scripts"]
    for search_root in search_roots:
        if not search_root.exists():
            continue
        for p in search_root.rglob("*.py"):
            text = _safe_read_text(p)
            if "skip_decisions.csv" not in text:
                continue
            if "to_csv(" in text or "Path(" in text or "copy2(" in text:
                hits.append(str(p.relative_to(ROOT)).replace("\\", "/"))
    return sorted(set(hits))


def _write_skip_schema_audit(
    latest_daily_date: str,
    latest_daily_dir: Path | None,
    date_formats_mixed: bool,
) -> tuple[bool, bool, list[str], dict[str, str]]:
    required_defs = [
        ("final_decision", ["final_decision", "decision"]),
        ("stop_reason", ["stop_reason", "skip_reason", "reason"]),
        ("odds_status", ["odds_status"]),
    ]
    rows: list[dict[str, str]] = []
    existing_columns: list[str] = []
    skip_path = latest_daily_dir / "skip_decisions.csv" if latest_daily_dir else Path("")
    has_required_exact = False
    has_alias_compatible = False
    missing_required: list[str] = []
    alias_columns: dict[str, str] = {}
    if skip_path.exists():
        has_required_exact, has_alias_compatible, existing_columns, missing_required, alias_columns = _check_skip_columns(skip_path)
        existing_set = set(existing_columns)
        for required, aliases in required_defs:
            present_aliases = [a for a in aliases if a in existing_set]
            if required in existing_set:
                status = "exact_ok"
            elif present_aliases:
                status = "alias_only"
            else:
                status = "missing"
            rows.append(
                {
                    "date": latest_daily_date,
                    "skip_path": str(skip_path).replace("\\", "/"),
                    "required_column": required,
                    "present": "true" if required in existing_set else "false",
                    "alias_candidate": "|".join(aliases),
                    "alias_present": "|".join(present_aliases) if present_aliases else "",
                    "status": status,
                }
            )
    else:
        for required, aliases in required_defs:
            rows.append(
                {
                    "date": latest_daily_date,
                    "skip_path": str(skip_path).replace("\\", "/") if latest_daily_dir else "",
                    "required_column": required,
                    "present": "false",
                    "alias_candidate": "|".join(aliases),
                    "alias_present": "",
                    "status": "missing_file",
                }
            )

    SKIP_SCHEMA_AUDIT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with SKIP_SCHEMA_AUDIT_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "date",
                "skip_path",
                "required_column",
                "present",
                "alias_candidate",
                "alias_present",
                "status",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    generators = _detect_skip_generators()
    lines = [
        "# Skip Decisions Schema Audit",
        "",
        f"- latest_daily_date: {latest_daily_date or 'missing'}",
        f"- latest_daily_dir: {str(latest_daily_dir).replace(chr(92), '/') if latest_daily_dir else 'missing'}",
        f"- date_dir_format_mixed: {date_formats_mixed}",
        f"- skip_decisions_exists: {skip_path.exists()}",
        f"- required_columns_exact: {has_required_exact}",
        f"- alias_compatible: {has_alias_compatible}",
        "",
        "## existing_columns",
    ]
    if not existing_columns:
        lines.append("- none")
    else:
        lines.extend(f"- {c}" for c in existing_columns)
    lines.extend(["", "## missing_required_columns"])
    if not missing_required:
        lines.append("- none")
    else:
        lines.extend(f"- {c}" for c in missing_required)
    lines.extend(["", "## alias_mapping"])
    for required, aliases in required_defs:
        lines.append(f"- {required}: {' | '.join(aliases)}")
    lines.extend(["", "## generator_candidates"])
    if not generators:
        lines.append("- none")
    else:
        lines.extend(f"- {g}" for g in generators)
    lines.extend(["", "## alias_columns_in_latest_skip"])
    if not alias_columns:
        lines.append("- none")
    else:
        for required_col, alias_col in alias_columns.items():
            lines.append(f"- {required_col} <- {alias_col}")
    SKIP_SCHEMA_AUDIT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return has_required_exact, has_alias_compatible, missing_required, alias_columns


def _is_complete_ops_day(daily_root: Path, compact_date: str) -> tuple[bool, list[str]]:
    missing: list[str] = []
    day_dir = _find_daily_dir(daily_root, compact_date)
    if day_dir is None:
        return False, ["daily_dir_missing"]
    for required_file in [
        "pre_race_run.json",
        "odds_refresh_run.json",
        "post_race_run.json",
        "daily_summary.json",
        "skip_decisions.csv",
    ]:
        if not (day_dir / required_file).exists():
            missing.append(required_file)
    summary_path = day_dir / "daily_summary.json"
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            if str(summary.get("results_status", "")).lower() not in {"ok", "available", "settled"}:
                missing.append("result_data_missing")
        except Exception:
            missing.append("daily_summary_parse_error")
    if not missing:
        has_required_exact, _, _, _, _ = _check_skip_columns(day_dir / "skip_decisions.csv")
        if not has_required_exact:
            missing.append("skip_decisions_required_columns_missing")
    return not missing, missing


def _latest_complete_ops_date(daily_root: Path) -> tuple[str, dict[str, list[str]]]:
    missing_map: dict[str, list[str]] = {}
    dates = sorted(_collect_known_daily_dates(daily_root), reverse=True)
    for d in dates:
        ok, missing = _is_complete_ops_day(daily_root, d)
        if ok:
            return d, missing_map
        missing_map[d] = missing
    return "", missing_map


def _classify_latest_daily_issue(latest_dir: Path | None) -> str:
    if not latest_dir:
        return "unknown"
    preflight_path = latest_dir / "preflight_source_check.json"
    pre_race_path = latest_dir / "pre_race_run.json"
    summary_path = latest_dir / "daily_summary.json"
    post_race_path = latest_dir / "post_race_run.json"
    preflight_classification = None
    pre_race_status = None
    pre_race_source_classification = None
    if preflight_path.exists():
        try:
            preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
            preflight_classification = preflight.get("sourceClassification")
        except Exception:
            preflight_classification = "official_index_parse_failed"
    if pre_race_path.exists():
        try:
            pre_race = json.loads(pre_race_path.read_text(encoding="utf-8"))
            pre_race_status = pre_race.get("status")
            pre_race_source_classification = pre_race.get("sourceClassification") or pre_race.get("failure_reason")
        except Exception:
            pre_race_status = "pipeline_failure"
    post_race_status = None
    if post_race_path.exists():
        try:
            post_race_status = json.loads(post_race_path.read_text(encoding="utf-8")).get("status")
        except Exception:
            post_race_status = "pipeline_failure"
    if summary_path.exists():
        try:
            summary_status = json.loads(summary_path.read_text(encoding="utf-8")).get("results_status")
            if str(summary_status).lower() != "available":
                return "result_data_missing"
        except Exception:
            return "pipeline_failure"
        return "complete"
    if preflight_classification and str(preflight_classification).lower() != "ready":
        return str(preflight_classification)
    if post_race_status == "missing_data":
        return "result_data_missing"
    if pre_race_status == "source_not_ready":
        if pre_race_source_classification:
            return str(pre_race_source_classification)
        return "pre_race_source_unavailable"
    if post_race_path.exists():
        return "pipeline_failure"
    if pre_race_path.exists() and pre_race_status:
        if pre_race_status == "source_not_ready" and pre_race_source_classification:
            return str(pre_race_source_classification)
        return "pre_race_pipeline_failure"
    return "unknown"


def _classify_daily_issue(daily_root: Path, date_str: str) -> str:
    day_dir = _find_daily_dir(daily_root, date_str)
    return _classify_latest_daily_issue(day_dir)


def _latest_date_by_issue(
    daily_root: Path,
    *,
    include: set[str] | None = None,
    exclude: set[str] | None = None,
) -> str:
    dates = sorted(_collect_known_daily_dates(daily_root), reverse=True)
    for d in dates:
        issue = _classify_daily_issue(daily_root, d)
        if include is not None and issue not in include:
            continue
        if exclude is not None and issue in exclude:
            continue
        return d
    return ""


def _write_latest_ops_gap_audit(
    daily_root: Path,
    latest_daily_date: str,
    latest_daily_dir: Path | None,
    latest_complete_ops_date: str,
    latest_daily_issue_classification: str,
    missing_map: dict[str, list[str]],
) -> None:
    lines = [
        "# Latest Ops Gap Audit",
        "",
        f"- latest_daily_date: {latest_daily_date or 'missing'}",
        f"- latest_daily_dir: {str(latest_daily_dir).replace(chr(92), '/') if latest_daily_dir else 'missing'}",
        f"- latest_complete_ops_date: {latest_complete_ops_date or 'none'}",
        f"- latest_daily_issue_classification: {latest_daily_issue_classification}",
        "",
        "## latest_daily_missing_items",
    ]
    if latest_daily_date in missing_map:
        lines.extend(f"- {m}" for m in missing_map[latest_daily_date])
    else:
        lines.append("- none")

    lines.extend(["", "## target_files"])
    if latest_daily_date:
        compact_dir = daily_root / compact_date_str(latest_daily_date)
        hyphen_dir = get_daily_report_dir(latest_daily_date, daily_root)
        for base_dir in [compact_dir, hyphen_dir]:
            lines.append(f"- base_dir={base_dir.as_posix()} exists={base_dir.exists()}")
            lines.append(
                f"- { (base_dir / 'odds_refresh_run.json').as_posix() } exists={ (base_dir / 'odds_refresh_run.json').exists() }"
            )
            lines.append(
                f"- { (base_dir / 'post_race_run.json').as_posix() } exists={ (base_dir / 'post_race_run.json').exists() }"
            )
    else:
        lines.append("- latest_daily_date_missing")

    logs_dir = ROOT / "logs" / "tasks"
    lines.extend(["", "## task_logs_latest_day"])
    if logs_dir.exists():
        date_key = latest_daily_date
        task_logs = sorted([p.name for p in logs_dir.glob(f"*{date_key}*.log")])
        if task_logs:
            lines.extend(f"- {name}" for name in task_logs)
        else:
            lines.append("- none")
    else:
        lines.append("- logs/tasks_missing")

    lines.extend(["", "## diagnosis"])
    if latest_daily_date and latest_daily_date in missing_map:
        missing = set(missing_map[latest_daily_date])
        if {"odds_refresh_run.json", "post_race_run.json"} & missing:
            lines.append("- likely_not_executed_yet_or_scheduled_for_evening_stage")
        if "daily_summary.json" in missing:
            lines.append("- daily_summary_json_missing_in_date_dir")
        if "skip_decisions_required_columns_missing" in missing:
            lines.append("- skip_decisions_schema_needs_canonical_columns")
    else:
        lines.append("- no_gap_detected")
    LATEST_OPS_GAP_AUDIT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_latest_complete_ops_audit(
    daily_root: Path,
    latest_complete_ops_date: str,
    latest_pre_race_date: str,
    latest_odds_refresh_date: str,
    latest_post_race_date: str,
) -> None:
    target = "2026-04-25"
    ok_20260425, missing_20260425 = _is_complete_ops_day(daily_root, target)
    lines = [
        "# Latest Complete Ops Audit",
        "",
        f"- latest_pre_race_date: {latest_pre_race_date or 'missing'}",
        f"- latest_odds_refresh_date: {latest_odds_refresh_date or 'missing'}",
        f"- latest_post_race_date: {latest_post_race_date or 'missing'}",
        f"- latest_complete_ops_date: {latest_complete_ops_date or 'none'}",
        "",
        f"## check_{target}",
        f"- complete: {ok_20260425}",
    ]
    if missing_20260425:
        lines.extend(f"- missing: {m}" for m in missing_20260425)
    else:
        lines.append("- missing: none")
    LATEST_COMPLETE_OPS_AUDIT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_today_ops_status(daily_root: Path) -> None:
    today_date = datetime.now().strftime("%Y-%m-%d")
    today_dir = _find_daily_dir(daily_root, today_date)
    pre_exists = bool(today_dir and (today_dir / "pre_race_run.json").exists())
    odds_exists = bool(today_dir and (today_dir / "odds_refresh_run.json").exists())
    post_exists = bool(today_dir and (today_dir / "post_race_run.json").exists())
    daily_summary_exists = bool(today_dir and (today_dir / "daily_summary.json").exists())
    lines = [
        "# Today Ops Status",
        "",
        f"- target_date: {today_date}",
        f"- daily_dir: {today_dir.as_posix() if today_dir else 'missing'}",
        f"- pre_race_completed: {pre_exists}",
        f"- odds_refresh_completed: {odds_exists}",
        f"- post_race_completed: {post_exists}",
        f"- daily_summary_exists: {daily_summary_exists}",
        "",
        "## operation_guidance",
        f"- pre_race まで完了: {'yes' if pre_exists else 'no'}",
        f"- odds_refresh 未実行: {'yes' if not odds_exists else 'no'}",
        f"- post_race は夜または結果取得後に実行すべき: {'yes' if not post_exists else 'already_executed'}",
        "- daily_summary は post_race 実行後に生成される想定",
    ]
    TODAY_OPS_STATUS_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_health_check() -> HealthCheckResult:
    daily_root = ROOT / "reports" / "daily"
    latest_date, latest_dir = _parse_latest_daily_date(daily_root)
    has_compact_date_format, has_hyphen_date_format = _detect_daily_date_formats(daily_root)
    date_formats_mixed = has_compact_date_format and has_hyphen_date_format

    pre_race = latest_dir / "pre_race_run.json" if latest_dir else Path("")
    odds_refresh = latest_dir / "odds_refresh_run.json" if latest_dir else Path("")
    post_race = latest_dir / "post_race_run.json" if latest_dir else Path("")
    daily_summary = latest_dir / "daily_summary.json" if latest_dir else Path("")
    skip_decisions = latest_dir / "skip_decisions.csv" if latest_dir else Path("")

    latest_pre_race_date = _latest_date_with_file(daily_root, "pre_race_run.json")
    latest_odds_refresh_date = _latest_date_with_file(daily_root, "odds_refresh_run.json")
    latest_post_race_date = _latest_date_with_file(daily_root, "post_race_run.json")
    latest_complete_ops_date, missing_map = _latest_complete_ops_date(daily_root)
    latest_daily_issue_classification = _classify_latest_daily_issue(latest_dir)
    latest_ready_daily_date = _latest_date_by_issue(
        daily_root,
        exclude={
            "future_date_not_ready",
            "source_not_ready",
            "official_index_unavailable",
            "official_index_parse_failed",
            "official_index_empty",
        },
    )
    latest_source_not_ready_date = _latest_date_by_issue(
        daily_root,
        include={
            "future_date_not_ready",
            "source_not_ready",
            "official_index_unavailable",
            "official_index_parse_failed",
            "official_index_empty",
        },
    )
    latest_incomplete_daily_date = _latest_date_by_issue(daily_root, exclude={"complete"})
    latest_daily_missing_items = missing_map.get(latest_date, [])
    legacy_dirs = list_legacy_daily_dirs(daily_root)

    ui_exists = False
    if latest_date:
        ui_dir = ROOT / "data" / "ui" / compact_date_str(latest_date)
        ui_exists = ui_dir.exists() and any(ui_dir.glob("raceyosou_*.json"))

    sample_symbol, true_positive_count, ignored_false_positive_count, true_hits, ignored_hits = _sample_symbol_audit()
    _write_sample_symbol_audit(true_positive_count, ignored_false_positive_count, true_hits, ignored_hits)
    skip_required, skip_alias_compatible, skip_missing_columns, skip_alias_columns = _write_skip_schema_audit(
        latest_date, latest_dir, date_formats_mixed
    )
    _write_latest_ops_gap_audit(
        daily_root,
        latest_date,
        latest_dir,
        latest_complete_ops_date,
        latest_daily_issue_classification,
        missing_map,
    )
    _write_latest_complete_ops_audit(
        daily_root,
        latest_complete_ops_date,
        latest_pre_race_date,
        latest_odds_refresh_date,
        latest_post_race_date,
    )
    _write_today_ops_status(daily_root)

    ev_lane_only = False
    build_ev = ROOT / "build_ev_table.py"
    if build_ev.exists():
        txt = _safe_read_text(build_ev)
        ev_lane_only = "lane-level recomputation is disabled" not in txt and "today_win_proba.csv" in txt

    ev_uses_candidates = False
    ev_script = ROOT / "src" / "strategy" / "evaluate_ev_and_skip.py"
    if ev_script.exists():
        txt = _safe_read_text(ev_script)
        ev_uses_candidates = "trifecta_candidates.csv" in txt and "approx_prob" in txt

    prod_summary_reads_demo_diag = False
    for p in [
        ROOT / "src" / "pipeline" / "run_daily_post_race.py",
        ROOT / "src" / "pipeline" / "daily_report.py",
        ROOT / "src" / "pipeline" / "health_check.py",
    ]:
        if not p.exists():
            continue
        txt = _safe_read_text(p)
        if "reports/demo" in txt or "reports/diagnostics" in txt:
            prod_summary_reads_demo_diag = True
            break

    required_audit_files = [
        REPORT_DIR / "keep_candidates.csv",
        REPORT_DIR / "archive_candidates.csv",
        REPORT_DIR / "review_required.csv",
        REPORT_DIR / "delete_candidates.csv",
        REPORT_DIR / "audit_summary.md",
        REPORT_DIR / "reference_matrix.csv",
        REPORT_DIR / "review_resolution.csv",
        REPORT_DIR / "safe_archive_plan.csv",
    ]
    audit_files_ready = all(p.exists() for p in required_audit_files)

    warnings: list[str] = []
    if not latest_date:
        warnings.append("latest_daily_date_missing")
    if sample_symbol:
        warnings.append("sample_symbol_detected_in_src")
    if date_formats_mixed:
        warnings.append("reports_daily_date_format_mixed")
    if ev_lane_only:
        warnings.append("ev_may_depend_on_lane_level_today_win_proba")
    if not ev_uses_candidates:
        warnings.append("ev_candidates_or_approx_prob_not_detected")
    if prod_summary_reads_demo_diag:
        warnings.append("production_summary_reads_demo_or_diagnostics")
    if latest_date and latest_date != latest_complete_ops_date:
        warnings.append("latest_daily_ops_incomplete")
    if skip_decisions.exists() and not skip_required:
        warnings.append("skip_decisions_required_columns_missing")
    if not audit_files_ready:
        warnings.append("repo_audit_required_files_missing")

    return HealthCheckResult(
        latest_daily_date=latest_date,
        latest_ready_daily_date=latest_ready_daily_date,
        latest_daily_dir=str(latest_dir) if latest_dir else "",
        latest_pre_race_date=latest_pre_race_date,
        latest_odds_refresh_date=latest_odds_refresh_date,
        latest_post_race_date=latest_post_race_date,
        latest_complete_ops_date=latest_complete_ops_date,
        latest_source_not_ready_date=latest_source_not_ready_date,
        latest_incomplete_daily_date=latest_incomplete_daily_date,
        latest_daily_missing_items=latest_daily_missing_items,
        latest_daily_issue_classification=latest_daily_issue_classification,
        legacy_daily_dirs=legacy_dirs,
        daily_date_format_mixed=date_formats_mixed,
        pre_race_run_exists=pre_race.exists() if latest_dir else False,
        odds_refresh_run_exists=odds_refresh.exists() if latest_dir else False,
        post_race_run_exists=post_race.exists() if latest_dir else False,
        daily_summary_exists=daily_summary.exists() if latest_date else False,
        skip_decisions_exists=skip_decisions.exists() if latest_dir else False,
        skip_decisions_required_columns=skip_required,
        skip_decisions_alias_compatible=skip_alias_compatible,
        skip_decisions_missing_columns=skip_missing_columns,
        skip_decisions_alias_columns=skip_alias_columns,
        raw_official_exists=(ROOT / "data" / "raw" / "official").exists(),
        models_exists=(ROOT / "models").exists(),
        ui_json_exists_for_latest_date=ui_exists,
        sample_symbol_in_src=sample_symbol,
        sample_symbol_true_positive_count=true_positive_count,
        sample_symbol_ignored_false_positive_count=ignored_false_positive_count,
        ev_uses_today_win_proba_lane_only=ev_lane_only,
        ev_uses_trifecta_candidates_and_approx_prob=ev_uses_candidates,
        demo_diagnostics_used_by_production_summary=prod_summary_reads_demo_diag,
        repo_audit_files_ready=audit_files_ready,
        warnings=warnings,
    )


def write_outputs(result: HealthCheckResult) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        **result.__dict__,
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# Repo Health Check",
        "",
        f"- latest_daily_date: {result.latest_daily_date or 'missing'}",
        f"- latest_ready_daily_date: {result.latest_ready_daily_date or 'missing'}",
        f"- latest_daily_dir: {result.latest_daily_dir or 'missing'}",
        f"- latest_pre_race_date: {result.latest_pre_race_date or 'missing'}",
        f"- latest_odds_refresh_date: {result.latest_odds_refresh_date or 'missing'}",
        f"- latest_post_race_date: {result.latest_post_race_date or 'missing'}",
        f"- latest_complete_ops_date: {result.latest_complete_ops_date or 'missing'}",
        f"- latest_source_not_ready_date: {result.latest_source_not_ready_date or 'missing'}",
        f"- latest_incomplete_daily_date: {result.latest_incomplete_daily_date or 'missing'}",
        f"- latest_daily_issue_classification: {result.latest_daily_issue_classification}",
        f"- latest_daily_missing_items: {', '.join(result.latest_daily_missing_items) if result.latest_daily_missing_items else 'none'}",
        f"- legacy_daily_dirs: {', '.join(result.legacy_daily_dirs) if result.legacy_daily_dirs else 'none'}",
        f"- daily_date_format_mixed: {result.daily_date_format_mixed}",
        f"- pre_race_run_exists: {result.pre_race_run_exists}",
        f"- odds_refresh_run_exists: {result.odds_refresh_run_exists}",
        f"- post_race_run_exists: {result.post_race_run_exists}",
        f"- daily_summary_exists: {result.daily_summary_exists}",
        f"- skip_decisions_exists: {result.skip_decisions_exists}",
        f"- skip_decisions_required_columns: {result.skip_decisions_required_columns}",
        f"- skip_decisions_alias_compatible: {result.skip_decisions_alias_compatible}",
        f"- skip_decisions_missing_columns: {', '.join(result.skip_decisions_missing_columns) if result.skip_decisions_missing_columns else 'none'}",
        f"- skip_decisions_alias_columns: {json.dumps(result.skip_decisions_alias_columns, ensure_ascii=False)}",
        f"- raw_official_exists: {result.raw_official_exists}",
        f"- models_exists: {result.models_exists}",
        f"- ui_json_exists_for_latest_date: {result.ui_json_exists_for_latest_date}",
        f"- sample_symbol_in_src: {result.sample_symbol_in_src}",
        f"- sample_symbol_true_positive_count: {result.sample_symbol_true_positive_count}",
        f"- sample_symbol_ignored_false_positive_count: {result.sample_symbol_ignored_false_positive_count}",
        f"- ev_uses_today_win_proba_lane_only: {result.ev_uses_today_win_proba_lane_only}",
        f"- ev_uses_trifecta_candidates_and_approx_prob: {result.ev_uses_trifecta_candidates_and_approx_prob}",
        f"- demo_diagnostics_used_by_production_summary: {result.demo_diagnostics_used_by_production_summary}",
        f"- repo_audit_files_ready: {result.repo_audit_files_ready}",
        "",
        "## latest_daily_completion",
        f"- latest daily complete for operations: {result.latest_complete_ops_date or 'none'}",
        f"- if latest_daily_date is newer than latest_complete_ops_date, see {LATEST_OPS_GAP_AUDIT_MD.as_posix()}",
        "",
        "## warnings",
    ]
    if not result.warnings:
        lines.append("- none")
    else:
        lines.extend(f"- {w}" for w in result.warnings)
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    result = run_health_check()
    write_outputs(result)
    print(
        {
            "health_check_json": str(OUT_JSON),
            "health_check_md": str(OUT_MD),
            "latest_daily_date": result.latest_daily_date,
            "warnings": result.warnings,
        }
    )


if __name__ == "__main__":
    main()
