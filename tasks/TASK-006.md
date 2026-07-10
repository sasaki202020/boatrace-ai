# TASK-006

## Title
not_in_60 の 282 件が候補生成のどの段階で落ちているかを分解する

## Background
TASK-002 で `not_in_60` は過大評価だったと分かったが、修正後も `not_in_60=282` が残っている。
TASK-004 の順序補正、TASK-005 の軽量再スコア比較では主要KPI改善が出なかったため、
次は候補集合不足そのものを分解し、どの段階が最大ボトルネックか確認する必要がある。

## Objective
`not_in_60` レースについて、正解三連単が
1. 1着候補段階で落ちたのか
2. 2着3着ペア生成段階で落ちたのか
3. 生成はされたが60件上限で落ちたのか
を分類して可視化する。

## Scope
- `not_in_60` ケースのみを対象に分析する
- 落ちた段階の件数比率を出す
- 必要なら代表例を数件出す
- レポートを `reports/` に保存する

## Out of Scope
- 候補生成ロジック変更
- `top_n_win` 変更
- `max_trifecta_combinations` 変更
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
各 `not_in_60` レースについて、少なくとも以下を判定する。

### 1. first_miss
正解1着艇が 1着候補集合に入っていない

### 2. pair_miss
正解1着艇は候補内だが、正解の2着3着艇ペアが候補生成に入っていない

### 3. cut_by_top60
正解三連単は生成対象に入るが、最終60件に残っていない

### 4. other_or_unknown
上記で説明しきれないケース

可能なら以下も補助出力する。
- 正解1着艇の win順位
- 正解三連単が理論上何位相当だったか
- cut_by_top60 の件数比率

## Validation
```bash
$env:PYTHONPATH='.'; py src/eval/ablation_and_bottleneck.py
```

## Completion Criteria
- [ ] `not_in_60` の内訳が分類される
- [ ] 最大ボトルネック段階が分かる
- [ ] JSONレポートが生成される
- [ ] 既存出力を壊していない

## Expected Output
- `reports/not_in_60_stage_breakdown.json`

## Risk
- 既存候補生成ロジックの再現解釈を誤ると分類がズレる
- pair生成条件が暗黙だと曖昧ケースが出る

## Report Path
- `reports/not_in_60_stage_breakdown.json`

## Notes for Agent
- 今回は分析だけ
- 候補集合不足の段階分解に集中する
- 改善実装は次タスクに分ける
