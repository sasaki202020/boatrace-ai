# Feature Tiering

Day 5: current feature universe を `Core / Conditional / Experimental` の 3 段階に固定する。

## Goal

- Core: 毎日安定して使う特徴量
- Conditional: 当日取得や race-card 完備が前提の特徴量
- Experimental: まだ本番既定にしない特徴量

この分類は、現行の 1着モデル運用を壊さずに「本番投入してよい範囲」を明確化するためのもの。

## Current relation to active sets

- `win_baseline_core` = Core の最小運用セット
- `win_baseline_core_relative` = Core + Conditional のうち、official predictor に必要な race-relative/just-before features を含むセット
- `win_baseline_extended` = Challenger 用の比較セット。Core/Conditional にまたがるが、正式採用前の比較対象として扱う

## Core

Stable, low-fragility, daily-safe features. ここは default production に載せてよい。

| feature_name | rationale |
| --- | --- |
| `boat_no` | 号艇。日次で必ずある基礎列。 |
| `jcd` | 開催場コード。会場差分のキー。 |
| `race_number` | レース番号。日中運用の基礎キー。 |
| `avg_st` | 履歴由来の安定した ST 要約。trainable で欠損は解消済み。 |
| `national_2ren_rate` | 履歴由来の選手能力指標。trainable で補完済み。 |
| `local_2ren_rate` | 履歴由来の選手能力指標。trainable で補完済み。 |
| `recent3_avg_finish` | 直近3走の着順要約。 |
| `recent3_win_rate` | 直近3走の1着率。 |
| `recent3_top3_rate` | 直近3走の3着内率。 |
| `recent3_avg_st` | 直近3走のST平均。 |
| `recent6_avg_finish` | 直近6走の着順要約。 |
| `recent6_win_rate` | 直近6走の1着率。 |
| `recent6_top3_rate` | 直近6走の3着内率。 |
| `recent6_avg_st` | 直近6走のST平均。 |
| `rank_mean_recent3` | recent3_avg_finish の別名。 |
| `rank_mean_recent6` | recent6_avg_finish の別名。 |
| `win_rate_recent6` | recent6_win_rate の別名。 |
| `top3_rate_recent6` | recent6_top3_rate の別名。 |
| `st_mean_recent6` | recent6_avg_st の別名。 |
| `st_std_recent6` | 直近6走STの分散度。 |
| `st_under010_rate` | 直近6走で ST 0.10 以下の比率。 |
| `racer_rank` | A1/A2/B1/B2 の数値化。 |
| `lane` | 枠番。 |
| `lane_num` | lane の数値コピー。 |
| `inside_course_flag` | インコース判定。 |
| `lane_win_rate_prior` | 選手×枠の過去勝率。 |
| `win_rate_venue` | 選手×会場の過去勝率。 |
| `course_win_rate` | コース別勝率の要約。 |
| `course1_win_rate` | 1コース条件の過去勝率。 |
| `rank_trend` | 直近3走の着順トレンド。 |

## Conditional

Day-of availability, race-card completeness, or sparse machine-state dependent features. default ではなく、入力が揃うときに使う。

| feature_name | rationale |
| --- | --- |
| `exhibition_time` | 当日取得が前提の展示タイム。 |
| `exhibition_time_rank` | 展示タイムのレース内順位。 |
| `start_timing` | 展示/直前情報から作る当日代表値。 |
| `motor_2ren_rate` | 機力系だが欠損しやすい。 |
| `boat_2ren_rate` | 機力系だが欠損しやすい。 |
| `low_motor_flag` | 機力欠損・低機力の補助フラグ。 |
| `low_boat_flag` | 機力欠損・低ボートの補助フラグ。 |
| `jcd_low_motor_flag` | 会場×低機力の交互作用。 |
| `jcd_low_boat_flag` | 会場×低ボートの交互作用。 |
| `national_2ren_rate_rank_in_race` | レース内相対化。カードが揃ってから計算。 |
| `national_2ren_rate_diff_from_race_mean` | レース平均との差。 |
| `national_2ren_rate_z_in_race` | レース内 z-score。 |
| `local_2ren_rate_rank_in_race` | レース内相対化。カードが揃ってから計算。 |
| `local_2ren_rate_diff_from_race_mean` | レース平均との差。 |
| `local_2ren_rate_z_in_race` | レース内 z-score。 |
| `avg_st_rank_in_race` | ST のレース内相対化。 |
| `avg_st_advantage_vs_mean` | 平均 ST との差分。 |
| `avg_st_advantage_z_in_race` | 平均 ST の z-score。 |

## Experimental

Not default production. Compare/ablation/regression candidate only.

| feature_name | rationale |
| --- | --- |
| `national_win_rate` | sparse で本番の安定性が低い。 |
| `win_rate_diff_to_avg` | レース平均との差分は有用だが、参照列の安定性に依存する。 |

## Operational policy

- Core は default で使う。
- Conditional は必要条件が満たされたときだけ使う。
- Experimental は標準運用に混ぜない。
- current official predictor (`win_baseline_core_relative`) は Core + Conditional の一部で構成される。
- 1着モデルの比較結果に応じて、Conditional から Core へ昇格する候補を今後選ぶ。

## Current counts

- Core: 30
- Conditional: 18
- Experimental: 2
