from __future__ import annotations

import argparse
import ast
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src"
OUT_ROOT = ROOT / "reports" / "monitoring"
OUT_JSON = OUT_ROOT / "model_policy_separation_audit.json"
OUT_CSV = OUT_ROOT / "model_policy_separation_audit.csv"
OUT_MD = OUT_ROOT / "model_policy_separation_audit.md"

MODEL_OUTPUT_FIELDS = [
    "candidateId",
    "raceId",
    "modelVersion",
    "featureVersion",
    "rawProbability",
    "predictionHash",
]
CALIBRATION_OUTPUT_FIELDS = [
    "candidateId",
    "calibratorVersion",
    "rawProbability",
    "calibratedProbability",
]
MARKET_OUTPUT_FIELDS = [
    "candidateId",
    "odds",
    "oddsCapturedAt",
    "deadlineAt",
    "marketProbability",
]
POLICY_OUTPUT_FIELDS = [
    "candidateId",
    "policyVersion",
    "policyDecision",
    "guardDecision",
    "guardReason",
    "estimatedEdge",
]


def _python_files(path: Path) -> list[Path]:
    if not path.exists():
        return []
    return sorted(file for file in path.rglob("*.py") if "__pycache__" not in file.parts)


def _imports_and_functions(path: Path) -> tuple[list[str], set[str], str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return [], set(), text
    imports: list[str] = []
    functions: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.add(node.name)
    return imports, functions, text


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root.parent).as_posix()
    except ValueError:
        return path.as_posix()


def build_model_policy_separation_audit(*, source_root: Path = SOURCE_ROOT) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    reverse_dependency_count = 0
    legacy_coupling_count = 0

    for path in _python_files(source_root / "models"):
        imports, _, _ = _imports_and_functions(path)
        for imported in imports:
            if "strategy.evaluate_ev_and_skip" in imported or imported.endswith("strategy.policy"):
                reverse_dependency_count += 1
                checks.append(
                    {
                        "layer": "model",
                        "checkCode": "model_imports_policy",
                        "status": "blocked",
                        "path": _relative(path, source_root),
                        "detail": imported,
                    }
                )

    policy_path = source_root / "strategy" / "evaluate_ev_and_skip.py"
    if policy_path.exists():
        imports, functions, _ = _imports_and_functions(policy_path)
        calibration_imports = sorted({name for name in imports if "calibrat" in name.lower()})
        if calibration_imports:
            legacy_coupling_count += 1
            checks.append(
                {
                    "layer": "policy",
                    "checkCode": "calibration_import_inside_policy",
                    "status": "warning",
                    "path": _relative(policy_path, source_root),
                    "detail": ", ".join(calibration_imports),
                }
            )
        calibration_methods = sorted(
            name for name in functions if name in {"_load_probability_calibrator", "_calibrate_probability_series"}
        )
        if calibration_methods:
            legacy_coupling_count += 1
            checks.append(
                {
                    "layer": "policy",
                    "checkCode": "calibration_method_inside_policy",
                    "status": "warning",
                    "path": _relative(policy_path, source_root),
                    "detail": ", ".join(calibration_methods),
                }
            )
    else:
        checks.append(
            {
                "layer": "policy",
                "checkCode": "policy_source_missing",
                "status": "blocked",
                "path": _relative(policy_path, source_root),
                "detail": "policy source not found",
            }
        )

    checks.sort(key=lambda row: ({"blocked": 0, "warning": 1, "pass": 2}.get(str(row["status"]), 9), str(row["checkCode"])))
    if reverse_dependency_count or any(row["status"] == "blocked" for row in checks):
        classification = "separation_blocked"
    elif legacy_coupling_count:
        classification = "separation_warning"
    else:
        classification = "separation_ready"

    return {
        "reportType": "model_policy_separation_audit",
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "sourceRoot": str(source_root),
        "productionBehaviorChanged": False,
        "contracts": {
            "modelOutputFields": MODEL_OUTPUT_FIELDS,
            "calibrationOutputFields": CALIBRATION_OUTPUT_FIELDS,
            "marketOutputFields": MARKET_OUTPUT_FIELDS,
            "policyOutputFields": POLICY_OUTPUT_FIELDS,
        },
        "counts": {
            "reverseDependencyViolationCount": reverse_dependency_count,
            "legacyCouplingCount": legacy_coupling_count,
            "checkCount": len(checks),
        },
        "quality": {
            "classification": classification,
            "productionPathUnchanged": True,
            "notes": "legacy coupling is reported; prediction and policy behavior are not modified",
        },
        "checks": checks,
    }


def _write_outputs(payload: dict[str, Any]) -> None:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with OUT_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["layer", "checkCode", "status", "path", "detail"])
        writer.writeheader()
        writer.writerows(payload["checks"])
    lines = [
        "# Model / Policy Separation Audit",
        "",
        f"- classification: {payload['quality']['classification']}",
        f"- reverseDependencyViolationCount: {payload['counts']['reverseDependencyViolationCount']}",
        f"- legacyCouplingCount: {payload['counts']['legacyCouplingCount']}",
        f"- productionBehaviorChanged: {payload['productionBehaviorChanged']}",
        "",
        "## Contract boundaries",
    ]
    for name, fields in payload["contracts"].items():
        lines.append(f"- {name}: {', '.join(fields)}")
    lines.extend(["", "## Checks"])
    if payload["checks"]:
        for row in payload["checks"]:
            lines.append(f"- [{row['status']}] {row['checkCode']}: {row['path']} ({row['detail']})")
    else:
        lines.append("- no coupling detected")
    lines.extend(
        [
            "",
            "## Safety",
            "- This is a static sidecar audit.",
            "- BUY / EV / calibration / hard guard behavior is unchanged.",
            "- frozen_bets and settlement are not written.",
        ]
    )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit model/calibration/market/policy boundaries")
    parser.add_argument("--source-root", type=Path, default=SOURCE_ROOT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    payload = build_model_policy_separation_audit(source_root=args.source_root)
    if not args.dry_run:
        _write_outputs(payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
