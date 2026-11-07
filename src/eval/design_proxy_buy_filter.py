import json
import re
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd


ROWS_CSV = Path("trifecta_rank_structure_rows.csv")
PROBA_CSV = Path("data/model_outputs/today_win_proba.csv")
FEAT_CSV = Path("data/features/today_features.csv")
SKIP_CSV = Path("data/strategy_outputs/skip_decisions.csv")
HIST_CSV = Path("data/processed/historical_races.csv")
OUT_JSON = Path("proxy_filter_result.json")


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
    skip_df = pd.read_csv(SKIP_CSV)
    hist_df = pd.read_csv(HIST_CSV)

    rows_df["race_id"] = rows_df["race_id"].astype(str).str.strip()
    rows_df["winner_rank"] = pd.to_numeric(rows_df["winner_rank"], errors="coerce")
    rows_df["trifecta_rank"] = pd.to_numeric(rows_df["trifecta_rank"], errors="coerce")

    proba_df["race_id"] = proba_df["race_id"].map(normalize_text)
    feat_df["race_id"] = feat_df["race_id"].map(normalize_text)
    skip_df["race_id"] = skip_df["race_id"].map(normalize_text)
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

    race_feats = []
    for race_id, group in merged.groupby("race_id"):
        group = group.sort_values("win_proba_norm", ascending=False).reset_index(drop=True)
        top1 = group.iloc[0]
        row = rows_df[rows_df["race_id"] == str(race_id)]
        winner_rank = float(row["winner_rank"].iloc[0]) if len(row) > 0 else 99.0
        race_feats.append(
            {
                "race_id": str(race_id),
                "winner_rank": winner_rank,
                "top1_motor_2ren_rate": float(top1.get("motor_2ren_rate", 99.0)),
                "top1_boat_2ren_rate": float(top1.get("boat_2ren_rate", 99.0)),
                "top1_local_2ren_rate": float(top1.get("local_2ren_rate", 99.0)),
                "top1_national_win_rate": float(top1.get("national_win_rate", 99.0)),
                "score_gap_top1_top2": float(group["win_proba_norm"].iloc[0] - group["win_proba_norm"].iloc[1]) if len(group) >= 2 else 0.0,
                "score_gap_top1_top3": float(group["win_proba_norm"].iloc[0] - group["win_proba_norm"].iloc[2]) if len(group) >= 3 else 0.0,
                "top1_win_proba": float(group["win_proba_norm"].iloc[0]),
                "trifecta_rank": int(row["trifecta_rank"].iloc[0]) if len(row) > 0 and pd.notna(row["trifecta_rank"].iloc[0]) else -1,
            }
        )

    feat_df2 = pd.DataFrame(race_feats)
    feat_df2["exact"] = (feat_df2["trifecta_rank"] == 1).astype(int)
    feat_df2["good"] = feat_df2["trifecta_rank"].between(1, 5).astype(int)

    proxy_cols = [
        "score_gap_top1_top2",
        "score_gap_top1_top3",
        "top1_win_proba",
        "top1_motor_2ren_rate",
        "top1_boat_2ren_rate",
        "top1_national_win_rate",
        "top1_local_2ren_rate",
    ]

    results = {}
    for col in proxy_cols:
        s = pd.to_numeric(feat_df2[col], errors="coerce").dropna()
        if s.empty:
            continue
        best = {"exact_hitrate": 0, "threshold": None, "direction": None, "count": 0}
        for pct in [0.25, 0.40, 0.50, 0.60, 0.75]:
            thresh = float(s.quantile(pct))
            for direction in [">", "<"]:
                mask = feat_df2[col] > thresh if direction == ">" else feat_df2[col] < thresh
                sub = feat_df2[mask]
                if len(sub) < 15:
                    continue
                exact_hr = float(sub["exact"].mean())
                if exact_hr > best["exact_hitrate"]:
                    best = {
                        "exact_hitrate": round(exact_hr, 4),
                        "good_hitrate": round(float(sub["good"].mean()), 4),
                        "threshold": round(thresh, 4),
                        "direction": direction,
                        "count": len(sub),
                        "exact_count": int(sub["exact"].sum()),
                    }
        results[col] = best

    top5 = sorted(
        [(col, v) for col, v in results.items() if v.get("threshold") is not None],
        key=lambda x: x[1]["exact_hitrate"],
        reverse=True,
    )[:5]

    combo_results = []
    for (c1, v1), (c2, v2) in combinations(top5, 2):
        mask = pd.Series([True] * len(feat_df2), index=feat_df2.index)
        for col, v in [(c1, v1), (c2, v2)]:
            if v["direction"] == ">":
                mask &= feat_df2[col] > v["threshold"]
            else:
                mask &= feat_df2[col] < v["threshold"]
        sub = feat_df2[mask]
        if len(sub) < 10:
            continue
        combo_results.append(
            {
                "conditions": f"{c1}{v1['direction']}{v1['threshold']} AND {c2}{v2['direction']}{v2['threshold']}",
                "count": len(sub),
                "exact_count": int(sub["exact"].sum()),
                "exact_hitrate": round(float(sub["exact"].mean()), 4),
                "good_hitrate": round(float(sub["good"].mean()), 4),
                "coverage": round(len(sub) / len(feat_df2), 4),
            }
        )

    combo_results = sorted(combo_results, key=lambda x: x["exact_hitrate"], reverse=True)[:5]

    final = {
        "total_races": int(len(feat_df2)),
        "exact_total": int(feat_df2["exact"].sum()),
        "single_filters": {col: v for col, v in top5},
        "combo_filters": combo_results,
        "recommended": combo_results[0] if combo_results else (top5[0][1] if top5 else None),
    }

    OUT_JSON.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(final, ensure_ascii=False, indent=2))
    print(f"[saved] {OUT_JSON}")


if __name__ == "__main__":
    main()
