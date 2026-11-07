"""
BOAT RACE 公式データ自動取得スクリプト

競走成績と番組表を公式サイト (mbrace.or.jp) からダウンロードし、
LZH解凍 → 固定長テキスト解析 → CSV変換まで一気に行う。

使い方:
  py src/data_fetch/fetch_official.py --type results --start 2025-01-01 --end 2025-01-31
  py src/data_fetch/fetch_official.py --type entries --date 2026-03-12
"""
import argparse
import io
import os
import time
from datetime import datetime, timedelta

import lhafile
import requests

# ─── URL パターン ───
# 月ディレクトリ (YYYYMM) が必要: e.g. /od2/K/202503/k250310.lzh
BASE_URLS = {
    "results": "https://www1.mbrace.or.jp/od2/K/{yyyymm}/k{yymmdd}.lzh",
    "entries": "https://www1.mbrace.or.jp/od2/B/{yyyymm}/b{yymmdd}.lzh",
    "fan":     "https://www1.mbrace.or.jp/od2/fan/fan{yymm}.lzh",
}

# 展示タイム・オッズのスクレイピング用URL（別途実装予定）
SCRAPE_URLS = {
    "pre_race": "https://boatrace.jp/owpc/pc/race/beforeinfo?jcd={jcd}&rno={rno}&hd={yyyymmdd}",
    "odds_3t":  "https://boatrace.jp/owpc/pc/race/odds3t?jcd={jcd}&rno={rno}&hd={yyyymmdd}",
}

SAVE_DIRS = {
    "results": "data/raw/official/results",
    "entries": "data/raw/official/entries",
    "fan":     "data/raw/official/fan",
}


def _date_range(start_str, end_str):
    """日付範囲ジェネレータ"""
    start = datetime.strptime(start_str, "%Y-%m-%d")
    end = datetime.strptime(end_str, "%Y-%m-%d")
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _download_lzh(url):
    """LZH ファイルをダウンロードしてバイト列を返す。404 なら None。"""
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200:
            return resp.content
        elif resp.status_code == 404:
            return None
        else:
            print(f"  HTTP {resp.status_code}: {url}")
            return None
    except requests.RequestException as e:
        print(f"  ERROR: {e}")
        return None


def _extract_lzh(lzh_bytes):
    """LZH バイト列を解凍し、ファイル名→内容のdictを返す。"""
    extracted = {}
    try:
        f = lhafile.Lhafile(io.BytesIO(lzh_bytes))
        for info in f.infolist():
            data = f.read(info.filename)
            extracted[info.filename] = data
    except Exception as e:
        print(f"  LZH extract error: {e}")
    return extracted


def fetch_and_save(data_type, target_date, save_dir, force=False):
    """1日分のデータを取得・解凍・保存する。"""
    yymmdd = target_date.strftime("%y%m%d")
    yymm = target_date.strftime("%y%m")
    yyyymm = target_date.strftime("%Y%m")
    url = BASE_URLS[data_type].format(yymmdd=yymmdd, yyyymm=yyyymm, yymm=yymm)

    # 既存チェック (LZH内のファイル名が不明なため、基本は日付ベースのプレフィックスで判定)
    # 簡易的に、保存先ディレクトリをチェックし、KYYMMDD.TXT / BYYMMDD.TXT があればスキップ
    prefix = url.split("/")[-1].replace(".lzh", "").upper() # k240101 -> K240101
    txt_name = f"{prefix}.TXT"
    output_path = os.path.join(save_dir, txt_name)
    
    if not force and os.path.exists(output_path):
        print(f"  Skip (already exists: {output_path})")
        return 0

    print(f"Fetching: {url}")
    lzh_bytes = _download_lzh(url)
    if lzh_bytes is None:
        print(f"  Skip (no data for {target_date.strftime('%Y-%m-%d')})")
        return 0

    files = _extract_lzh(lzh_bytes)
    if not files:
        print(f"  Skip (empty archive)")
        return 0

    os.makedirs(save_dir, exist_ok=True)
    saved = 0
    for fname, content in files.items():
        dst_path = os.path.join(save_dir, fname)
        with open(dst_path, "wb") as out:
            out.write(content)
        saved += 1
        print(f"  Saved: {dst_path} ({len(content)} bytes)")

    return saved


def main():
    parser = argparse.ArgumentParser(description="BOAT RACE 公式データ取得")
    parser.add_argument("--type", required=True, choices=["results", "entries", "fan"],
                        help="取得データの種類 (results=競走成績, entries=番組表, fan=選手期別成績)")
    parser.add_argument("--date", help="単日取得 (YYYY-MM-DD)")
    parser.add_argument("--start", help="範囲開始日 (YYYY-MM-DD)")
    parser.add_argument("--end", help="範囲終了日 (YYYY-MM-DD)")
    parser.add_argument("--force", action="store_true",
                        help="既存ファイルがあっても再取得する")
    parser.add_argument("--delay", type=float, default=1.0,
                        help="リクエスト間隔（秒）。サーバ負荷軽減のため")
    args = parser.parse_args()

    save_dir = SAVE_DIRS[args.type]

    if args.date:
        dates = [datetime.strptime(args.date, "%Y-%m-%d")]
    elif args.start and args.end:
        dates = list(_date_range(args.start, args.end))
    else:
        print("ERROR: --date または --start/--end を指定してください。")
        return

    total_files = 0
    for dt in dates:
        count = fetch_and_save(args.type, dt, save_dir, force=args.force)
        total_files += count
        if count > 0:
            time.sleep(args.delay)

    print(f"\n=== 取得完了: {total_files} files saved to {save_dir} ===")


if __name__ == "__main__":
    main()
