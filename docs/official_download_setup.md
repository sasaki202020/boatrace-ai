# Official Download Setup

## 目的

BOAT RACE 公式ダウンロード導線に沿って、以下の raw 配布物を固定パスへ保存する。

- 競走成績: `data/raw/official/results`
- 番組表: `data/raw/official/entries`
- レーサー期別成績 LZH: `data/raw/official/fanbook`
- ダウンロードログ: `data/raw/official/logs/download_manifest.json`

この実装はダウンロード専用であり、解凍・パース・特徴量生成・モデル更新は含めない。

## 公式導線

- 競走成績ダウンロード: `https://www1.mbrace.or.jp/od2/K/dindex.html`
- 番組表ダウンロード: `https://www1.mbrace.or.jp/od2/B/dindex.html`
- ダウンロード・他: `https://boatrace.jp/owpc/pc/extra/data/download.html`
- レーサー期別成績 LZH 例: `https://boatrace.jp/static_extra/pc_static/download/data/kibetsu/fan2510.lzh`

## CLI

```powershell
py scripts/download_results.py --start 2024-01-01 --end 2024-12-31
py scripts/download_entries.py --start 2024-01-01 --end 2024-12-31
py scripts/download_fanbook.py --start 2024-01 --end 2024-12
py scripts/download_all.py --start-date 2024-01-01 --end-date 2024-12-31
```

オプション:

- `--force`: 既存ファイルがあっても再取得する
- `--dry-run`: HTTP 取得は行わず、manifest に試行だけ記録する
- `--delay`: リクエスト間隔。既定値は `1.0`
- `--timeout`: HTTP タイムアウト秒。既定値は `30.0`

## 命名規則

- results: `kYYMMDD.lzh`
- entries: `bYYMMDD.lzh`
- fanbook: `fanYYMM.lzh`

保存先は dataset ごとに固定で、他の命名規則には変換しない。

## Manifest

`download_manifest.json` には各試行を追記する。主な項目:

- `dataset`
- `target_key`
- `download_url`
- `status`
- `http_status`
- `saved_path`
- `byte_size`
- `checksum_sha256`
- `message`

ステータス例:

- `success`
- `skipped_existing`
- `dry_run`
- `http_error`
- `network_error`
- `error`

## 範囲指定の考え方

- results / entries は日次で `--start YYYY-MM-DD --end YYYY-MM-DD`
- fanbook は月次で `--start YYYY-MM --end YYYY-MM`
- `download_all.py` は fanbook の開始月・終了月を省略した場合、`start-date` と `end-date` の月から自動推定する

## 非対象

- LZH の解凍
- TXT への変換
- parser / model / strategy との接続
- 旧閲覧ページを前提にしたスクレイピング
