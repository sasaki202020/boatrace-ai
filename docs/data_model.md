# Data Model

## 標準キー

- `race_date`: `YYYY-MM-DD`
- `jcd`: 2桁の開催場コード
- `race_no`: 1-12 のレース番号
- `lane`: 1-6 の艇番

## 派生キー

- `race_id`: `YYYYMMDD-JJ-RR`
- `race_key`: `dYYYYMMDD-cJJ-rRR`
- `boat_key`: `YYYYMMDD-JJ-RR-LL`

## テーブル

- `normalized_entries`
  - レース単位の出走表を艇単位に正規化した表
  - `player_id`, `player_name`, `class`, `branch`, `age`, `weight`, `avg_st`, `nat_win_rate`, `local_win_rate`, `motor_rate`, `boat_rate` を含む

- `normalized_pre_race`
  - `normalized_entries` に直前情報を加えた live 前提の表
  - `snapshot_time`, `exhibition_time`, `exhibition_type`, `body_weight`, `weather`, `wind_speed`, `wave_height` を含む

- `normalized_results`
  - 結果確定後のラベル表
  - `finish_position`, `is_win`, `is_top2`, `is_top3`, `winning_trifecta`, `payout_trifecta` を含む

- `pre_race_features`
  - 学習・本番共通で使うリーク防止済みの特徴量表
  - result 系の列は含めない

- `training_dataset`
  - `pre_race_features` に `normalized_results` を結合した学習用表
  - 1レース6艇で揃ったレースだけを残す

## 責務

- `raw`
  - 原本を壊さず保存する
- `processed`
  - 正規化済みの学習・推論用テーブルを保存する
- `metadata`
  - feature availability などの管理情報を保存する

