# TASK-003

## Title
候補集合内に入った実三連単の順位低下要因を分解する

## Background
TASK-002 により、旧 bottleneck 判定の `not_in_60` は過大であったことが確認できた。
修正後は `not_in_60=282` まで低下した一方、`rank_21_40=109` と `rank_41_60=113` が大きく、
`trifecta_avg_rank=31.2`、`diagnosis=in_set_ranking_insufficiency_dominant` となった。
このため、次は候補外不足よりも、候補集合内で実三連単の順位が沈む原因の分解が必要。

## Objective
実三連単が候補集合内に含まれているレースを対象に、
順位低下の主因が「2着3着の順序差」なのか「approx_prob の並び自体の弱さ」なのかを分解して可視化する。

## Scope
- 候補集合内ヒットレースのみを対象に分析する
- 実三連単順位の分布を追加で分解する
- 2着3着入れ替え時の順位比較を出す
- approx_prob と実順位の関係を集計する
- レポートを `reports/` に保存する

## Out of Scope
- 候補生成ロジックの変更
- `strategy_config.json` の変更
- 学習特徴量の追加
- 買い目ロジックの変更
- 実際の改善ロジック実装

## Target Files
- `src/eval/ablation_and_bottleneck.py`

## Constraints
- 変更は最小限
- 既存出力を壊さない
- 外部依存を追加しない
- 今回は分析のみ行い、改善実装は次タスクに分ける

## Implementation Notes
候補集合内に入った実三連単のみを対象に、以下を追加出力する。

### 1. 順位分布の詳細
- `rank_1_5`
- `rank_6_10`
- `rank_11_20`
- `rank_21_40`
- `rank_41_60`

### 2. 2着3着入れ替え比較
各対象レースについて、実三連単 `(1,2,3)` に対して
`(1,3,2)` が候補集合内に存在する場合は、その順位と `approx_prob` を比較する。

最低限以下を集計する。
- 実三連単より 2着3着入れ替え候補のほうが上位だった件数
- 同一1着で順序違いだけが原因と見なせる件数
- 順序違い候補自体が存在しない件数

### 3. スコア差の集計
各対象レースについて、以下を集計する。
- 実三連単の `approx_prob`
- 1位候補の `approx_prob`
- 実三連単より上位にある候補数
- 1位候補との差分
- 上位5候補平均との差分（可能なら）

### 4. 簡易分類
各対象レースを以下のどれかに分類する。
- `second_third_order_issue`
- `scoring_issue`
- `mixed_or_other`

分類ルールは簡易でよいが、レポート内に明示すること。

例:
- 順序違い候補が存在し、それが実三連単より上位なら `second_third_order_issue`
- 順序違いだけでは説明できず、多数候補に負けているなら `scoring_issue`
- 判定困難なら `mixed_or_other`

## Validation
```bash
$env:PYTHONPATH='.'; py src/eval/ablation_and_bottleneck.py
```

## Completion Criteria
- [ ] 候補集合内ヒットの順位低下要因が分類される
- [ ] 2着3着順序問題かスコア問題かの比率が出る
- [ ] JSONレポートが生成される
- [ ] 既存の `reports/ablation_result.json` を壊していない
- [ ] 既存の `reports/bottleneck_analysis.json` を壊していない

## Expected Output
- `reports/bottleneck_ranking_breakdown.json`

## Risk
- 分類ルールが簡易的で厳密ではない
- 順序問題とスコア問題が完全分離できないケースがある

## Report Path
- `reports/bottleneck_ranking_breakdown.json`

## Notes for Agent
- 今回は分析だけに留める
- 改善ロジックは実装しない
- 実三連単が候補集合内にあるケースだけを見る
- 既存出力との互換性を優先する
