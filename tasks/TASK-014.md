# TASK-014

## Title
ROI・回収率・ドローダウン主体の評価レポートを追加する

## Background
TASK-013 で指標名称/定義のズレを整理した後、次は「当たり率」中心ではなく
「利益が残るか」を判断できる評価軸へ移行する必要がある。
現状は精度系指標の比較が中心で、実運用判断に必要な損益系KPIが不足している。

## Objective
既存出力を使って、ROI・回収率・ドローダウン主体で比較可能な評価レポートを追加する。

## Scope
- 評価レポートに以下を追加
  - 投資額合計
  - 払戻額合計
  - 利益（払戻 - 投資）
  - ROI（利益 / 投資）
  - 回収率（払戻 / 投資）
  - 最大ドローダウン（累積損益ベース）
  - BUY件数・的中件数・的中率
- 既存方式比較（baseline と採用候補）で同じ指標を並べる
- 出力を `reports/` に保存

## Out of Scope
- 資金配分ロジック変更（定額/比例/EV比例の導入）
- 候補生成ロジック変更
- モデル再学習
- 閾値変更

## Target Files
- `src/eval/ablation_and_bottleneck.py`
- 必要なら `src/eval/` 配下に補助スクリプト1ファイル

## Constraints
- 変更は最小限
- 既存JSON出力は壊さない
- 外部依存を追加しない
- まずは分析・評価のみ（戦略本体は変えない）

## Implementation Notes
- データは既存の backtest 系出力を優先利用する
- 1レースあたり投資額は現行前提に合わせる（不明な場合は明示して固定値を採用）
- ドローダウンは時系列（date, race）順の累積損益から計算
- 方式比較は最低でも以下
  - baseline_top60
  - per_first_m12_global

## Validation
```bash
$env:PYTHONPATH='.'; py src/eval/ablation_and_bottleneck.py
```

## Completion Criteria
- [ ] ROI/回収率/ドローダウンを含むレポートが生成される
- [ ] baseline と per_first_m12_global の差分が確認できる
- [ ] 既存レポート出力を壊していない

## Expected Output
- `reports/roi_drawdown_evaluation.json`

## Risk
- 投資額前提が固定値の場合、実運用金額とは乖離する
- 結果データの欠損行があると ROI が不安定になる

## Report Path
- `reports/roi_drawdown_evaluation.json`

## Notes for Agent
- 今回は評価軸の追加のみ
- 次タスクで資金配分ロジック比較へ進む
