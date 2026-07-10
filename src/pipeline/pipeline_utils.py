from __future__ import annotations

import json
import shutil
import subprocess
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

import pandas as pd
from src.utils.date_paths import (
    find_existing_daily_report_dir,
    get_daily_report_dir,
    normalize_date_str,
)


ROOT = Path(__file__).resolve().parents[2]
REPORTS_ROOT = ROOT / "reports" / "daily"
LOGS_ROOT = ROOT / "logs"


def parse_date(value: str | None, *, default: date | None = None) -> date:
    if value:
        return datetime.strptime(normalize_date_str(value), "%Y-%m-%d").date()
    if default is not None:
        return default
    return datetime.now().date()


def report_dir_for(target_date: date) -> Path:
    path = get_daily_report_dir(target_date, REPORTS_ROOT)
    path.mkdir(parents=True, exist_ok=True)
    return path


def existing_report_dir_for(target_date: date) -> Path:
    return find_existing_daily_report_dir(target_date, REPORTS_ROOT)


def iso_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def write_json(path: Path, payload: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def log_file_for(pipeline_name: str, target_date: date) -> Path:
    path = LOGS_ROOT / pipeline_name / f"{target_date.isoformat()}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def append_log(log_path: Path, message: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"{iso_now()} {message}\n")


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def run_step(
    label: str,
    cmd: list[str],
    *,
    cwd: Path = ROOT,
    allow_failure: bool = False,
    timeout: int | None = None,
    log_path: Path | None = None,
) -> dict:
    started_at = iso_now()
    started = time.perf_counter()
    if log_path is not None:
        append_log(log_path, f"[step:start] {label} cmd={cmd}")
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        returncode = int(proc.returncode)
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
    except subprocess.TimeoutExpired as exc:
        returncode = 124
        stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        stderr = (stderr + f"\n[timeout] {label} exceeded {timeout} seconds").strip()
    ended_at = iso_now()
    duration = round(time.perf_counter() - started, 3)
    status = "ok" if returncode == 0 else ("allowed_failure" if allow_failure else "failed")
    if log_path is not None:
        if stdout:
            append_log(log_path, f"[step:stdout] {label}\n{stdout[-4000:]}")
        if stderr:
            append_log(log_path, f"[step:stderr] {label}\n{stderr[-2500:]}")
        append_log(
            log_path,
            f"[step:end] {label} status={status} returncode={returncode} duration_sec={duration}",
        )
    return {
        "label": label,
        "cmd": cmd,
        "cwd": str(cwd),
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_sec": duration,
        "status": status,
        "returncode": returncode,
        "stdout_tail": stdout[-4000:],
        "stderr_tail": stderr[-2500:],
        "allow_failure": bool(allow_failure),
    }


def backup_files(paths: Iterable[Path], backup_dir: Path) -> list[dict]:
    backup_dir.mkdir(parents=True, exist_ok=True)
    backups: list[dict] = []
    for src in paths:
        if not src.exists():
            continue
        dst = backup_dir / src.name
        shutil.copy2(src, dst)
        backups.append({"src": str(src), "backup": str(dst)})
    return backups


def restore_backups(backups: Iterable[dict]) -> None:
    for entry in backups:
        src = Path(entry["src"])
        backup = Path(entry["backup"])
        if backup.exists():
            src.parent.mkdir(parents=True, exist_ok=True)
            last_error: Exception | None = None
            for attempt in range(5):
                try:
                    shutil.copy2(backup, src)
                    last_error = None
                    break
                except PermissionError as exc:
                    last_error = exc
                    time.sleep(1.0)
                except Exception as exc:
                    last_error = exc
                    break
            if last_error is not None:
                raise last_error


def copy_artifact(src: Path, dest_dir: Path, dest_name: str | None = None) -> str | None:
    if not src.exists():
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    dst = dest_dir / (dest_name or src.name)
    shutil.copy2(src, dst)
    return str(dst)


def actual_trifecta_by_race(historical_df: pd.DataFrame) -> pd.DataFrame:
    work = historical_df.copy()
    work["finish_position"] = pd.to_numeric(work["finish_position"], errors="coerce")
    work["lane"] = pd.to_numeric(work["lane"], errors="coerce")
    work = work.dropna(subset=["race_id", "finish_position", "lane"])
    top3 = work[work["finish_position"].isin([1, 2, 3])].sort_values(["race_id", "finish_position"])
    trifecta = (
        top3.groupby("race_id")["lane"]
        .apply(lambda s: "-".join(str(int(v)) for v in s.tolist()) if len(s) == 3 else None)
        .reset_index(name="actual_trifecta")
    )
    return trifecta.dropna(subset=["actual_trifecta"])


def summarize_reason_keywords(skip_df: pd.DataFrame, limit: int = 10) -> list[dict]:
    if skip_df.empty:
        return []
    if "skip_reason" in skip_df.columns:
        reason_series = skip_df["skip_reason"].fillna("").astype(str)
    elif "reason" in skip_df.columns:
        reason_series = skip_df["reason"].fillna("").astype(str)
    elif "stop_reason" in skip_df.columns:
        reason_series = skip_df["stop_reason"].fillna("").astype(str)
    else:
        return []
    tokens: dict[str, int] = {}
    for text in reason_series:
        parts = [part.strip() for part in text.replace("・", "/").split("/") if part.strip()]
        for part in parts:
            tokens[part] = tokens.get(part, 0) + 1
    rows = [{"reason": key, "count": count} for key, count in sorted(tokens.items(), key=lambda x: x[1], reverse=True)]
    return rows[:limit]


def update_rolling_summary(reports_root: Path = REPORTS_ROOT) -> dict:
    summaries = sorted(reports_root.glob("*/daily_summary.json"))
    rows: list[dict] = []
    for path in summaries:
        data = read_json(path)
        if not data:
            continue
        rows.append(data)

    def aggregate(window_rows: list[dict], label: str) -> dict:
        races = sum(int(r.get("races", 0) or 0) for r in window_rows)
        buy_count = sum(int(r.get("buy_count", 0) or 0) for r in window_rows)
        hit_count = sum(int(r.get("hit_count", 0) or 0) for r in window_rows)
        total_stake = sum(float(r.get("total_stake", 0.0) or 0.0) for r in window_rows)
        total_return = sum(float(r.get("total_return", 0.0) or 0.0) for r in window_rows)
        exact_count = sum(int(r.get("exact_count", 0) or 0) for r in window_rows)
        top5_count = sum(int(r.get("top5_count", 0) or 0) for r in window_rows)
        top10_count = sum(int(r.get("top10_count", 0) or 0) for r in window_rows)
        avg_rank_num = sum(float(r.get("avg_rank", 0.0) or 0.0) * int(r.get("ranked_race_count", 0) or 0) for r in window_rows)
        ranked_race_count = sum(int(r.get("ranked_race_count", 0) or 0) for r in window_rows)
        status_counts = {}
        for row in window_rows:
            status = str(row.get("results_status", "unknown") or "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1
        evaluable_days = status_counts.get("available", 0)
        nonevaluable_days = len(window_rows) - evaluable_days
        return {
            "window": label,
            "days": len(window_rows),
            "races": races,
            "buy_count": buy_count,
            "hit_count": hit_count,
            "hit_rate": round(hit_count / buy_count, 4) if buy_count else None,
            "roi": round(total_return / total_stake, 4) if total_stake else None,
            "total_stake": round(total_stake, 2),
            "total_return": round(total_return, 2),
            "exact_rate": round(exact_count / races, 4) if races else None,
            "top5_rate": round(top5_count / races, 4) if races else None,
            "top10_rate": round(top10_count / races, 4) if races else None,
            "avg_rank": round(avg_rank_num / ranked_race_count, 3) if ranked_race_count else None,
            "ranked_race_count": ranked_race_count,
            "results_status_counts": status_counts,
            "evaluable_days": evaluable_days,
            "nonevaluable_days": nonevaluable_days,
        }

    rows = sorted(rows, key=lambda r: str(r.get("date", "")))
    recent7 = rows[-7:]
    recent30 = rows[-30:]
    all_period = rows
    payload = {
        "generated_at": iso_now(),
        "source_count": len(rows),
        "windows": [
            aggregate(recent7, "recent7"),
            aggregate(recent30, "recent30"),
            aggregate(all_period, "all"),
        ],
    }
    baseline = next((w for w in payload["windows"] if w["window"] == "all"), None)
    for window in payload["windows"]:
        if not baseline or window["window"] == "all":
            continue
        window["vs_all"] = {
            "roi_delta": round((window["roi"] or 0.0) - (baseline["roi"] or 0.0), 4)
            if window["roi"] is not None and baseline["roi"] is not None
            else None,
            "hit_rate_delta": round((window["hit_rate"] or 0.0) - (baseline["hit_rate"] or 0.0), 4)
            if window["hit_rate"] is not None and baseline["hit_rate"] is not None
            else None,
            "top5_delta": round((window["top5_rate"] or 0.0) - (baseline["top5_rate"] or 0.0), 4)
            if window["top5_rate"] is not None and baseline["top5_rate"] is not None
            else None,
            "avg_rank_delta": round((window["avg_rank"] or 0.0) - (baseline["avg_rank"] or 0.0), 4)
            if window["avg_rank"] is not None and baseline["avg_rank"] is not None
            else None,
        }
        window["sample_note"] = "件数が少ない場合は改善断定しない"

    write_json(reports_root / "rolling_summary.json", payload)
    if rows:
        pd.DataFrame(rows).to_csv(reports_root / "daily_summary_history.csv", index=False)
    return payload


def build_results_status_diagnostic(reports_root: Path = REPORTS_ROOT) -> dict:
    summaries = sorted(reports_root.glob("*/daily_summary.json"))
    rows: list[dict] = []
    for path in summaries:
        data = read_json(path)
        if not data:
            continue
        rows.append(data)

    status_counts: dict[str, int] = {}
    warning_count = 0
    raw_missing_count = 0
    raw_incomplete_count = 0
    processed_not_reflected_count = 0
    read_mismatch_count = 0
    races_gt0_days = 0
    roi_numeric_days = 0
    major_fields = [
        "results_status",
        "results_source",
        "results_rows",
        "results_warning",
        "races",
        "buy_count",
        "hit_count",
        "roi",
        "exact_rate",
        "top5_rate",
        "top10_rate",
        "avg_rank",
    ]
    fill_counts = {field: 0 for field in major_fields}

    for row in rows:
        status = str(row.get("results_status", "unknown") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        warning = row.get("results_warning")
        if warning:
            warning_count += 1
        if status == "raw_missing":
            raw_missing_count += 1
        elif status == "raw_incomplete":
            raw_incomplete_count += 1
        elif status == "processed_not_reflected":
            processed_not_reflected_count += 1
        elif status == "read_mismatch":
            read_mismatch_count += 1
        if int(row.get("races", 0) or 0) > 0:
            races_gt0_days += 1
        if row.get("roi") is not None:
            roi_numeric_days += 1

        for field in major_fields:
            value = row.get(field)
            if value is not None:
                fill_counts[field] += 1

    days = len(rows)
    payload = {
        "days": days,
        "results_status_counts": status_counts,
        "results_unavailable_for_date_count": warning_count,
        "raw_missing_count": raw_missing_count,
        "raw_incomplete_count": raw_incomplete_count,
        "processed_not_reflected_count": processed_not_reflected_count,
        "read_mismatch_count": read_mismatch_count,
        "available_count": status_counts.get("available", 0),
        "evaluable_days": status_counts.get("available", 0),
        "nonevaluable_days": days - status_counts.get("available", 0),
        "roi_numeric_days": roi_numeric_days,
        "races_gt0_days": races_gt0_days,
        "major_field_fill_rate": {
            field: round(fill_counts[field] / days, 3) if days else 0.0
            for field in major_fields
        },
        "main_causes": sorted(status_counts.items(), key=lambda item: item[1], reverse=True)[:3],
    }
    write_json(reports_root / "results_status_diagnostic.json", payload)
    return payload
