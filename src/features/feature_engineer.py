"""特徴量エンジニアリング。

リーク防止の原則:
- 選手の過去成績系の特徴量は必ず groupby(racer_id) → shift(1) を通す。
  「そのレース開始前に判明している情報」のみを使う。
- 当該レースの結果列(finish_position / win / place / show / start_time)は
  特徴量として直接使わない。start_time は実測STのため結果扱いとし、
  過去レースの shift(1) 集計でのみ利用する。
- exhibition_time(展示タイム)はレース前に判明するため直接使用してよい。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# モデルに入力する特徴量列
FEATURE_COLUMNS = [
    # 基本属性
    "lane", "age", "weight", "grade_code",
    # レース環境
    "weather_code", "wind_speed", "water_temp", "wave_height", "race_number",
    # 直前情報(レース前に判明)
    "exhibition_time", "exhibition_rank", "exhibition_diff",
    # 時系列(過去レースのみ・shift(1)済み)
    "win_rate_3", "win_rate_5", "win_rate_10",
    "show_rate_5", "avg_finish_5", "avg_st_5",
    "lane_win_rate", "course_win_rate", "races_count",
    # 相互作用
    "grade_x_lane", "skill_x_inner",
]


class FeatureEngineer:
    """前処理済みデータから特徴量を作成する。"""

    def __init__(self, rolling_windows: list[int] | None = None):
        self.rolling_windows = rolling_windows or [3, 5, 10]

    def create_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """特徴量列を付与した DataFrame を返す。

        入力は race_date 昇順を前提とする(Preprocessor がソート済み)。
        結果列が NaN の行(=未開催の当日予想対象)があっても動作する。
        """
        df = df.copy()
        df = df.sort_values(["race_date", "race_id", "lane"]).reset_index(drop=True)
        df = self._add_time_series_features(df)
        df = self._add_race_relative_features(df)
        df = self._add_interaction_features(df)
        return df

    # ------------------------------------------------------------------
    # 時系列特徴量(すべて shift(1) でリーク防止)
    # ------------------------------------------------------------------
    def _add_time_series_features(self, df: pd.DataFrame) -> pd.DataFrame:
        g = df.groupby("racer_id", sort=False)

        # 過去Nレースの1着率(当該レースを含めないよう shift(1) してから rolling)
        for w in self.rolling_windows:
            df[f"win_rate_{w}"] = (
                g["win"].transform(lambda s, w=w: s.shift(1).rolling(w, min_periods=1).mean())
            )
        df["show_rate_5"] = g["show"].transform(
            lambda s: s.shift(1).rolling(5, min_periods=1).mean()
        )
        df["avg_finish_5"] = g["finish_position"].transform(
            lambda s: s.shift(1).rolling(5, min_periods=1).mean()
        )
        # 実測STの過去平均(当該レースのSTは結果なので使わない)
        df["avg_st_5"] = g["start_time"].transform(
            lambda s: s.shift(1).rolling(5, min_periods=1).mean()
        )
        # 出走数(経験値)
        df["races_count"] = g.cumcount()

        # 同一枠番での過去1着率
        gl = df.groupby(["racer_id", "lane"], sort=False)
        df["lane_win_rate"] = gl["win"].transform(
            lambda s: s.shift(1).expanding(min_periods=1).mean()
        )
        # 同一場での過去1着率
        gc = df.groupby(["racer_id", "course_id"], sort=False)
        df["course_win_rate"] = gc["win"].transform(
            lambda s: s.shift(1).expanding(min_periods=1).mean()
        )

        # 履歴がない選手(初出走)は中立値で補完
        defaults = {
            "show_rate_5": 0.5, "avg_finish_5": 3.5, "avg_st_5": 0.17,
            "lane_win_rate": 1 / 6, "course_win_rate": 1 / 6,
        }
        for w in self.rolling_windows:
            defaults[f"win_rate_{w}"] = 1 / 6
        for col, val in defaults.items():
            df[col] = df[col].fillna(val)
        return df

    # ------------------------------------------------------------------
    # レース内相対特徴量(展示タイムはレース前情報なので直接使用可)
    # ------------------------------------------------------------------
    def _add_race_relative_features(self, df: pd.DataFrame) -> pd.DataFrame:
        gr = df.groupby("race_id", sort=False)
        df["exhibition_rank"] = gr["exhibition_time"].rank(method="min")
        df["exhibition_diff"] = df["exhibition_time"] - gr["exhibition_time"].transform("mean")
        df["exhibition_rank"] = df["exhibition_rank"].fillna(3.5)
        df["exhibition_diff"] = df["exhibition_diff"].fillna(0.0)
        return df

    # ------------------------------------------------------------------
    # 相互作用特徴量
    # ------------------------------------------------------------------
    def _add_interaction_features(self, df: pd.DataFrame) -> pd.DataFrame:
        # 級別が高い × 内枠ほど強い
        df["grade_x_lane"] = df["grade_code"] * (7 - df["lane"])
        # 実力(過去勝率)が高い選手が内枠に入ったときの強さ
        df["skill_x_inner"] = df["win_rate_10"] * (df["lane"] <= 2).astype(int)
        return df

    @staticmethod
    def feature_columns() -> list[str]:
        return list(FEATURE_COLUMNS)
