# Inference Contract

## 当日推論

当日推論では、結果系の列を一切使わずに候補生成だけを行う。

### 入力

- `win_proba` 系の予測出力
- 必要に応じて `pre_race_features`

### 禁止

- `winning_trifecta`
- `finish_position`
- `is_win`
- `is_top2`
- `is_top3`
- `payout_trifecta`
- `odds`
- `ev`

## 事後評価

事後評価では `winning_trifecta` を使って Hit@K と Mean Winning Rank を計測する。
候補生成ロジック自体は結果列を参照しない。

## 出力の役割

- `data/strategy_outputs/trifecta_candidates.csv`
  - downstream 互換の full candidates
- `data/predictions/trifecta_candidates_full_<range>.csv`
  - 解析・再評価向けの full table
- `data/predictions/trifecta_candidates_topn_<range>.csv`
  - 上位候補の確認用

## リーク防止ルール

- `feature_availability.csv` を参照し、`allowed_for_live=false` の列は使わない
- result phase の列が混ざったらエラーにする
- odds / EV はこのフェーズでは使わない

