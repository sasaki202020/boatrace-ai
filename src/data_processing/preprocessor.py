"""前処理モジュール: クリーニング・欠損値補完・カテゴリエンコード。"""
from __future__ import annotations

import pandas as pd

GRADE_ORDER = {"B2": 0, "B1": 1, "A2": 2, "A1": 3}
WEATHER_CODE = {"晴": 0, "曇": 1, "雨": 2, "雪": 3, "霧": 4}

NUMERIC_COLUMNS = [
    "wind_speed", "water_temp", "wave_height",
    "age", "weight", "exhibition_time", "start_time", "win_odds",
]


class Preprocessor:
    """生データをモデル入力可能な形に整える。

    fit 時に学習データの中央値を記録し、transform で欠損補完に使う
    (テストデータの統計を使わないことでリークを防ぐ)。
    """

    def __init__(self):
        self._medians: dict[str, float] = {}

    def fit(self, df: pd.DataFrame) -> "Preprocessor":
        cleaned = self._clean(df)
        for col in NUMERIC_COLUMNS:
            if col in cleaned.columns:
                self._medians[col] = float(cleaned[col].median())
        return self

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        df = self._clean(df)
        df = self._fill_missing(df)
        df = self._encode(df)
        return df

    def fit_transform(self, df: pd.DataFrame) -> pd.DataFrame:
        return self.fit(df).transform(df)

    def _clean(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df = df.drop_duplicates(subset=["race_id", "lane"], keep="first")
        for col in NUMERIC_COLUMNS:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        if "race_date" in df.columns:
            df["race_date"] = pd.to_datetime(df["race_date"]).dt.strftime("%Y-%m-%d")
        # レース内で艇が6艇に満たない行はそのまま残す(実データでは欠場がありうる)
        df = df.sort_values(["race_date", "race_id", "lane"]).reset_index(drop=True)
        return df

    def _fill_missing(self, df: pd.DataFrame) -> pd.DataFrame:
        for col in NUMERIC_COLUMNS:
            if col in df.columns:
                fill = self._medians.get(col, float(df[col].median()))
                df[col] = df[col].fillna(fill)
        if "weather" in df.columns:
            df["weather"] = df["weather"].fillna("晴")
        if "grade" in df.columns:
            df["grade"] = df["grade"].fillna("B1")
        return df

    def _encode(self, df: pd.DataFrame) -> pd.DataFrame:
        if "grade" in df.columns:
            df["grade_code"] = df["grade"].map(GRADE_ORDER).fillna(1).astype(int)
        if "weather" in df.columns:
            df["weather_code"] = df["weather"].map(WEATHER_CODE).fillna(0).astype(int)
        return df
