import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


LANES = [1, 2, 3, 4, 5, 6]
GLOBAL_CONTEXT = "__global__"


def _to_int(v):
    try:
        if pd.isna(v):
            return None
    except Exception:
        pass
    try:
        return int(float(v))
    except Exception:
        return None


def _normalize_context(v):
    ctx = _to_int(v)
    if ctx is None:
        return GLOBAL_CONTEXT
    return str(ctx)


def _build_p2_counts(df: pd.DataFrame) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    if df.empty:
        return out
    grouped = df.groupby(["first_lane", "second_lane"]).size().reset_index(name="count")
    for _, row in grouped.iterrows():
        first_key = str(int(row["first_lane"]))
        second_key = str(int(row["second_lane"]))
        out.setdefault(first_key, {})
        out[first_key][second_key] = int(row["count"])
    return out


def _build_p3_counts(df: pd.DataFrame) -> dict[str, dict[str, dict[str, int]]]:
    out: dict[str, dict[str, dict[str, int]]] = {}
    if df.empty:
        return out
    grouped = df.groupby(["first_lane", "second_lane", "third_lane"]).size().reset_index(name="count")
    for _, row in grouped.iterrows():
        first_key = str(int(row["first_lane"]))
        second_key = str(int(row["second_lane"]))
        third_key = str(int(row["third_lane"]))
        out.setdefault(first_key, {})
        out[first_key].setdefault(second_key, {})
        out[first_key][second_key][third_key] = int(row["count"])
    return out


def _add_global_tables(race_df: pd.DataFrame, tables: dict) -> None:
    p2_global = _build_p2_counts(race_df)
    p3_global = _build_p3_counts(race_df)

    tables["p2"][f"{GLOBAL_CONTEXT}"] = {
        "support": int(len(race_df)),
        "counts": {k: v for k, v in p2_global.items()},
    }
    tables["p3"][f"{GLOBAL_CONTEXT}"] = {
        "support": int(len(race_df)),
        "counts": {k: v for k, v in p3_global.items()},
    }


def _add_context_tables(race_df: pd.DataFrame, tables: dict, context_col: str = "jcd") -> None:
    for context, ctx_df in race_df.groupby(context_col):
        ctx_key = _normalize_context(context)
        p2 = _build_p2_counts(ctx_df)
        p3 = _build_p3_counts(ctx_df)
        tables["p2"][ctx_key] = {
            "support": int(len(ctx_df)),
            "counts": p2,
        }
        tables["p3"][ctx_key] = {
            "support": int(len(ctx_df)),
            "counts": p3,
        }


def main() -> None:
    labels_path = Path("data/processed/historical_races.csv")
    out_path = Path("models/conditional_place_tables.json")
    out_summary = Path("data/model_outputs/conditional_place_tables_summary.json")

    use_cols = ["race_id", "date", "jcd", "lane", "finish_position"]
    df = pd.read_csv(labels_path, usecols=lambda c: c in use_cols, low_memory=False)
    df = df.dropna(subset=["race_id", "lane", "finish_position"]).copy()
    df["race_id"] = df["race_id"].astype(str)
    df["lane"] = pd.to_numeric(df["lane"], errors="coerce")
    df["finish_position"] = pd.to_numeric(df["finish_position"], errors="coerce")
    df["jcd"] = pd.to_numeric(df["jcd"], errors="coerce")
    df = df[df["finish_position"].isin([1, 2, 3])].copy()
    df = df.dropna(subset=["lane", "finish_position"]).copy()
    df["lane"] = df["lane"].astype(int)
    df["finish_position"] = df["finish_position"].astype(int)

    top3 = (
        df.sort_values(["race_id", "finish_position"])
        .pivot_table(
            index=["race_id", "date", "jcd"],
            columns="finish_position",
            values="lane",
            aggfunc="first",
        )
        .rename(columns={1: "first_lane", 2: "second_lane", 3: "third_lane"})
        .reset_index()
    )
    top3 = top3.dropna(subset=["first_lane", "second_lane", "third_lane"]).copy()
    for col in ["first_lane", "second_lane", "third_lane"]:
        top3[col] = top3[col].astype(int)

    tables = {
        "meta": {
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "race_count": int(top3["race_id"].nunique()),
            "rows": int(len(top3)),
            "contexts": int(top3["jcd"].nunique(dropna=True)),
            "lanes": LANES,
            "description": "Conditional place lookup tables with jcd backoff.",
        },
        "p2": {},
        "p3": {},
    }

    _add_global_tables(top3, tables)
    _add_context_tables(top3, tables, context_col="jcd")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(tables, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "created_at": tables["meta"]["created_at"],
        "race_count": tables["meta"]["race_count"],
        "rows": tables["meta"]["rows"],
        "contexts": tables["meta"]["contexts"],
        "p2_keys": len(tables["p2"]),
        "p3_keys": len(tables["p3"]),
        "output": str(out_path),
        "sample_p2_keys": list(sorted(tables["p2"].keys()))[:5],
        "sample_p3_keys": list(sorted(tables["p3"].keys()))[:5],
    }
    out_summary.parent.mkdir(parents=True, exist_ok=True)
    out_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
