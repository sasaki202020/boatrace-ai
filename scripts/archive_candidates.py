from __future__ import annotations

import argparse
import csv
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO_AUDIT = ROOT / "reports" / "repo_audit"
ARCHIVE_CANDIDATES_CSV = REPO_AUDIT / "archive_candidates.csv"
REVIEW_RESOLUTION_CSV = REPO_AUDIT / "review_resolution.csv"
SAFE_PLAN_CSV = REPO_AUDIT / "safe_archive_plan.csv"
DRYRUN_CSV = REPO_AUDIT / "archive_dry_run.csv"
DRYRUN_MD = REPO_AUDIT / "archive_dry_run.md"

GUARD_PREFIXES = (
    "data/raw",
    "data/raw/official",
    "models",
    "configs",
    "src/pipeline",
    "src/ingest",
    "src/odds",
    "src/strategy",
    "src/web",
    "tests",
)
GUARD_EXACT = (
    "src/evaluation/run_day_evaluation_v2.py",
    "src/evaluation/run_batch_evaluation_v2.py",
)


@dataclass
class PlanRow:
    path: str
    source: str
    reason: str
    risk_level: str
    action: str
    planned_destination: str
    safety_status: str


def _norm(p: str) -> str:
    return p.replace("\\", "/").strip()


def _is_guarded(path_pattern: str) -> bool:
    p = _norm(path_pattern).rstrip("/*")
    if any(p == _norm(x) for x in GUARD_EXACT):
        return True
    return any(p.startswith(prefix) for prefix in GUARD_PREFIXES)


def _load_archive_candidates(path: Path) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    if not path.exists():
        return out
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            key = _norm(str(row.get("path", "")))
            if not key:
                continue
            out[key] = {
                "classification": str(row.get("classification", "")).strip(),
                "reason": str(row.get("reason", "")).strip(),
            }
    return out


def _load_review_resolution(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            rows.append({k: str(v or "").strip() for k, v in row.items()})
    return rows


def build_safe_plan(today: str) -> list[PlanRow]:
    archive_candidates = _load_archive_candidates(ARCHIVE_CANDIDATES_CSV)
    resolution_rows = _load_review_resolution(REVIEW_RESOLUTION_CSV)
    plan_rows: list[PlanRow] = []

    for row in resolution_rows:
        path = _norm(row.get("path", ""))
        if not path:
            continue
        proposed = row.get("proposed_classification", "")
        action = row.get("action", "")
        risk = row.get("risk_level", "")
        if proposed != "archive":
            continue
        if action != "archive_later":
            continue
        if risk != "low":
            continue
        if _is_guarded(path):
            continue
        if _norm(path).startswith("review_required"):
            continue

        source = "review_resolution"
        if path in archive_candidates:
            source = "archive_candidates+review_resolution"
        reason = row.get("reason", "") or archive_candidates.get(path, {}).get("reason", "")
        planned_destination = f"_archive/{today}/{path}"
        plan_rows.append(
            PlanRow(
                path=path,
                source=source,
                reason=reason,
                risk_level=risk,
                action=action,
                planned_destination=planned_destination,
                safety_status="safe_for_dry_run",
            )
        )

    return plan_rows


def write_safe_plan(rows: list[PlanRow]) -> None:
    SAFE_PLAN_CSV.parent.mkdir(parents=True, exist_ok=True)
    with SAFE_PLAN_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["path", "source", "reason", "risk_level", "action", "planned_destination", "safety_status"])
        for r in rows:
            writer.writerow([r.path, r.source, r.reason, r.risk_level, r.action, r.planned_destination, r.safety_status])


def _expand_matches(path_pattern: str) -> list[Path]:
    pattern = _norm(path_pattern)
    matches = sorted(ROOT.glob(pattern))
    return [m for m in matches if m.exists()]


def _safe_move(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))


def run_dry_or_apply(rows: list[PlanRow], apply: bool, today: str) -> tuple[list[dict[str, str]], int]:
    detail_rows: list[dict[str, str]] = []
    moved_count = 0
    for row in rows:
        matches = _expand_matches(row.path)
        if not matches:
            detail_rows.append(
                {
                    "path": row.path,
                    "match_path": "",
                    "status": "no_match",
                    "planned_destination": row.planned_destination,
                    "safety_status": "safe_for_dry_run",
                }
            )
            continue
        for match in matches:
            rel = _norm(str(match.relative_to(ROOT)))
            if _is_guarded(rel):
                detail_rows.append(
                    {
                        "path": row.path,
                        "match_path": rel,
                        "status": "blocked_by_guard",
                        "planned_destination": row.planned_destination,
                        "safety_status": "blocked",
                    }
                )
                continue
            dst = ROOT / "_archive" / today / rel
            if apply:
                _safe_move(match, dst)
                moved_count += 1
                status = "moved"
            else:
                status = "dry_run"
            detail_rows.append(
                {
                    "path": row.path,
                    "match_path": rel,
                    "status": status,
                    "planned_destination": _norm(str(dst.relative_to(ROOT))),
                    "safety_status": "safe_for_dry_run",
                }
            )
    return detail_rows, moved_count


def write_dryrun_reports(details: list[dict[str, str]], apply: bool, moved_count: int, plan_count: int) -> None:
    REPO_AUDIT.mkdir(parents=True, exist_ok=True)
    with DRYRUN_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["path", "match_path", "status", "planned_destination", "safety_status"],
        )
        writer.writeheader()
        writer.writerows(details)

    status_counts: dict[str, int] = {}
    for row in details:
        status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1

    with DRYRUN_MD.open("w", encoding="utf-8") as f:
        f.write("# Archive Dry Run Report\n\n")
        f.write(f"- mode: {'apply' if apply else 'dry-run'}\n")
        f.write(f"- plan_rows: {plan_count}\n")
        f.write(f"- detail_rows: {len(details)}\n")
        f.write(f"- moved_count: {moved_count}\n")
        f.write(f"- dry_run_count: {status_counts.get('dry_run', 0)}\n")
        f.write(f"- no_match_count: {status_counts.get('no_match', 0)}\n")
        f.write(f"- blocked_by_guard_count: {status_counts.get('blocked_by_guard', 0)}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Archive candidate runner with safety guards.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    today = datetime.now().strftime("%Y%m%d")
    plan_rows = build_safe_plan(today=today)
    write_safe_plan(plan_rows)
    details, moved_count = run_dry_or_apply(plan_rows, apply=args.apply, today=today)
    write_dryrun_reports(details, apply=args.apply, moved_count=moved_count, plan_count=len(plan_rows))

    print(
        {
            "safe_plan_csv": str(SAFE_PLAN_CSV),
            "archive_dry_run_csv": str(DRYRUN_CSV),
            "archive_dry_run_md": str(DRYRUN_MD),
            "mode": "apply" if args.apply else "dry-run",
            "plan_rows": len(plan_rows),
            "detail_rows": len(details),
            "moved_count": moved_count,
        }
    )


if __name__ == "__main__":
    main()
