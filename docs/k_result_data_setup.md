# K Result Data Setup

## 命名ルール

- `KYYMMDD.TXT`
- 例: `K260404.TXT` は `2026-04-04`

## 置き場所

- 入力 inbox: `data/inbox/k_results/`
- `data/raw/official/results/`

不足 K ファイルは `data/inbox/k_results/` に置き、`import_k_results` で `data/raw/official/results/` へ反映します。

## 取り込みコマンド

```powershell
py -m src.pipeline.import_k_results --input-dir data/inbox/k_results --target-dir data/raw/official/results
py -m src.ingest.official_k_loader --start-date 20260401 --end-date 20260425 --input-dir data/raw/official/results
py -m src.pipeline.collect_historical_inputs --start-date 20260401 --end-date 20260425 --jcd all --stages result_txt --input-dir data/raw/official/results
```

## 不足 K ファイル一覧

```powershell
py -m src.evaluation.export_missing_k_checklist --start-date 20260401 --end-date 20260425 --input-dir data/raw/official/results
```

## 手動投入手順

1. 不足 K ファイルを取得する
2. `data/inbox/k_results/` に置く
3. `scripts/check_k_inbox.bat` で inbox 状態を確認する
4. `scripts/import_k_results.bat` を実行する
5. `scripts/import_and_refresh_k_results.bat` を実行する
6. `reports/backtest/k_result_import_manifest.json` を確認する
7. `reports/backtest/20260401_20260425_k_refresh_summary.json` を確認する
8. `backfillSettledBetCount` が増えたか確認する
9. `canTuneWithBackfill` が `true` になるまで BUY 閾値は変更しない

## 投入前チェック

```powershell
py -m src.pipeline.check_k_inbox --input-dir data/inbox/k_results --start-date 20260401 --end-date 20260425
```

- `recommendedNextAction=place_missing_k_files_in_inbox` のときは、まだ投入待ちです。
- `importTargetCount>0` のときだけ取り込み対象があります。
- `skipTargetCount>0` は既存と重複しているファイルです。
- `invalidTargetCount>0` はファイル名や対象外判定に問題があります。

## backfill 再評価

```powershell
py -m src.evaluation.audit_k_result_coverage --start-date 20260401 --end-date 20260425 --input-dir data/raw/official/results
py -m src.pipeline.refresh_k_backtest --start-date 20260401 --end-date 20260425 --jcd all --input-dir data/raw/official/results --stake 100
py -m src.evaluation.backtest_range --start-date 20260401 --end-date 20260425 --jcd all --stake 100 --prediction-source backfill
py -m src.pipeline.import_and_refresh_k_results --input-dir data/inbox/k_results --start-date 20260401 --end-date 20260425 --jcd all --stake 100
py -m src.pipeline.check_k_inbox --input-dir data/inbox/k_results --start-date 20260401 --end-date 20260425
```

## `canTuneWithBackfill` の基準

- `backfillSettledBetCount >= 300`
- `settlementCoverage >= 0.5`
- `leakageGuardStatus == ok`
- `resultSourceBreakdown` に `official_txt_k` または `official_html` が十分ある

## K ファイルを追加した後の順番

1. `audit_k_result_coverage`
2. `export_missing_k_checklist`
3. `import_k_results`
4. `official_k_loader`
5. `collect_historical_inputs --stages result_txt`
6. `audit_historical_inputs`
7. `backtest_range --prediction-source backfill`
8. `compare_prediction_sources`
9. `refresh_k_backtest`
10. `import_and_refresh_k_results`

`BUY` 閾値や score weight はこの段階では変更しません。K 結果は settle / backtest 専用です。
