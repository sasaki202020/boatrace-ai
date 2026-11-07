from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.evaluation.v2_audit import build_migration_audit
from src.ingest.v2 import load_v2_sources
from src.storage.duckdb import DuckDBStore, duckdb_available


DEFAULT_HISTORICAL_PATH = Path("data/processed/historical_races.csv")
DEFAULT_ODDS_ROOT = Path("data/odds")
DEFAULT_DB_PATH = Path("data/v2/boatrace_v2.duckdb")
DEFAULT_OUTPUT_DIR = Path("reports/v2")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate historical fact data into v2 DuckDB tables.")
    parser.add_argument("--historical-path", type=Path, default=DEFAULT_HISTORICAL_PATH)
    parser.add_argument("--odds-root", type=Path, default=DEFAULT_ODDS_ROOT)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--dry-run", action="store_true", help="Load and validate only. Do not write DuckDB.")
    return parser.parse_args()


def resolve_path(path: Path) -> Path:
    return path if path.is_absolute() else REPO_ROOT / path


def main() -> int:
    args = parse_args()
    historical_path = resolve_path(args.historical_path)
    odds_root = resolve_path(args.odds_root)
    db_path = resolve_path(args.db_path)
    output_dir = resolve_path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    sources, warnings = load_v2_sources(historical_path=historical_path, odds_root=odds_root)
    audit = build_migration_audit(sources, warnings=warnings)
    audit["source_paths"] = {
        "historical_path": str(historical_path),
        "odds_root": str(odds_root),
        "db_path": str(db_path),
    }

    summary_path = output_dir / "v2_migration_summary.json"
    summary_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.dry_run:
        print("Dry run only. Loaded tables:")
        for name, table in audit["tables"].items():
            print(
                f"- {name}: rows={table.get('rows', 0)} "
                f"duplicates={table.get('duplicate_rows', 0)} "
                f"range={table.get('min_date')}..{table.get('max_date')}"
            )
        if warnings:
            print("\nWarnings:")
            for warning in warnings[:20]:
                print(f"- {warning}")
        print(f"\nSaved summary: {summary_path}")
        return 0

    if not duckdb_available():
        print(
            "duckdb is not installed. Install dependencies first, then rerun without --dry-run.",
            file=sys.stderr,
        )
        return 1

    store = DuckDBStore(db_path=db_path)
    conn = store.connect()
    try:
        store.initialize_schema(conn)
        for table_name, df in sources.items():
            store.replace_table(conn, table_name, df)
        table_counts = store.table_counts(conn)
    finally:
        conn.close()

    audit["duckdb_table_counts"] = table_counts
    summary_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")

    print("Migration completed.")
    for table_name, count in table_counts.items():
        print(f"- {table_name}: {count}")
    print(f"Saved DB: {db_path}")
    print(f"Saved summary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
