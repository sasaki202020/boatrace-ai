from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.evaluation.run_day_evaluation_v2 import evaluate_shadow_day_v2
from src.ingest import load_comparison_target_status, load_v2_sources
from src.storage.duckdb import DuckDBStore, duckdb_available


DEFAULT_HISTORICAL_PATH = Path("data/processed/historical_races.csv")
DEFAULT_ODDS_ROOT = Path("data/odds")
DEFAULT_DB_PATH = Path("data/v2/shadow_v2.duckdb")
DEFAULT_OUTPUT_DIR = Path("reports/v2_shadow")
DEFAULT_V2_OUTPUT_DIR = Path("reports/v2")
DEFAULT_RAW_CANDIDATES = Path("data/strategy_outputs/skip_decisions.csv")
DEFAULT_CAL_CANDIDATES = Path("data/strategy_outputs/skip_decisions_with_calibrated_prob.csv")
DEFAULT_COMPARISON_TARGETS = Path("data/v2/comparison_target_days.csv")
DEFAULT_V1_COMPARE_DIR = Path("reports/comparison")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a minimal v2 shadow ingest/eval for one day.")
    parser.add_argument("--date", required=True, help="YYYY-MM-DD or YYYYMMDD")
    parser.add_argument("--historical-path", type=Path, default=DEFAULT_HISTORICAL_PATH)
    parser.add_argument("--odds-root", type=Path, default=DEFAULT_ODDS_ROOT)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--raw-candidates", type=Path, default=DEFAULT_RAW_CANDIDATES)
    parser.add_argument("--cal-candidates", type=Path, default=DEFAULT_CAL_CANDIDATES)
    parser.add_argument("--comparison-targets", type=Path, default=DEFAULT_COMPARISON_TARGETS)
    parser.add_argument("--v1-compare-dir", type=Path, default=DEFAULT_V1_COMPARE_DIR)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def normalize_date_str(value: object) -> str:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) != 8:
        raise ValueError(f"Invalid date: {value}")
    return digits


def _filter_day(df: pd.DataFrame, date_str: str) -> pd.DataFrame:
    if df.empty or "date" not in df.columns:
        return df.iloc[0:0].copy()
    out = df.copy()
    out["date"] = out["date"].astype(str).map(normalize_date_str)
    return out[out["date"] == date_str].copy()


def _load_candidates(path: Path, date_str: str) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    if df.empty or "date" not in df.columns:
        return pd.DataFrame()
    return _filter_day(df, date_str)


def _load_v1_reference(v1_compare_dir: Path, date_str: str) -> tuple[dict, dict, str]:
    compare_path = v1_compare_dir / f"raw_vs_calibrated_{date_str}.json"
    if not compare_path.exists():
        return {}, {}, "missing"
    try:
        payload = json.loads(compare_path.read_text(encoding="utf-8"))
    except Exception:
        return {}, {}, "unreadable"
    return (
        payload.get("raw_summary", {}) or {},
        payload.get("calibrated_summary", {}) or {},
        str(payload.get("judgement", "")),
    )


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_md(path: Path, summary: dict, raw_candidates: pd.DataFrame, cal_candidates: pd.DataFrame) -> None:
    lines: list[str] = []
    lines.append("# v2 Shadow Summary")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    for key in [
        "date",
        "compare_status",
        "target_races",
        "entry_rows",
        "result_rows",
        "result_available_races",
        "odds_rows",
        "odds_covered_races",
        "odds_coverage",
        "compare_possible",
    ]:
        lines.append(f"- {key}: {summary.get(key)}")
    lines.append("")
    lines.append("## Raw / Calibrated")
    lines.append("")
    lines.append(f"- raw_candidates_rows: {summary.get('raw_candidates', {}).get('rows')}")
    lines.append(f"- raw_candidates_avg_prob: {summary.get('raw_candidates', {}).get('avg_prob')}")
    lines.append(f"- calibrated_candidates_rows: {summary.get('calibrated_candidates', {}).get('rows')}")
    lines.append(f"- calibrated_candidates_avg_prob: {summary.get('calibrated_candidates', {}).get('avg_prob')}")
    lines.append(f"- raw_calibrated_diff: {summary.get('raw_calibrated_diff')}")
    lines.append("")
    lines.append("## V1 Reference")
    lines.append("")
    ref = summary.get("v1_reference", {})
    comp = summary.get("v1_compare", {})
    if ref:
        for key, value in ref.items():
            lines.append(f"- raw_reference_{key}: {value}")
    if comp:
        for key, value in comp.items():
            lines.append(f"- calibrated_reference_{key}: {value}")
    if not ref and not comp:
        lines.append("- missing")
    lines.append("")
    lines.append("## Candidates Snapshot")
    lines.append("")
    lines.append(f"- raw_rows: {len(raw_candidates)}")
    lines.append(f"- cal_rows: {len(cal_candidates)}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _candidate_race_count(df: pd.DataFrame) -> int:
    if df.empty or "race_key" not in df.columns:
        return 0
    return int(df["race_key"].astype(str).nunique())


def main() -> int:
    args = parse_args()
    date_str = normalize_date_str(args.date)
    output_dir = args.output_dir if args.output_dir.is_absolute() else REPO_ROOT / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    v2_output_dir = DEFAULT_V2_OUTPUT_DIR if DEFAULT_V2_OUTPUT_DIR.is_absolute() else REPO_ROOT / DEFAULT_V2_OUTPUT_DIR
    v2_output_dir.mkdir(parents=True, exist_ok=True)

    compare_status = load_comparison_target_status(date_str, args.comparison_targets)
    sources, warnings = load_v2_sources(
        historical_path=(args.historical_path if args.historical_path.is_absolute() else REPO_ROOT / args.historical_path),
        odds_root=(args.odds_root if args.odds_root.is_absolute() else REPO_ROOT / args.odds_root),
    )

    day_tables = {name: _filter_day(df, date_str) for name, df in sources.items()}
    raw_candidates = _load_candidates(args.raw_candidates if args.raw_candidates.is_absolute() else REPO_ROOT / args.raw_candidates, date_str)
    cal_candidates = _load_candidates(args.cal_candidates if args.cal_candidates.is_absolute() else REPO_ROOT / args.cal_candidates, date_str)

    v1_raw_summary, v1_cal_summary, v1_judgement = _load_v1_reference(
        args.v1_compare_dir if args.v1_compare_dir.is_absolute() else REPO_ROOT / args.v1_compare_dir,
        date_str,
    )
    v1_compare_path = (args.v1_compare_dir if args.v1_compare_dir.is_absolute() else REPO_ROOT / args.v1_compare_dir) / f"raw_vs_calibrated_{date_str}.json"

    eval_db_path = (args.db_path if args.db_path.is_absolute() else REPO_ROOT / args.db_path)
    if not args.dry_run and duckdb_available():
        store = DuckDBStore(db_path=eval_db_path)
        conn = store.connect()
        try:
            store.initialize_schema(conn)
            for table_name, df in day_tables.items():
                store.replace_table(conn, table_name, df)
            db_counts = store.table_counts(conn)
        finally:
            conn.close()
    elif not args.dry_run:
        db_counts = {}
    else:
        db_counts = {}

    shadow_summary, shadow_diff, db_warnings = evaluate_shadow_day_v2(
        date_str=date_str,
        compare_status=compare_status,
        db_path=eval_db_path if not args.dry_run else REPO_ROOT / "data/v2/__shadow_dry_run__.duckdb",
        fallback_tables=day_tables,
        raw_candidates=raw_candidates,
        calibrated_candidates=cal_candidates,
        v1_compare_path=v1_compare_path,
    )
    shadow_summary["duckdb_table_counts"] = db_counts
    shadow_summary["warnings"] = warnings + db_warnings
    shadow_summary["v1_judgement"] = v1_judgement
    shadow_summary["v1_raw_summary"] = v1_raw_summary
    shadow_summary["v1_calibrated_summary"] = v1_cal_summary

    base_name = f"shadow_v2_{date_str}"
    csv_path = output_dir / f"{base_name}.csv"
    json_path = output_dir / f"{base_name}.json"
    md_path = output_dir / f"{base_name}.md"
    v2_summary_path = v2_output_dir / f"shadow_summary_{date_str}.json"
    v2_diff_path = v2_output_dir / f"shadow_diff_{date_str}.json"

    pd.DataFrame([{
        "date": shadow_summary.get("date"),
        "compare_status": shadow_summary.get("compare_status"),
        "target_races": shadow_summary.get("target_races"),
        "results_ready_count": shadow_summary.get("results_ready_count"),
        "odds_coverage": shadow_summary.get("odds_coverage"),
        "compare_possible": shadow_summary.get("compare_possible"),
        "reference_only": shadow_summary.get("reference_only"),
        "v1_compareable": shadow_summary.get("v1_compareable"),
        "raw_candidate_rows": shadow_summary.get("raw_candidates", {}).get("rows"),
        "cal_candidate_rows": shadow_summary.get("calibrated_candidates", {}).get("rows"),
        "raw_candidate_avg_prob": shadow_summary.get("raw_candidates", {}).get("avg_prob"),
        "cal_candidate_avg_prob": shadow_summary.get("calibrated_candidates", {}).get("avg_prob"),
        "candidate_rows_diff": shadow_summary.get("raw_calibrated_diff", {}).get("candidate_rows_diff"),
        "avg_prob_diff": shadow_summary.get("raw_calibrated_diff", {}).get("avg_prob_diff"),
        "v1_judgement": shadow_summary.get("v1_judgement"),
        "failure_reasons": ",".join(shadow_summary.get("failure_reasons", [])),
    }]).to_csv(csv_path, index=False, encoding="utf-8")
    _write_json(json_path, shadow_summary)
    _write_json(v2_summary_path, shadow_summary)
    _write_json(v2_diff_path, shadow_diff)
    _write_md(md_path, shadow_summary, raw_candidates, cal_candidates)

    print("=== v2 Shadow Summary ===")
    for key in [
        "date",
        "compare_status",
        "target_races",
        "results_ready_count",
        "odds_coverage",
        "compare_possible",
        "v1_compareable",
    ]:
        print(f"{key}: {shadow_summary.get(key)}")
    print("v1_race_count:", shadow_diff.get("v1_race_count"))
    print("v2_race_count:", shadow_diff.get("v2_race_count"))
    print("raw_buy:", shadow_diff.get("raw_buy"))
    print("cal_buy:", shadow_diff.get("cal_buy"))
    print("raw_hit:", shadow_diff.get("raw_hit"))
    print("cal_hit:", shadow_diff.get("cal_hit"))
    print("raw_roi:", shadow_diff.get("raw_roi"))
    print("cal_roi:", shadow_diff.get("cal_roi"))
    print("\nRaw/Calibrated diff:")
    print(shadow_summary.get("raw_calibrated_diff"))
    print("\nFailure reasons:")
    print(shadow_summary.get("failure_reasons"))
    print("\nV1 judgement:")
    print(shadow_summary.get("v1_judgement"))
    print("\nSaved:")
    print(f"- {csv_path}")
    print(f"- {md_path}")
    print(f"- {json_path}")
    print(f"- {v2_summary_path}")
    print(f"- {v2_diff_path}")
    if shadow_summary.get("duckdb_warning"):
        print(f"- {shadow_summary['duckdb_warning']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
