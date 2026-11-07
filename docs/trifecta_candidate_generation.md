# Trifecta Candidate Generation

## 目的

`win_proba` を起点に、1レース6艇の 3連単候補を安定してランキングする。
今回は 3連単を直接学習しない。まずは 1着確率ベースの heuristic baseline を固定し、後続フェーズで改善しやすい形にする。

## 入力

- 必須
  - `race_date`
  - `jcd`
  - `race_no`
  - `lane`
  - `win_proba` または互換列
- 参照可能
  - `pred_rank_within_race`
  - `class`
  - `avg_st`
  - `nat_win_rate`
  - `local_win_rate`
  - `motor_rate`
  - `boat_rate`
  - `exhibition_time`

## 使用しないもの

- `winning_trifecta`
- `finish_position`
- `is_win`
- `is_top2`
- `is_top3`
- `payout_trifecta`
- `odds` 系列

## 標準キー

- レースキー: `race_date + jcd + race_no`
- 艇キー: `race_date + jcd + race_no + lane`
- 派生キー: `race_id`, `race_key`, `trifecta_key`

## 候補生成ルール

- 各レースで 6P3 = 120 通りを全列挙する
- lane の重複は禁止
- `first_lane`, `second_lane`, `third_lane` はすべて異なること
- 6艇未満のレースは skip する

## スコア定義

- `first_score` = 正規化した `win_proba`
- `second_score` / `third_score` = レース内の相対強度スコア
- `candidate_score` = `first_score * second_score * third_score`
- `candidate_rank` = `candidate_score` 降順で 1 始まりの順位

### 相対強度スコアの優先順

1. `pred_rank_within_race` 由来の順位スコア
2. `nat_win_rate`
3. `avg_st`
4. `motor_rate`
5. `boat_rate`
6. `local_win_rate`
7. `class`

`candidate_score` は真の 3連単確率ではない。あくまで比較用の heuristic score である。

## 出力

- `data/predictions/trifecta_candidates_full_<range>.csv`
- `data/predictions/trifecta_candidates_topn_<range>.csv`
- `data/strategy_outputs/trifecta_candidates.csv`

### full table の最低限列

- `race_date`
- `jcd`
- `race_no`
- `race_id`
- `race_key`
- `trifecta_key`
- `first_lane`
- `second_lane`
- `third_lane`
- `first_score`
- `second_score`
- `third_score`
- `candidate_score`
- `candidate_rank`

## リーク防止

- result phase の列が入力に混ざっていたらエラーにする
- `feature_availability.csv` で `allowed_for_live=false` の列は使わない
- `winning_trifecta` は評価時のみ使う
- `odds` や `ev` はこのフェーズでは使わない

## 既知の制約

- `candidate_score` は確率ではない
- 2着・3着の依存関係は厳密には表現していない
- 期待値と購入判断はまだ未接続

