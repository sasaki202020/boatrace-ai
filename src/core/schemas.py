from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TableSpec:
    name: str
    ddl: str


V2_TABLE_SPECS: tuple[TableSpec, ...] = (
    TableSpec(
        name="races",
        ddl="""
        CREATE TABLE IF NOT EXISTS races (
            race_id TEXT,
            race_key TEXT,
            race_date DATE,
            date TEXT,
            jcd INTEGER,
            venue TEXT,
            race_no INTEGER,
            source_kind TEXT,
            source_path TEXT,
            source_row_count INTEGER,
            created_at TIMESTAMP
        )
        """,
    ),
    TableSpec(
        name="entries",
        ddl="""
        CREATE TABLE IF NOT EXISTS entries (
            entry_id TEXT,
            race_id TEXT,
            race_key TEXT,
            race_date DATE,
            date TEXT,
            jcd INTEGER,
            venue TEXT,
            race_no INTEGER,
            lane INTEGER,
            racer_id INTEGER,
            racer_class TEXT,
            st DOUBLE,
            exhibition_time DOUBLE,
            national_win_rate DOUBLE,
            national_2ren_rate DOUBLE,
            local_win_rate DOUBLE,
            local_2ren_rate DOUBLE,
            motor_2ren_rate DOUBLE,
            boat_2ren_rate DOUBLE,
            source_path TEXT,
            created_at TIMESTAMP
        )
        """,
    ),
    TableSpec(
        name="results",
        ddl="""
        CREATE TABLE IF NOT EXISTS results (
            race_id TEXT,
            race_key TEXT,
            race_date DATE,
            date TEXT,
            jcd INTEGER,
            venue TEXT,
            race_no INTEGER,
            winning_ticket_id TEXT,
            status TEXT,
            raw_rows INTEGER,
            source_path TEXT,
            created_at TIMESTAMP
        )
        """,
    ),
    TableSpec(
        name="odds_snapshots",
        ddl="""
        CREATE TABLE IF NOT EXISTS odds_snapshots (
            race_id TEXT,
            race_key TEXT,
            race_date DATE,
            date TEXT,
            jcd INTEGER,
            stadium TEXT,
            race_no INTEGER,
            ticket_id TEXT,
            snapshot_ts TIMESTAMP,
            odds DOUBLE,
            odds_status TEXT,
            odds_fetch_status TEXT,
            odds_fetch_used_cache BOOLEAN,
            odds_missing_odds_cells INTEGER,
            odds_source TEXT,
            source_url TEXT,
            source_path TEXT,
            created_at TIMESTAMP
        )
        """,
    ),
)

V2_TABLE_NAMES: tuple[str, ...] = tuple(spec.name for spec in V2_TABLE_SPECS)


def v2_schema_sql() -> list[str]:
    return [spec.ddl for spec in V2_TABLE_SPECS]
