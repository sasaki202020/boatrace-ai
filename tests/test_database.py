"""database.py のテスト。"""
from __future__ import annotations

import pandas as pd
import pytest

from src.data_processing.database import BoatRaceDatabase
from src.data_processing.dummy_generator import DummyDataGenerator, SCHEMA_COLUMNS


@pytest.fixture
def db(tmp_path):
    return BoatRaceDatabase(str(tmp_path / "test.db"))


@pytest.fixture
def sample_df():
    return DummyDataGenerator(n_races=12, n_racers=20, seed=7).generate()


def test_save_and_load_roundtrip(db, sample_df):
    n = db.save_dataframe(sample_df)
    assert n == len(sample_df)
    assert db.count_races() == 12
    loaded = db.load_dataframe()
    assert list(loaded.columns) == SCHEMA_COLUMNS
    assert len(loaded) == len(sample_df)
    a = sample_df.sort_values(["race_id", "lane"]).reset_index(drop=True)
    b = loaded.sort_values(["race_id", "lane"]).reset_index(drop=True)
    pd.testing.assert_frame_equal(
        a[["race_id", "lane", "racer_id", "win", "win_odds"]],
        b[["race_id", "lane", "racer_id", "win", "win_odds"]],
        check_dtype=False,
    )


def test_upsert_no_duplicates(db, sample_df):
    db.save_dataframe(sample_df)
    db.save_dataframe(sample_df)  # 再保存しても重複しない
    assert db.count_races() == 12
    assert len(db.load_dataframe()) == len(sample_df)


def test_date_filter(db, sample_df):
    db.save_dataframe(sample_df)
    dates = sorted(sample_df["race_date"].unique())
    first = db.load_dataframe(end_date=dates[0])
    assert set(first["race_date"]) == {dates[0]}


def test_pending_races_excluded_with_results_only(db, sample_df):
    pending = sample_df.copy()
    for col in ("finish_position", "win", "place", "show", "win_odds"):
        pending[col] = None
    pending["race_id"] = "9" + pending["race_id"].str[1:]
    db.save_dataframe(sample_df)
    db.save_dataframe(pending)
    full = db.load_dataframe()
    with_results = db.load_dataframe(with_results_only=True)
    assert len(full) == len(sample_df) * 2
    assert len(with_results) == len(sample_df)


def test_empty_load(db):
    df = db.load_dataframe()
    assert df.empty
    assert list(df.columns) == SCHEMA_COLUMNS
