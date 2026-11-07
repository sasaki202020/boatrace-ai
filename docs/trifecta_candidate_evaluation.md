# Trifecta Candidate Evaluation

## 目的

3連単候補の良し悪しを「買ったかどうか」ではなく、「正解の 3連単が候補の上位に入るか」で評価する。

## 評価入力

- 候補:
  - `trifecta_candidates_full_<range>.csv`
- 正解:
  - `winning_trifecta` を含む結果データ

## 評価で使う列

- `race_id`
- `race_date`
- `jcd`
- `race_no`
- `winning_trifecta`

`winning_trifecta` は評価時のみ使用する。候補生成本体では参照しない。

## 指標

### Hit@K

各レースで実際の `winning_trifecta` が上位 `K` 候補に含まれていた割合。

### Mean Winning Rank

実際の `winning_trifecta` が候補表の何位だったかの平均。

### Candidate Coverage Rate

評価対象レースのうち、実際の `winning_trifecta` の順位を計算できた割合。

## 出力

- `reports/trifecta_candidate_eval/trifecta_candidate_summary.json`
- `reports/trifecta_candidate_eval/trifecta_candidate_metrics.csv`
- `reports/trifecta_candidate_eval/trifecta_candidate_by_venue.csv`
- `reports/trifecta_candidate_eval/trifecta_candidate_by_rank_cut.csv`
- `reports/trifecta_candidate_eval/used_columns.json`

## 既知の制約

- Hit@K が高くても、まだ EV や購入最適化は未評価
- 候補スコアは heuristic なので、真の確率順位とは一致しない場合がある

