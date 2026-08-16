# V2 Migration Memo

Phase 1 の目的は、Markdown の記録ではなく、事実データを DuckDB に退避できるようにすることです。

## 追加された v2 基盤
- `src/core/ids.py`
- `src/core/schemas.py`
- `src/storage/duckdb.py`
- `src/ingest/v2.py`
- `src/evaluation/v2_audit.py`
- `scripts/migrate_to_v2.py`

## 使い方

まず dry-run で読み込み確認をします。

```powershell
Set-Location -LiteralPath (git rev-parse --show-toplevel)
py .\scripts\migrate_to_v2.py --dry-run
```

実際に DuckDB へ書き込むときは、依存を入れてから実行します。

```powershell
Set-Location -LiteralPath (git rev-parse --show-toplevel)
py .\scripts\migrate_to_v2.py --db-path data/v2/boatrace_v2.duckdb
```

## テーブル
- `races`
- `entries`
- `results`
- `odds_snapshots`

## キー
- `race_id`
- `ticket_id`
- `snapshot_ts`

## 方針
- odds は上書きせず snapshot として保存する
- BUY/SKIP 判定は Phase 1 では作らない
- calibration は Phase 1 では主役にしない
- 既存の scripts は壊さない
