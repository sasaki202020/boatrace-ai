# Schema: today_races.csv

基本構成は `historical_races.csv` と同じだが、**着順 (finish_position) および目的変数 (win_label) は存在しない**。

| 列名 | 型 | 役割 | 備考 |
| :--- | :--- | :--- | :--- |
| race_id | string | ID | |
| date | string | 特徴量 | |
| venue | string | 特徴量 | |
| lane | int | 特徴量 | |
| ... | ... | ... | (historical_races と共通) |
| exhibition_time | float | 特徴量 | 直前展示タイム |
| odds_trifecta | float | 非特徴量 | 当日リアルタイムオッズ (期待値計算用) |
