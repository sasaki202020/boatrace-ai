# Feature Inventory

Scope: current `train_features.csv` feature matrix used by the training pipeline, plus the race-relative columns added by `src/features/build_relative_features.py`.
Rows: 655,625. Missing rates are computed on `data/features/train_features.csv`.

Legend: `leakage_risk` = low / medium / high.

Note: classification is primary-axis based; some columns can plausibly fit more than one axis, so the notes call out the practical interpretation.

## Current active sets

This repo currently treats the following sets as the operational reference for 1着モデル work.

| set_name | feature_count | purpose | source |
| --- | ---: | --- | --- |
| `win_baseline_core` | 4 | stable core baseline used as the official regression baseline | `config/feature_sets/win_baseline_core.json` |
| `win_baseline_core_relative` | 13 | official Phase 1 predictor (`core` + race-relative features) | `config/feature_sets/win_baseline_core_relative.json` |
| `win_baseline_extended` | 7 | challenger baseline that keeps补完依存列候補 for comparison only | `config/feature_sets/win_baseline_extended.json` |

Core columns used by the active sets:

- `boat_no`
- `exhibition_time`
- `jcd`
- `race_number`

Relative columns used by `core_relative`:

- `national_2ren_rate_rank_in_race`
- `national_2ren_rate_diff_from_race_mean`
- `national_2ren_rate_z_in_race`
- `local_2ren_rate_rank_in_race`
- `local_2ren_rate_diff_from_race_mean`
- `local_2ren_rate_z_in_race`
- `avg_st_rank_in_race`
- `avg_st_advantage_vs_mean`
- `avg_st_advantage_z_in_race`

## 選手能力系

| feature_name | source_file | source_column | when_available | leakage_risk | missing_rate | notes |
| --- | --- | --- | --- | --- | ---: | --- |
| `national_win_rate` | `src/features/build_features.py` | `historical_races.csv.national_win_rate` | pre-race (history-derived) | low | 0.985179 | 過去成績ベースの能力指標。current baseline configs では未採用だが、現行 feature matrix には残る。 |
| `national_2ren_rate` | `src/features/build_features.py` | `historical_races.csv.national_2ren_rate` | pre-race (history-derived) | low | 0.985179 | 2連対率の全国指標。欠損が非常に多く、現行 matrix では sparse。 |
| `local_2ren_rate` | `src/features/build_features.py` | `historical_races.csv.local_2ren_rate` | pre-race (history-derived) | low | 0.985179 | 地元側の2連対率。全国指標と並ぶ能力系の補助列。 |
| `recent3_avg_finish` | `src/features/build_features.py` | `racer_id history -> finish_position (prior 3 races)` | pre-race | low | 0.002587 | 直近3走の平均着順。選手フォームの要約。 |
| `recent3_win_rate` | `src/features/build_features.py` | `racer_id history -> finish_position (prior 3 races)` | pre-race | low | 0.000000 | 直近3走の1着率。 |
| `recent3_top3_rate` | `src/features/build_features.py` | `racer_id history -> finish_position (prior 3 races)` | pre-race | low | 0.000000 | 直近3走の3着内率。 |
| `recent3_avg_st` | `src/features/build_features.py` | `racer_id history -> avg_st/start_display_st/st (prior 3 races)` | pre-race | low | 0.050819 | 直近3走のST平均。 |
| `recent6_avg_finish` | `src/features/build_features.py` | `racer_id history -> finish_position (prior 6 races)` | pre-race | low | 0.002587 | 直近6走の平均着順。 |
| `recent6_win_rate` | `src/features/build_features.py` | `racer_id history -> finish_position (prior 6 races)` | pre-race | low | 0.000000 | 直近6走の1着率。 |
| `recent6_top3_rate` | `src/features/build_features.py` | `racer_id history -> finish_position (prior 6 races)` | pre-race | low | 0.000000 | 直近6走の3着内率。 |
| `recent6_avg_st` | `src/features/build_features.py` | `racer_id history -> avg_st/start_display_st/st (prior 6 races)` | pre-race | low | 0.019171 | 直近6走のST平均。 |
| `rank_mean_recent3` | `src/features/build_features.py` | `alias of recent3_avg_finish` | pre-race | low | 0.002587 | recent3_avg_finish の別名。 |
| `rank_mean_recent6` | `src/features/build_features.py` | `alias of recent6_avg_finish` | pre-race | low | 0.002587 | recent6_avg_finish の別名。 |
| `win_rate_recent6` | `src/features/build_features.py` | `alias of recent6_win_rate` | pre-race | low | 0.000000 | recent6_win_rate の別名。 |
| `top3_rate_recent6` | `src/features/build_features.py` | `alias of recent6_top3_rate` | pre-race | low | 0.000000 | recent6_top3_rate の別名。 |
| `st_mean_recent6` | `src/features/build_features.py` | `alias of recent6_avg_st` | pre-race | low | 0.019171 | recent6_avg_st の別名。 |
| `st_std_recent6` | `src/features/build_features.py` | `racer_id history -> avg_st (prior 6 races)` | pre-race | low | 0.019171 | 直近6走STの分散度。 |
| `st_under010_rate` | `src/features/build_features.py` | `racer_id history -> avg_st <= 0.10 (prior 6 races)` | pre-race | low | 0.000000 | 直近6走で ST 0.10 以下の比率。 |
| `racer_rank` | `src/features/build_features.py` | `historical_races.csv.racer_class -> ordinal rank` | pre-race | low | 0.000000 | racer_class から A1/A2/B1/B2 を数値化。 |

## 機力系

| feature_name | source_file | source_column | when_available | leakage_risk | missing_rate | notes |
| --- | --- | --- | --- | --- | ---: | --- |
| `motor_2ren_rate` | `src/features/build_features.py` | `historical_races.csv.motor_2ren_rate` | pre-race (history-derived) | low | 0.985179 | モーター連対率。欠損が多いので current matrix では sparse。 |
| `boat_2ren_rate` | `src/features/build_features.py` | `historical_races.csv.boat_2ren_rate` | pre-race (history-derived) | low | 0.985179 | ボート連対率。欠損が多いので current matrix では sparse。 |
| `low_motor_flag` | `src/features/build_features.py` | `motor_2ren_rate < MOTOR_LOW_THRESH` | pre-race | low | 0.000000 | 低機力フラグ。 |
| `low_boat_flag` | `src/features/build_features.py` | `boat_2ren_rate < BOAT_LOW_THRESH` | pre-race | low | 0.000000 | 低ボートフラグ。 |

## コース系

| feature_name | source_file | source_column | when_available | leakage_risk | missing_rate | notes |
| --- | --- | --- | --- | --- | ---: | --- |
| `lane` | `src/features/build_features.py` | `historical_races.csv.lane` | ingest / pre-race | low | 0.000000 | 枠番そのもの。current matrix の基礎列。 |
| `lane_num` | `src/features/build_features.py` | `lane (numeric copy)` | ingest / pre-race | low | 0.000000 | lane の数値コピー。 |
| `inside_course_flag` | `src/features/build_features.py` | `lane <= 2` | pre-race | low | 0.000000 | インコース判定。 |
| `lane_win_rate_prior` | `src/features/build_features.py` | `racer_id history -> finish_position by lane` | pre-race | low | 0.000000 | 選手×枠の過去勝率。 |
| `win_rate_venue` | `src/features/build_features.py` | `racer_id × jcd history -> finish_position win rate` | pre-race | low | 0.000000 | 選手×会場の過去勝率。 |
| `course_win_rate` | `src/features/build_features.py` | `racer_id history -> finish_position by lane` | pre-race | low | 0.015096 | コース別の勝率要約。 |
| `course1_win_rate` | `src/features/build_features.py` | `racer_id history -> finish_position by lane=1` | pre-race | low | 0.067067 | 1コース条件に限定した勝率。 |

## 当日系

| feature_name | source_file | source_column | when_available | leakage_risk | missing_rate | notes |
| --- | --- | --- | --- | --- | ---: | --- |
| `exhibition_time` | `src/features/build_features.py` | `historical_races.csv.exhibition_time` | just-before race | medium | 0.000000 | 展示タイム。現行 baseline configs では重要だが、取得失敗時は欠損扱い。 |
| `exhibition_time_rank` | `src/features/build_features.py` | `race_id group rank(exhibition_time)` | just-before race | medium | 0.000000 | 展示タイムのレース内順位。 |
| `start_timing` | `src/features/build_features.py` | `exhibition_time -> start_display_st -> avg_st fallback` | just-before race | medium | 0.000000 | 当日開始タイミングの代表値。 |

## レース文脈系

| feature_name | source_file | source_column | when_available | leakage_risk | missing_rate | notes |
| --- | --- | --- | --- | --- | ---: | --- |
| `race_id` | `src/features/build_features.py` | `input row key / raw race identifier` | ingest / split key | medium | 0.000000 | レース識別子。学習ではキー用途が主で、モデル入力には非推奨。 |
| `racer_id` | `src/features/build_features.py` | `historical_races.csv.racer_id` | ingest / split key | high | 0.000000 | 選手ID。過学習・識別子リークのリスクが高い。 |
| `date` | `src/features/build_features.py` | `historical_races.csv.date` | ingest / split key | low | 0.000000 | 時系列分割の基準日。 |
| `jcd` | `src/features/build_features.py` | `historical_races.csv.jcd` | ingest / split key | low | 0.000000 | 会場コード。venue の代替キー。 |
| `rank_trend` | `src/features/build_features.py` | `racer_id history -> finish_position trend (prior 3 races)` | pre-race | low | 0.007756 | 直近3走の着順トレンド。 |
| `win_rate_diff_to_avg` | `src/features/build_features.py` | `national_win_rate - race mean(national_win_rate)` | pre-race | low | 0.985179 | レース内平均との差分。 |

## 相互作用系

| feature_name | source_file | source_column | when_available | leakage_risk | missing_rate | notes |
| --- | --- | --- | --- | --- | ---: | --- |
| `national_2ren_rate_rank_in_race` | `src/features/build_relative_features.py` | `race_id group rank(national_2ren_rate)` | pre-race after race-card assembly | low | 0.985179 | レース内順位化。 |
| `national_2ren_rate_diff_from_race_mean` | `src/features/build_relative_features.py` | `national_2ren_rate - race mean(national_2ren_rate)` | pre-race after race-card assembly | low | 0.985179 | レース平均との差。 |
| `national_2ren_rate_z_in_race` | `src/features/build_relative_features.py` | `(national_2ren_rate - race mean) / race std` | pre-race after race-card assembly | low | 0.985179 | レース内 z-score。 |
| `local_2ren_rate_rank_in_race` | `src/features/build_relative_features.py` | `race_id group rank(local_2ren_rate)` | pre-race after race-card assembly | low | 0.985179 | レース内順位化。 |
| `local_2ren_rate_diff_from_race_mean` | `src/features/build_relative_features.py` | `local_2ren_rate - race mean(local_2ren_rate)` | pre-race after race-card assembly | low | 0.985179 | レース平均との差。 |
| `local_2ren_rate_z_in_race` | `src/features/build_relative_features.py` | `(local_2ren_rate - race mean) / race std` | pre-race after race-card assembly | low | 0.985179 | レース内 z-score。 |
| `avg_st_rank_in_race` | `src/features/build_relative_features.py` | `race_id group rank(avg_st)` | pre-race after race-card assembly | low | 1.000000 | ST のレース内順位。 |
| `avg_st_advantage_vs_mean` | `src/features/build_relative_features.py` | `race mean(avg_st) - avg_st` | pre-race after race-card assembly | low | 1.000000 | 平均よりどれだけ速いか。 |
| `avg_st_advantage_z_in_race` | `src/features/build_relative_features.py` | `(race mean(avg_st) - avg_st) / race std` | pre-race after race-card assembly | low | 1.000000 | ST 優位のレース内 z-score。 |
| `jcd_low_motor_flag` | `src/features/build_features.py` | `jcd <= threshold && motor_2ren_rate < threshold` | pre-race | low | 0.000000 | 会場×低機力の交互作用フラグ。 |
| `jcd_low_boat_flag` | `src/features/build_features.py` | `jcd <= threshold && boat_2ren_rate < threshold` | pre-race | low | 0.000000 | 会場×低ボートの交互作用フラグ。 |

## Cross-check

- `racer_id` is intentionally listed with high leakage risk; it is a key, not a safe modeling feature.
- `exhibition_time`, `exhibition_time_rank`, and `start_timing` are the closest to just-before-race signals in the current matrix.
- Relative features are the only columns whose source file is `src/features/build_relative_features.py`; the rest come from `src/features/build_features.py`.
- The narrower baseline readiness subset lives in `data/processed/trainable_win_training_data.csv` and `config/feature_sets/*.json`; this inventory focuses on the current feature matrix.
