# TASK-010

## Title
per_first_balanced_60 の配分パターンを比較し、exact改善を維持しながら副作用を減らせるか検証する

## Background
TASK-009 により、60件固定の選抜方式比較では `per_first_balanced_60` のみが
exact_hitrate, top1_hitrate, trifecta_avg_rank で有意な改善を示した。
一方で、in_set_rate の微悪化と not_in_60 の増加 (+6) も見られた。
このため、次は per_first_balanced_60 の配分パターンを少数比較し、
改善を維持しつつ副作用を小さくできるかを確認する。

## Objective
per_first_balanced_60 の枠配分パターンを比較し、
exact改善を維持しながら not_in_60 悪化を抑えられる条件があるかを検証する。

## Scope
- 比較対象は per_first_balanced_60 系のみ
- 少数の配分パターンを比較する
- baseline_top60 との差分を確認する
- 結果を reports/ に保存する

## Out of Scope
- 候補生成ロジック変更
- 60件超への拡張
- 新しい選抜方式追加
- 学習特徴量変更
- 本採用確定

## Target Files
- `src/eval/ablation_and_bottleneck.py`

## Constraints
- 変更は最小限
- 既存出力を壊さない
- 外部依存を追加しない
- 今回は比較のみ

## Implementation Notes
以下のような配分パターンを3〜5案比較する。
例:
- firstごとの最低枠を弱める
- firstごとの最低枠を強める
- 残り枠を global score 優先で埋める
- 残り枠を first内上位優先で埋める

各パターンについて少なくとも以下を比較する。
- exact_hitrate
- top1_hitrate
- in_set_rate
- trifecta_avg_rank
- not_in_60

baseline_top60 と、現行 per_first_balanced_60 の両方に対する差分があるとよい。

## Validation
```bash
$env:PYTHONPATH='.'; py src/eval/ablation_and_bottleneck.py
```

## Completion Criteria
- [ ] per_first_balanced_60 系の複数比較結果が出る
- [ ] 改善維持と副作用のバランスを確認できる
- [ ] JSONレポートが生成される
- [ ] 既存出力を壊していない

## Expected Output
- `reports/per_first_balanced_tuning.json`

## Risk
- exact改善が偶然で、配分を変えると消える可能性がある
- not_in_60 改善と exact 改善が両立しない可能性がある

## Report Path
- `reports/per_first_balanced_tuning.json`

## Notes for Agent
- 今回は per_first_balanced_60 の掘り下げだけ
- diverse_pair_60 は不採用寄りとして扱ってよい
- 併せて top1_hitrate の名称/定義ズレがあればメモに残す
