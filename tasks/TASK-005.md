# TASK-005

## Title
同一候補集合のまま approx_prob の再スコア比較を行い順位付け改善余地を検証する

## Background
TASK-003 により、候補集合内順位不足が主要ボトルネックであることが確認された。
TASK-004 では 2着3着順序補正を小さく試したが、rank帯の一部移動はあったものの、
exact_hitrate, top1_hitrate, trifecta_avg_rank, not_in_60 の主要KPI改善は確認できなかった。
このため、次は順序補正ではなく、同一候補集合のまま `approx_prob` の順位付け自体を改善できるかを検証する。

## Objective
候補集合を固定したまま、既存 `approx_prob` に小さな再スコア項を追加し、
主要KPIが改善するかをオフライン比較で確認する。

## Scope
- 候補集合は変更しない
- 既存 `approx_prob` に対する小さな再スコア比較を追加する
- 複数の小さな係数条件を比較する
- 補正あり/なしで主要KPI差分を出す
- 結果を `reports/` に保存する

## Out of Scope
- 候補生成ロジック変更
- 特徴量追加
- 学習処理変更
- モデル変更
- 本体ロジックへの採用確定

## Target Files
- `src/eval/ablation_and_bottleneck.py`

## Constraints
- 変更は最小限
- 既存JSON出力は壊さない
- 外部依存を追加しない
- 今回は分析比較のみ
- 係数条件は少数に絞る

## Implementation Notes
- 既存 `approx_prob` を基準スコアとする
- 小さな再スコア項を加えた比較を行う
- 候補集合・候補数は固定
- 比較条件は少数グリッドでよい（例: 3〜5条件）
- 条件ごとに以下を比較する
  - exact_hitrate
  - top1_hitrate
  - trifecta_avg_rank
  - rank帯分布
  - not_in_60
- ベースラインとの差分を JSON にまとめる
- 主要KPI非悪化かつ一部改善の条件があるかを見る

## Validation
```bash
$env:PYTHONPATH='.'; py src/eval/ablation_and_bottleneck.py
```

## Completion Criteria
- [ ] 再スコア比較結果が JSON で出る
- [ ] 複数条件の差分比較ができる
- [ ] 既存出力を壊していない
- [ ] 次に採用候補となる条件があるか判断できる

## Expected Output
- `reports/approx_rescore_comparison.json`

## Risk
- 小さな再スコアでは差が出ない可能性がある
- 比較条件が少なすぎると改善を見逃す可能性がある
- 本質が候補集合不足側にも残っているため、改善幅は限定的かもしれない

## Report Path
- `reports/approx_rescore_comparison.json`

## Notes for Agent
- 今回は比較のみ
- 候補集合は固定
- 改善が弱ければ次は候補集合不足側に戻る可能性がある
