# TASK-015

## Title
per_first_m12_global を固定し、資金配分ルール（定額 / 比例 / EV比例）をオフライン比較する

## Background
TASK-014 で ROI・回収率・最大ドローダウンの評価基盤を整備し、
per_first_m12_global が baseline を上回ることを確認した。
次は候補選抜を固定したまま、資金配分（ベッティング）ルールの違いが
収益とリスクに与える影響を比較する。

## Objective
同一候補（per_first_m12_global）に対して、複数の資金配分ルールを適用し、
ROI・回収率・ドローダウンの差分を定量比較する。

## Scope
- 候補方式は per_first_m12_global に固定
- 複数の資金配分ルールを比較する
- 既存の ROI 評価ロジックを再利用する
- 結果を reports/ に保存する

## Out of Scope
- 候補生成ロジック変更
- 候補数変更
- 実運用資金管理（資金曲線最適化）
- パラメータ最適化

## Target Files
- src/eval/ablation_and_bottleneck.py

## Constraints
- 変更は最小限
- 既存出力を壊さない
- 外部依存を追加しない
- オフライン比較のみ

## Implementation Notes

### 資金配分ルール（最小3種）
1. flat_bet
   - 各買い目に同額（例: 1単位）

2. prob_proportional
   - approx_prob に比例して配分
   - 正規化して総ベット額を一定にする

3. ev_proportional
   - approx_prob × 払戻期待値ベース（簡易EV）で配分
   - マイナス値は0クリップ

※ 実装は簡易でよい（相対比較が目的）

### 出力指標（各方式）
- total_stake
- total_return
- profit
- roi
- recovery_rate
- hit_rate
- max_drawdown
- longest_losing_streak

### 差分
- flat_bet を baseline として差分を出す
  - roi_diff
  - max_drawdown_diff
  - profit_diff

## Validation
```bash
$env:PYTHONPATH='.'; py src/eval/ablation_and_bottleneck.py
```

## Completion Criteria

- [ ] 3つの資金配分方式で比較できる
- [ ] ROI・DDが出力される
- [ ] 差分が確認できる
- [ ] JSONレポート生成
- [ ] 既存出力を壊していない

## Expected Output

- reports/betting_strategy_comparison.json

## Risk

- EV推定が粗いため過学習的に見える可能性
- 比例配分で極端な賭けが発生する可能性（必要ならクリップ）

## Notes for Agent

- 候補選抜は固定
- 評価だけ追加
- シンプル実装優先
