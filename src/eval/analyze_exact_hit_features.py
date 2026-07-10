import json
import re
from pathlib import Path

import numpy as np
import pandas as pd


ROWS_CSV = Path("trifecta_rank_structure_rows.csv")
PROBA_CSV = Path("data/model_outputs/today_win_proba.csv")
FEAT_CSV = Path("data/features/today_features.csv")
HIST_CSV = Path("data/processed/historical_races.csv")
OUT_JSON = Path("exact_hit_feature_analysis.json")
OUT_FILTER = Path("exact_hit_filter_candidates.json")


def normalize_text(value) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def normalize_race_key(race_id: str) -> str | None:
    if pd.isna(race_id):
        return None
    rid = str(race_id).strip()
    if not rid:
        return None

    m = re.match(r"^(\d{8})-[A-Z]\d{6}-(\d{1,3})$", rid)
    if m:
        date8, serial = m.groups()
        return f"d{date8}-n{int(serial):03d}"

    m = re.match(r"^(\d{8})-[A-Z]\d{6}_s(\d{2})-(\d{2})$", rid)
    if m:
        date8, sec, race_no = m.groups()
        serial = (int(sec) - 1) * 12 + int(race_no)
        return f"d{date8}-n{serial:03d}"

    m = re.match(r"^(\d{8})-(\d{2})-(\d{2})$", rid)
    if m:
        date8, venue, race_no = m.groups()
        return f"d{date8}-v{int(venue):02d}-r{int(race_no):02d}"

    return rid


def prediction_match_key(race_id: str) -> str | None:
    if pd.isna(race_id):
        return None
    rid = str(race_id).strip()
    m = re.match(r"^(\d{8})-[A-Z]\d{6}-(\d{1,3})$", rid)
    if not m:
        return None
    date8, serial = m.groups()
    serial_i = int(serial)
    section_compact = (serial_i - 1) // 12 + 1
    race_no = (serial_i - 1) % 12 + 1
    return f"d{date8}-c{section_compact:02d}-r{race_no:02d}"


def extract_outcome_section(race_id: str) -> tuple[str, int, int] | None:
    if pd.isna(race_id):
        return None
    rid = str(race_id).strip()
    m = re.match(r"^(\d{8})-[A-Z]\d{6}_s(\d{2})-(\d{2})$", rid)
    if not m:
        return None
    date8, section_raw, race_no = m.groups()
    return date8, int(section_raw), int(race_no)


def build_outcome_match_keys(race_ids: pd.Series) -> pd.Series:
    parsed = race_ids.apply(extract_outcome_section)
    df = pd.DataFrame(
        {
            "race_id": race_ids.astype(str),
            "date8": [x[0] if x else None for x in parsed],
            "section_raw": [x[1] if x else None for x in parsed],
            "race_no": [x[2] if x else None for x in parsed],
        }
    )
    has_parts = df["date8"].notna() & df["section_raw"].notna() & df["race_no"].notna()
    keys = pd.Series([None] * len(df), index=df.index, dtype=object)
    if has_parts.any():
        tmp = df.loc[has_parts, ["date8", "section_raw", "race_no"]].copy()
        tmp["section_raw"] = pd.to_numeric(tmp["section_raw"], errors="coerce")
        tmp["race_no"] = pd.to_numeric(tmp["race_no"], errors="coerce")
        tmp["section_compact"] = tmp.groupby("date8")["section_raw"].rank(method="dense").astype(int)
        keys.loc[has_parts] = tmp.apply(
            lambda r: f"d{r['date8']}-c{int(r['section_compact']):02d}-r{int(r['race_no']):02d}",
            axis=1,
        )
    return keys


def main() -> None:
    rows_df = pd.read_csv(ROWS_CSV)
    proba_df = pd.read_csv(PROBA_CSV)
    feat_df = pd.read_csv(FEAT_CSV)
    hist_df = pd.read_csv(HIST_CSV)

    rows_df["race_id"] = rows_df["race_id"].astype(str).str.strip()
    rows_df["trifecta_rank"] = pd.to_numeric(rows_df["trifecta_rank"], errors="coerce")
    rows_df["winner_rank"] = pd.to_numeric(rows_df["winner_rank"], errors="coerce")

    proba_df["race_id"] = proba_df["race_id"].map(normalize_text)
    feat_df["race_id"] = feat_df["race_id"].map(normalize_text)
    hist_df["race_id"] = hist_df["race_id"].map(normalize_text)

    merged = proba_df.merge(feat_df, on=["race_id", "lane"], how="left")
    merged["normalized_race_key_legacy"] = merged["race_id"].apply(normalize_race_key)
    merged["normalized_race_key"] = merged["race_id"].apply(prediction_match_key)
    merged["normalized_race_key"] = merged["normalized_race_key"].fillna(merged["normalized_race_key_legacy"])

    top3 = (
        hist_df[hist_df["finish_position"].isin([1, 2, 3])]
        .sort_values(["race_id", "finish_position"])
        .groupby("race_id")["lane"]
        .apply(lambda x: "-".join(x.astype(int).astype(str)))
        .reset_index()
        .rename(columns={"lane": "actual_trifecta"})
    )
    top3["race_id"] = top3["race_id"].astype(str)
    top3["normalized_race_key_legacy"] = top3["race_id"].apply(normalize_race_key)
    top3["normalized_race_key"] = build_outcome_match_keys(top3["race_id"])
    top3["normalized_race_key"] = top3["normalized_race_key"].fillna(top3["normalized_race_key_legacy"])

    race_rows = []
    for _, row in rows_df.iterrows():
        race_id = str(row["race_id"])
        race_key = merged.loc[merged["race_id"] == race_id, "normalized_race_key"].iloc[0]
        actual_row = top3[top3["normalized_race_key"] == race_key]
        if actual_row.empty:
            continue
        actual_tri = str(actual_row.iloc[0]["actual_trifecta"])
        parts = actual_tri.split("-")
        if len(parts) != 3:
            continue

        race_data = merged[merged["race_id"] == race_id].copy()
        if race_data.empty:
            continue
        race_data = race_data.sort_values("win_proba_norm", ascending=False).reset_index(drop=True)
        scores = race_data["win_proba_norm"].astype(float).values
        top1 = race_data.iloc[0]

        rec = {
            "race_id": race_id,
            "exact_hit": int(int(row["trifecta_rank"]) == 1),
            "tri_rank": int(row["trifecta_rank"]),
            "winner_rank": int(row["winner_rank"]),
            "score_gap_top1_top2": round(float(scores[0] - scores[1]), 6) if len(scores) >= 2 else 0.0,
            "score_gap_top1_top3": round(float(scores[0] - scores[2]), 6) if len(scores) >= 3 else 0.0,
            "score_entropy": round(
                float(-np.sum((scores / scores.sum()) * np.log(scores / scores.sum() + 1e-9))),
                6,
            ),
            "top1_win_proba": round(float(top1["win_proba_norm"]), 6),
            "top1_national_win_rate": round(float(top1.get("national_win_rate", 0.0)), 4),
            "top1_local_2ren_rate": round(float(top1.get("local_2ren_rate", 0.0)), 4),
            "top1_motor_2ren_rate": round(float(top1.get("motor_2ren_rate", 0.0)), 4),
            "top1_boat_2ren_rate": round(float(top1.get("boat_2ren_rate", 0.0)), 4),
            "top1_low_motor_flag": int(top1.get("low_motor_flag", 0)),
            "top1_low_boat_flag": int(top1.get("low_boat_flag", 0)),
            "weather": hist_df.loc[hist_df["race_id"] == race_key, "weather"].iloc[0]
            if "weather" in hist_df.columns and not hist_df.loc[hist_df["race_id"] == race_key].empty
            else None,
            "wind_speed": hist_df.loc[hist_df["race_id"] == race_key, "wind_speed"].iloc[0]
            if "wind_speed" in hist_df.columns and not hist_df.loc[hist_df["race_id"] == race_key].empty
            else None,
            "wave_height": hist_df.loc[hist_df["race_id"] == race_key, "wave_height"].iloc[0]
            if "wave_height" in hist_df.columns and not hist_df.loc[hist_df["race_id"] == race_key].empty
            else None,
            "jcd": hist_df.loc[hist_df["race_id"] == race_key, "jcd"].iloc[0]
            if "jcd" in hist_df.columns and not hist_df.loc[hist_df["race_id"] == race_key].empty
            else None,
        }
        race_rows.append(rec)

    feat_df2 = pd.DataFrame(race_rows)
    hit_df = feat_df2[feat_df2["exact_hit"] == 1].copy()
    miss_df = feat_df2[feat_df2["exact_hit"] == 0].copy()

    print(f"exact_hit: {len(hit_df)}件 / miss: {len(miss_df)}件")

    num_cols = [
        "score_gap_top1_top2",
        "score_gap_top1_top3",
        "score_entropy",
        "top1_win_proba",
        "top1_national_win_rate",
        "top1_local_2ren_rate",
        "top1_motor_2ren_rate",
        "top1_boat_2ren_rate",
        "winner_rank",
        "wind_speed",
        "wave_height",
    ]

    num_comparison = {}
    filter_candidates = []
    for col in num_cols:
        h = pd.to_numeric(hit_df[col], errors="coerce").dropna()
        m = pd.to_numeric(miss_df[col], errors="coerce").dropna()
        if len(h) < 3 or len(m) < 3:
            continue
        num_comparison[col] = {
            "hit_median": round(float(h.median()), 4),
            "miss_median": round(float(m.median()), 4),
            "diff": round(float(h.median() - m.median()), 4),
            "hit_mean": round(float(h.mean()), 4),
            "miss_mean": round(float(m.mean()), 4),
        }
        diff = abs(float(h.median() - m.median()))
        if diff > 0.02:
            direction = ">" if float(h.median()) > float(m.median()) else "<"
            threshold = round(float(h.median()), 4)
            filter_candidates.append(
                {
                    "feature": col,
                    "direction": direction,
                    "threshold": threshold,
                    "diff": round(diff, 4),
                    "hit_median": round(float(h.median()), 4),
                    "miss_median": round(float(m.median()), 4),
                }
            )

    filter_candidates = sorted(filter_candidates, key=lambda x: x["diff"], reverse=True)

    cat_comparison = {}
    for col in ["weather", "jcd", "top1_low_motor_flag", "top1_low_boat_flag"]:
        if col not in feat_df2.columns:
            continue
        ct = (
            feat_df2.groupby(col)["exact_hit"]
            .agg(count="count", hits="sum")
            .assign(hitrate=lambda x: (x["hits"] / x["count"]).round(4))
        )
        cat_comparison[col] = ct.to_dict("index")

    filter_combos = []
    if len(filter_candidates) >= 2 and not feat_df2.empty:
        top2 = filter_candidates[:2]
        combo_desc = " AND ".join(
            f"{f['feature']} {f['direction']} {f['threshold']}" for f in top2
        )
        mask = pd.Series([True] * len(feat_df2), index=feat_df2.index)
        for f in top2:
            col = f["feature"]
            if f["direction"] == ">":
                mask = mask & (pd.to_numeric(feat_df2[col], errors="coerce") > f["threshold"])
            else:
                mask = mask & (pd.to_numeric(feat_df2[col], errors="coerce") < f["threshold"])
        filtered = feat_df2[mask]
        filter_combos.append(
            {
                "conditions": combo_desc,
                "total": int(len(filtered)),
                "hits": int(filtered["exact_hit"].sum()) if len(filtered) > 0 else 0,
                "hitrate": round(float(filtered["exact_hit"].mean()), 4) if len(filtered) > 0 else 0,
                "coverage": round(len(filtered) / len(feat_df2), 4) if len(feat_df2) > 0 else 0,
            }
        )

    result = {
        "exact_hit_count": int(len(hit_df)),
        "miss_count": int(len(miss_df)),
        "num_comparison": num_comparison,
        "cat_comparison": {k: {str(kk): vv for kk, vv in v.items()} for k, v in cat_comparison.items()},
        "filter_candidates": filter_candidates,
        "filter_combos": filter_combos,
    }

    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_FILTER.write_text(
        json.dumps(
            {"filter_candidates": filter_candidates, "filter_combos": filter_combos},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"filter_candidates": filter_candidates[:5], "filter_combos": filter_combos}, ensure_ascii=False, indent=2))
    print(f"[saved] {OUT_JSON}")
    print(f"[saved] {OUT_FILTER}")


if __name__ == "__main__":
    main()
