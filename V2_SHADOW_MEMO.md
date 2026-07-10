# V2 Shadow Memo

## 目的
v1 を壊さずに、v2 の ingest -> evaluation を 1日単位で shadow 実行する。

## 実行

```powershell
py .\scripts\run_shadow_v2.py --date 2026-04-11 --dry-run
```

実DBへ書く場合:

```powershell
py .\scripts\run_shadow_v2.py --date 2026-04-11 --db-path data/v2/shadow_v2.duckdb
```

## 出力
- `reports/v2/shadow_summary_YYYYMMDD.json`
- `reports/v2/shadow_diff_YYYYMMDD.json`
- `reports/v2_shadow/shadow_v2_YYYYMMDD.csv`
- `reports/v2_shadow/shadow_v2_YYYYMMDD.md`
- `reports/v2_shadow/shadow_v2_YYYYMMDD.json`

## 判定
- `TARGET` 日だけ compare_possible を真にする
- `HOLD` 日は shadow 参照のみ
- `odds_coverage` が低い日は比較保留
- 比較対象日の主入力は `data/v2/comparison_target_days.csv`、Markdown は互換 fallback
- `v1` / `v2` の差分は `shadow_diff_YYYYMMDD.json` を見る
