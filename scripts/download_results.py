"""
競走成績 (K*.LZH) ダウンロードスクリプト

使い方:
  py scripts/download_results.py --start 20240101 --end 20241231
  py scripts/download_results.py --start 20240101 --end 20241231 --dry-run
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from dl_common import (
    build_url, date_range, download_one,
    load_manifest, save_manifest, record_manifest,
    setup_logging, REQUEST_DELAY, SAVE_DIRS, logger,
)
from datetime import datetime


def main():
    parser = argparse.ArgumentParser(description="競走成績 (K*.LZH) ダウンロード")
    parser.add_argument("--start", required=True, help="開始日 (YYYYMMDD)")
    parser.add_argument("--end", required=True, help="終了日 (YYYYMMDD)")
    parser.add_argument("--dry-run", action="store_true", help="実際にはDLしない")
    parser.add_argument("--delay", type=float, default=REQUEST_DELAY, help="リクエスト間隔(秒)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    setup_logging(args.verbose)
    manifest = load_manifest()

    category = "results"
    save_dir = SAVE_DIRS[category]
    stats = {"success": 0, "skip": 0, "fail": 0, "dry_run": 0, "error": 0}

    for dt in date_range(args.start, args.end):
        url, filename = build_url(category, dt)
        save_path = os.path.join(save_dir, filename)

        result = download_one(url, save_path, dry_run=args.dry_run)
        record_manifest(manifest, category, filename, result)
        stats[result["status"]] = stats.get(result["status"], 0) + 1

        if result["status"] == "success":
            time.sleep(args.delay)

    save_manifest(manifest)

    logger.info("=== Results Download Summary ===")
    for k, v in stats.items():
        if v > 0:
            logger.info(f"  {k}: {v}")


if __name__ == "__main__":
    main()
