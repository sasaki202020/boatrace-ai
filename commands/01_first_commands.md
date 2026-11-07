# First Run Commands: 実データ適合の最初の1周

迷ったらこの順番でコマンドを打ってください。

## PHASE 1: 列名の監査 (Audit)
実データの列名が定義と一致しているか確認します。
```powershell
py src/ingest/inspect_raw_columns.py
```
→ 結果確認: `docs/raw_column_audit.md`

## PHASE 2: エイリアスの自動提案 (Suggest)
未知の列名に対し、どれが正解かAIに聞きます。
```powershell
py src/ingest/suggest_alias_candidates.py
```

## PHASE 3: 取り込みとバリデーション (Ingest)
エイリアスを修正（`config/column_aliases.json`）した後、実行します。
```powershell
py src/ingest/build_processed.py
```
→ 結果確認: `data/processed/validation_summary.json`

---
**NOTE**: `validation_summary.json` の `status` が `"PASS"` になるまで、決して `Gate 3` 以降へ進んではいけません。
成功したら、満を持して `py master_run.py` で全行程を走らせてください。
