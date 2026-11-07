import json
import sys
from pathlib import Path

import pandas as pd

from src.ingest.official_txt_parser import OfficialTxtParser


TXT_DIR = Path("data/raw/official/results")
OUT_DIR = Path("data/raw/official/parsed")
OUT_JSON = Path("data/raw/official/parse_report.json")


def detect_raw_kind(txt_path: Path) -> str:
    name = txt_path.name.lower()
    if name.startswith("k"):
        return "kse_txt"
    if name.startswith("b"):
        return "kbn_txt"
    if name.startswith("fan"):
        return "fan_txt"
    return "txt"


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = OfficialTxtParser()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    txt_files = sorted({*TXT_DIR.glob("*.TXT"), *TXT_DIR.glob("*.txt")})
    txt_files = [p for p in txt_files if "_head" not in p.stem.lower()]
    print(f"[TXT] {len(txt_files)}件を検出")

    report = {"files": [], "total_rows": 0, "total_races": 0}

    for txt_path in txt_files:
        print(f"  処理中: {txt_path.name}")
        try:
            raw = txt_path.read_text(encoding="cp932", errors="replace")
        except Exception:
            raw = txt_path.read_text(encoding="utf-8", errors="replace")

        # 全件表示はログ過多になるため、先頭1ファイルのみプレビュー
        if len(report["files"]) == 0:
            print("  --- 先頭20行 ---")
            for i, line in enumerate(raw.splitlines()[:20]):
                safe_line = line.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
                print(f"  {i + 1:3d}: {safe_line}")
            print("  ----------------")

        try:
            parsed = parser.parse(str(txt_path), raw_kind=detect_raw_kind(txt_path))
            df = parsed["dataframe"].copy()
        except Exception as exc:
            report["files"].append({"file": txt_path.name, "status": "error", "reason": str(exc)})
            continue

        if df.empty:
            report["files"].append({"file": txt_path.name, "status": "empty", "rows": 0})
            continue

        out_path = OUT_DIR / f"{txt_path.stem}.csv"
        df.to_csv(out_path, index=False)

        race_count = int(df["race_id"].nunique()) if "race_id" in df.columns else 0
        report["files"].append(
            {
                "file": txt_path.name,
                "status": "ok",
                "rows": int(len(df)),
                "race_count": race_count,
                "output": str(out_path),
            }
        )
        report["total_rows"] += int(len(df))
        report["total_races"] += race_count
        print(f"  → {len(df)}行 / {race_count}レース → {out_path.name}")

    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if report["total_rows"] > 0:
        print("\n次のステップ:")
        print("  py -m src.data.build_historical_races")
    else:
        print("\n[warn] 変換できた TXT が 0 件でした")
        print("先頭20行の出力を貼ってもらえれば")
        print("パーサーを実データに合わせて修正します")


if __name__ == "__main__":
    main()
