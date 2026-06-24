"""システム単体テスト。

実行方法:
    python test_system.py        # スタンドアロン実行
    pytest test_system.py        # pytest 経由
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from src.data_processing.dummy_generator import DummyDataGenerator, SCHEMA_COLUMNS
from src.data_processing.preprocessor import Preprocessor
from src.evaluation.backtester import Backtester
from src.features.feature_engineer import FeatureEngineer, FEATURE_COLUMNS
from src.models.prediction_system import PredictionSystem, time_series_split


def _make_dataset(n_races=120, seed=0):
    df = DummyDataGenerator(n_races=n_races, n_racers=30, seed=seed).generate()
    df = Preprocessor().fit_transform(df)
    return FeatureEngineer().create_features(df)


# ----------------------------------------------------------------------
def test_dummy_generator_schema():
    df = DummyDataGenerator(n_races=10, n_racers=20, seed=1).generate()
    assert list(df.columns) == SCHEMA_COLUMNS
    assert len(df) == 60  # 10レース × 6艇
    assert df.groupby("race_id")["lane"].apply(
        lambda s: sorted(s.tolist()) == [1, 2, 3, 4, 5, 6]).all()
    # 各レースに1着がちょうど1艇
    assert (df.groupby("race_id")["win"].sum() == 1).all()
    assert df["win_odds"].between(1.0, 99.9).all()
    assert df["grade"].isin(["A1", "A2", "B1", "B2"]).all()


def test_preprocessor():
    df = DummyDataGenerator(n_races=10, n_racers=20, seed=2).generate()
    df.loc[0, "wind_speed"] = None
    df.loc[1, "grade"] = None
    out = Preprocessor().fit_transform(df)
    assert out["wind_speed"].notna().all()
    assert "grade_code" in out.columns and out["grade_code"].between(0, 3).all()
    assert "weather_code" in out.columns


def test_feature_engineer_no_leak():
    """時系列特徴量が当該レースの結果を含まないこと(shift(1)の検証)。"""
    # 1人の選手が5レース連続で出走し、3戦目だけ勝つ系列を作る
    rows = []
    for i, win in enumerate([0, 0, 1, 0, 0]):
        rows.append({
            "race_id": f"r{i}", "race_date": f"2024-01-0{i+1}",
            "course_id": 1, "race_number": 1,
            "weather": "晴", "wind_speed": 1.0, "water_temp": 15.0, "wave_height": 1.0,
            "racer_id": 100, "name": "x", "age": 30, "weight": 52.0,
            "grade": "A1", "lane": 1,
            "exhibition_time": 6.70, "start_time": 0.15,
            "finish_position": 1 if win else 2, "win": win,
            "place": 1, "show": 1, "win_odds": 2.0,
        })
    df = pd.DataFrame(rows)
    df = Preprocessor().fit_transform(df)
    out = FeatureEngineer().create_features(df).sort_values("race_date")
    wr = out["win_rate_3"].tolist()
    # 3戦目(勝利レース)の特徴量は過去2戦のみ → 0.0 でなければリーク
    assert wr[2] == 0.0
    # 4戦目には3戦目の勝利が反映される
    assert wr[3] > 0.0
    # 初出走は中立値 1/6
    assert abs(wr[0] - 1 / 6) < 1e-9


def test_feature_columns_exist():
    df = _make_dataset(n_races=30)
    for col in FEATURE_COLUMNS:
        assert col in df.columns, f"特徴量が欠落: {col}"
        assert df[col].notna().all(), f"特徴量にNaN: {col}"


def test_time_series_split():
    df = _make_dataset(n_races=50)
    train, test = time_series_split(df, test_ratio=0.2)
    # レースが train/test に跨がらない
    assert set(train["race_id"]) & set(test["race_id"]) == set()
    # テストは必ず学習より未来
    assert train["race_date"].max() <= test["race_date"].min()


def test_prediction_system():
    df = _make_dataset(n_races=150)
    train, test = time_series_split(df, test_ratio=0.2)
    cfg = {"xgboost": {"n_estimators": 30}, "lightgbm": {"n_estimators": 30},
           "random_forest": {"n_estimators": 30}}
    system = PredictionSystem(cfg).fit(train)
    metrics = system.evaluate(test)
    assert 0.0 <= metrics["hit_rate"] <= 1.0
    # 学習可能なシグナルがあるのでランダムは上回るはず
    assert metrics["hit_rate"] > 1 / 6
    pred = system.predict_races(test)
    # レース内確率は合計1に正規化
    sums = pred.groupby("race_id")["pred_proba"].sum()
    assert np.allclose(sums, 1.0)
    # 各レースで予想1位はちょうど1艇
    assert (pred.groupby("race_id")["pred_rank"]
            .apply(lambda s: (s == 1).sum()) == 1).all()


def test_model_save_load(tmp_path=None):
    import tempfile
    df = _make_dataset(n_races=60)
    train, test = time_series_split(df)
    cfg = {"xgboost": {"n_estimators": 10}, "lightgbm": {"n_estimators": 10},
           "random_forest": {"n_estimators": 10}}
    system = PredictionSystem(cfg).fit(train)
    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "model.pkl")
        system.save(path)
        loaded = PredictionSystem.load(path)
        p1 = system.predict_proba(test)
        p2 = loaded.predict_proba(test)
        assert np.allclose(p1, p2)


def test_backtester():
    pred = pd.DataFrame({
        "race_id": ["a", "a", "b", "b"],
        "pred_rank": [1, 2, 1, 2],
        "pred_proba": [0.6, 0.4, 0.3, 0.7],
        "win": [1, 0, 0, 1],
        "win_odds": [2.0, 3.0, 5.0, 1.5],
    })
    bt = Backtester(unit_stake=100, ev_threshold=1.2, confidence_threshold=0.5)
    res = bt.run(pred)
    # 本命戦略: 2レースで賭け、1的中、払戻 2.0*100=200 / 投資200 → ROI 100%
    assert res["honmei"]["n_bets"] == 2
    assert res["honmei"]["n_hits"] == 1
    assert abs(res["honmei"]["roi"] - 1.0) < 1e-9
    # 確信度戦略(>0.5): レースaのみ賭け → 的中 ROI 200%
    assert res["confidence"]["n_bets"] == 1
    assert abs(res["confidence"]["roi"] - 2.0) < 1e-9
    # 期待値戦略(p*odds>1.2): a=1.2(除外) b=1.5(賭け・外れ)
    assert res["expected_value"]["n_bets"] == 1
    assert res["expected_value"]["roi"] == 0.0


# ----------------------------------------------------------------------
if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
