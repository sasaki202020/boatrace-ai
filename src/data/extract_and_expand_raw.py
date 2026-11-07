from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw" / "official"
RESULT_DIR = RAW_DIR / "results"
OUT_JSON = RAW_DIR / "extract_report.json"


def pick_extractor() -> str | None:
    seven_zip = shutil.which("7z") or shutil.which("7za")
    if seven_zip:
        return seven_zip
    fallback = Path(r"C:\Program Files\7-Zip\7z.exe")
    if fallback.exists():
        return str(fallback)
    return None


def extract_lzh_files() -> list[dict]:
    lzh_files = sorted(RAW_DIR.glob("**/*.lzh")) + sorted(RAW_DIR.glob("**/*.LZH"))
    extractor = pick_extractor()
    results: list[dict] = []

    print(f"[LZH] {len(lzh_files)}件検出")
    if not lzh_files:
        return results

    if extractor is None:
        for lzh in lzh_files:
            results.append(
                {
                    "file": lzh.name,
                    "status": "error",
                    "reason": "7z/7za が見つかりません。",
                }
            )
        return results

    RESULT_DIR.mkdir(parents=True, exist_ok=True)

    for lzh in lzh_files:
        try:
            cmd = [extractor, "e", str(lzh), f"-o{RESULT_DIR}", "-y"]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            status = "ok" if proc.returncode == 0 else "error"
            results.append(
                {
                    "file": lzh.name,
                    "status": status,
                    "returncode": proc.returncode,
                    "stdout": proc.stdout[-500:],
                    "stderr": proc.stderr[-500:],
                }
            )
            print(f"  [{status}] {lzh.name} -> {RESULT_DIR}")
        except Exception as exc:
            results.append({"file": lzh.name, "status": "error", "reason": str(exc)})

    return results


def run_step(label: str, args: list[str]) -> dict:
    proc = subprocess.run(
        args,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    return {
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "")[-1000:],
        "stderr": (proc.stderr or "")[-500:],
        "label": label,
    }


def count_historical_rows() -> dict:
    hist_path = ROOT / "data" / "processed" / "historical_races.csv"
    if not hist_path.exists():
        return {"exists": False}
    import pandas as pd

    hist = pd.read_csv(hist_path)
    date_col = next((c for c in hist.columns if "date" in c.lower()), None)
    date_range = {}
    if date_col:
        # Handle mixed types safely (str/float/NaN) before min/max.
        dt = pd.to_datetime(hist[date_col], errors="coerce")
        dt = dt.dropna()
        if len(dt) > 0:
            date_range = {"min": str(dt.min().date()), "max": str(dt.max().date())}

    tri_count = 0
    if "finish_position" in hist.columns:
        tri = hist[hist["finish_position"].isin([1, 2, 3])]
        tri_count = (
            tri.groupby("race_id")
            .filter(lambda x: {1, 2, 3}.issubset(set(x["finish_position"])))["race_id"]
            .nunique()
        )

    return {
        "exists": True,
        "rows": int(len(hist)),
        "race_count": int(hist["race_id"].nunique()) if "race_id" in hist.columns else None,
        "trifecta_race_count": int(tri_count),
        "date_range": date_range,
    }


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    report: dict = {"root": str(ROOT), "raw_dir": str(RAW_DIR), "steps": {}}

    report["steps"]["extract"] = extract_lzh_files()

    print("\n[parse] 実行")
    report["steps"]["parse"] = run_step("parse", [sys.executable, "-m", "src.data.parse_official_txt"])
    print(report["steps"]["parse"]["stdout"][-500:])
    if report["steps"]["parse"]["returncode"] != 0:
        print(report["steps"]["parse"]["stderr"][-500:])

    print("\n[build] 実行")
    report["steps"]["build"] = run_step("build", [sys.executable, "-m", "src.data.build_historical_races"])
    print(report["steps"]["build"]["stdout"][-500:])
    if report["steps"]["build"]["returncode"] != 0:
        print(report["steps"]["build"]["stderr"][-500:])

    print("\n[refresh_today] 実行")
    report["steps"]["refresh_today"] = run_step("refresh_today", [sys.executable, "-m", "src.data.refresh_today_races"])
    print(report["steps"]["refresh_today"]["stdout"][-500:])
    if report["steps"]["refresh_today"]["returncode"] != 0:
        print(report["steps"]["refresh_today"]["stderr"][-500:])

    report["historical_after"] = count_historical_rows()
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
