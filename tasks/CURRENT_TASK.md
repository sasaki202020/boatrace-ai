# TASK-016

## Title
レース単位フィルタを追加し、買うレースを絞ったときの ROI / DD 改善余地を比較する

## Background
TASK-015 で資金配分ルール（flat / prob / EV比例）を比較したが、
flat_bet が最良で、比例配分は ROI・profit・max_drawdown のすべてで悪化した。
このため、現状の確率やEV推定は配分強弱に使うには弱く、
次は「何に厚く張るか」ではなく「どのレースを見送るか」を先に検証すべきである。

## Objective
候補方式 per_first_m12_global を固定したまま、
レース単位フィルタを導入して、買うレースを絞った場合の ROI / DD 改善余地を比較する。

## Scope
- 候補方式は per_first_m12_global に固定
- ベット方式は flat_bet に固定
- 複数のレース単位フィルタを比較する
- ROI / 回収率 / DD を比較する
- 結果を reports/ に保存する

## Out of Scope
- 資金配分変更
- 候補生成ロジック変更
- 買い目単位フィルタ
- 実運用反映

## Target Files
- src/eval/ablation_and_bottleneck.py

## Constraints
- 変更は最小限
- 既存出力を壊さない
- 外部依存を追加しない
- 今回はオフライン比較のみ

## Implementation Notes
比較対象は少数でよい。例:
1. no_filter
   - 全レース買う

2. first_gap_filter
   - 1着候補上位のスコア差が一定以上のレースのみ買う

3. top_score_gap_filter
   - 上位候補群のスコア差が一定以上のレースのみ買う

4. concentration_filter
   - 候補スコアが上位に集中しているレースのみ買う

各方式について少なくとも以下を出す。
- bought_races
- skipped_races
- total_stake
- total_return
- profit
- roi
- recovery_rate
- max_drawdown
- longest_losing_streak

baseline は no_filter とする。
差分:
- roi_diff
- profit_diff
- max_drawdown_diff

## Validation
```bash
$env:PYTHONPATH='.'; py src/eval/ablation_and_bottleneck.py
```

Reproducibility note:

```powershell
$env:PYTHONPATH='.'
py src/eval/ablation_and_bottleneck.py --proba-csv data/tmp/20260311_eval/today_win_proba.csv --features-csv data/tmp/20260311_eval/today_features.csv --backtest-csv reports/t016_backtest_race_results.csv
```

## Completion Criteria

- [x] 複数のレース単位フィルタを比較できる
- [x] ROI / DD の差分が出る
- [x] JSONレポートが生成される
- [x] 既存出力を壊していない

## Expected Output

- reports/race_filter_comparison.json

## Risk

- フィルタで対象レースを減らしすぎると分散が大きくなる
- 一時的にROIが改善してもサンプル数減少の影響で不安定になる可能性がある

## Notes for Agent

- 候補方式は固定
- ベット方式は flat_bet 固定
- まずはレース単位フィルタだけを見る
- 実行時の比較入力は 2026-03-11 スナップショットを使った
