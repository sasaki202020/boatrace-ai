import json
from pathlib import Path

import pandas as pd


ROWS_CSV = Path("trifecta_rank_structure_rows.csv")
PROBA_CSV = Path("data/model_outputs/today_win_proba.csv")
FEAT_CSV = Path("data/features/today_features.csv")
OUT_JSON = Path("rank1_5_diagnosis.json")


def load_inputs() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    rows_df = pd.read_csv(ROWS_CSV)
    proba_df = pd.read_csv(PROBA_CSV)
    feat_df = pd.read_csv(FEAT_CSV)
    return rows_df, proba_df, feat_df


def pick_numeric_feature_cols(df: pd.DataFrame) -> list[str]:
    exclude = {"race_id", "lane"}
    cols = []
    for col in df.columns:
        if col in exclude:
            continue
        if pd.api.types.is_numeric_dtype(df[col]):
            cols.append(col)
    return cols


def race_top1_features(race_ids: pd.Series, merged: pd.DataFrame, feat_cols: list[str]) -> pd.DataFrame:
    records = []
    race_ids = race_ids.astype(str)
    for race_id in race_ids:
        race = merged[merged["race_id"].astype(str) == race_id]
        if race.empty:
            continue
        top1 = race.sort_values("win_proba_norm", ascending=False).iloc[0]
        rec = {"race_id": race_id}
        for feat in feat_cols:
            rec[feat] = top1.get(feat)
        rec["top1_win_proba_norm"] = float(top1["win_proba_norm"])
        if len(race) >= 2:
            ordered = race.sort_values("win_proba_norm", ascending=False)["win_proba_norm"].astype(float).tolist()
            rec["score_gap_top1_top2"] = float(ordered[0] - ordered[1])
        else:
            rec["score_gap_top1_top2"] = None
        records.append(rec)
    return pd.DataFrame(records)


def summarize_group(df: pd.DataFrame, feat_cols: list[str]) -> dict:
    if df.empty:
        return {"count": 0}

    summary = {"count": int(len(df))}
    if "winner_rank" in df.columns:
        summary["winner_rank_median"] = round(float(df["winner_rank"].median()), 2)
        summary["winner_rank_mean"] = round(float(df["winner_rank"].mean()), 2)
        summary["winner_rank_top3_rate"] = round(float((df["winner_rank"] <= 3).mean()), 4)

    if "top1_win_proba_norm" in df.columns:
        summary["top1_win_proba_norm_median"] = round(float(df["top1_win_proba_norm"].median()), 6)
        summary["top1_win_proba_norm_mean"] = round(float(df["top1_win_proba_norm"].mean()), 6)

    if "score_gap_top1_top2" in df.columns:
        gap = df["score_gap_top1_top2"].dropna()
        if not gap.empty:
            summary["score_gap_top1_top2_median"] = round(float(gap.median()), 6)
            summary["score_gap_top1_top2_mean"] = round(float(gap.mean()), 6)

    for feat in feat_cols:
        if feat not in df.columns:
            continue
        s = pd.to_numeric(df[feat], errors="coerce").dropna()
        if s.empty:
            continue
        summary.setdefault("features", {})[feat] = {
            "median": round(float(s.median()), 4),
            "mean": round(float(s.mean()), 4),
        }

    return summary


def main() -> None:
    rows_df, proba_df, feat_df = load_inputs()

    rows_df = rows_df.copy()
    rows_df["race_id"] = rows_df["race_id"].astype(str).str.strip()
    rows_df["trifecta_rank"] = pd.to_numeric(rows_df["trifecta_rank"], errors="coerce")
    rows_df["winner_rank"] = pd.to_numeric(rows_df["winner_rank"], errors="coerce")

    merged = proba_df.merge(feat_df, on=["race_id", "lane"], how="left")
    merged["race_id"] = merged["race_id"].astype(str).str.strip()

    feat_cols = pick_numeric_feature_cols(merged)

    good_ids = rows_df.loc[rows_df["trifecta_rank"].between(1, 5), "race_id"]
    bad_ids = rows_df.loc[rows_df["trifecta_rank"] > 10, "race_id"]

    good_df = race_top1_features(good_ids, merged, feat_cols)
    bad_df = race_top1_features(bad_ids, merged, feat_cols)

    feat_diff = {}
    for feat in feat_cols:
        if feat not in good_df.columns or feat not in bad_df.columns:
            continue
        g = pd.to_numeric(good_df[feat], errors="coerce").dropna()
        b = pd.to_numeric(bad_df[feat], errors="coerce").dropna()
        if len(g) < 5 or len(b) < 5:
            continue
        feat_diff[feat] = {
            "good_median": round(float(g.median()), 4),
            "bad_median": round(float(b.median()), 4),
            "diff": round(float(g.median() - b.median()), 4),
        }

    top_discriminating_features = dict(
        sorted(
            feat_diff.items(),
            key=lambda kv: abs(kv[1]["diff"]),
            reverse=True,
        )[:15]
    )

    result = {
        "good_count": int(len(good_df)),
        "bad_count": int(len(bad_df)),
        "good_summary": summarize_group(good_df, feat_cols),
        "bad_summary": summarize_group(bad_df, feat_cols),
        "top_discriminating_features": top_discriminating_features,
        "winner_rank_by_group": {
            "good": summarize_group(rows_df[rows_df["trifecta_rank"].between(1, 5)], feat_cols),
            "bad": summarize_group(rows_df[rows_df["trifecta_rank"] > 10], feat_cols),
        },
        "diagnosis": (
            "score_concentration_high_and_feature_diff_present"
            if (
                not good_df.empty
                and not bad_df.empty
                and "score_gap_top1_top2" in good_df.columns
                and "score_gap_top1_top2" in bad_df.columns
                and good_df["score_gap_top1_top2"].median() > bad_df["score_gap_top1_top2"].median()
            )
            else "needs_further_review"
        ),
        "notes": [
            "good = trifecta_rank 1..5",
            "bad = trifecta_rank > 10",
            "top1 features are taken from the highest win_proba_norm horse in each race",
        ],
    }

    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"[saved] {OUT_JSON}")


if __name__ == "__main__":
    main()
