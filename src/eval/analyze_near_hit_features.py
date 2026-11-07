import json
import re
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd


ROWS_CSV = Path("trifecta_rank_structure_rows.csv")
PROBA_CSV = Path("data/model_outputs/today_win_proba.csv")
FEAT_CSV = Path("data/features/today_features.csv")
HIST_CSV = Path("data/processed/historical_races.csv")
OUT_JSON = Path("near_hit_feature_analysis.json")
OUT_FILTER = Path("near_hit_filter_candidates.json")


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

        tri_rank = int(row["trifecta_rank"]) if pd.notna(row["trifecta_rank"]) else -1
        if tri_rank == 1:
            group = "exact"
        elif 2 <= tri_rank <= 5:
            group = "near"
        elif tri_rank > 5:
            group = "miss"
        else:
            continue

        race_data = merged[merged["race_id"] == race_id].copy()
        if race_data.empty:
            continue
        race_data = race_data.sort_values("win_proba_norm", ascending=False).reset_index(drop=True)
        scores = race_data["win_proba_norm"].astype(float).values
        top1 = race_data.iloc[0]

        hist_race = hist_df[hist_df["race_id"] == race_key]
        def hist_val(col):
            return hist_race[col].iloc[0] if col in hist_race.columns and len(hist_race) > 0 else None

        race_rows.append(
            {
                "race_id": race_id,
                "group": group,
                "tri_rank": tri_rank,
                "winner_rank": int(row["winner_rank"]) if pd.notna(row["winner_rank"]) else -1,
                "score_gap_top1_top2": round(float(scores[0] - scores[1]), 4) if len(scores) >= 2 else 0.0,
                "score_gap_top1_top3": round(float(scores[0] - scores[2]), 4) if len(scores) >= 3 else 0.0,
                "score_entropy": round(
                    float(-np.sum((scores / scores.sum()) * np.log(scores / scores.sum() + 1e-9))),
                    4,
                ),
                "top1_win_proba": round(float(top1["win_proba_norm"]), 4),
                "top1_national_win_rate": round(float(top1.get("national_win_rate", 0)), 4),
                "top1_local_2ren_rate": round(float(top1.get("local_2ren_rate", 0)), 4),
                "top1_motor_2ren_rate": round(float(top1.get("motor_2ren_rate", 0)), 4),
                "top1_boat_2ren_rate": round(float(top1.get("boat_2ren_rate", 0)), 4),
                "top1_low_motor_flag": int(top1.get("low_motor_flag", 0)),
                "top1_low_boat_flag": int(top1.get("low_boat_flag", 0)),
                "weather": hist_val("weather"),
                "wind_speed": hist_val("wind_speed"),
                "wave_height": hist_val("wave_height"),
                "jcd": hist_val("jcd"),
            }
        )

    feat_df2 = pd.DataFrame(race_rows)
    exact_df = feat_df2[feat_df2["group"] == "exact"]
    near_df = feat_df2[feat_df2["group"] == "near"]
    miss_df = feat_df2[feat_df2["group"] == "miss"]
    good_df = feat_df2[feat_df2["group"].isin(["exact", "near"])]

    print(f"exact={len(exact_df)} / near={len(near_df)} / miss={len(miss_df)}")

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
        e = pd.to_numeric(exact_df[col], errors="coerce").dropna()
        n = pd.to_numeric(near_df[col], errors="coerce").dropna()
        m = pd.to_numeric(miss_df[col], errors="coerce").dropna()
        g = pd.to_numeric(good_df[col], errors="coerce").dropna()
        if len(g) < 5:
            continue

        num_comparison[col] = {
            "exact_median": round(float(e.median()), 4) if len(e) > 0 else None,
            "near_median": round(float(n.median()), 4) if len(n) > 0 else None,
            "miss_median": round(float(m.median()), 4) if len(m) > 0 else None,
            "good_median": round(float(g.median()), 4),
            "diff_good_miss": round(float(g.median() - m.median()), 4) if len(m) > 0 else None,
        }

        if len(m) > 0:
            diff = abs(float(g.median() - m.median()))
            if diff > 0.02:
                direction = ">" if float(g.median()) > float(m.median()) else "<"
                filter_candidates.append(
                    {
                        "feature": col,
                        "direction": direction,
                        "threshold": round(float(g.median()), 4),
                        "diff": round(diff, 4),
                        "good_median": round(float(g.median()), 4),
                        "miss_median": round(float(m.median()), 4),
                    }
                )

    filter_candidates = sorted(filter_candidates, key=lambda x: x["diff"], reverse=True)

    feat_df2["is_good"] = feat_df2["group"].isin(["exact", "near"]).astype(int)
    filter_combos = []
    top_cands = filter_candidates[:6]
    if top_cands:
        for r in [1, 2, 3]:
            for combo in combinations(top_cands, r):
                mask = pd.Series([True] * len(feat_df2), index=feat_df2.index)
                for f in combo:
                    col = f["feature"]
                    if col not in feat_df2.columns:
                        continue
                    col_series = pd.to_numeric(feat_df2[col], errors="coerce")
                    if f["direction"] == ">":
                        mask = mask & (col_series > f["threshold"])
                    else:
                        mask = mask & (col_series < f["threshold"])

                filtered = feat_df2[mask]
                if len(filtered) < 10:
                    continue
                filter_combos.append(
                    {
                        "conditions": " AND ".join(
                            f"{f['feature']} {f['direction']} {f['threshold']}" for f in combo
                        ),
                        "n_conditions": r,
                        "total": int(len(filtered)),
                        "exact_hits": int((filtered["group"] == "exact").sum()),
                        "near_hits": int((filtered["group"] == "near").sum()),
                        "good_hits": int(filtered["is_good"].sum()),
                        "hitrate": round(float(filtered["is_good"].mean()), 4),
                        "coverage": round(len(filtered) / len(feat_df2), 4),
                    }
                )

    filter_combos = sorted(
        [c for c in filter_combos if c["coverage"] >= 0.05],
        key=lambda x: (x["hitrate"], x["exact_hits"]),
        reverse=True,
    )[:10]

    result = {
        "group_counts": {
            "exact": int(len(exact_df)),
            "near": int(len(near_df)),
            "miss": int(len(miss_df)),
        },
        "num_comparison": num_comparison,
        "filter_candidates": filter_candidates[:8],
        "filter_combos": filter_combos,
    }

    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_FILTER.write_text(
        json.dumps(
            {"filter_candidates": filter_candidates[:8], "filter_combos": filter_combos},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "group_counts": result["group_counts"],
                "top_filter_candidates": filter_candidates[:5],
                "top_filter_combos": filter_combos[:5],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print(f"[saved] {OUT_JSON}")
    print(f"[saved] {OUT_FILTER}")


if __name__ == "__main__":
    main()
