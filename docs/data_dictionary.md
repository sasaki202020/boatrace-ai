# BoatRace-AI-MVP Data Dictionary

## 1. 事前データ (事前列)
レース開始前に公式番組表、期別成績、レース場データ等から取得可能な項目。

| カラム名 | 型 | 説明 | 出典・補足 |
| :--- | :--- | :--- | :--- |
| race_id | string | 日付+場+R | ID |
| date | string | YYYY-MM-DD | |
| venue | string | レース場名 | 全国24場 |
| race_no | int | レース番号 | 1-12 |
| lane | int | 枠番 | 1-6 |
| racer_id | int | 選手登録番号 | |
| racer_class | string | 級別 | A1, A2, B1, B2 |
| avg_st | float | 平均スタートタイミング | 期別成績 |
| national_win_rate | float | 全国勝率 | 期別成績 |
| national_2ren_rate | float | 全国2連率 | 期別成績 |
| local_2ren_rate | float | 当地2連率 | レース場データ |
| motor_no | int | モーター番号 | |
| motor_2ren_rate | float | モーター2連率 | 当該節・前検データ |
| boat_no | int | ボート番号 | |
| boat_2ren_rate | float | ボート2連率 | |
| venue_lane1-6_win | float | 当該場のコース別入着率 | 全国24場データ |
| season | string | 季節 | 春, 夏, 秋, 冬 |
| day_number | int | 節間何日目か | 1-7 |

## 2. 直前データ (直前列)
展示航走後、投票締切直前に取得可能な項目。

| カラム名 | 型 | 説明 | 出典・補足 |
| :--- | :--- | :--- | :--- |
| exhibition_time | float | 展示タイム | 直前情報 |
| body_weight | float | 選手体重 | 直前情報 |
| tilt | float | チルト角度 | 直前情報 |
| parts_change_flag | int | 部品交換有無 (0/1) | 直前情報 |
| propeller_new_flag | int | 新プロペラ有無 (0/1) | 直前情報 |
| prev_race_course | int | 前走進入コース | 直前情報 |
| prev_race_st | float | 前走ST | 直前情報 |
| prev_race_finish | int | 前走着順 | 直前情報 |
| start_display_st | float | スタート展示ST | 直前情報 |
| wind_speed | int | 風速 (m) | 水面気象情報 |
| weather | string | 天候 | 水面気象情報 |
| water_temp | float | 水温 | 水面気象情報 |
| wave_height | int | 波高 (cm) | 水面気象情報 |

## 3. 目的変数 (Target)
| カラム名 | 型 | 説明 | 備考 |
| :--- | :--- | :--- | :--- |
| finish_position | int | 着順 (1-6) | |
| win_label | int | 1着（1）/ その他（0） | 二値分類用 |

## 4. オッズ・期待値関連
| カラム名 | 型 | 説明 | 備考 |
| :--- | :--- | :--- | :--- |
| odds_win | float | 単勝オッズ | |
| odds_exacta | float | 2連単オッズ | |
| odds_trifecta | float | 3連単オッズ | |
