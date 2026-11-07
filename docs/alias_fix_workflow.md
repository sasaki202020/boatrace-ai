# Alias Fix Workflow

本ワークフローは、Ingestion 前に列名のエイリアスを確定させるための定型手順である。

## 手順 1: 全データの監査
1. 実データ（CSV）を `data/raw/official/` に配置。
2. `python src/ingest/inspect_raw_columns.py` を実行。
3. `docs/raw_column_audit.md` を開き、**Unknown Columns** と **Missing Canonical Columns** を確認。

## 手順 2: エイリアスの追加
1. `config/column_aliases.json` を開く。
2. **Unknown Columns** にリストされた列名が、どの **Canonical Column**（スキーマ上の正式名）に相当するか判断。
3. 該当する項目のリストに、新しい列名を追加する。

## 手順 3: 再監査と確定
1. 再度 `python src/ingest/inspect_raw_columns.py` を実行。
2. 必要な列がすべて **Mapped** に入り、**Missing (Required)** がゼロになったことを確認。

## 手順 4: 本処理の実行
1. `python src/ingest/build_processed.py` を実行し、正規化データを生成。
