# Developer Setup And Gate Run

## 1. Environment Setup

```powershell
Set-Location <repository-root>
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

`py` launcher が使えない環境では Python 実体を直接指定:

```powershell
py -3.12 -m pip install -r requirements.txt
```

## 2. Gate Mapping

- Gate 1 (Schema/Contract): `schemas/*.schema.md` と `config/source_manifest.json` / `config/column_aliases.json`
- Gate 2.5 (Alias Audit): `src/ingest/inspect_raw_columns.py`, `src/ingest/suggest_alias_candidates.py`
- Gate 2 (Ingestion): `src/ingest/build_processed.py`
- Gate 3 (Feature): `src/features/build_features.py`
- Gate 4 (Model): `src/models/train_win_model.py`, `src/models/predict_win_proba.py`
- Gate 5 (Strategy): `src/strategy/generate_trifecta_candidates.py`, `src/strategy/evaluate_ev_and_skip.py`
- Gate 6 (Report): `src/report/build_daily_report.py`

## 3. Recommended Execution Order

```powershell
py src/ingest/inspect_raw_columns.py
py src/ingest/build_processed.py
py src/features/build_features.py
py src/models/train_win_model.py
py src/models/predict_win_proba.py
py src/strategy/generate_trifecta_candidates.py
py src/strategy/evaluate_ev_and_skip.py
py src/report/build_daily_report.py
```

一括実行:

```powershell
py master_run.py
```

## 4. Gate 1 Checkpoint (Operational)

- `data/processed/validation_summary.json` が出力されること
- `fatal_errors` が空、または許容可能な内容であること
- `historical_races.csv` / `today_races.csv` が生成されること
- スキーマ定義は `schemas/historical_races.schema.md` / `schemas/today_races.schema.md` を参照すること
