import argparse
import json
from pathlib import Path


INPUT_JSON = Path("reports/race_filter_rolling_snapshot/race_filter_comparison.json")
INPUT_MANIFEST = Path("reports/race_filter_rolling_snapshot/manifest.json")
OUTPUT_MD = Path("reports/t016_race_filter_stability.md")
OUTPUT_JSON = Path("reports/t016_race_filter_stability.json")


def fmt_num(value, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_report(comp: dict, manifest: dict) -> dict:
    methods = comp.get("methods", {})
    primary = "concentration_filter"
    secondary = "first_gap_filter"
    selected_dates = manifest.get("selected_dates", [])
    days = manifest.get("days", [])
    return {
        "task": "TASK-016",
        "status": "shadow_candidate_reviewed_across_dates",
        "source": str(INPUT_JSON),
        "selected_dates": selected_dates,
        "day_count": len(selected_dates),
        "days": days,
        "combined_rows": manifest.get("combined_rows", {}),
        "primary_candidate": primary,
        "secondary_candidate": secondary,
        "production_adoption": False,
        "summary": {
            "no_filter_roi": methods.get("no_filter", {}).get("roi"),
            "no_filter_max_drawdown": methods.get("no_filter", {}).get("max_drawdown"),
            "primary_roi": methods.get(primary, {}).get("roi"),
            "primary_max_drawdown": methods.get(primary, {}).get("max_drawdown"),
            "secondary_roi": methods.get(secondary, {}).get("roi"),
            "secondary_max_drawdown": methods.get(secondary, {}).get("max_drawdown"),
        },
        "reason": (
            "The rolling snapshot over 2026-04-03 and 2026-04-20 does not validate a production BUY rule. "
            "All requested filters remain worse than no_filter on roi in this small cross-date sample, "
            "so the earlier single-snapshot candidates stay shadow-only."
        ),
        "risks": [
            "small cross-date sample",
            "missing 2026-04-22 and 2026-04-23 results",
            "single-date candidate ranking is not sufficient for production",
        ],
        "next_action": (
            "Collect more dated snapshots and rerun the same comparison before any BUY-rule adoption."
        ),
    }


def build_markdown(report: dict) -> str:
    summary = report["summary"]
    days = report.get("days", [])
    day_lines = "\n".join(
        f"- {d.get('date')}: features_rows={d.get('features_rows')}, proba_rows={d.get('proba_rows')}, result_rows={d.get('result_rows')}, source={d.get('result_source')}"
        for d in days
    )
    md = f"""# TASK-016 Stability Note

## Purpose
Confirm whether the race filter candidate ranking from TASK-016 holds across more than one date.

## Source
- `reports/race_filter_rolling_snapshot/race_filter_comparison.json`
- `reports/race_filter_rolling_snapshot/manifest.json`

## Snapshot Coverage
{day_lines}

## Cross-date Summary
- `day_count`: {report.get("day_count", 0)}
- `combined_rows.features`: {report.get("combined_rows", {}).get("features", "n/a")}
- `combined_rows.proba`: {report.get("combined_rows", {}).get("proba", "n/a")}
- `combined_rows.backtest`: {report.get("combined_rows", {}).get("backtest", "n/a")}
- `combined_rows.actual_results`: {report.get("combined_rows", {}).get("actual_results", "n/a")}

## Key Numbers
| item | value |
|---|---:|
| no_filter roi | {fmt_num(summary.get("no_filter_roi"))} |
| no_filter max_drawdown | {fmt_num(summary.get("no_filter_max_drawdown"))} |
| concentration_filter roi | {fmt_num(summary.get("primary_roi"))} |
| concentration_filter max_drawdown | {fmt_num(summary.get("primary_max_drawdown"))} |
| first_gap_filter roi | {fmt_num(summary.get("secondary_roi"))} |
| first_gap_filter max_drawdown | {fmt_num(summary.get("secondary_max_drawdown"))} |

## Interpretation
- The rolling snapshot is not a production validation.
- `concentration_filter` remains the first shadow candidate from the single-snapshot review.
- `first_gap_filter` remains the second shadow candidate.
- However, the cross-date sample is too small and does not beat `no_filter`, so nothing is promoted.

## Decision
- `production_adoption`: `false`
- `status`: `shadow_candidate_reviewed_across_dates`

## Risks
- small cross-date sample
- missing 2026-04-22 and 2026-04-23 results
- single-date candidate ranking is not sufficient for production

## Next Action
Collect more dated snapshots and rerun the same comparison before any BUY-rule adoption.
"""
    return md


def main() -> None:
    parser = argparse.ArgumentParser(description="Write TASK-016 race filter stability note.")
    parser.add_argument("--input", default=str(INPUT_JSON))
    parser.add_argument("--manifest", default=str(INPUT_MANIFEST))
    parser.add_argument("--output-md", default=str(OUTPUT_MD))
    parser.add_argument("--output-json", default=str(OUTPUT_JSON))
    args = parser.parse_args()

    input_path = Path(args.input)
    manifest_path = Path(args.manifest)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest file not found: {manifest_path}")

    comp = load_json(input_path)
    manifest = load_json(manifest_path)
    report = build_report(comp, manifest)
    md_text = build_markdown(report)

    output_md = Path(args.output_md)
    output_json = Path(args.output_json)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(md_text, encoding="utf-8")
    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[saved] {output_md}")
    print(f"[saved] {output_json}")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
