"""
Download Pack 共通ライブラリ

URL生成、ダウンロード、マニフェスト管理の共通ロジック。
"""
import json
import os
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

logger = logging.getLogger("dl_pack")

# ─── URL パターン ───
# 月ディレクトリ (YYYYMM) を含む形式
BASE_URLS = {
    "results": "https://www1.mbrace.or.jp/od2/K/{yyyymm}/k{yymmdd}.lzh",
    "entries": "https://www1.mbrace.or.jp/od2/B/{yyyymm}/b{yymmdd}.lzh",
    "fanbook": "https://boatrace.jp/static_extra/pc_static/download/data/kibetsu/fan{yymm}.lzh",
}

SAVE_DIRS = {
    "results": "data/raw/official/results",
    "entries": "data/raw/official/entries",
    "fanbook": "data/raw/official/fanbook",
}

MANIFEST_PATH = "data/raw/official/logs/download_manifest.json"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
REQUEST_DELAY = 1.0


# ─── マニフェスト ───
def load_manifest() -> dict:
    """マニフェストを読み込む。存在しなければ空辞書を返す。"""
    if os.path.exists(MANIFEST_PATH):
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_manifest(manifest: dict):
    """マニフェストを保存する。"""
    os.makedirs(os.path.dirname(MANIFEST_PATH), exist_ok=True)
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)


def record_manifest(manifest: dict, category: str, filename: str, entry: dict):
    """マニフェストに1件記録する。"""
    if category not in manifest:
        manifest[category] = {}
    manifest[category][filename] = entry


# ─── URL 生成 ───
def build_url(category: str, dt: datetime) -> tuple:
    """カテゴリと日付からURL・ファイル名を生成する。"""
    yymmdd = dt.strftime("%y%m%d")
    yymm = dt.strftime("%y%m")
    yyyymm = dt.strftime("%Y%m")

    url = BASE_URLS[category].format(yymmdd=yymmdd, yymm=yymm, yyyymm=yyyymm)
    filename = url.split("/")[-1]
    return url, filename


def date_range(start_str: str, end_str: str):
    """YYYYMMDD 形式の開始・終了日からジェネレータを返す。"""
    start = datetime.strptime(start_str, "%Y%m%d")
    end = datetime.strptime(end_str, "%Y%m%d")
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def month_range(start_str: str, end_str: str):
    """YYYYMM 形式の開始・終了月からジェネレータを返す（fan用）。"""
    start = datetime.strptime(start_str + "01", "%Y%m%d")
    end = datetime.strptime(end_str + "01", "%Y%m%d")
    current = start
    while current <= end:
        yield current
        # 翌月1日
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)


# ─── ダウンロード ───
def download_one(url: str, save_path: str, dry_run: bool = False) -> dict:
    """
    1ファイルをダウンロードし、結果を dict で返す。
    dry_run=True の場合は実際にはダウンロードしない。
    """
    if dry_run:
        logger.info(f"[DRY-RUN] {url} -> {save_path}")
        return {
            "status": "dry_run",
            "url": url,
            "save_path": save_path,
            "http_status": None,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }

    # 既存チェック
    if os.path.exists(save_path):
        logger.info(f"[SKIP] Already exists: {save_path}")
        return {
            "status": "skip",
            "url": url,
            "save_path": save_path,
            "http_status": None,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }

    os.makedirs(os.path.dirname(save_path), exist_ok=True)

    try:
        req = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=30) as resp:
            data = resp.read()
            with open(save_path, "wb") as f:
                f.write(data)
            logger.info(f"[OK] {url} -> {save_path} ({len(data)} bytes)")
            return {
                "status": "success",
                "url": url,
                "save_path": save_path,
                "http_status": 200,
                "bytes": len(data),
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            }
    except HTTPError as e:
        logger.warning(f"[FAIL] HTTP {e.code}: {url}")
        return {
            "status": "fail",
            "url": url,
            "save_path": save_path,
            "http_status": e.code,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }
    except (URLError, OSError) as e:
        logger.error(f"[ERROR] {url}: {e}")
        return {
            "status": "error",
            "url": url,
            "save_path": save_path,
            "http_status": None,
            "error": str(e),
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }


def setup_logging(verbose: bool = False):
    """ロギング設定。"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
