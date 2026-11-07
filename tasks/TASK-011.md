# TASK-011

## Title
per_first_m12_global の改善が別期間でも再現するか確認する

## Background
TASK-010 の配分比較で `per_first_m12_global` が最有力だった。
baseline 比で
- exact_hitrate: +0.01
- in_set_rate: +0.0852
- not_in_60: -51
- trifecta_avg_rank: -13.34
を示した。
ただし現評価期間ベースの結果であり、再現性確認が必要である。

## Objective
per_first_m12_global を複数期間で再評価し、改善が再現するか確認する。

## Scope
- baseline_top60 と per_first_m12_global を複数期間で比較する
- exact_hitrate
- in_set_rate
- not_in_60
- trifecta_avg_rank
を比較する
- 結果を reports/ に保存する

## Out of Scope
- 新しい選抜方式追加
- 候補生成ロジック変更
- 実運用反映

## Target Files
- `src/eval/ablation_and_bottleneck.py`

## Constraints
- 変更は最小限
- 既存出力を壊さない
- 外部依存を追加しない
- 今回は再現確認のみ

## Implementation Notes
- 比較対象は `baseline_top60` と `per_first_m12_global` の2方式に限定する。
- 期間は少なくとも2〜3ウィンドウに分ける。
- 各ウィンドウで以下を出す。
  - exact_hitrate
  - in_set_rate
  - not_in_60
  - trifecta_avg_rank
- baseline 比差分をウィンドウごとに保存する。
- 可能なら「改善再現あり/なし」の簡易判定を入れる。

## Validation
```bash
$env:PYTHONPATH='.'; py src/eval/ablation_and_bottleneck.py
```

## Completion Criteria
- [ ] 複数期間で2方式の比較結果が出る
- [ ] ウィンドウ別の baseline 差分が確認できる
- [ ] JSONレポートが生成される
- [ ] 既存出力を壊していない

## Expected Output
- `reports/per_first_m12_global_repro.json`

## Risk
- 改善が特定期間だけの可能性がある
- 期間分割の粒度で結果がぶれる可能性がある

## Report Path
- `reports/per_first_m12_global_repro.json`

## Notes for Agent
- 今回は再現確認だけ
- 方式は2本に固定
- 改善が弱ければ採用保留を明記する
