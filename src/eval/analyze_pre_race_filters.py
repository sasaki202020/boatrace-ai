from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
GROUPS_CSV = ROOT / "trifecta_ordering_feature_diff.csv"
FEAT_CSV = ROOT / "data" / "features" / "today_features.csv"
ODDS_CSV = ROOT / "data" / "odds" / "today_trifecta_odds.csv"
OUT_DIR = ROOT / "reports" / "pre_race_filters"
OUT_JSON = OUT_DIR / "pre_race_filter_summary.json"
OUT_TIMING = OUT_DIR / "timing_rank_breakdown.csv"
OUT_MOTOR = OUT_DIR / "motor_rank_breakdown.csv"
OUT_COMBO = OUT_DIR / "combo_breakdown.csv"


def _norm_tri(v: object) -> str:
    parts = re.findall(r"\d+", str(v or ""))
    return "-".join(parts[:3]) if len(parts) >= 3 else ""


def _load_inputs() -> tuple[pd.DataFrame, pd.DataFrame]:
    if not GROUPS_CSV.exists():
        raise FileNotFoundError(f"missing group file: {GROUPS_CSV}")
    if not FEAT_CSV.exists():
        raise FileNotFoundError(f"missing feature file: {FEAT_CSV}")
    if not ODDS_CSV.exists():
        raise FileNotFoundError(f"missing odds file: {ODDS_CSV}")

    groups = pd.read_csv(GROUPS_CSV, low_memory=False)
    feats = pd.read_csv(FEAT_CSV, low_memory=False)
    odds = pd.read_csv(ODDS_CSV, low_memory=False)
    return groups, feats, odds


def _prepare_features(feat: pd.DataFrame) -> pd.DataFrame:
    df = feat.copy()
    df["race_id"] = df["race_id"].astype(str).str.strip()
    df["lane"] = pd.to_numeric(df["lane"], errors="coerce")
    df["exhibition_time"] = pd.to_numeric(df["exhibition_time"], errors="coerce")
    df["start_display_st"] = pd.to_numeric(df.get("start_display_st"), errors="coerce")
    df["start_timing"] = pd.to_numeric(df.get("start_timing"), errors="coerce")
    df["exhibition_time_rank"] = pd.to_numeric(df["exhibition_time_rank"], errors="coerce")
    df["motor_2ren_rate"] = pd.to_numeric(df["motor_2ren_rate"], errors="coerce")
    df["motor_rank_in_race"] = (
        df.groupby("race_id")["motor_2ren_rate"].rank(method="min", ascending=False)
    )

    # 展示タイムが空のため、使える直前タイミング指標を優先順で補完する
    df["timing_time"] = df["exhibition_time"]
    df["timing_time"] = df["timing_time"].fillna(df["start_display_st"])
    df["timing_time"] = df["timing_time"].fillna(df["start_timing"])
    fallback_timing_rank = df.groupby("race_id")["timing_time"].rank(method="min", ascending=True)
    df["timing_rank_in_race"] = df["exhibition_time_rank"].fillna(fallback_timing_rank)
    return df


def _prepare_odds(odds: pd.DataFrame) -> pd.DataFrame:
    df = odds.copy()
    df["race_id"] = df["race_id"].astype(str).str.strip()
    df["trifecta"] = df["trifecta"].map(_norm_tri)
    df["odds"] = pd.to_numeric(df["odds"], errors="coerce")
    return df


def _build_race_rows(groups: pd.DataFrame, feats: pd.DataFrame, odds: pd.DataFrame) -> pd.DataFrame:
    summary = groups.copy()
    summary["race_id"] = summary["race_id"].astype(str).str.strip()
    summary["group"] = summary["group"].astype(str).str.strip()
    summary["trifecta_rank"] = pd.to_numeric(summary["trifecta_rank"], errors="coerce")
    summary["winner_rank"] = pd.to_numeric(summary["winner_rank"], errors="coerce")
    summary["actual_trifecta"] = summary["actual_trifecta"].map(_norm_tri)

    race_rows: list[dict] = []
    for _, row in summary.iterrows():
        race_id = str(row["race_id"])
        actual_tri = str(row["actual_trifecta"])
        parts = actual_tri.split("-")
        if len(parts) != 3:
            continue

        lanes = []
        for part in parts:
            try:
                lanes.append(int(part))
            except Exception:
                lanes = []
                break
        if len(lanes) != 3:
            continue

        race_feat = feats[feats["race_id"] == race_id].copy()
        if race_feat.empty:
            continue

        trio = race_feat[race_feat["lane"].isin(lanes)].copy()
        if len(trio) != 3:
            continue

        odds_row = odds[(odds["race_id"] == race_id) & (odds["trifecta"] == actual_tri)]
        odds_val = float(odds_row["odds"].iloc[0]) if not odds_row.empty else float("nan")

        race_mean_exh = float(pd.to_numeric(race_feat["exhibition_time"], errors="coerce").mean())
        race_mean_motor = float(pd.to_numeric(race_feat["motor_2ren_rate"], errors="coerce").mean())

        trio_timing = pd.to_numeric(trio["timing_time"], errors="coerce")
        trio_motor = pd.to_numeric(trio["motor_2ren_rate"], errors="coerce")
        trio_timing_rank = pd.to_numeric(trio["timing_rank_in_race"], errors="coerce")
        trio_motor_rank = pd.to_numeric(trio["motor_rank_in_race"], errors="coerce")

        rec = {
            "race_id": race_id,
            "actual_trifecta": actual_tri,
            "trifecta_rank": float(row["trifecta_rank"]) if pd.notna(row["trifecta_rank"]) else float("nan"),
            "winner_rank": float(row["winner_rank"]) if pd.notna(row["winner_rank"]) else float("nan"),
            "exact_hit": int(float(row["trifecta_rank"]) == 1) if pd.notna(row["trifecta_rank"]) else 0,
            "near_hit": int(0 < float(row["trifecta_rank"]) <= 5) if pd.notna(row["trifecta_rank"]) else 0,
            "top10_hit": int(0 < float(row["trifecta_rank"]) <= 10) if pd.notna(row["trifecta_rank"]) else 0,
            "group": row["group"],
            "odds_trifecta": odds_val,
            "stake": 1.0,
            "return": odds_val if pd.notna(row["trifecta_rank"]) and float(row["trifecta_rank"]) == 1 and pd.notna(odds_val) else 0.0,
            "timing_mean": float(trio_timing.mean()),
            "timing_min": float(trio_timing.min()),
            "timing_max": float(trio_timing.max()),
            "timing_rank_mean": float(trio_timing_rank.mean()),
            "timing_rank_best": float(trio_timing_rank.min()),
            "timing_rank_worst": float(trio_timing_rank.max()),
            "motor_mean": float(trio_motor.mean()),
            "motor_min": float(trio_motor.min()),
            "motor_max": float(trio_motor.max()),
            "motor_rank_mean": float(trio_motor_rank.mean()),
            "motor_rank_best": float(trio_motor_rank.min()),
            "motor_rank_worst": float(trio_motor_rank.max()),
            "race_mean_timing": float(pd.to_numeric(race_feat["timing_time"], errors="coerce").mean()),
            "race_mean_motor": race_mean_motor,
            "timing_gap_to_race_mean": float(pd.to_numeric(race_feat["timing_time"], errors="coerce").mean() - trio_timing.mean()),
            "motor_gap_to_race_mean": float(trio_motor.mean() - race_mean_motor),
        }
        race_rows.append(rec)

    out = pd.DataFrame(race_rows)
    out = out.dropna(subset=["trifecta_rank", "odds_trifecta"])
    return out


def _bucketize(series: pd.Series, bins: list[float], labels: list[str]) -> pd.Series:
    return pd.cut(series, bins=bins, labels=labels, include_lowest=True, right=False)


def _summarize(df: pd.DataFrame, bucket_col: str) -> pd.DataFrame:
    g = (
        df.groupby(bucket_col, observed=True)
        .agg(
            sample_count=("race_id", "count"),
            exact_hits=("exact_hit", "sum"),
            near_hits=("near_hit", "sum"),
            top10_hits=("top10_hit", "sum"),
            total_stake=("stake", "sum"),
            total_return=("return", "sum"),
        )
        .reset_index()
    )
    g["exact_hit_rate"] = g["exact_hits"] / g["sample_count"]
    g["near_hit_rate"] = g["near_hits"] / g["sample_count"]
    g["top10_hit_rate"] = g["top10_hits"] / g["sample_count"]
    g["roi"] = g["total_return"] / g["total_stake"]
    return g.sort_values(bucket_col).reset_index(drop=True)


def main() -> None:
    groups, feats, odds = _load_inputs()
    feats = _prepare_features(feats)
    odds = _prepare_odds(odds)
    race_df = _build_race_rows(groups, feats, odds)
    if race_df.empty:
        raise ValueError("no race rows were built for analysis")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Rank bins: 平均的な位置で切る。展示タイムは小さいほど良いので rank が小さいほど強い。
    rank_bins = [0, 1.5, 2.5, 3.5, 4.5, 6.1]
    rank_labels = ["1", "2", "3", "4", "5-6"]
    race_df["timing_rank_mean_bin"] = _bucketize(race_df["timing_rank_mean"], rank_bins, rank_labels)
    race_df["motor_rank_mean_bin"] = _bucketize(race_df["motor_rank_mean"], rank_bins, rank_labels)
    race_df["timing_rank_best_bin"] = _bucketize(race_df["timing_rank_best"], rank_bins, rank_labels)
    race_df["motor_rank_best_bin"] = _bucketize(race_df["motor_rank_best"], rank_bins, rank_labels)

    timing_summary = _summarize(race_df, "timing_rank_mean_bin")
    motor_summary = _summarize(race_df, "motor_rank_mean_bin")

    combo = (
        race_df.groupby(["timing_rank_mean_bin", "motor_rank_mean_bin"], observed=True)
        .agg(
            sample_count=("race_id", "count"),
            exact_hits=("exact_hit", "sum"),
            near_hits=("near_hit", "sum"),
            total_stake=("stake", "sum"),
            total_return=("return", "sum"),
        )
        .reset_index()
    )
    combo["exact_hit_rate"] = combo["exact_hits"] / combo["sample_count"]
    combo["near_hit_rate"] = combo["near_hits"] / combo["sample_count"]
    combo["roi"] = combo["total_return"] / combo["total_stake"]
    combo = combo.sort_values(["timing_rank_mean_bin", "motor_rank_mean_bin"]).reset_index(drop=True)

    # 追加で「全3艇が上位寄りか」を見たい場合の補助指標
    race_df["timing_worst_bin"] = _bucketize(race_df["timing_rank_worst"], rank_bins, rank_labels)
    race_df["motor_worst_bin"] = _bucketize(race_df["motor_rank_worst"], rank_bins, rank_labels)
    worst_combo = (
        race_df.groupby(["timing_worst_bin", "motor_worst_bin"], observed=True)
        .agg(
            sample_count=("race_id", "count"),
            exact_hits=("exact_hit", "sum"),
            near_hits=("near_hit", "sum"),
            total_stake=("stake", "sum"),
            total_return=("return", "sum"),
        )
        .reset_index()
    )
    worst_combo["exact_hit_rate"] = worst_combo["exact_hits"] / worst_combo["sample_count"]
    worst_combo["near_hit_rate"] = worst_combo["near_hits"] / worst_combo["sample_count"]
    worst_combo["roi"] = worst_combo["total_return"] / worst_combo["total_stake"]
    worst_combo = worst_combo.sort_values(["timing_worst_bin", "motor_worst_bin"]).reset_index(drop=True)

    timing_summary.to_csv(OUT_TIMING, index=False, encoding="utf-8-sig")
    motor_summary.to_csv(OUT_MOTOR, index=False, encoding="utf-8-sig")
    combo.to_csv(OUT_COMBO, index=False, encoding="utf-8-sig")

    best_combo = combo.loc[combo["sample_count"].ge(10)].sort_values(
        ["roi", "exact_hit_rate", "near_hit_rate"], ascending=False
    ).head(10)
    best_combo_mode = "mean"
    if best_combo.empty:
        best_combo = worst_combo.loc[worst_combo["sample_count"].ge(10)].sort_values(
            ["roi", "exact_hit_rate", "near_hit_rate"], ascending=False
        ).head(10)
        best_combo_mode = "worst"
    strict_combo = combo.loc[combo["sample_count"].ge(20)].sort_values(
        ["roi", "exact_hit_rate", "near_hit_rate"], ascending=False
    ).head(10)
    strict_worst_combo = worst_combo.loc[worst_combo["sample_count"].ge(20)].sort_values(
        ["roi", "exact_hit_rate", "near_hit_rate"], ascending=False
    ).head(10)

    result = {
        "total_races": int(len(race_df)),
        "exact_hits": int(race_df["exact_hit"].sum()),
        "near_hits": int(race_df["near_hit"].sum()),
        "top10_hits": int(race_df["top10_hit"].sum()),
        "avg_odds_exact_hit": float(race_df.loc[race_df["exact_hit"] == 1, "odds_trifecta"].mean())
        if int(race_df["exact_hit"].sum()) > 0
        else None,
        "timing_rank_mean": race_df["timing_rank_mean"].describe().round(4).to_dict(),
        "motor_rank_mean": race_df["motor_rank_mean"].describe().round(4).to_dict(),
        "group_counts": race_df["group"].value_counts().to_dict(),
        "best_combo_mode": best_combo_mode,
        "best_combo_candidates": best_combo.to_dict(orient="records"),
        "strict_combo_candidates": strict_combo.to_dict(orient="records"),
        "best_worst_combo_candidates": strict_worst_combo.to_dict(orient="records"),
        "timing_source": "timing_time = exhibition_time -> start_display_st -> start_timing",
        "output_files": {
            "summary": str(OUT_JSON),
            "timing": str(OUT_TIMING),
            "motor": str(OUT_MOTOR),
            "combo": str(OUT_COMBO),
        },
    }

    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
