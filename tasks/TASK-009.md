# TASK-009

## Title
同じ60件のまま候補の残し方を変えたオフライン比較を行う

## Background
TASK-008 により、max_trifecta_combinations を 60/80/100/120 に増やしても
exact_hitrate と top1_hitrate は改善しなかった一方、
in-set rate は大きく改善した。
これは候補生成自体は一定機能しているが、最終60件の選抜方法に問題がある可能性を示す。
次は、件数を増やすのではなく、同じ60件のまま残し方を変えた場合の改善余地を比較する。

## Objective
候補件数を60に固定したまま、異なる選抜方式で最終60件を構成し、
主要KPIの差分を比較して、選抜改善の余地を確認する。

## Scope
- 候補生成ロジックは変更しない
- 生成された候補全体から60件を選ぶ方式だけを比較する
- 少数の選抜方式を比較する
- 結果を reports/ に保存する

## Out of Scope
- 上限値変更
- 候補生成ロジック変更
- 学習特徴量変更
- モデル変更
- 本体採用確定

## Target Files
- `src/eval/ablation_and_bottleneck.py`

## Constraints
- 変更は最小限
- 既存出力は壊さない
- 外部依存は追加しない
- 今回は比較のみ
- 比較方式は3案程度に絞る

## Implementation Notes
baseline は現行の `approx_prob` 上位60とする。

比較方式の例:
1. `baseline_top60`
   - 現行どおり単純上位60

2. `per_first_balanced_60`
   - 1着候補ごとに最低枠を持たせつつ残りをスコア順で埋める

3. `diverse_pair_60`
   - 同一1着・同一ペアへの偏りを少し抑え、2着3着ペアの多様性を持たせる

方式名は実装に合わせて調整してよいが、
「単純上位60」と異なる考え方の比較を少なくとも2案入れる。

各方式について少なくとも以下を比較する。
- exact_hitrate
- top1_hitrate
- trifecta_avg_rank
- not_in_60 相当
- in_set_rate
- rank帯分布

baseline 差分を JSON にまとめる。

## Validation
```bash
$env:PYTHONPATH='.'; py src/eval/ablation_and_bottleneck.py
```

## Completion Criteria
- [ ] 60件固定の複数選抜方式比較ができる
- [ ] baseline差分が確認できる
- [ ] JSONレポートが生成される
- [ ] 既存出力を壊していない

## Expected Output
- `reports/top60_selection_comparison.json`

## Risk
- 比較方式が弱いと差が出ない可能性がある
- 多様性を入れすぎると top1 が逆に悪化する可能性がある
- 実運用に必要な買い目コスト最適化は別タスクになる

## Report Path
- `reports/top60_selection_comparison.json`

## Notes for Agent
- 今回は比較のみ
- 件数は必ず60固定
- 候補生成ロジックは変更しない
- 次タスクで採用候補を1案に絞る
