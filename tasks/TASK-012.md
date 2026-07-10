# TASK-012

## Title
per_first_m12_global を本体候補の top60 選抜ロジックとして切替可能に実装する

## Background
TASK-009〜011 の比較で、per_first_m12_global が最も安定して改善した。
比較専用コードだけでなく、本体候補生成フローで切替可能にして再現性を確認する。

## Objective
per_first_m12_global を本体の候補選抜候補として実装し、baseline_top60 と設定切替できる状態にする。

## Scope
- 本体候補選抜フローに per_first_m12_global を追加
- config で baseline/per_first を切替可能にする
- 既存評価で整合確認レポートを出力

## Out of Scope
- 新方式追加
- 候補生成式の全面変更
- 資金管理ロジック変更

## Target Files
- `src/strategy/generate_trifecta_candidates.py`
- `src/eval/ablation_and_bottleneck.py`
- `config/strategy_config.json`

## Expected Output
- `reports/per_first_m12_global_integration_check.json`
