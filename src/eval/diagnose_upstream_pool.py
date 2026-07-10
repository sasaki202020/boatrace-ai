import argparse
import json
import re
from pathlib import Path

import pandas as pd


def normalize_text(value) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def build_match_key(race_id: object) -> str | None:
    rid = normalize_text(race_id)
    if not rid:
        return None

    m = re.match(r"^(\d{8})-[A-Z]\d{6}-(\d{1,3})$", rid)
    if m:
        date8, serial = m.groups()
        serial_i = int(serial)
        section_compact = (serial_i - 1) // 12 + 1
        race_no = (serial_i - 1) % 12 + 1
        return f"d{date8}-c{section_compact:02d}-r{race_no:02d}"

    m = re.match(r"^(\d{8})-[A-Z]\d{6}_s(\d{2})-(\d{2})$", rid)
    if m:
        date8, section_raw, race_no = m.groups()
        return f"d{date8}-s{int(section_raw):02d}-r{int(race_no):02d}"

    m = re.match(r"^(\d{8})-(\d{2})-(\d{2})$", rid)
    if m:
        date8, venue, race_no = m.groups()
        return f"d{date8}-v{int(venue):02d}-r{int(race_no):02d}"

    return rid


def compact_outcome_sections(keys: pd.Series) -> pd.Series:
    parsed = keys.astype(str).str.extract(r"^d(?P<date8>\d{8})-s(?P<section>\d{2})-r(?P<race_no>\d{2})$")
    out = pd.Series([None] * len(keys), index=keys.index, dtype=object)
    mask = parsed["date8"].notna()
    if not mask.any():
        return keys
    tmp = parsed.loc[mask].copy()
    tmp["section"] = pd.to_numeric(tmp["section"], errors="coerce")
    tmp["race_no"] = pd.to_numeric(tmp["race_no"], errors="coerce")
    tmp["section_compact"] = tmp.groupby("date8")["section"].rank(method="dense").astype(int)
    out.loc[mask] = tmp.apply(
        lambda r: f"d{r['date8']}-c{int(r['section_compact']):02d}-r{int(r['race_no']):02d}",
        axis=1,
    )
    return out.fillna(keys)


def build_truth(historical_path: Path) -> pd.DataFrame:
    hist = pd.read_csv(historical_path, low_memory=False)
    cols = {"race_id", "lane", "finish_position"}
    missing = cols - set(hist.columns)
    if missing:
        raise ValueError(f"historical file missing columns: {sorted(missing)}")

    work = hist.copy()
    work["race_id"] = work["race_id"].map(normalize_text)
    work["lane"] = pd.to_numeric(work["lane"], errors="coerce")
    work["finish_position"] = pd.to_numeric(work["finish_position"], errors="coerce")
    if "odds_trifecta" in work.columns:
        work["odds_trifecta"] = pd.to_numeric(work["odds_trifecta"], errors="coerce")
    else:
        work["odds_trifecta"] = pd.NA
    work = work.dropna(subset=["race_id", "lane", "finish_position"]).copy()

    rows = []
    for race_id, grp in work.groupby("race_id", sort=False):
        top3 = grp[grp["finish_position"].isin([1, 2, 3])].sort_values("finish_position").copy()
        if len(top3) != 3:
            continue
        rows.append(
            {
                "race_id": race_id,
                "actual_trifecta": "-".join(top3["lane"].astype(int).astype(str).tolist()),
                "official_odds": pd.to_numeric(top3["odds_trifecta"], errors="coerce").dropna().iloc[0]
                if top3["odds_trifecta"].notna().any()
                else pd.NA,
            }
        )

    truth = pd.DataFrame(rows)
    truth["match_key_raw"] = truth["race_id"].map(build_match_key)
    truth["match_key"] = compact_outcome_sections(truth["match_key_raw"])
    if {"date", "jcd", "race_no"}.issubset(hist.columns):
        race_meta = (
            hist[["date", "jcd", "race_no", "race_id"]]
            .dropna(subset=["date", "jcd", "race_no"])
            .copy()
        )
        race_meta["date"] = pd.to_datetime(race_meta["date"], errors="coerce").dt.strftime("%Y%m%d")
        race_meta["jcd"] = pd.to_numeric(race_meta["jcd"], errors="coerce")
        race_meta["race_no"] = pd.to_numeric(race_meta["race_no"], errors="coerce")
        race_meta = race_meta.dropna(subset=["date", "jcd", "race_no"]).copy()
        race_meta["match_key_vcode"] = race_meta.apply(
            lambda r: f"d{r['date']}-v{int(r['jcd']):02d}-r{int(r['race_no']):02d}",
            axis=1,
        )
        truth = truth.merge(
            race_meta[["race_id", "match_key_vcode"]].drop_duplicates(subset=["race_id"]),
            on="race_id",
            how="left",
        )
    return truth.drop_duplicates(subset=["match_key"]).reset_index(drop=True)


def attach_truth(df: pd.DataFrame, truth_df: pd.DataFrame, race_col: str = "race_id") -> pd.DataFrame:
    out = df.copy()
    out[race_col] = out[race_col].map(normalize_text)
    out["match_key_raw"] = out[race_col].map(build_match_key)
    out["match_key"] = compact_outcome_sections(out["match_key_raw"])
    merged = out.merge(
        truth_df[["match_key", "actual_trifecta", "official_odds"]],
        on="match_key",
        how="left",
    )
    if merged["actual_trifecta"].notna().any():
        return merged
    if "match_key_vcode" in truth_df.columns:
        vcode_truth = truth_df[["match_key_vcode", "actual_trifecta", "official_odds"]].dropna(subset=["match_key_vcode"]).drop_duplicates(subset=["match_key_vcode"])
        merged_vcode = out.merge(
            vcode_truth,
            left_on="match_key",
            right_on="match_key_vcode",
            how="left",
        )
        if merged_vcode["actual_trifecta"].notna().any():
            return merged_vcode
    return merged


def summarize_probability_bins(candidate_df: pd.DataFrame, metric_col: str, bins: list[float]) -> pd.DataFrame:
    work = candidate_df.copy()
    work[metric_col] = pd.to_numeric(work[metric_col], errors="coerce")
    work["official_odds"] = pd.to_numeric(work["official_odds"], errors="coerce")
    work = work.dropna(subset=[metric_col, "actual_trifecta"]).copy()
    work["hit"] = work["trifecta"].astype(str).eq(work["actual_trifecta"].astype(str))
    work["return_amount"] = work["hit"].astype(int) * work["official_odds"].fillna(0.0)
    labels = [f"[{bins[i]:.2f},{bins[i+1]:.2f})" for i in range(len(bins) - 1)]
    work["prob_bin"] = pd.cut(
        work[metric_col],
        bins=bins,
        labels=labels,
        include_lowest=True,
        right=False,
    )
    grouped = (
        work.groupby("prob_bin", dropna=False)
        .agg(
            sample_count=("trifecta", "size"),
            hit_count=("hit", "sum"),
            avg_pred=("trifecta", lambda s: 0.0),
            avg_prob=(metric_col, "mean"),
            avg_official_odds=("official_odds", "mean"),
            total_return=("return_amount", "sum"),
        )
        .reset_index()
    )
    grouped["metric"] = metric_col
    grouped["hit_count"] = grouped["hit_count"].astype(int)
    grouped["hit_rate"] = grouped["hit_count"] / grouped["sample_count"].where(grouped["sample_count"] > 0, 1)
    grouped["roi"] = grouped["total_return"] / grouped["sample_count"].where(grouped["sample_count"] > 0, 1)
    grouped["calibration_gap"] = grouped["avg_prob"] - grouped["hit_rate"]
    grouped["avg_pred"] = grouped["avg_prob"]
    return grouped[
        [
            "metric",
            "prob_bin",
            "sample_count",
            "hit_count",
            "hit_rate",
            "roi",
            "avg_pred",
            "avg_official_odds",
            "calibration_gap",
            "total_return",
        ]
    ]


def rank_stats(df: pd.DataFrame) -> dict[str, float | int | None]:
    ranks = pd.to_numeric(df["actual_rank"], errors="coerce")
    valid = ranks.dropna()
    total = int(len(df))
    if total == 0:
        return {
            "race_count": 0,
            "exact_rate": 0.0,
            "top5_rate": 0.0,
            "top10_rate": 0.0,
            "top20_rate": 0.0,
            "in_candidates_rate": 0.0,
            "avg_rank": None,
            "median_rank": None,
        }
    return {
        "race_count": total,
        "exact_rate": round(float((ranks == 1).mean()), 4),
        "top5_rate": round(float(ranks.between(1, 5).mean()), 4),
        "top10_rate": round(float(ranks.between(1, 10).mean()), 4),
        "top20_rate": round(float(ranks.between(1, 20).mean()), 4),
        "in_candidates_rate": round(float(ranks.notna().mean()), 4),
        "avg_rank": round(float(valid.mean()), 2) if not valid.empty else None,
        "median_rank": round(float(valid.median()), 2) if not valid.empty else None,
    }


def build_rank_rows(candidate_df: pd.DataFrame, skip_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    merged = candidate_df.copy()
    merged["approx_prob"] = pd.to_numeric(merged["approx_prob"], errors="coerce").fillna(0.0)
    for race_id, grp in merged.groupby("race_id", sort=False):
        grp = grp.sort_values("approx_prob", ascending=False).reset_index(drop=True)
        actual_trifecta = normalize_text(grp["actual_trifecta"].iloc[0])
        if not actual_trifecta:
            continue
        actual_first = actual_trifecta.split("-")[0]
        actual_match = grp.index[grp["trifecta"].astype(str) == actual_trifecta]
        actual_rank = int(actual_match[0] + 1) if len(actual_match) else None
        same_first = grp[grp["first_lane"].astype(str) == actual_first].copy()
        best_same_first_rank = int(same_first.index[0] + 1) if not same_first.empty else None
        within_same_first = same_first.reset_index(drop=True)
        within_match = within_same_first.index[within_same_first["trifecta"].astype(str) == actual_trifecta]
        actual_rank_within_same_first = int(within_match[0] + 1) if len(within_match) else None
        rows.append(
            {
                "race_id": race_id,
                "match_key": grp["match_key"].iloc[0],
                "actual_trifecta": actual_trifecta,
                "actual_first_lane": actual_first,
                "candidate_count": int(len(grp)),
                "actual_rank": actual_rank,
                "best_same_first_rank": best_same_first_rank,
                "actual_rank_within_same_first": actual_rank_within_same_first,
                "first_lane_ok_but_order_weak": bool(
                    best_same_first_rank is not None and best_same_first_rank <= 10 and (actual_rank is None or actual_rank > 10)
                ),
                "first_lane_itself_weak": bool(
                    best_same_first_rank is None or best_same_first_rank > 10
                ),
            }
        )

    rank_df = pd.DataFrame(rows)
    if rank_df.empty:
        empty_cols = [
            "race_id",
            "match_key",
            "actual_trifecta",
            "actual_first_lane",
            "candidate_count",
            "actual_rank",
            "best_same_first_rank",
            "actual_rank_within_same_first",
            "first_lane_ok_but_order_weak",
            "first_lane_itself_weak",
            "race_gate",
            "first_place_gate",
            "pre_race_gate",
            "has_real_odds",
            "decision",
        ]
        return pd.DataFrame(columns=empty_cols)
    skip_cols = ["race_id", "race_gate", "first_place_gate", "pre_race_gate", "has_real_odds", "decision"]
    available_cols = [c for c in skip_cols if c in skip_df.columns]
    if available_cols:
        rank_df = rank_df.merge(skip_df[available_cols].drop_duplicates(subset=["race_id"]), on="race_id", how="left")
    return rank_df


def summarize_groups(rank_df: pd.DataFrame, group_col: str) -> pd.DataFrame:
    if group_col not in rank_df.columns:
        return pd.DataFrame(columns=["group_by", "group_value", "race_count", "exact_rate", "top5_rate", "top10_rate", "top20_rate", "in_candidates_rate", "avg_rank", "median_rank"])
    rows = []
    for value, grp in rank_df.groupby(group_col, dropna=False, sort=False):
        stats = rank_stats(grp)
        rows.append({"group_by": group_col, "group_value": str(value), **stats})
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose approx_prob calibration and trifecta ranking bottlenecks")
    parser.add_argument("--ev", required=True)
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--skip", required=True)
    parser.add_argument("--historical", default="data/processed/historical_races.csv")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--bins", default="0,0.05,0.10,0.15,0.20,0.25,0.30,0.40,0.50,1.01")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    bins = [float(x.strip()) for x in str(args.bins).split(",") if x.strip()]

    truth_df = build_truth(Path(args.historical))
    ev_df = attach_truth(pd.read_csv(args.ev, low_memory=False), truth_df)
    candidate_df = attach_truth(pd.read_csv(args.candidates, low_memory=False), truth_df)
    skip_df = attach_truth(pd.read_csv(args.skip, low_memory=False), truth_df)

    prob_frames = []
    for metric in ["approx_prob", "calibrated_hit_prob"]:
        if metric in ev_df.columns:
            prob_frames.append(summarize_probability_bins(ev_df, metric, bins))
    prob_df = pd.concat(prob_frames, ignore_index=True) if prob_frames else pd.DataFrame()
    prob_df.to_csv(out_dir / "approx_prob_diagnostics.csv", index=False)

    rank_df = build_rank_rows(candidate_df, skip_df)
    rank_df.to_csv(out_dir / "trifecta_rank_race_rows.csv", index=False)

    group_frames = []
    for col in ["race_gate", "first_place_gate", "pre_race_gate", "has_real_odds", "decision"]:
        group_frames.append(summarize_groups(rank_df, col))
    group_df = pd.concat(group_frames, ignore_index=True)
    group_df.to_csv(out_dir / "trifecta_rank_group_summary.csv", index=False)

    prob_summary = {}
    if not prob_df.empty:
        for metric, grp in prob_df.groupby("metric", sort=False):
            used = grp[grp["sample_count"] > 0].copy()
            prob_summary[str(metric)] = {
                "rows": int(used["sample_count"].sum()),
                "hit_count": int(used["hit_count"].sum()),
                "hit_rate": round(float(used["hit_count"].sum() / used["sample_count"].sum()), 4) if used["sample_count"].sum() > 0 else 0.0,
                "avg_pred": round(float((used["avg_pred"] * used["sample_count"]).sum() / used["sample_count"].sum()), 4) if used["sample_count"].sum() > 0 else 0.0,
                "roi": round(float(used["total_return"].sum() / used["sample_count"].sum()), 4) if "total_return" in used.columns and used["sample_count"].sum() > 0 else None,
                "non_empty_bins": int((used["sample_count"] > 0).sum()),
            }

    overall_rank = rank_stats(rank_df)
    cause_summary = {
        "race_count": int(len(rank_df)),
        "first_lane_ok_but_order_weak_count": int(rank_df["first_lane_ok_but_order_weak"].sum()) if "first_lane_ok_but_order_weak" in rank_df.columns else 0,
        "first_lane_itself_weak_count": int(rank_df["first_lane_itself_weak"].sum()) if "first_lane_itself_weak" in rank_df.columns else 0,
        "actual_outside_top20_count": int(pd.to_numeric(rank_df["actual_rank"], errors="coerce").gt(20).sum()) if "actual_rank" in rank_df.columns else 0,
    }
    if rank_df.empty:
        cause_summary["note"] = "no evaluable races matched historical truth"

    summary = {
        "inputs": {
            "ev": args.ev,
            "candidates": args.candidates,
            "skip": args.skip,
            "historical": args.historical,
        },
        "approx_prob_summary": prob_summary,
        "trifecta_rank_summary": overall_rank,
        "cause_summary": cause_summary,
    }
    (out_dir / "diagnostic_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
