# TASK-004

## Title
同一1着の2着3着順序補正を小さく導入して候補内順位を改善する

## Background
TASK-003 により、候補集合内に入った実三連単 317 件の順位低下要因を分解した結果、
`second_third_order_issue=158 (49.84%)` が最大比率だった。
このため、次は候補生成数や候補集合自体は変えず、
同一1着・同一2艇3艇セット内での 2着3着順序補正を小さく試すのが最も自然である。

## Objective
既存候補集合の範囲内で、同一1着・同一2艇3艇セットの順序違い候補に対する
軽微な順序補正を導入し、候補内順位の改善余地を検証する。

## Scope
- 三連単候補順位づけの箇所に小さな順序補正を追加する
- `(1,2,3)` と `(1,3,2)` のような順序違い候補の順位差に影響する補正を試す
- 補正あり / 補正なしで比較レポートを出す
- exact / top1 / 平均順位 / rank帯分布の差分を確認する

## Out of Scope
- 候補生成ロジックの全面変更
- 特徴量追加
- 学習データ変更
- `max_trifecta_combinations` の変更
- `approx_prob` 全面再設計

## Target Files
- `src/eval/ablation_and_bottleneck.py`
- 必要なら順序スコア算出箇所の最小変更対象1ファイル

## Constraints
- 変更は最小限
- 補正はオン/オフ比較できる形にする
- 既存JSON出力は壊さない
- 外部依存は追加しない
- 効果がなければ戻しやすい実装にする

## Implementation Notes
- 同一1着・同一2艇3艇セットの順序違い候補ペアを対象にする
- 既存スコアに対し、小さな補正項を加える
- 補正ロジックは明示的に分離する
- 補正前後で以下を比較する
  - trifecta exact 相当指標
  - top1
  - 平均順位
  - rank_1_5 / rank_6_10 / rank_11_20 / rank_21_40 / rank_41_60 / not_in_60
- 補正が悪化した場合も結果を残す

## Validation
```bash
$env:PYTHONPATH='.'; py src/eval/ablation_and_bottleneck.py
```

## Completion Criteria
- [ ] 順序補正あり/なしの比較結果が出る
- [ ] 既存出力を壊していない
- [ ] 効果の有無が数値で確認できる
- [ ] 補正ロジックが分離されていて戻しやすい

## Expected Output
- `reports/order_adjustment_comparison.json`

## Risk
- 順序補正で一部レースは改善しても全体では悪化する可能性がある
- 補正項が強すぎると他順位帯を壊す可能性がある
- 本質が `approx_prob` 側にあるケースでは効果が限定的

## Report Path
- `reports/order_adjustment_comparison.json`

## Notes for Agent
- 今回は「小さく試す」が目的
- 大改修しない
- 効かなければ次に `scoring_issue` 側へ進む
