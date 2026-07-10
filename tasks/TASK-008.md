# TASK-008

## Title
max_trifecta_combinations の上限を 60/80/100/120 でオフライン比較し改善余地を定量化する

## Background
TASK-006 により `not_in_60` の主因は `cut_by_top60` であることが確認された。
TASK-007 により、`cut_by_top60` 281件の理論順位分布は
- rank_61_80: 110
- rank_81_100: 94
- rank_101_120: 77
- rank_121_plus: 0
であり、全件が 120 位以内に収まることが分かった。
このため、まずは `max_trifecta_combinations` の上限変更だけで主要KPIがどこまで改善するかを
オフライン比較で定量化するのが最短である。

## Objective
候補生成ロジックは変えず、`max_trifecta_combinations` を 60 / 80 / 100 / 120 に変えた場合の
主要KPI差分を比較し、上限変更の改善余地を定量化する。

## Scope
- 候補生成ロジックは変更しない
- 上限値だけを変えた比較を行う
- 60 / 80 / 100 / 120 で結果比較する
- 主要KPI差分を JSON に保存する

## Out of Scope
- 候補選別基準の変更
- 再スコア実装
- 学習特徴量変更
- 買い目ロジック変更
- 本体設定の採用確定

## Target Files
- `src/eval/ablation_and_bottleneck.py`

## Constraints
- 変更は最小限
- 既存出力は壊さない
- 外部依存を追加しない
- 今回はオフライン比較のみ

## Implementation Notes
60 / 80 / 100 / 120 について少なくとも以下を比較する。
- exact_hitrate
- top1_hitrate
- trifecta_avg_rank
- rank帯分布
- not_in_60
- candidate_include_rate（取得可能なら）

差分は baseline=60 基準でまとめる。
結果は新規 JSON に保存する。

## Validation
```bash
$env:PYTHONPATH='.'; py src/eval/ablation_and_bottleneck.py
```

## Completion Criteria
- [ ] 60 / 80 / 100 / 120 の比較結果が出る
- [ ] baseline=60 比差分が確認できる
- [ ] JSON レポートが生成される
- [ ] 既存出力を壊していない

## Expected Output
- `reports/max_trifecta_comparison.json`

## Risk
- 上限を増やしても top1 はほぼ改善しない可能性がある
- exact 改善と買い目数増加のトレードオフ評価は別途必要
- 実運用では点数増加コストを別に見る必要がある

## Report Path
- `reports/max_trifecta_comparison.json`

## Notes for Agent
- 今回は比較のみ
- 候補生成ロジックは固定
- 次タスクで「上限拡張採用」か「60件の残し方改善」かを判断する
