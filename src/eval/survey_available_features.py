import argparse
import json
from pathlib import Path

import pandas as pd


def main():
    parser = argparse.ArgumentParser(description="Survey CSVs joinable by race_id + lane.")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--out-json", default="available_features_survey.json")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    join_keys = {"race_id", "lane"}
    survey: dict[str, dict] = {}

    for csv_path in sorted(data_dir.glob("**/*.csv")):
        key = str(csv_path.as_posix())
        try:
            head = pd.read_csv(csv_path, nrows=5)
            cols = list(head.columns)
            has_race_id = "race_id" in cols
            has_lane = "lane" in cols
            numeric_cols = [
                c
                for c in cols
                if c not in join_keys and pd.api.types.is_numeric_dtype(head[c])
            ]
            row_count = int(sum(1 for _ in open(csv_path, "r", encoding="utf-8", errors="ignore")) - 1)
            survey[key] = {
                "columns": cols,
                "row_count": row_count,
                "has_race_id": has_race_id,
                "has_lane": has_lane,
                "joinable": bool(has_race_id and has_lane),
                "numeric_cols": numeric_cols,
            }
        except Exception as e:
            survey[key] = {"error": str(e)}

    joinable = {
        k: v
        for k, v in survey.items()
        if v.get("joinable") and len(v.get("numeric_cols", [])) > 0
    }

    result = {
        "all_files": len(survey),
        "joinable_files": len(joinable),
        "joinable": joinable,
        "not_joinable": {
            k: {"columns": v.get("columns"), "row_count": v.get("row_count")}
            for k, v in survey.items()
            if not v.get("joinable")
        },
    }

    out = Path(args.out_json)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved: {out}")
    print(f"joinable: {len(joinable)}")
    for path, info in joinable.items():
        print(path)
        print(f"  numeric_cols: {info.get('numeric_cols', [])}")


if __name__ == "__main__":
    main()
