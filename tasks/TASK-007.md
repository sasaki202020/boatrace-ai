# TASK-007

## Title
cut_by_top60 の理論順位分布を分解し、上限拡張と選別基準変更のどちらを優先すべきか判断する

## Background
TASK-006 により、not_in_60 の 282 件中 281 件 (99.65%) が `cut_by_top60` であることが確認された。
これは、正解三連単が候補生成対象には入っている一方、最終60件の選別で落ちていることを意味する。
次は、cut_by_top60 の正解候補が理論上何位に位置しているかを確認し、
1. 上限数を増やすべきか
2. 60件の残し方を変えるべきか
を分岐できる状態にする必要がある。

## Objective
`cut_by_top60` に分類されたレースについて、正解三連単の理論順位分布を集計し、
上限拡張が効く問題なのか、選別基準改善が必要な問題なのかを判断できるようにする。

## Scope
- `cut_by_top60` ケースのみを対象に分析する
- 正解三連単の理論順位分布を集計する
- 順位帯ごとの件数・比率を出す
- 60/80/100/120 件まで広げた場合の累積捕捉率を出す
- レポートを `reports/` に保存する

## Out of Scope
- 候補生成ロジックの変更
- 実際の max 値変更
- 再スコア実装
- 買い目ロジック変更

## Target Files
- `src/eval/ablation_and_bottleneck.py`

## Constraints
- 変更は最小限
- 既存JSON出力は壊さない
- 外部依存を追加しない
- 今回は分析のみ

## Implementation Notes
`cut_by_top60` レースについて、少なくとも以下を出力する。

### 1. 理論順位帯分布
- rank_61_80
- rank_81_100
- rank_101_120
- rank_121_plus

### 2. 累積捕捉率
- top_60_capture
- top_80_capture
- top_100_capture
- top_120_capture

### 3. 補助統計
- 理論順位の平均・中央値
- 代表例を数件（可能なら）

## Validation
```bash
$env:PYTHONPATH='.'; py src/eval/ablation_and_bottleneck.py
```

## Completion Criteria
- [ ] cut_by_top60 の理論順位分布が出る
- [ ] 60/80/100/120 の累積捕捉率が出る
- [ ] JSONレポートが生成される
- [ ] 既存出力を壊していない

## Expected Output
- `reports/cut_by_top60_rank_distribution.json`

## Risk
- 理論順位再現が既存候補生成ロジックに依存する
- 深い順位帯に多い場合、次タスクで上限拡張だけでは不十分になる

## Report Path
- `reports/cut_by_top60_rank_distribution.json`

## Notes for Agent
- 今回は分析のみ
- cut_by_top60 の深さを測ることに集中する
- 次タスクで max拡張か選別基準変更かを決める
