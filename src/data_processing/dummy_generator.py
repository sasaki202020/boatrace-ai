"""ダミーデータ生成モジュール。

実データと同一スキーマのレースデータ(オッズ付き)を生成する。
選手ごとの潜在能力・枠番の有利不利・展示タイムを反映した着順を
シミュレートするため、学習可能なシグナルを含む。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

GRADES = ["A1", "A2", "B1", "B2"]
WEATHERS = ["晴", "曇", "雨"]
# 競艇はイン(1号艇)が圧倒的に有利。全国平均の1着率に近い枠番バイアス。
LANE_ADVANTAGE = {1: 1.20, 2: 0.35, 3: 0.25, 4: 0.20, 5: 0.10, 6: 0.05}

# データセットの列順(実データ取得モジュールもこのスキーマに合わせる)
SCHEMA_COLUMNS = [
    "race_id", "race_date", "course_id", "race_number",
    "weather", "wind_speed", "water_temp", "wave_height",
    "racer_id", "name", "age", "weight", "grade", "lane",
    "exhibition_time", "start_time",
    "finish_position", "win", "place", "show",
    "win_odds",
]


class DummyDataGenerator:
    """学習・検証用のダミーレースデータを生成する。"""

    def __init__(self, n_races: int = 2000, n_racers: int = 200, seed: int = 42):
        self.n_races = n_races
        self.n_racers = n_racers
        self.rng = np.random.default_rng(seed)
        self._racers = self._build_racer_pool()

    def _build_racer_pool(self) -> pd.DataFrame:
        """潜在能力つきの選手プールを作る。能力は級別と相関させる。"""
        skill = self.rng.normal(0.0, 1.0, self.n_racers)
        order = np.argsort(-skill)
        grade = np.empty(self.n_racers, dtype=object)
        # 上位から A1:20% / A2:20% / B1:40% / B2:20% に振り分け
        n = self.n_racers
        grade[order[: int(n * 0.2)]] = "A1"
        grade[order[int(n * 0.2): int(n * 0.4)]] = "A2"
        grade[order[int(n * 0.4): int(n * 0.8)]] = "B1"
        grade[order[int(n * 0.8):]] = "B2"
        return pd.DataFrame({
            "racer_id": np.arange(3000, 3000 + self.n_racers),
            "name": [f"選手{i:03d}" for i in range(self.n_racers)],
            "age": self.rng.integers(20, 60, self.n_racers),
            "weight": np.round(self.rng.normal(53.0, 2.5, self.n_racers), 1),
            "grade": grade,
            "skill": skill,
        })

    def generate(self) -> pd.DataFrame:
        """全レースのフラットな DataFrame(1行=1艇)を返す。"""
        rows = []
        start_date = pd.Timestamp("2024-01-01")
        races_per_day = 12
        for i in range(self.n_races):
            race_date = start_date + pd.Timedelta(days=i // races_per_day)
            course_id = int(self.rng.integers(1, 25))
            race_number = i % races_per_day + 1
            race_id = f"{race_date:%Y%m%d}{course_id:02d}{race_number:02d}"
            weather = str(self.rng.choice(WEATHERS, p=[0.5, 0.35, 0.15]))
            wind_speed = float(np.round(self.rng.gamma(2.0, 1.5), 1))
            water_temp = float(np.round(self.rng.normal(18.0, 6.0), 1))
            wave_height = float(np.round(self.rng.gamma(1.5, 2.0), 0))

            entries = self._racers.sample(6, random_state=int(self.rng.integers(0, 2**31)))
            lanes = np.arange(1, 7)

            # 展示タイム: 能力が高いほど速い(小さい)
            exhibition = 6.70 - 0.05 * entries["skill"].to_numpy() \
                + self.rng.normal(0, 0.05, 6)
            # ST: 能力が高いほど速い
            st = 0.16 - 0.015 * entries["skill"].to_numpy() \
                + self.rng.normal(0, 0.03, 6)
            st = np.clip(st, 0.01, 0.40)

            # 着順決定: 能力 + 枠番有利 + 展示の良さ + ノイズ
            strength = (
                entries["skill"].to_numpy()
                + np.array([LANE_ADVANTAGE[l] for l in lanes]) * 2.0
                - (exhibition - 6.70) * 3.0
                + self.rng.normal(0, 1.0, 6)
            )
            finish = np.empty(6, dtype=int)
            finish[np.argsort(-strength)] = np.arange(1, 7)

            # 単勝オッズ: 真の勝率の逆数に控除率とノイズを乗せる
            true_p = np.exp(strength / 1.2)
            true_p = true_p / true_p.sum()
            odds = np.clip(0.75 / true_p * self.rng.normal(1.0, 0.1, 6), 1.0, 99.9)

            for j, (_, r) in enumerate(entries.iterrows()):
                rows.append({
                    "race_id": race_id,
                    "race_date": race_date.strftime("%Y-%m-%d"),
                    "course_id": course_id,
                    "race_number": race_number,
                    "weather": weather,
                    "wind_speed": wind_speed,
                    "water_temp": water_temp,
                    "wave_height": wave_height,
                    "racer_id": int(r["racer_id"]),
                    "name": r["name"],
                    "age": int(r["age"]),
                    "weight": float(r["weight"]),
                    "grade": r["grade"],
                    "lane": int(lanes[j]),
                    "exhibition_time": float(np.round(exhibition[j], 2)),
                    "start_time": float(np.round(st[j], 2)),
                    "finish_position": int(finish[j]),
                    "win": int(finish[j] == 1),
                    "place": int(finish[j] <= 2),
                    "show": int(finish[j] <= 3),
                    "win_odds": float(np.round(odds[j], 1)),
                })
        return pd.DataFrame(rows, columns=SCHEMA_COLUMNS)
