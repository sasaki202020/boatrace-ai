import os
import subprocess
import glob
import logging
import shutil

# ログ設定
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BoatRaceExtractor:
    """
    7-Zip を使用して LZH ファイルを解凍し、正規のフォルダへ配置する。
    """
    SEVEN_ZIP_PATH = r"C:\Program Files\7-Zip\7z.exe"

    def __init__(self, raw_dir="data/raw/official"):
        self.raw_dir = raw_dir

    def extract_all(self):
        """
        data/raw/official 配下の各ディレクトリ (results, entries) 内の LZH を解凍する。
        """
        for dtype in ["results", "entries"]:
            src_dir = os.path.join(self.raw_dir, dtype)
            if not os.path.exists(src_dir):
                continue
            
            lzh_files = glob.glob(os.path.join(src_dir, "*.lzh"))
            logger.info(f"Found {len(lzh_files)} LZH files in {dtype}")
            
            for lzh_path in lzh_files:
                self._extract_single(lzh_path, src_dir)

    def _extract_single(self, lzh_path, target_dir):
        """
        1つの LZH ファイルを解凍し、中の TXT を抽出する。
        """
        # 展開コマンド: 7z e {lzh_path} -o{target_dir} -y
        # e: extract (flatten paths)
        # -y: yes to all (overwrite)
        try:
            cmd = [self.SEVEN_ZIP_PATH, "e", lzh_path, f"-o{target_dir}", "-y"]
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode == 0:
                logger.debug(f"Extracted: {os.path.basename(lzh_path)}")
                # 元の LZH は容量削減のために削除しても良いが、一旦残す (ユーザーの判断)
                # os.remove(lzh_path) 
            else:
                logger.error(f"Failed to extract {lzh_path}: {result.stderr}")
        except Exception as e:
            logger.error(f"Error extracting {lzh_path}: {e}")

if __name__ == "__main__":
    if not os.path.exists(BoatRaceExtractor.SEVEN_ZIP_PATH):
        logger.error(f"7-Zip not found at {BoatRaceExtractor.SEVEN_ZIP_PATH}")
    else:
        extractor = BoatRaceExtractor()
        extractor.extract_all()
        logger.info("Extraction finished.")
