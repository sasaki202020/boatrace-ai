from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "reports" / "repo_audit" / "review_required.csv"
DEFAULT_OUTPUT = ROOT / "reports" / "repo_audit" / "reference_matrix.csv"
DEFAULT_SUMMARY = ROOT / "reports" / "repo_audit" / "reference_matrix_summary.md"
DEFAULT_RESOLUTION = ROOT / "reports" / "repo_audit" / "review_resolution.csv"
DEFAULT_RESOLUTION_SUMMARY = ROOT / "reports" / "repo_audit" / "review_resolution_summary.md"

ALLOWED_EXTENSIONS = {
    ".py",
    ".js",
    ".md",
    ".txt",
    ".json",
    ".csv",
    ".bat",
    ".ps1",
    ".yaml",
    ".yml",
}

EXCLUDED_DIR_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".pytest_tmp",
    ".pytest_tmp_check",
    ".pytest_tmp_check2",
    ".pytest_tmp_local",
    ".pytest_tmp_safe",
    ".pytest-tmp",
    ".codex_tmp",
    "_tmp",
    "_pytest_root",
    "_pytest_root2",
    "tmp0ml_t7_5",
    "tmphhr11xjy",
    "Usersgoo10.codexmemoriespytest_tmp",
}


@dataclass
class PathCheck:
    path: str
    current_classification: str
    probe: str
    hit_count: int
    referenced_by: str
    referenced_in_src_pipeline: bool
    referenced_in_src_evaluation: bool
    referenced_in_src_web: bool
    referenced_in_scripts: bool
    referenced_in_docs_only: bool
    referenced_in_tests_only: bool


@dataclass
class ResolutionRow:
    path: str
    current_classification: str
    proposed_classification: str
    reason: str
    referenced_by: str
    hit_count: int
    risk_level: str
    action: str


def iter_scan_files(roots: list[Path]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() not in ALLOWED_EXTENSIONS:
                continue
            parts = set(part.lower() for part in p.parts)
            if any(ex.lower() in parts for ex in EXCLUDED_DIR_NAMES):
                continue
            files.append(p)
    return files


def normalize_path_pattern(path_pattern: str) -> str:
    normalized = path_pattern.replace("\\", "/").strip()
    if normalized.endswith("/**"):
        normalized = normalized[:-3]
    if normalized.endswith("/*"):
        normalized = normalized[:-2]
    return normalized.rstrip("/")


def build_probe(path_pattern: str) -> str:
    normalized = normalize_path_pattern(path_pattern)
    if "TASK-*" in normalized:
        return "TASK-"
    if normalized.endswith(".py"):
        return Path(normalized).name
    if "/" in normalized:
        return f"{normalized}/"
    return normalized


def file_role(path: Path) -> str:
    norm = str(path.relative_to(ROOT)).replace("\\", "/")
    if norm.startswith("src/pipeline/"):
        return "src_pipeline"
    if norm.startswith("src/evaluation/"):
        return "src_evaluation"
    if norm.startswith("src/web/"):
        return "src_web"
    if norm.startswith("scripts/"):
        return "scripts"
    if norm.startswith("docs/"):
        return "docs"
    if norm.startswith("tests/"):
        return "tests"
    return "other"


def scan_probe(files: list[Path], probe: str) -> tuple[list[Path], dict[str, int]]:
    hits: list[Path] = []
    role_counts = {
        "src_pipeline": 0,
        "src_evaluation": 0,
        "src_web": 0,
        "scripts": 0,
        "docs": 0,
        "tests": 0,
        "other": 0,
    }
    for p in files:
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                text = p.read_text(encoding="cp932")
            except Exception:
                continue
        except Exception:
            continue
        if probe and probe in text:
            hits.append(p)
            role_counts[file_role(p)] += 1
    return hits, role_counts


def load_review_required(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(
                {
                    "classification": str(row.get("classification", "")).strip() or "review_required",
                    "path": str(row.get("path", "")).strip(),
                }
            )
    return rows


def to_relative(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


def build_matrix(rows: list[dict[str, str]], scan_files: list[Path]) -> list[PathCheck]:
    matrix: list[PathCheck] = []
    for row in rows:
        path_pattern = row["path"]
        probe = build_probe(path_pattern)
        hits, roles = scan_probe(scan_files, probe)
        rel_hits = [to_relative(p) for p in hits[:12]]
        only_docs = roles["docs"] > 0 and sum(v for k, v in roles.items() if k != "docs") == 0
        only_tests = roles["tests"] > 0 and sum(v for k, v in roles.items() if k != "tests") == 0
        matrix.append(
            PathCheck(
                path=path_pattern,
                current_classification=row["classification"],
                probe=probe,
                hit_count=len(hits),
                referenced_by=";".join(rel_hits) if rel_hits else "",
                referenced_in_src_pipeline=roles["src_pipeline"] > 0,
                referenced_in_src_evaluation=roles["src_evaluation"] > 0,
                referenced_in_src_web=roles["src_web"] > 0,
                referenced_in_scripts=roles["scripts"] > 0,
                referenced_in_docs_only=only_docs,
                referenced_in_tests_only=only_tests,
            )
        )
    return matrix


def _split_refs(referenced_by: str) -> list[str]:
    return [part.strip() for part in referenced_by.split(";") if part.strip()]


def _is_prod_script_ref(refs: Iterable[str]) -> bool:
    prod_prefixes = (
        "scripts/run_daily_",
        "scripts/run_evening_",
        "scripts/health_check",
        "scripts/register_tasks",
        "scripts/import_k_results",
        "scripts/check_k_inbox",
        "scripts/import_and_refresh_k_results",
    )
    for ref in refs:
        if any(ref.startswith(prefix) for prefix in prod_prefixes):
            return True
    return False


def _contains_keyword(path: str, keywords: Iterable[str]) -> bool:
    lowered = path.lower()
    return any(keyword in lowered for keyword in keywords)


def classify_resolution(row: PathCheck) -> ResolutionRow:
    refs = _split_refs(row.referenced_by)
    path_l = row.path.lower()
    docs_current_status = any(ref == "docs/CURRENT_STATUS.md" for ref in refs)
    prod_script_ref = _is_prod_script_ref(refs)

    forced_review = (
        path_l.startswith("app/")
        or path_l.startswith("raw/")
        or path_l.startswith("outputs/")
        or "tasks/task-*" in path_l
        or row.referenced_in_tests_only
    )

    is_core_path = (
        path_l.startswith("src/pipeline/")
        or path_l.startswith("src/evaluation/")
        or path_l.startswith("src/web/")
        or "models" in path_l
        or "config" in path_l
        or "data/raw" in path_l
    )

    archival_hint = _contains_keyword(path_l, ("demo", "diagnostics", "task", "old", "shadow", "dry-run"))

    if forced_review:
        proposed = "review_required"
        action = "manual_review_needed"
        reason = "rule-matched review_required guard"
    elif path_l.startswith("src/") or path_l.startswith("models/") or path_l.startswith("config/"):
        proposed = "keep"
        action = "keep_as_is"
        reason = "protected core path"
    elif path_l.startswith("output/") or path_l.startswith("_archive/"):
        proposed = "review_required"
        action = "manual_review_needed"
        reason = "runtime relevance is ambiguous"
    elif row.referenced_in_src_pipeline or row.referenced_in_src_evaluation or row.referenced_in_src_web or prod_script_ref or docs_current_status:
        proposed = "keep"
        action = "keep_as_is"
        reason = "referenced by production/runtime path"
    elif row.referenced_in_docs_only or archival_hint:
        proposed = "archive"
        action = "archive_later"
        reason = "docs-only or archive-oriented artifact"
    elif row.hit_count == 0 and not is_core_path:
        proposed = "archive"
        action = "archive_later"
        reason = "no runtime references found outside protected core paths"
    else:
        proposed = "review_required"
        action = "manual_review_needed"
        reason = "reference exists but production relevance is ambiguous"

    if is_core_path or row.referenced_in_src_pipeline or row.referenced_in_src_evaluation or row.referenced_in_src_web:
        risk = "high"
    elif row.referenced_in_scripts or row.referenced_in_docs_only or row.referenced_in_tests_only:
        risk = "medium"
    elif archival_hint or row.hit_count == 0:
        risk = "low"
    else:
        risk = "medium"

    return ResolutionRow(
        path=row.path,
        current_classification=row.current_classification,
        proposed_classification=proposed,
        reason=reason,
        referenced_by=row.referenced_by,
        hit_count=row.hit_count,
        risk_level=risk,
        action=action,
    )


def write_resolution_csv(path: Path, rows: list[ResolutionRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "path",
                "current_classification",
                "proposed_classification",
                "reason",
                "referenced_by",
                "hit_count",
                "risk_level",
                "action",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row.path,
                    row.current_classification,
                    row.proposed_classification,
                    row.reason,
                    row.referenced_by,
                    row.hit_count,
                    row.risk_level,
                    row.action,
                ]
            )


def write_resolution_summary(path: Path, rows: list[ResolutionRow]) -> None:
    keep_count = sum(1 for r in rows if r.proposed_classification == "keep")
    archive_count = sum(1 for r in rows if r.proposed_classification == "archive")
    review_count = sum(1 for r in rows if r.proposed_classification == "review_required")
    high_count = sum(1 for r in rows if r.risk_level == "high")
    medium_count = sum(1 for r in rows if r.risk_level == "medium")
    low_count = sum(1 for r in rows if r.risk_level == "low")
    manual_items = [r.path for r in rows if r.action == "manual_review_needed"]

    with path.open("w", encoding="utf-8") as f:
        f.write("# Review Resolution Summary\n\n")
        f.write(f"- keep 推奨数: {keep_count}\n")
        f.write(f"- archive 推奨数: {archive_count}\n")
        f.write(f"- review_required 継続数: {review_count}\n")
        f.write(f"- high risk 件数: {high_count}\n")
        f.write(f"- medium risk 件数: {medium_count}\n")
        f.write(f"- low risk 件数: {low_count}\n\n")
        f.write("## archive 実行前に手動確認すべき項目\n")
        if not manual_items:
            f.write("- なし\n")
        else:
            for item in manual_items:
                f.write(f"- `{item}`\n")


def write_csv(path: Path, rows: list[PathCheck]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "path",
                "current_classification",
                "probe",
                "hit_count",
                "referenced_by",
                "referenced_in_src_pipeline",
                "referenced_in_src_evaluation",
                "referenced_in_src_web",
                "referenced_in_scripts",
                "referenced_in_docs_only",
                "referenced_in_tests_only",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row.path,
                    row.current_classification,
                    row.probe,
                    row.hit_count,
                    row.referenced_by,
                    str(row.referenced_in_src_pipeline).lower(),
                    str(row.referenced_in_src_evaluation).lower(),
                    str(row.referenced_in_src_web).lower(),
                    str(row.referenced_in_scripts).lower(),
                    str(row.referenced_in_docs_only).lower(),
                    str(row.referenced_in_tests_only).lower(),
                ]
            )


def write_summary(path: Path, rows: list[PathCheck]) -> None:
    with path.open("w", encoding="utf-8") as f:
        f.write("# Reference Matrix Summary\n\n")
        f.write(f"- rows: {len(rows)}\n")
        f.write(f"- hits_found: {sum(1 for r in rows if r.hit_count > 0)}\n")
        f.write(f"- no_hits: {sum(1 for r in rows if r.hit_count == 0)}\n\n")
        f.write("## No Hits\n")
        for row in rows:
            if row.hit_count == 0:
                f.write(f"- `{row.path}`\n")
        f.write("\n## Docs Only\n")
        for row in rows:
            if row.referenced_in_docs_only:
                f.write(f"- `{row.path}`\n")
        f.write("\n## Tests Only\n")
        for row in rows:
            if row.referenced_in_tests_only:
                f.write(f"- `{row.path}`\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build reference matrix for repo audit paths.")
    parser.add_argument("--input-csv", default=str(DEFAULT_INPUT))
    parser.add_argument("--output-csv", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--output-summary", default=str(DEFAULT_SUMMARY))
    parser.add_argument("--emit-resolution", default="")
    parser.add_argument("--resolution-summary", default=str(DEFAULT_RESOLUTION_SUMMARY))
    parser.add_argument("--search-roots", default="src,scripts,docs,tests")
    args = parser.parse_args()

    in_csv = Path(args.input_csv)
    out_csv = Path(args.output_csv)
    out_summary = Path(args.output_summary)
    resolution_csv = Path(args.emit_resolution) if args.emit_resolution else None
    resolution_summary = Path(args.resolution_summary)
    search_roots = [ROOT / p.strip() for p in args.search_roots.split(",") if p.strip()]

    rows = load_review_required(in_csv)
    files = iter_scan_files(search_roots)
    matrix = build_matrix(rows, files)
    write_csv(out_csv, matrix)
    write_summary(out_summary, matrix)

    resolution_rows: list[ResolutionRow] = []
    if resolution_csv is not None:
        resolution_rows = [classify_resolution(row) for row in matrix]
        write_resolution_csv(resolution_csv, resolution_rows)
        write_resolution_summary(resolution_summary, resolution_rows)

    print(
        {
            "input": str(in_csv),
            "output_csv": str(out_csv),
            "output_summary": str(out_summary),
            "output_resolution": str(resolution_csv) if resolution_csv is not None else "",
            "output_resolution_summary": str(resolution_summary) if resolution_csv is not None else "",
            "rows": len(matrix),
            "hits_found": sum(1 for r in matrix if r.hit_count > 0),
            "no_hits": sum(1 for r in matrix if r.hit_count == 0),
            "keep_count": sum(1 for r in resolution_rows if r.proposed_classification == "keep") if resolution_rows else 0,
            "archive_count": sum(1 for r in resolution_rows if r.proposed_classification == "archive") if resolution_rows else 0,
            "review_count": sum(1 for r in resolution_rows if r.proposed_classification == "review_required") if resolution_rows else 0,
        }
    )


if __name__ == "__main__":
    main()
