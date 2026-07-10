# データ収集スクリプト 使い方ガイド

## 概要

`scripts/` 配下のダウンロードスクリプト群を使用して、BOAT RACE 公式サイトから過去データを一括取得します。

## 保存先

| 種別 | ファイル形式 | 保存先 |
| :--- | :--- | :--- |
| 競走成績 | `k{YYMMDD}.lzh` | `data/raw/official/results/` |
| 番組表   | `b{YYMMDD}.lzh` | `data/raw/official/entries/` |
| 選手期別 | `fan{YYMM}.lzh` | `data/raw/official/fanbook/` |

ログ: `data/raw/official/logs/download_manifest.json`

## コマンド一覧

### 一括取得

```bash
# 2024年1月〜2025年12月の全データ
py scripts/download_all.py --start 20240101 --end 20251231

# dry-run（実際にはDLしない / URL確認のみ）
py scripts/download_all.py --start 20240101 --end 20251231 --dry-run

# 競走成績と番組表だけ取得
py scripts/download_all.py --start 20240101 --end 20251231 --types results entries
```

### 種別ごと

```bash
# 競走成績
py scripts/download_results.py --start 20240101 --end 20241231

# 番組表
py scripts/download_entries.py --start 20240101 --end 20241231

# レーサー期別成績（月単位: YYYYMM）
py scripts/download_fanbook.py --start 202401 --end 202612
```

### 共通オプション

| オプション | 説明 | デフォルト |
| :--- | :--- | :--- |
| `--dry-run` | 実際にはDLせず、URLと保存先を表示 | off |
| `--delay N` | リクエスト間隔（秒） | 1.0 |
| `-v` | 詳細ログ出力 | off |

## マニフェスト

`download_manifest.json` に全ダウンロード履歴が記録されます。

```json
{
  "results": {
    "k240101.lzh": {
      "status": "success",
      "url": "https://www1.mbrace.or.jp/od2/K/202401/k240101.lzh",
      "save_path": "data/raw/official/results/k240101.lzh",
      "http_status": 200,
      "bytes": 12345,
      "timestamp": "2026-03-13T17:30:00"
    }
  }
}
```

ステータス一覧: `success` / `skip`（既存） / `fail`（HTTP error） / `error`（接続不可） / `dry_run`

## fan 追加取得の準備 (欠損低減向け)

- 取得対象期間（今回の整理）: `202401` 〜 `202603`
- 根拠: 現在の学習系データ期間（`2024-01-01` 〜 `2026-03-11`）を月次 fanbook でカバーするため
- 保存先ルール: `data/raw/official/fanbook/fan{YYMM}.lzh`

実行前の確認（取得はしない）:

```bash
py scripts/download_fanbook.py --start 202401 --end 202603 --dry-run
```

次回の実取得コマンド（大規模取得を開始するタイミングで実行）:

```bash
py scripts/download_fanbook.py --start 202401 --end 202603 --delay 1.0
```

## 注意事項

- サーバー負荷軽減のため `--delay 1.0` がデフォルト。大量取得時は `2.0` 以上を推奨。
- ダウンロード済みファイルは自動スキップされます（再取得したい場合はファイルを削除）。
- LZH の解凍は別ステップです（`src/data/extract_official.py` を使用）。
