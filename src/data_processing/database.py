"""SQLite データベース層。

races / entries / results / odds の4テーブルに正規化して保存し、
パイプラインにはフラットな DataFrame(dummy_generator と同一スキーマ)で返す。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from src.data_processing.dummy_generator import SCHEMA_COLUMNS

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS races (
    race_id     TEXT PRIMARY KEY,
    race_date   TEXT NOT NULL,
    course_id   INTEGER NOT NULL,
    race_number INTEGER NOT NULL,
    weather     TEXT,
    wind_speed  REAL,
    water_temp  REAL,
    wave_height REAL
);
CREATE TABLE IF NOT EXISTS entries (
    race_id         TEXT NOT NULL,
    lane            INTEGER NOT NULL,
    racer_id        INTEGER,
    name            TEXT,
    age             INTEGER,
    weight          REAL,
    grade           TEXT,
    exhibition_time REAL,
    start_time      REAL,
    PRIMARY KEY (race_id, lane),
    FOREIGN KEY (race_id) REFERENCES races(race_id)
);
CREATE TABLE IF NOT EXISTS results (
    race_id         TEXT NOT NULL,
    lane            INTEGER NOT NULL,
    finish_position INTEGER,
    win             INTEGER,
    place           INTEGER,
    show            INTEGER,
    PRIMARY KEY (race_id, lane),
    FOREIGN KEY (race_id) REFERENCES races(race_id)
);
CREATE TABLE IF NOT EXISTS odds (
    race_id  TEXT NOT NULL,
    lane     INTEGER NOT NULL,
    win_odds REAL,
    PRIMARY KEY (race_id, lane),
    FOREIGN KEY (race_id) REFERENCES races(race_id)
);
CREATE INDEX IF NOT EXISTS idx_races_date ON races(race_date);
"""


class BoatRaceDatabase:
    def __init__(self, db_path: str = "data/boat_race.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA_SQL)

    # ------------------------------------------------------------------
    def save_dataframe(self, df: pd.DataFrame) -> int:
        """フラットな DataFrame(SCHEMA_COLUMNS 形式)を4テーブルへ保存する。

        既存の race_id + lane は上書き(UPSERT)。保存した行数を返す。
        """
        if df.empty:
            return 0
        with self._connect() as conn:
            races = df.drop_duplicates("race_id")
            conn.executemany(
                "INSERT OR REPLACE INTO races VALUES (?,?,?,?,?,?,?,?)",
                races[["race_id", "race_date", "course_id", "race_number",
                       "weather", "wind_speed", "water_temp", "wave_height"]]
                .itertuples(index=False, name=None),
            )
            conn.executemany(
                "INSERT OR REPLACE INTO entries VALUES (?,?,?,?,?,?,?,?,?)",
                df[["race_id", "lane", "racer_id", "name", "age", "weight",
                    "grade", "exhibition_time", "start_time"]]
                .itertuples(index=False, name=None),
            )
            results = df.dropna(subset=["finish_position"]) \
                if "finish_position" in df.columns else pd.DataFrame()
            if len(results):
                conn.executemany(
                    "INSERT OR REPLACE INTO results VALUES (?,?,?,?,?,?)",
                    results[["race_id", "lane", "finish_position",
                             "win", "place", "show"]]
                    .itertuples(index=False, name=None),
                )
            odds = df.dropna(subset=["win_odds"]) if "win_odds" in df.columns \
                else pd.DataFrame()
            if len(odds):
                conn.executemany(
                    "INSERT OR REPLACE INTO odds VALUES (?,?,?)",
                    odds[["race_id", "lane", "win_odds"]]
                    .itertuples(index=False, name=None),
                )
        return len(df)

    # ------------------------------------------------------------------
    def load_dataframe(self, start_date: str | None = None,
                       end_date: str | None = None,
                       with_results_only: bool = False) -> pd.DataFrame:
        """4テーブルを結合してフラットな DataFrame を返す。"""
        query = """
            SELECT r.race_id, r.race_date, r.course_id, r.race_number,
                   r.weather, r.wind_speed, r.water_temp, r.wave_height,
                   e.racer_id, e.name, e.age, e.weight, e.grade, e.lane,
                   e.exhibition_time, e.start_time,
                   res.finish_position, res.win, res.place, res.show,
                   o.win_odds
            FROM races r
            JOIN entries e ON e.race_id = r.race_id
            LEFT JOIN results res ON res.race_id = e.race_id AND res.lane = e.lane
            LEFT JOIN odds o ON o.race_id = e.race_id AND o.lane = e.lane
        """
        conds, params = [], []
        if start_date:
            conds.append("r.race_date >= ?")
            params.append(start_date)
        if end_date:
            conds.append("r.race_date <= ?")
            params.append(end_date)
        if with_results_only:
            conds.append("res.finish_position IS NOT NULL")
        if conds:
            query += " WHERE " + " AND ".join(conds)
        query += " ORDER BY r.race_date, r.race_id, e.lane"
        with self._connect() as conn:
            df = pd.read_sql_query(query, conn, params=params)
        return df[SCHEMA_COLUMNS] if len(df) else pd.DataFrame(columns=SCHEMA_COLUMNS)

    def count_races(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM races").fetchone()[0]
