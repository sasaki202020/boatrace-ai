# CURRENT STATUS

## 本番目的
- 競艇AIを毎日自動運用し、`frozen_bets` / `result` / `settlement` / `daily_report` / `monitoring` を継続蓄積する。
- 目的は予想改善ではなく、運用安定性と記録品質の維持。

## 本番入口コマンド
- `py -m src.pipeline.run_daily_pre_race --date YYYY-MM-DD`
- `py -m src.pipeline.run_daily_odds_refresh --date YYYY-MM-DD`
- `py -m src.pipeline.run_daily_post_race --date YYYY-MM-DD`
- `py -m src.evaluation.run_day_evaluation_v2 --date YYYY-MM-DD`
- `py -m src.evaluation.run_batch_evaluation_v2 --mode explicit-dates --dates YYYY-MM-DD`

## 本番成果物
- `data/frozen_bets/YYYYMMDD/frozen_bets_all.json`
- `data/ui/YYYYMMDD/raceyosou_XX.json`
- `reports/daily/YYYYMMDD_summary.json`
- `reports/daily/YYYYMMDD_settlement.json`
- `reports/monitoring/YYYYMMDD_health_check.json`
- `reports/monitoring/live_operation_summary.json`
- `reports/monitoring/tuning_gate.json`

## 削除禁止
- `data/raw/official/**`
- `models/**`
- `config/**`

## 今は変更禁止
- BUY閾値変更禁止。
- `baseline_score_model` 重み変更禁止。
- 新しい予想ロジック追加禁止。

## 毎日見るべき指標
- `settledBetCount`
- `unresolvedBetCount`
- `resultMissingCount`
- `errorCount`
- `liveSettledBetCount`
- `liveSettlementCoverage`
- `canTuneWithLiveOnly`
- `canTuneWithBackfill`

## archive / review 運用方針
- archive はまだ dry-run のみ（実移動なし）。
- `review_required` 8件は手動確認が必要。
