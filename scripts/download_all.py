"""
全データ一括ダウンロードスクリプト (オーケストレーター)

results / entries / fanbook を一括で取得する。

使い方:
  py scripts/download_all.py --start 20240101 --end 20251231
  py scripts/download_all.py --start 20240101 --end 20251231 --dry-run
  py scripts/download_all.py --start 20240101 --end 20251231 --types results entries
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from dl_common import (
    build_url, date_range, month_range, download_one,
    load_manifest, save_manifest, record_manifest,
    setup_logging, REQUEST_DELAY, SAVE_DIRS, logger,
)
from datetime import datetime


def download_daily_category(category, start, end, dry_run, delay, manifest):
    """日次データ (results/entries) のダウンロード。"""
    save_dir = SAVE_DIRS[category]
    stats = {"success": 0, "skip": 0, "fail": 0, "dry_run": 0, "error": 0}

    for dt in date_range(start, end):
        url, filename = build_url(category, dt)
        save_path = os.path.join(save_dir, filename)
        result = download_one(url, save_path, dry_run=dry_run)
        record_manifest(manifest, category, filename, result)
        stats[result["status"]] = stats.get(result["status"], 0) + 1
        if result["status"] == "success":
            time.sleep(delay)

    return stats


def download_fanbook(start, end, dry_run, delay, manifest):
    """月次データ (fanbook) のダウンロード。"""
    category = "fanbook"
    save_dir = SAVE_DIRS[category]
    # 日付範囲から月範囲に変換 (YYYYMMDD -> YYYYMM)
    start_month = start[:6]
    end_month = end[:6]
    stats = {"success": 0, "skip": 0, "fail": 0, "dry_run": 0, "error": 0}

    for dt in month_range(start_month, end_month):
        url, filename = build_url(category, dt)
        save_path = os.path.join(save_dir, filename)
        result = download_one(url, save_path, dry_run=dry_run)
        record_manifest(manifest, category, filename, result)
        stats[result["status"]] = stats.get(result["status"], 0) + 1
        if result["status"] == "success":
            time.sleep(delay)

    return stats


def main():
    parser = argparse.ArgumentParser(description="BOAT RACE 公式データ一括ダウンロード")
    parser.add_argument("--start", required=True, help="開始日 (YYYYMMDD)")
    parser.add_argument("--end", required=True, help="終了日 (YYYYMMDD)")
    parser.add_argument("--types", nargs="+",
                        default=["results", "entries", "fanbook"],
                        choices=["results", "entries", "fanbook"],
                        help="取得するデータ種別")
    parser.add_argument("--dry-run", action="store_true", help="実際にはDLしない")
    parser.add_argument("--delay", type=float, default=REQUEST_DELAY, help="リクエスト間隔(秒)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    setup_logging(args.verbose)
    manifest = load_manifest()
    all_stats = {}

    for dtype in args.types:
        logger.info(f"--- {dtype} ---")
        if dtype in ("results", "entries"):
            stats = download_daily_category(
                dtype, args.start, args.end, args.dry_run, args.delay, manifest
            )
        elif dtype == "fanbook":
            stats = download_fanbook(
                args.start, args.end, args.dry_run, args.delay, manifest
            )
        all_stats[dtype] = stats

    save_manifest(manifest)

    logger.info("=== Download All Summary ===")
    for dtype, stats in all_stats.items():
        non_zero = {k: v for k, v in stats.items() if v > 0}
        logger.info(f"  {dtype}: {non_zero}")


if __name__ == "__main__":
    main()
