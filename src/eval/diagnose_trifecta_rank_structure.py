import json
import re
from pathlib import Path

import numpy as np
import pandas as pd


SKIP_CSV = Path("data/strategy_outputs/skip_decisions.csv")
CANDIDATES_CSV = Path("data/strategy_outputs/trifecta_candidates.csv")
PROBA_CSV = Path("data/model_outputs/today_win_proba.csv")
HIST_CSV = Path("data/processed/historical_races.csv")
FEAT_CSV = Path("data/features/today_features.csv")
OUT_JSON = Path("trifecta_rank_structure.json")
OUT_CSV = Path("trifecta_rank_structure_rows.csv")


TIEBREAK_FEATS = ["national_win_rate", "local_2ren_rate"]
BETA = 0.2


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


def reconstruct_actual_trifecta(hist_df: pd.DataFrame) -> pd.DataFrame:
    required = {"race_id", "lane", "finish_position"}
    missing = required - set(hist_df.columns)
    if missing:
        raise ValueError(f"historical file missing required columns: {sorted(missing)}")

    work = hist_df.copy()
    work["race_id"] = work["race_id"].map(normalize_text)
    work["lane"] = pd.to_numeric(work["lane"], errors="coerce")
    work["finish_position"] = pd.to_numeric(work["finish_position"], errors="coerce")
    work = work.dropna(subset=["race_id", "lane", "finish_position"]).copy()

    rows = []
    for race_id, grp in work.groupby("race_id", sort=False):
        top3 = (
            grp[grp["finish_position"].isin([1, 2, 3])]
            .sort_values("finish_position")
            .copy()
        )
        if len(top3) < 3:
            continue
        rows.append(
            {
                "race_id": race_id,
                "actual_trifecta": "-".join(top3["lane"].astype(int).astype(str).tolist()),
            }
        )

    actual = pd.DataFrame(rows)
    if not actual.empty:
        actual["normalized_race_key_legacy"] = actual["race_id"].apply(normalize_race_key)
        actual["normalized_race_key"] = build_outcome_match_keys(actual["race_id"])
        actual["normalized_race_key"] = actual["normalized_race_key"].fillna(actual["normalized_race_key_legacy"])
    return actual


def load_candidates() -> pd.DataFrame:
    if not CANDIDATES_CSV.exists():
        raise FileNotFoundError(f"candidate file not found: {CANDIDATES_CSV}")
    cand = pd.read_csv(CANDIDATES_CSV)
    required = {"race_id", "trifecta", "approx_prob", "first_lane"}
    missing = required - set(cand.columns)
    if missing:
        raise ValueError(f"candidate file missing required columns: {sorted(missing)}")
    cand["race_id"] = cand["race_id"].map(normalize_text)
    cand["approx_prob"] = pd.to_numeric(cand["approx_prob"], errors="coerce").fillna(0.0)
    cand["normalized_race_key_legacy"] = cand["race_id"].apply(normalize_race_key)
    cand["normalized_race_key"] = cand["race_id"].apply(prediction_match_key)
    cand["normalized_race_key"] = cand["normalized_race_key"].fillna(cand["normalized_race_key_legacy"])
    return cand


def load_first_lane_ranks() -> pd.DataFrame:
    if not PROBA_CSV.exists():
        raise FileNotFoundError(f"probability file not found: {PROBA_CSV}")
    proba = pd.read_csv(PROBA_CSV)
    required = {"race_id", "lane", "win_proba_norm"}
    missing = required - set(proba.columns)
    if missing:
        raise ValueError(f"probability file missing required columns: {sorted(missing)}")

    feat_df = pd.read_csv(FEAT_CSV) if FEAT_CSV.exists() else pd.DataFrame()
    merged = proba.copy()
    merged["race_id"] = merged["race_id"].map(normalize_text)
    merged["lane"] = pd.to_numeric(merged["lane"], errors="coerce")
    merged["win_proba_norm"] = pd.to_numeric(merged["win_proba_norm"], errors="coerce").fillna(0.0)
    if not feat_df.empty and {"race_id", "lane"}.issubset(feat_df.columns):
        feat_df = feat_df.copy()
        feat_df["race_id"] = feat_df["race_id"].map(normalize_text)
        merged = merged.merge(feat_df, on=["race_id", "lane"], how="left")

    for feat in TIEBREAK_FEATS:
        if feat in merged.columns:
            col_min = pd.to_numeric(merged[feat], errors="coerce").min()
            col_max = pd.to_numeric(merged[feat], errors="coerce").max()
            merged[f"{feat}_scaled"] = (
                pd.to_numeric(merged[feat], errors="coerce") - col_min
            ) / (col_max - col_min + 1e-9)
        else:
            merged[f"{feat}_scaled"] = 0.0

    merged["rerank_score"] = merged["win_proba_norm"].copy()
    for feat in TIEBREAK_FEATS:
        merged["rerank_score"] += BETA * merged[f"{feat}_scaled"]

    merged = merged.sort_values(["race_id", "rerank_score"], ascending=[True, False]).copy()
    merged["winner_rank"] = merged.groupby("race_id").cumcount() + 1
    return merged[["race_id", "lane", "winner_rank", "win_proba_norm", "rerank_score"]]


def rank_distribution(series: pd.Series) -> dict[str, int | float | None]:
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return {
            "count": 0,
            "mean": None,
            "median": None,
            "rank_1_5": 0,
            "rank_6_10": 0,
            "rank_11_20": 0,
            "rank_21_40": 0,
            "rank_41_60": 0,
            "not_in_60": 0,
        }
    return {
        "count": int(len(s)),
        "mean": round(float(s.mean()), 2),
        "median": round(float(s.median()), 2),
        "rank_1_5": int(((s >= 1) & (s <= 5)).sum()),
        "rank_6_10": int(((s >= 6) & (s <= 10)).sum()),
        "rank_11_20": int(((s >= 11) & (s <= 20)).sum()),
        "rank_21_40": int(((s >= 21) & (s <= 40)).sum()),
        "rank_41_60": int(((s >= 41) & (s <= 60)).sum()),
        "not_in_60": int((s > 60).sum()),
    }


def main() -> None:
    if not CANDIDATES_CSV.exists():
        raise FileNotFoundError(f"candidate file not found: {CANDIDATES_CSV}")
    if not HIST_CSV.exists():
        raise FileNotFoundError(f"historical file not found: {HIST_CSV}")

    cand = load_candidates().rename(columns={"race_id": "candidate_race_id"})
    hist = pd.read_csv(HIST_CSV)
    actual = reconstruct_actual_trifecta(hist)
    first_lane_ranks = load_first_lane_ranks()

    merged = cand.merge(
        actual[["race_id", "actual_trifecta", "normalized_race_key"]],
        on="normalized_race_key",
        how="left",
        suffixes=("", "_actual"),
    )
    merged["actual_found"] = merged["actual_trifecta"].notna()

    # raceごとに、実三連単の候補順位を計算
    race_rows = []
    for race_id, group in merged.groupby("candidate_race_id", sort=False):
        group = group.sort_values("approx_prob", ascending=False).reset_index(drop=True)
        actual_row = group[group["actual_found"]].head(1)
        if actual_row.empty:
            continue
        actual_tri = str(actual_row.iloc[0]["actual_trifecta"]).strip()
        actual_parts = actual_tri.split("-")
        actual_winner = actual_parts[0] if len(actual_parts) == 3 else None

        tri_rank = None
        for idx, tri in enumerate(group["trifecta"].astype(str).tolist(), start=1):
            if str(tri).strip() == actual_tri:
                tri_rank = idx
                break

        winner_rank = None
        if actual_winner is not None:
            winner_rows = first_lane_ranks[first_lane_ranks["race_id"] == race_id]
            if not winner_rows.empty:
                match = winner_rows[winner_rows["lane"].astype(str).str.strip() == actual_winner]
                if not match.empty:
                    winner_rank = int(match.iloc[0]["winner_rank"])

        race_rows.append(
            {
                "race_id": race_id,
                "actual_trifecta": actual_tri,
                "actual_winner": actual_winner,
                "trifecta_rank": tri_rank if tri_rank is not None else -1,
                "winner_rank": winner_rank if winner_rank is not None else -1,
                "candidate_count": int(len(group)),
                "actual_in_candidates": int(tri_rank is not None),
            }
        )

    race_df = pd.DataFrame(race_rows)
    if race_df.empty:
        raise ValueError("no evaluable races found")

    # summary
    tri_rank = pd.to_numeric(race_df["trifecta_rank"], errors="coerce")
    winner_rank = pd.to_numeric(race_df["winner_rank"], errors="coerce")
    in_candidates = tri_rank[tri_rank > 0]
    winner_found = winner_rank[winner_rank > 0]

    summary = {
        "target": "candidate_rank_structure",
        "source_files": {
            "candidates": str(CANDIDATES_CSV),
            "historical": str(HIST_CSV),
            "probabilities": str(PROBA_CSV),
            "features": str(FEAT_CSV) if FEAT_CSV.exists() else None,
        },
        "total_races": int(len(race_df)),
        "candidate_include_rate": round(float(race_df["actual_in_candidates"].mean()), 4),
        "candidate_count_avg": round(float(race_df["candidate_count"].mean()), 2),
        "candidate_count_max": int(race_df["candidate_count"].max()),
        "actual_trifecta_rank": rank_distribution(tri_rank),
        "actual_winner_rank": rank_distribution(winner_rank),
        "hit_rates": {
            "tri_rank_1": round(float((tri_rank == 1).mean()), 4),
            "tri_rank_top5": round(float((tri_rank.between(1, 5)).mean()), 4),
            "tri_rank_top10": round(float((tri_rank.between(1, 10)).mean()), 4),
            "tri_rank_top20": round(float((tri_rank.between(1, 20)).mean()), 4),
            "tri_rank_top60": round(float((tri_rank.between(1, 60)).mean()), 4),
        },
        "winner_rank_hit_rates": {
            "winner_rank_1": round(float((winner_rank == 1).mean()), 4),
            "winner_rank_top3": round(float((winner_rank.between(1, 3)).mean()), 4),
            "winner_rank_top5": round(float((winner_rank.between(1, 5)).mean()), 4),
            "winner_rank_top10": round(float((winner_rank.between(1, 10)).mean()), 4),
        },
        "classification": {
            "winner_rank_1_and_tri_rank_1": int(((winner_rank == 1) & (tri_rank == 1)).sum()),
            "winner_rank_1_but_tri_rank_gt1": int(((winner_rank == 1) & (tri_rank > 1)).sum()),
            "winner_rank_gt1_but_tri_rank_le10": int(((winner_rank > 1) & (tri_rank.between(1, 10))).sum()),
            "winner_rank_gt1_and_tri_rank_gt10": int(((winner_rank > 1) & (tri_rank > 10)).sum()),
        },
        "notes": [
            "actual_trifecta is reconstructed from finish_position 1/2/3.",
            "trifecta_rank is the position of actual_trifecta among candidates sorted by approx_prob descending.",
            "winner_rank is the lane rank of the actual winner in today_win_proba sorted by win_proba_norm/rerank_score.",
        ],
    }

    OUT_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    race_df.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"[saved] {OUT_JSON}")
    print(f"[saved] {OUT_CSV}")


if __name__ == "__main__":
    main()
