# Official File Checklist

実データ（公式ダウンロードファイル）を受領し、`data/raw/official/` に配置する際の事前・事後点検リスト。

## 事前確認
- [ ] ファイル形式が UTF-8 (BOMなし) または Shift-JIS (CP932) で統一されているか
- [ ] ファイル名に半角スペースや特殊記号が含まれていないか
- [ ] 直近のレースが含まれているか（日付の確認）

## 配置後の実行
- [ ] `src/ingest/inspect_raw_columns.py` を実行
- [ ] `docs/raw_column_audit.md` で Unknown Columns がゼロであることを確認
- [ ] 必須項目（race_id, lane, racer_id 等）がすべて Mapped であるか確認

## 解析後の確認 (build_processed 実行後)
- [ ] `data/processed/validation_summary.json` で FATAL がゼロか
- [ ] 着順 (finish_position) に数値以外の異常値が大量に残っていないか
- [ ] レースIDの重複が報告されていないか
