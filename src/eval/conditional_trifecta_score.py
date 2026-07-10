import json
import re
from itertools import permutations
from pathlib import Path

import numpy as np
import pandas as pd


PROBA_CSV = Path("data/model_outputs/today_win_proba.csv")
FEAT_CSV = Path("data/features/today_features.csv")
HIST_CSV = Path("data/processed/historical_races.csv")
OUT_JSON = Path("conditional_score_comparison.json")

WIN_FEAT = "national_win_rate"
PLACE2_FEATS = ["local_2ren_rate", "national_2ren_rate"]
PLACE3_FEATS = ["boat_2ren_rate", "motor_2ren_rate"]
WIN_BETA = 0.2


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


def scale_series(s: pd.Series) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce")
    if s.dropna().empty:
        return pd.Series(0.0, index=s.index)
    col_min = float(s.min())
    col_max = float(s.max())
    return ((s - col_min) / (col_max - col_min + 1e-9)).fillna(0.0)


def reconstruct_actual_trifecta(hist_df: pd.DataFrame) -> pd.DataFrame:
    work = hist_df.copy()
    work["race_id"] = work["race_id"].astype(str).str.strip()
    work["lane"] = pd.to_numeric(work["lane"], errors="coerce")
    work["finish_position"] = pd.to_numeric(work["finish_position"], errors="coerce")
    work = work.dropna(subset=["race_id", "lane", "finish_position"]).copy()

    rows = []
    for race_id, grp in work.groupby("race_id", sort=False):
        top3 = grp[grp["finish_position"].isin([1, 2, 3])].sort_values("finish_position")
        if len(top3) < 3:
            continue
        rows.append(
            {
                "race_id": race_id,
                "actual_trifecta": "-".join(top3["lane"].astype(int).astype(str).tolist()),
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    proba_df = pd.read_csv(PROBA_CSV)
    feat_df = pd.read_csv(FEAT_CSV)
    hist_df = pd.read_csv(HIST_CSV)

    proba_df["race_id"] = proba_df["race_id"].map(normalize_text)
    feat_df["race_id"] = feat_df["race_id"].map(normalize_text)
    hist_df["race_id"] = hist_df["race_id"].map(normalize_text)

    merged = proba_df.merge(feat_df, on=["race_id", "lane"], how="left")
    merged["normalized_race_key_legacy"] = merged["race_id"].apply(normalize_race_key)
    merged["normalized_race_key"] = merged["race_id"].apply(prediction_match_key)
    merged["normalized_race_key"] = merged["normalized_race_key"].fillna(merged["normalized_race_key_legacy"])

    for col in [WIN_FEAT] + PLACE2_FEATS + PLACE3_FEATS:
        if col not in merged.columns:
            merged[f"{col}_scaled"] = 0.0
        else:
            merged[f"{col}_scaled"] = scale_series(merged[col])

    top3 = reconstruct_actual_trifecta(hist_df)
    top3["race_id"] = top3["race_id"].astype(str)
    top3["normalized_race_key_legacy"] = top3["race_id"].apply(normalize_race_key)
    top3["normalized_race_key"] = build_outcome_match_keys(top3["race_id"])
    top3["normalized_race_key"] = top3["normalized_race_key"].fillna(top3["normalized_race_key_legacy"])

    race_ids = merged["race_id"].unique()
    counters = {
        "baseline": {"tri_ranks": [], "exact": 0},
        "conditional": {"tri_ranks": [], "exact": 0},
    }

    for race_id in race_ids:
        race_key = merged.loc[merged["race_id"] == race_id, "normalized_race_key"].iloc[0]
        actual_row = top3[top3["normalized_race_key"] == race_key]
        if actual_row.empty:
            continue
        actual_tri = str(actual_row.iloc[0]["actual_trifecta"])
        parts = actual_tri.split("-")
        if len(parts) != 3:
            continue
        w1, w2, w3 = parts

        race_data = merged[merged["race_id"] == race_id].copy()
        if race_data.empty:
            continue

        horses = race_data["lane"].astype(str).str.strip().tolist()
        if w1 not in horses:
            continue

        win_map = dict(
            zip(
                race_data["lane"].astype(str).str.strip(),
                pd.to_numeric(race_data["win_proba_norm"], errors="coerce").fillna(0.0)
                + WIN_BETA * pd.to_numeric(race_data[f"{WIN_FEAT}_scaled"], errors="coerce").fillna(0.0),
            )
        )
        place2_map = dict(
            zip(
                race_data["lane"].astype(str).str.strip(),
                race_data[[f"{f}_scaled" for f in PLACE2_FEATS]].fillna(0.0).mean(axis=1),
            )
        )
        place3_map = dict(
            zip(
                race_data["lane"].astype(str).str.strip(),
                race_data[[f"{f}_scaled" for f in PLACE3_FEATS]].fillna(0.0).mean(axis=1),
            )
        )
        mean_win = float(np.mean(list(win_map.values()))) if win_map else 0.0

        def baseline_score(h1, h2, h3):
            return win_map.get(h1, 0.0) * win_map.get(h2, 0.0) * win_map.get(h3, 0.0)

        def conditional_score(h1, h2, h3):
            w_a = float(win_map.get(h1, 0.0))
            dominance = w_a / (mean_win + 1e-9)
            p2 = float(place2_map.get(h2, 0.0)) * max(0.5, 1.0 - 0.1 * dominance)
            p3 = float(place3_map.get(h3, 0.0))
            return w_a * (p2 + 1e-6) * (p3 + 1e-6)

        perms = list(permutations(horses, 3))
        for config_name, score_fn in [
            ("baseline", baseline_score),
            ("conditional", conditional_score),
        ]:
            scored = sorted(perms, key=lambda p: score_fn(*p), reverse=True)
            tri_rank = -1
            for i, (h1, h2, h3) in enumerate(scored):
                if h1 == w1 and h2 == w2 and h3 == w3:
                    tri_rank = i + 1
                    break
            counters[config_name]["tri_ranks"].append(tri_rank)
            if tri_rank == 1:
                counters[config_name]["exact"] += 1

    result = {"comparison": {}, "verdict": ""}
    for config_name, c in counters.items():
        tr = pd.Series(c["tri_ranks"])
        valid = tr[tr > 0]
        result["comparison"][config_name] = {
            "median": round(float(valid.median()), 2),
            "mean": round(float(valid.mean()), 2),
            "in_top5": int((valid <= 5).sum()),
            "in_top10": int((valid <= 10).sum()),
            "exact": c["exact"],
            "not_found": int((tr == -1).sum()),
        }

    base_med = result["comparison"]["baseline"]["median"]
    cond_med = result["comparison"]["conditional"]["median"]
    result["verdict"] = (
        f"conditional が {base_med - cond_med:.2f} rank 改善 → 本体反映候補"
        if cond_med < base_med
        else "改善なし → score 設計の見直しが必要"
    )

    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"[saved] {OUT_JSON}")


if __name__ == "__main__":
    main()
