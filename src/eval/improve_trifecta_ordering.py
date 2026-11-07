import json
import re
from pathlib import Path

import numpy as np
import pandas as pd


ROWS_CSV = Path("trifecta_rank_structure_rows.csv")
PROBA_CSV = Path("data/model_outputs/today_win_proba.csv")
FEAT_CSV = Path("data/features/today_features.csv")
HIST_CSV = Path("data/processed/historical_races.csv")
OUT_JSON = Path("trifecta_ordering_diagnosis.json")
OUT_CSV = Path("trifecta_ordering_feature_diff.csv")

TIEBREAK_FEATS = ["national_win_rate", "local_2ren_rate"]
BETA = 0.2


def normalize_text(value) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def load_actual_ranks(rows_csv: Path) -> pd.DataFrame:
    if not rows_csv.exists():
        raise FileNotFoundError(f"rank structure rows not found: {rows_csv}")
    df = pd.read_csv(rows_csv)
    required = {"race_id", "actual_trifecta", "trifecta_rank", "winner_rank"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"rank structure rows missing columns: {sorted(missing)}")
    df["race_id"] = df["race_id"].map(normalize_text)
    df["actual_trifecta"] = df["actual_trifecta"].map(normalize_text)
    df["trifecta_rank"] = pd.to_numeric(df["trifecta_rank"], errors="coerce").fillna(-1).astype(int)
    df["winner_rank"] = pd.to_numeric(df["winner_rank"], errors="coerce").fillna(-1).astype(int)
    return df


def build_rerank_scores(proba_df: pd.DataFrame, feat_df: pd.DataFrame) -> pd.DataFrame:
    merged = proba_df.copy()
    merged["race_id"] = merged["race_id"].map(normalize_text)
    merged["lane"] = pd.to_numeric(merged["lane"], errors="coerce")
    merged["win_proba_norm"] = pd.to_numeric(merged["win_proba_norm"], errors="coerce").fillna(0.0)
    if not feat_df.empty and {"race_id", "lane"}.issubset(feat_df.columns):
        feat_df = feat_df.copy()
        feat_df["race_id"] = feat_df["race_id"].map(normalize_text)
        feat_df["lane"] = pd.to_numeric(feat_df["lane"], errors="coerce")
        merged = merged.merge(feat_df, on=["race_id", "lane"], how="left")

    for feat in TIEBREAK_FEATS:
        if feat in merged.columns:
            s = pd.to_numeric(merged[feat], errors="coerce")
            merged[f"{feat}_scaled"] = (s - s.min()) / (s.max() - s.min() + 1e-9)
        else:
            merged[f"{feat}_scaled"] = 0.0

    merged["rerank_score"] = merged["win_proba_norm"].copy()
    for feat in TIEBREAK_FEATS:
        merged["rerank_score"] += BETA * merged[f"{feat}_scaled"]

    merged = merged.sort_values(["race_id", "rerank_score"], ascending=[True, False]).copy()
    merged["lane_rank"] = merged.groupby("race_id").cumcount() + 1
    return merged


def summarize_rank_groups(df: pd.DataFrame) -> dict[str, object]:
    tri_rank = pd.to_numeric(df["trifecta_rank"], errors="coerce")
    winner_rank = pd.to_numeric(df["winner_rank"], errors="coerce")
    good_mask = tri_rank.between(1, 5)
    mid_mask = tri_rank.between(6, 10)
    bad_mask = tri_rank.gt(10)

    def stats(mask):
        s = df.loc[mask]
        if s.empty:
            return {"count": 0}
        return {
            "count": int(len(s)),
            "tri_rank_mean": round(float(pd.to_numeric(s["trifecta_rank"], errors="coerce").mean()), 2),
            "tri_rank_median": round(float(pd.to_numeric(s["trifecta_rank"], errors="coerce").median()), 2),
            "winner_rank_mean": round(float(pd.to_numeric(s["winner_rank"], errors="coerce").mean()), 2),
            "winner_rank_median": round(float(pd.to_numeric(s["winner_rank"], errors="coerce").median()), 2),
            "winner_rank_le_3_rate": round(float((pd.to_numeric(s["winner_rank"], errors="coerce") <= 3).mean()), 4),
            "winner_rank_gt_5_rate": round(float((pd.to_numeric(s["winner_rank"], errors="coerce") > 5).mean()), 4),
            "tri_rank_le_10_rate": round(float((pd.to_numeric(s["trifecta_rank"], errors="coerce") <= 10).mean()), 4),
        }

    return {
        "all": stats(tri_rank > 0),
        "good": stats(good_mask),
        "mid": stats(mid_mask),
        "bad": stats(bad_mask),
    }


def collect_feature_diffs(race_df: pd.DataFrame, merged: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    exclude_cols = {"race_id", "lane", "win_proba_norm"}
    feature_cols = [
        c
        for c in merged.columns
        if c not in exclude_cols
        and not c.endswith("_scaled")
        and pd.api.types.is_numeric_dtype(merged[c])
    ]

    records = []
    for _, row in race_df.iterrows():
        race_id = row["race_id"]
        tri_rank = int(row["trifecta_rank"])
        if tri_rank <= 0:
            continue
        actual_parts = str(row["actual_trifecta"]).split("-")
        if len(actual_parts) != 3:
            continue
        w1, w2, w3 = actual_parts
        race_data = merged[merged["race_id"] == race_id].copy()
        if race_data.empty:
            continue
        race_data = race_data.sort_values("rerank_score", ascending=False).reset_index(drop=True)
        ranked_lanes = race_data["lane"].astype(int).astype(str).tolist()

        def lane_rank(lane: str) -> int:
            return ranked_lanes.index(lane) + 1 if lane in ranked_lanes else -1

        r1 = lane_rank(w1)
        r2 = lane_rank(w2)
        r3 = lane_rank(w3)
        group = "good" if tri_rank <= 5 else "mid" if tri_rank <= 10 else "bad"
        records.append(
            {
                "race_id": race_id,
                "group": group,
                "actual_trifecta": row["actual_trifecta"],
                "trifecta_rank": tri_rank,
                "winner_rank": int(row["winner_rank"]),
                "second_rank": r2,
                "third_rank": r3,
                "winner_rank_gt_1": int(r1 > 1),
                "second_gt_5": int(r2 > 5 if r2 > 0 else False),
                "third_gt_5": int(r3 > 5 if r3 > 0 else False),
                "second_gt_10": int(r2 > 10 if r2 > 0 else False),
                "third_gt_10": int(r3 > 10 if r3 > 0 else False),
            }
        )

    diff_df = pd.DataFrame(records)
    if diff_df.empty:
        return diff_df, pd.DataFrame()

    # feature medians for actual 2nd/3rd lanes, good vs bad
    feat_records = []
    grouped = diff_df[diff_df["group"].isin(["good", "bad"])].copy()
    for feat in feature_cols:
        feat_good = []
        feat_bad = []
        for _, row in grouped.iterrows():
            race_id = row["race_id"]
            actual_parts = row["actual_trifecta"].split("-")
            if len(actual_parts) != 3:
                continue
            _, w2, w3 = actual_parts
            race_data = merged[merged["race_id"] == race_id]
            for lane in [w2, w3]:
                lane_rows = race_data[race_data["lane"].astype(int).astype(str) == lane]
                if lane_rows.empty or feat not in lane_rows.columns:
                    continue
                val = pd.to_numeric(lane_rows.iloc[0][feat], errors="coerce")
                if pd.isna(val):
                    continue
                if row["group"] == "good":
                    feat_good.append(float(val))
                else:
                    feat_bad.append(float(val))
        if len(feat_good) >= 5 and len(feat_bad) >= 5:
            good_s = pd.Series(feat_good)
            bad_s = pd.Series(feat_bad)
            feat_records.append(
                {
                    "feature": feat,
                    "good_median": round(float(good_s.median()), 4),
                    "bad_median": round(float(bad_s.median()), 4),
                    "diff": round(float(good_s.median() - bad_s.median()), 4),
                    "good_higher": bool(good_s.median() > bad_s.median()),
                    "good_count": int(len(good_s)),
                    "bad_count": int(len(bad_s)),
                }
            )

    feat_df_out = pd.DataFrame(feat_records).sort_values("diff", ascending=False)
    return diff_df, feat_df_out


def main() -> None:
    if not ROWS_CSV.exists():
        raise FileNotFoundError(f"rank structure rows not found: {ROWS_CSV}")
    if not PROBA_CSV.exists():
        raise FileNotFoundError(f"probability file not found: {PROBA_CSV}")
    if not HIST_CSV.exists():
        raise FileNotFoundError(f"historical file not found: {HIST_CSV}")

    rows_df = load_actual_ranks(ROWS_CSV)
    proba_df = pd.read_csv(PROBA_CSV)
    feat_df = pd.read_csv(FEAT_CSV) if FEAT_CSV.exists() else pd.DataFrame()
    hist_df = pd.read_csv(HIST_CSV)

    merged = build_rerank_scores(proba_df, feat_df)
    tri_map = rows_df[["race_id", "actual_trifecta", "trifecta_rank", "winner_rank"]].copy()
    tri_map["race_id"] = tri_map["race_id"].map(normalize_text)

    # rank structure of the exact trifecta
    rank_rows = []
    for race_id, group in merged.groupby("race_id", sort=False):
        group = group.sort_values("rerank_score", ascending=False).reset_index(drop=True)
        actual = tri_map[tri_map["race_id"] == race_id]
        if actual.empty:
            continue
        actual_tri = str(actual.iloc[0]["actual_trifecta"]).strip()
        parts = actual_tri.split("-")
        if len(parts) != 3:
            continue
        w1, w2, w3 = parts
        lanes = group["lane"].astype(int).astype(str).tolist()
        tri_rank = int(actual.iloc[0]["trifecta_rank"])
        rank_rows.append(
            {
                "race_id": race_id,
                "actual_trifecta": actual_tri,
                "actual_winner": w1,
                "actual_second": w2,
                "actual_third": w3,
                "trifecta_rank": tri_rank,
                "winner_rank": lanes.index(w1) + 1 if w1 in lanes else -1,
                "second_rank": lanes.index(w2) + 1 if w2 in lanes else -1,
                "third_rank": lanes.index(w3) + 1 if w3 in lanes else -1,
                "winner_in_top3": int(w1 in lanes[:3]),
                "second_in_top3": int(w2 in lanes[:3]),
                "third_in_top3": int(w3 in lanes[:3]),
            }
        )

    rank_df = pd.DataFrame(rank_rows)
    if rank_df.empty:
        raise ValueError("no rank rows found")

    rank_df["group"] = np.where(rank_df["trifecta_rank"] <= 5, "good", np.where(rank_df["trifecta_rank"] <= 10, "mid", "bad"))
    rank_df["is_top1_exact"] = rank_df["trifecta_rank"].eq(1)
    rank_df["is_top10_exact"] = rank_df["trifecta_rank"].between(1, 10)

    group_stats = summarize_rank_groups(rank_df)
    breakdown = (
        rank_df.groupby("group")[["winner_rank", "second_rank", "third_rank"]]
        .agg(["count", "mean", "median"])
        .round(2)
    )

    diff_df, feat_summary_df = collect_feature_diffs(rank_df, merged)

    diff_df.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
    if not feat_summary_df.empty:
        feat_summary = feat_summary_df.head(15).to_dict(orient="records")
    else:
        feat_summary = []

    diagnosis = {
        "target": "2/3_ordering_diagnosis",
        "source_files": {
            "rank_structure": str(ROWS_CSV),
            "probabilities": str(PROBA_CSV),
            "features": str(FEAT_CSV) if FEAT_CSV.exists() else None,
            "historical": str(HIST_CSV),
        },
        "rank_group_stats": group_stats,
        "group_rank_breakdown": json.loads(breakdown.to_json()),
        "feature_candidates_top15": feat_summary,
        "classification": {
            "winner_rank_1_but_tri_rank_gt1": int(((rank_df["winner_rank"] == 1) & (rank_df["trifecta_rank"] > 1)).sum()),
            "winner_rank_gt1_but_tri_rank_le10": int(((rank_df["winner_rank"] > 1) & (rank_df["trifecta_rank"].between(1, 10))).sum()),
            "winner_rank_gt1_and_tri_rank_gt10": int(((rank_df["winner_rank"] > 1) & (rank_df["trifecta_rank"] > 10)).sum()),
        },
        "diagnosis": (
            "2/3着が候補内で沈んでいるレースが多く、順序スコアの改善が優先"
            if int(((rank_df["winner_rank"] == 1) & (rank_df["trifecta_rank"] > 1)).sum()) >= int(((rank_df["winner_rank"] > 1) & (rank_df["trifecta_rank"] > 10)).sum())
            else "1着外れも残るが、2/3着順序の改善余地が大きい"
        ),
        "notes": [
            "tri_rank uses approx_prob order over candidates.",
            "winner_rank/second_rank/third_rank use rerank_score order over lanes.",
            "feature_candidates_top15 ranks features by median(good) - median(bad) on actual 2nd/3rd lanes.",
        ],
    }

    OUT_JSON.write_text(json.dumps(diagnosis, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(diagnosis, ensure_ascii=False, indent=2))
    print(f"[saved] {OUT_JSON}")
    print(f"[saved] {OUT_CSV}")


if __name__ == "__main__":
    main()
