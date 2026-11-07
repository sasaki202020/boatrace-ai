# Schema: historical_races.csv

| 列名 | 型 | 役割 | 備考 |
| :--- | :--- | :--- | :--- |
| race_id | string | ID | 日付+場+R |
| date | string | 特徴量 | YYYY-MM-DD |
| venue | string | 特徴量 | 場名 |
| lane | int | 特徴量 | 枠番 (1-6) |
| racer_id | int | 特徴量 | 選手ID |
| racer_class | string | 特徴量 | A1/A2/B1/B2 |
| avg_st | float | 特徴量 | 平均ST |
| national_win_rate | float | 特徴量 | 全国勝率 |
| motor_2ren_rate | float | 特徴量 | モーター成績 |
| boat_2ren_rate | float | 特徴量 | ボート成績 |
| exhibition_time | float | 特徴量 | 展示タイム (直前) |
| wind_speed | int | 特徴量 | 風速 |
| finish_position | int | 非特徴量 | 着順 |
| win_label | int | 目的変数 | 1着なら1 |
| odds_trifecta | float | 非特徴量 | 3連単オッズ (期待値計算用) |
