# V2 Batch Evaluation Memo

## 目的
v2 の比較対象日だけをまとめて回し、HOLD を主集計に混ぜずに batch 評価する。

## 実行

```powershell
py .\scripts\run_batch_evaluation_v2.py --mode target-only
```

明示日付で回す場合:

```powershell
py .\scripts\run_batch_evaluation_v2.py --mode explicit-dates --dates 2026-04-03,2026-04-04
```

dry-run:

```powershell
py .\scripts\run_batch_evaluation_v2.py --mode target-only --dry-run
```

## 出力
- `reports/v2/batch_results.csv`
- `reports/v2/batch_summary.json`
- `reports/v2/batch_failures.csv`

## 判定
- `TARGET` のみを主集計に入れる
- `HOLD` は reference_only として別扱い
- `EXCLUDE` は回さない
- `raw_incomplete`, `missing result`, `missing odds` は failure として残す
