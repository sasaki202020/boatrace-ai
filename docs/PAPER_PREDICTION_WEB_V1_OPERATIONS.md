# 紙上予想Web v1 運用手順

## 1. 朝・予想生成
`scripts\run_paper_ops_preflight.bat YYYY-MM-DD`

`scripts\run_paper_ops_morning.bat YYYY-MM-DD`

## 2. Web確認
`http://localhost:5000/predictions`

## 3. 夜・結果照合
`scripts\run_paper_ops_evening.bat YYYY-MM-DD`

## 4. 監視更新
`scripts\run_paper_ops_monitor.bat YYYY-MM-DD`

## 5. 1コマンドで回す場合
`scripts\run_daily_paper_ops_check.bat YYYY-MM-DD`

## 6. 見るファイル
- `reports/daily/YYYY-MM-DD/daily_paper_ops_check.json`
- `reports/predictions/YYYY-MM-DD/prediction_sheet.json`
- `reports/predictions/YYYY-MM-DD/prediction_review.json`
- `reports/consensus/YYYY-MM-DD/consensus_sheet.json`
- `reports/monitoring/live_operation_summary.json`
- `reports/monitoring/tuning_gate.json`

## 7. 判定ルール
- `ready` なら運用
- `future_date_not_ready` なら待機
- `source_not_ready` は失敗ではない
- `result_data_missing` は失敗ではない
- `pipeline_failure` だけ要調査
- `BUY 0` は失敗ではない
- 実賭け禁止
- 合意スコアは表示専用
- 外部予想は BUY 判定に未使用
