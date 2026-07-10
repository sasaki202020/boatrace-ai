import os
import time
import datetime
import urllib.request
import logging

# ログ設定
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BoatRaceDownloader:
    """
    BOAT RACE 公式サイト (mbrace.or.jp) から LZH 形式のデータを取得する。
    """
    BASE_URLS = {
        "results": "http://www1.mbrace.or.jp/od2/K/", # 競走成績
        "entries": "http://www1.mbrace.or.jp/od2/B/", # 番組表
    }
    
    FILE_PREFIXES = {
        "results": "k",
        "entries": "b",
    }

    def __init__(self, output_base="data/raw/official"):
        self.output_base = output_base

    def download_range(self, start_date, end_date, data_types=["results", "entries"]):
        """
        指定期間のデータをダウンロードする。
        """
        current_date = start_date
        while current_date <= end_date:
            date_str = current_date.strftime("%y%m%d") # YYMMDD
            
            for dtype in data_types:
                prefix = self.FILE_PREFIXES[dtype]
                filename = f"{prefix}{date_str}.lzh"
                url = f"{self.BASE_URLS[dtype]}{filename}"
                
                target_dir = os.path.join(self.output_base, dtype)
                os.makedirs(target_dir, exist_ok=True)
                
                target_path = os.path.join(target_dir, filename)
                
                if os.path.exists(target_path):
                    logger.debug(f"Already exists: {filename}")
                    continue
                
                try:
                    logger.info(f"Downloading: {url}")
                    # ユーザーエージェントを設定しないと拒否される場合がある
                    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req) as response:
                        with open(target_path, 'wb') as out_file:
                            out_file.write(response.read())
                    # サーバー負荷軽減のためのウェイト
                    time.sleep(1.0)
                except urllib.error.HTTPError as e:
                    if e.code == 404:
                        logger.warning(f"Not found: {url}")
                    else:
                        logger.error(f"HTTP Error {e.code}: {url}")
                except Exception as e:
                    logger.error(f"Failed to download {url}: {e}")

            current_date += datetime.timedelta(days=1)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Download BOAT RACE official data (LZH).")
    parser.add_argument("--start", type=str, default="20240101", help="Start date (YYYYMMDD)")
    parser.add_argument("--end", type=str, default=datetime.date.today().strftime("%Y%m%d"), help="End date (YYYYMMDD)")
    parser.add_argument("--types", nargs="+", default=["results", "entries"], help="Data types to download")
    
    args = parser.parse_args()
    
    start_dt = datetime.datetime.strptime(args.start, "%Y%m%d").date()
    end_dt = datetime.datetime.strptime(args.end, "%Y%m%d").date()
    
    downloader = BoatRaceDownloader()
    logger.info(f"Starting download from {start_dt} to {end_dt} for types {args.types}")
    downloader.download_range(start_dt, end_dt, data_types=args.types)
    logger.info("Download process finished.")
