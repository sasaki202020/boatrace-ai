from __future__ import annotations

import argparse
import re
import ssl
import urllib.request
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "data" / "raw" / "official" / "missing_months_manifest.csv"
DEFAULT_OUT = ROOT / "data" / "raw" / "official" / "urls.txt"
BASE = "https://www1.mbrace.or.jp/od2/K"


def fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
        raw = resp.read()
    for enc in ("cp932", "shift_jis", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin1", errors="replace")


def urls_for_month(yyyymm: str) -> list[str]:
    mday_url = f"{BASE}/{yyyymm}/mday.html"
    html = fetch_text(mday_url)

    # Example:
    # var dir="/od2/K/202402/k2402";
    m = re.search(r'var\s+dir\s*=\s*"([^"]+)"', html)
    if not m:
        return []
    dir_part = m.group(1).rstrip("/")
    if not dir_part.startswith("/"):
        dir_part = "/" + dir_part

    # Example:
    # <INPUT TYPE="radio" NAME="MDAY" VALUE="01" >
    days = re.findall(r'NAME="MDAY"\s+VALUE="(\d{2})"', html, flags=re.IGNORECASE)
    days = sorted(set(days))
    if not days:
        return []

    return [f"https://www1.mbrace.or.jp{dir_part}{d}.lzh" for d in days]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = p.parse_args()

    if not args.manifest.exists():
        raise FileNotFoundError(f"manifest not found: {args.manifest}")

    mf = pd.read_csv(args.manifest)
    if "month" not in mf.columns or "status" not in mf.columns:
        raise ValueError("manifest must have columns: month,status")

    target_months = [m for m in mf.loc[mf["status"] == "missing", "month"].astype(str).tolist()]
    yyyymm_list = [m.replace("-", "") for m in target_months]

    urls: list[str] = []
    misses: list[str] = []
    for yyyymm in yyyymm_list:
        try:
            us = urls_for_month(yyyymm)
            if not us:
                misses.append(yyyymm)
                print(f"[miss] {yyyymm}")
                continue
            urls.extend(us)
            print(f"[ok] {yyyymm} -> {len(us)} urls")
        except Exception:
            misses.append(yyyymm)
            print(f"[miss] {yyyymm}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(urls) + ("\n" if urls else ""), encoding="utf-8")

    print(f"[saved] {args.out}")
    print(f"months_target={len(yyyymm_list)} months_ok={len(yyyymm_list)-len(misses)} months_miss={len(misses)}")
    print(f"urls_total={len(urls)}")


if __name__ == "__main__":
    main()

