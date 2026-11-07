# Feature Availability

`data/metadata/feature_availability.csv` は、各特徴量がどのフェーズで利用可能かを固定する表である。

## available_phase

- `entry`
  - 出走表だけで使える
- `pre_race`
  - 締切前の直前情報まで使える
- `final_odds`
  - 確定オッズが必要
- `result`
  - 結果確定後のみ使える

## フラグ

- `allowed_for_training`
  - 学習に使ってよいか
- `allowed_for_live`
  - 本番予想で使ってよいか

## ルール

- `pre_race_features` には `allowed_for_live=false` の列を入れない
- `pre_race_features` に `result` phase の列を混ぜたらエラー
- `training_dataset` は `result` phase のラベルを持ってよいが、live 出力には使わない

## 最低限の管理対象

- `lane`
- `class`
- `age`
- `weight`
- `avg_st`
- `nat_win_rate`
- `local_win_rate`
- `motor_rate`
- `boat_rate`
- `exhibition_time`
- `exhibition_type`
- `weather`
- `wind_speed`
- `wave_height`
- `finish_position`
- `is_win`
- `is_top2`
- `is_top3`
- `winning_trifecta`
- `payout_trifecta`

