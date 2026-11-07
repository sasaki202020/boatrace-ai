from __future__ import annotations

import argparse
import io
import json
import time
from datetime import date, timedelta
from pathlib import Path

try:
    import requests
except ImportError as exc:
    raise ImportError("pip install requests を実行してください") from exc

try:
    import lhafile
except ImportError as exc:
    raise ImportError("pip install lhafile を実行してください") from exc


def url_b(ds: str) -> str:
    """番組表URL。ds = YYYYMMDD"""
    yyyymm = ds[:6]
    yymmdd = ds[2:]
    return f"http://www1.mbrace.or.jp/od2/B/{yyyymm}/b{yymmdd}.lzh"


def url_k(ds: str) -> str:
    """競走成績URL。ds = YYYYMMDD"""
    yyyymm = ds[:6]
    yymmdd = ds[2:]
    return f"http://www1.mbrace.or.jp/od2/K/{yyyymm}/k{yymmdd}.lzh"


FAN_CODES = [
    "1910", "2004",
    "2010", "2104",
    "2110", "2204",
    "2210", "2304",
    "2310", "2404",
    "2410", "2504",
    "2510",
]

FAN_URL = "https://www.boatrace.jp/static_extra/pc_static/download/data/kibetsu/fan{code}.lzh"


def fetch_lzh(url: str, interval: float) -> bytes | None:
    """
    LZHをダウンロードして中のファイルのバイト列を返す。
    404（非開催日）はNoneを返す。その他エラーもNoneを返してスキップする。
    """
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        time.sleep(interval)
        lha_cls = getattr(lhafile, "LhaFile", None) or getattr(lhafile, "Lhafile", None)
        if lha_cls is None:
            raise AttributeError("lhafile has no LhaFile/Lhafile class")
        lzh = lha_cls(io.BytesIO(resp.content))
        names = lzh.namelist()
        if not names:
            return None
        return lzh.read(names[0])
    except Exception as e:
        print(f"  [WARN] 失敗: {url}  {e}")
        time.sleep(interval)
        return None


def to_text(data: bytes) -> str:
    """Shift-JIS / UTF-8 変換。失敗時は UTF-8 で再試行する。"""
    for enc in ["shift_jis", "utf-8", "cp932"]:
        try:
            return data.decode(enc, errors="replace")
        except Exception:
            continue
    return data.decode("utf-8", errors="replace")


def main():
    parser = argparse.ArgumentParser(description="boatrace.jp 公式データ一括取得 v2")
    parser.add_argument("--start", default="20240601", help="取得開始日 YYYYMMDD")
    parser.add_argument("--end", default="20240603", help="取得終了日 YYYYMMDD")
    parser.add_argument("--output-dir", default="./data", help="出力ディレクトリ（デフォルト: ./data）")
    parser.add_argument("--interval", type=float, default=3.0, help="リクエスト間隔（秒）。3未満は非推奨")
    parser.add_argument("--fan-only", action="store_true", help="選手期別成績のみ取得")
    parser.add_argument("--skip-fan", action="store_true", help="選手期別成績をスキップ")
    parser.add_argument("--dry-run", action="store_true", help="URLだけ表示して実取得しない")
    args = parser.parse_args()

    if args.interval < 3.0:
        print("[WARN] --interval が3秒未満です。サーバ負荷軽減のため3秒以上推奨。")

    out = Path(args.output_dir)
    raw_b = out / "raw" / "B"
    raw_k = out / "raw" / "K"
    raw_f = out / "raw" / "fan"
    for d in [raw_b, raw_k, raw_f]:
        d.mkdir(parents=True, exist_ok=True)

    started_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    stats = {"b": 0, "k": 0, "fan": 0, "skip_404": 0, "skip_exists": 0}

    if not args.skip_fan:
        print("[INFO] 選手期別成績 取得開始...")
        for code in FAN_CODES:
            save = raw_f / f"fan{code}.txt"
            if save.exists():
                print(f"  [SKIP] fan{code} 取得済み")
                stats["skip_exists"] += 1
                continue
            url = FAN_URL.format(code=code)
            if args.dry_run:
                print(f"  [DRY]  {url}")
                continue
            print(f"  [GET]  {url}")
            data = fetch_lzh(url, args.interval)
            if data:
                save.write_text(to_text(data), encoding="utf-8")
                print(f"  [OUT]  {save}")
                stats["fan"] += 1
            else:
                print(f"  [WARN] fan{code} 取得失敗（スキップ）")

    if args.fan_only:
        _write_meta(out, started_at, args, stats)
        print("[DONE] ファン手帳のみ取得完了")
        return

    start_d = date(int(args.start[:4]), int(args.start[4:6]), int(args.start[6:]))
    end_d = date(int(args.end[:4]), int(args.end[4:6]), int(args.end[6:]))
    total = (end_d - start_d).days + 1
    print(f"[INFO] B/K取得: {args.start}〜{args.end} ({total}日分)")
    if not args.dry_run:
        est_min = total * 2 * args.interval / 60
        print(f"[INFO] 推定時間: 約 {est_min:.0f} 分（非開催日スキップで短縮）")

    cur = start_d
    done = 0
    while cur <= end_d:
        ds = cur.strftime("%Y%m%d")
        done += 1
        if done % 100 == 0:
            print(f"[PROG] {done}/{total} ({done / total * 100:.0f}%) {ds}")

        for prefix, dir_, url_fn, key in [
            ("B", raw_b, url_b, "b"),
            ("K", raw_k, url_k, "k"),
        ]:
            save = dir_ / f"{ds}.txt"
            if save.exists():
                stats["skip_exists"] += 1
                continue
            url = url_fn(ds)
            if args.dry_run:
                print(f"  [DRY] {url}")
                continue
            data = fetch_lzh(url, args.interval)
            if data:
                save.write_text(to_text(data), encoding="utf-8")
                stats[key] += 1
            else:
                stats["skip_404"] += 1

        cur += timedelta(days=1)

    _write_meta(out, started_at, args, stats)
    print("\n[DONE] 完了")
    print(f"  B取得: {stats['b']} 日")
    print(f"  K取得: {stats['k']} 日")
    print(f"  ファン手帳: {stats['fan']} ファイル")
    print(f"  404スキップ（非開催日）: {stats['skip_404']}")
    print(f"  取得済みスキップ: {stats['skip_exists']}")


def _write_meta(out: Path, started_at: str, args, stats: dict):
    meta = {
        "schema_version": "1.0.0",
        "generated_at": started_at,
        "start_date": getattr(args, "start", ""),
        "end_date": getattr(args, "end", ""),
        "interval_sec": args.interval,
        "stats": stats,
        "url_b_example": url_b("20240601"),
        "url_k_example": url_k("20240601"),
        "note": "生テキストは raw/ に保存。パースは parse_boatrace_data.py で別途実行。",
    }
    path = out / "run_meta.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"[OUT] {path}")


if __name__ == "__main__":
    main()
