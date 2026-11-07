from __future__ import annotations

import argparse
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_URLS = ROOT / "data" / "raw" / "official" / "urls.txt"
DEFAULT_OUT = ROOT / "data" / "raw" / "official"
DEFAULT_REPORT = ROOT / "data" / "raw" / "official" / "download_report.json"


def read_urls(path: Path) -> list[str]:
    urls = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        urls.append(s)
    return urls


def download(url: str, out_dir: Path, timeout: int, skip_existing: bool) -> dict:
    filename = Path(urllib.parse.urlparse(url).path).name
    if not filename:
        return {"url": url, "status": "error", "reason": "no filename in url"}

    dst = out_dir / filename
    if skip_existing and dst.exists():
        return {"url": url, "file": filename, "status": "skip_existing", "size": dst.stat().st_size}

    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            content = resp.read()
            dst.write_bytes(content)
        return {"url": url, "file": filename, "status": "ok", "size": dst.stat().st_size}
    except urllib.error.HTTPError as e:
        return {"url": url, "file": filename, "status": "http_error", "code": e.code, "reason": str(e)}
    except Exception as e:  # noqa: BLE001
        return {"url": url, "file": filename, "status": "error", "reason": str(e)}


def main() -> None:
    p = argparse.ArgumentParser(description="Download LZH files from urls.txt")
    p.add_argument("--urls", type=Path, default=DEFAULT_URLS, help="Path to urls.txt")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Download destination directory")
    p.add_argument("--timeout", type=int, default=30, help="Per-file timeout seconds")
    p.add_argument("--no-skip-existing", action="store_true", help="Re-download existing files")
    args = p.parse_args()

    if not args.urls.exists():
        raise FileNotFoundError(f"urls file not found: {args.urls}")

    args.out.mkdir(parents=True, exist_ok=True)
    urls = read_urls(args.urls)
    if not urls:
        raise ValueError("no urls found in urls.txt")

    results = []
    for u in urls:
        r = download(u, args.out, args.timeout, skip_existing=not args.no_skip_existing)
        results.append(r)
        status = r.get("status")
        name = r.get("file", "(unknown)")
        print(f"[{status}] {name}")

    summary = {
        "urls_file": str(args.urls),
        "output_dir": str(args.out),
        "total": len(results),
        "ok": sum(1 for r in results if r["status"] == "ok"),
        "skip_existing": sum(1 for r in results if r["status"] == "skip_existing"),
        "http_error": sum(1 for r in results if r["status"] == "http_error"),
        "error": sum(1 for r in results if r["status"] == "error"),
        "results": results,
    }

    DEFAULT_REPORT.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[saved] {DEFAULT_REPORT}")
    print(
        "summary:",
        f"ok={summary['ok']}",
        f"skip_existing={summary['skip_existing']}",
        f"http_error={summary['http_error']}",
        f"error={summary['error']}",
    )


if __name__ == "__main__":
    main()

