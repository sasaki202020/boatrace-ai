# Feature Leakage Suspects

Day 3 audit: `groupby + shift + rolling` を使う時系列特徴量を点検した。

Recheck: 2026-04-19. 現在の `src/features/build_features.py` / `src/features/build_relative_features.py` を再確認し、明確な future row leak は追加で見つからなかった。

## 結論

- `src/features/build_features.py` で確認した time-series 系特徴量は、いずれも `shift(1)` を先に通してから `rolling` / `cumsum` / `ffill` を使っていた。
- そのため、**現時点で明確な future row leak は確認できなかった**。
- ただし、以下は実運用で壊れやすいので **要レビュー候補** として残す。

## 要レビュー候補

| feature_name | source_file | grouping_unit | pattern | why review |
| --- | --- | --- | --- | --- |
| `recent3_avg_finish` | `src/features/build_features.py` | `racer_id` | `shift(1) -> rolling(3)` | 実装は安全だが、日付順ソートが崩れると prior row の解釈が壊れる。 |
| `recent3_win_rate` | `src/features/build_features.py` | `racer_id` | `shift(1) -> rolling(3)` | 同上。学習時と本番時で row order が一致している前提が必要。 |
| `recent3_top3_rate` | `src/features/build_features.py` | `racer_id` | `shift(1) -> rolling(3)` | 同上。 |
| `recent3_avg_st` | `src/features/build_features.py` | `racer_id` | `shift(1) -> rolling(3)` | 同上。 |
| `recent6_avg_finish` | `src/features/build_features.py` | `racer_id` | `shift(1) -> rolling(6)` | 同上。 |
| `recent6_win_rate` | `src/features/build_features.py` | `racer_id` | `shift(1) -> rolling(6)` | 同上。 |
| `recent6_top3_rate` | `src/features/build_features.py` | `racer_id` | `shift(1) -> rolling(6)` | 同上。 |
| `recent6_avg_st` | `src/features/build_features.py` | `racer_id` | `shift(1) -> rolling(6)` | 同上。 |
| `st_std_recent6` | `src/features/build_features.py` | `racer_id` | `shift(1) -> rolling(6).std()` | 同上。 |
| `st_under010_rate` | `src/features/build_features.py` | `racer_id` | `shift(1) -> rolling(6).mean()` | 同上。 |
| `rank_trend` | `src/features/build_features.py` | `racer_id` | `shift(1) -> rolling(3).apply()` | 同上。 |
| `course_win_rate` | `src/features/build_features.py` | `racer_id + lane_num` | `cumcount() + shift(1) + cumsum()` | 安全寄りだが、`date` / `race_no` / `lane` の並びが崩れると過去判定がずれる。 |
| `course1_win_rate` | `src/features/build_features.py` | `racer_id` | `lane=1` subset + `shift(1) + cumsum() + ffill()` | `ffill()` を使うため、欠損補完の意図と範囲を要確認。ここが最もレビュー優先度が高い。 |
| `lane_win_rate_prior` | `src/features/build_features.py` | `racer_id + lane` | `group prior aggregate` | 安全寄りだが、入力 row order の前提が崩れると prior 集計の意味が変わる。 |
| `win_rate_venue` | `src/features/build_features.py` | `racer_id + jcd` | `group prior aggregate` | 安全寄りだが、同一選手×会場の履歴順が壊れない前提が必要。 |
| `win_rate_diff_to_avg` | `src/features/build_features.py` | `race_id` | `race mean diff` | レース全体が揃っている前提。中途半端な途中フレームで計算すると平均との差が歪む。 |
| `national_2ren_rate_rank_in_race` | `src/features/build_relative_features.py` | `race_id` | `rank within race` | レースカードが欠損した状態で計算すると順位が変わる。 |
| `national_2ren_rate_diff_from_race_mean` | `src/features/build_relative_features.py` | `race_id` | `mean diff within race` | 同上。 |
| `national_2ren_rate_z_in_race` | `src/features/build_relative_features.py` | `race_id` | `z-score within race` | 同上。 |
| `local_2ren_rate_rank_in_race` | `src/features/build_relative_features.py` | `race_id` | `rank within race` | 同上。 |
| `local_2ren_rate_diff_from_race_mean` | `src/features/build_relative_features.py` | `race_id` | `mean diff within race` | 同上。 |
| `local_2ren_rate_z_in_race` | `src/features/build_relative_features.py` | `race_id` | `z-score within race` | 同上。 |
| `avg_st_rank_in_race` | `src/features/build_relative_features.py` | `race_id` | `rank within race` | 同上。 |
| `avg_st_advantage_vs_mean` | `src/features/build_relative_features.py` | `race_id` | `mean advantage within race` | 同上。 |
| `avg_st_advantage_z_in_race` | `src/features/build_relative_features.py` | `race_id` | `z-score within race` | 同上。 |

## 監査メモ

- `shift(1)` を使う系列は、**現在行そのものを学習に使わない** という意味では安全。
- 真のリスクは、`date` / `race_no` / `lane` のソート前提が壊れることと、`course1_win_rate` の `ffill()` の補完範囲が意図どおりかどうか。
- `race_id` 単位の相互作用系は、レースカードが揃ってから計算される前提なら future leak ではない。

## Recheck notes

- `course1_win_rate` は引き続き最優先レビュー候補。
- `rank_trend` と `win_rate_diff_to_avg` は、レースカードの row order が安定している前提でのみ安全。
- `build_relative_features.py` の race 内相対特徴は、レースカード整列後の計算であり、現時点では future leak の疑いを追加で持たない。

## 参考

- `docs/feature_inventory.md`
- `docs/feature_availability_matrix.md`
- `reports/data_audit/feature_leakage_day3_check.json`
