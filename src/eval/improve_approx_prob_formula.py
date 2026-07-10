import json
import re
from itertools import permutations
from pathlib import Path

import pandas as pd


PROBA_CSV = Path("data/model_outputs/today_win_proba.csv")
FEAT_CSV = Path("data/features/today_features.csv")
HIST_CSV = Path("data/processed/historical_races.csv")
CONFIG_CSV = Path("config/strategy_config.json")
OUT_JSON = Path("approx_prob_formula_comparison.json")

WIN_FEAT = "national_win_rate"
PLACE_FEATS = ["local_2ren_rate", "national_2ren_rate", "boat_2ren_rate"]
DEFAULT_TOP_N_WIN = 6
DEFAULT_MAX_CANDIDATES = 60
EPS = 1e-12


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


def scale_column(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(0.0, index=df.index)
    s = pd.to_numeric(df[col], errors="coerce")
    col_min = float(s.min()) if not s.dropna().empty else 0.0
    col_max = float(s.max()) if not s.dropna().empty else 0.0
    return (s - col_min) / (col_max - col_min + 1e-9)


def load_candidate_generation_config() -> tuple[int, int]:
    if not CONFIG_CSV.exists():
        return DEFAULT_TOP_N_WIN, DEFAULT_MAX_CANDIDATES
    config = json.loads(CONFIG_CSV.read_text(encoding="utf-8"))
    candidate_cfg = config.get("candidate_generation", {})
    return (
        int(candidate_cfg.get("top_n_win", DEFAULT_TOP_N_WIN)),
        int(candidate_cfg.get("max_trifecta_combinations", DEFAULT_MAX_CANDIDATES)),
    )


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
        top3 = grp[grp["finish_position"].isin([1, 2, 3])].sort_values("finish_position")
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


def build_formulas(first_score: float, pair_score: float, approx_prob: float) -> dict[str, float]:
    return {
        "A_baseline": approx_prob,
        "B_place_additive": approx_prob + (0.2 * first_score) + (0.3 * pair_score),
        "C_place_multiplicative": approx_prob * (1.0 + pair_score),
        "D_place_strong": approx_prob + (0.5 * pair_score),
    }


def summarize_ranks(ranks: list[int | None]) -> dict[str, object]:
    s = pd.Series([r for r in ranks if r is not None and r > 0], dtype=float)
    if s.empty:
        return {
            "count": 0,
            "mean": None,
            "median": None,
            "exact_hit_count": 0,
            "hit_top5": 0,
            "hit_top10": 0,
            "hit_top20": 0,
            "hit_top60": 0,
            "not_found": len(ranks),
        }
    return {
        "count": int(len(s)),
        "mean": round(float(s.mean()), 2),
        "median": round(float(s.median()), 2),
        "exact_hit_count": int((s == 1).sum()),
        "hit_top5": int((s <= 5).sum()),
        "hit_top10": int((s <= 10).sum()),
        "hit_top20": int((s <= 20).sum()),
        "hit_top60": int((s <= 60).sum()),
        "not_found": int(sum(r is None or r <= 0 for r in ranks)),
    }


def main() -> None:
    if not PROBA_CSV.exists():
        raise FileNotFoundError(f"probability file not found: {PROBA_CSV}")
    if not FEAT_CSV.exists():
        raise FileNotFoundError(f"feature file not found: {FEAT_CSV}")
    if not HIST_CSV.exists():
        raise FileNotFoundError(f"historical file not found: {HIST_CSV}")

    top_n_win, max_trifecta_combinations = load_candidate_generation_config()

    proba_df = pd.read_csv(PROBA_CSV)
    feat_df = pd.read_csv(FEAT_CSV)
    hist_df = pd.read_csv(HIST_CSV)

    proba_df["race_id"] = proba_df["race_id"].map(normalize_text)
    feat_df["race_id"] = feat_df["race_id"].map(normalize_text)
    hist_df["race_id"] = hist_df["race_id"].map(normalize_text)

    merged = proba_df.merge(feat_df, on=["race_id", "lane"], how="left", suffixes=("", "_feat"))
    merged["lane"] = pd.to_numeric(merged["lane"], errors="coerce")
    merged["win_proba_norm"] = pd.to_numeric(merged["win_proba_norm"], errors="coerce").fillna(0.0)

    scale_cols = [WIN_FEAT] + PLACE_FEATS
    for col in scale_cols:
        merged[f"{col}_scaled"] = scale_column(merged, col)

    actual = reconstruct_actual_trifecta(hist_df)
    if actual.empty:
        raise ValueError("no actual trifectas could be reconstructed from historical data")

    actual_lookup = actual.set_index("normalized_race_key")[["actual_trifecta"]].to_dict(orient="index")
    merged["normalized_race_key"] = merged["race_id"].apply(prediction_match_key)
    merged["normalized_race_key_legacy"] = merged["race_id"].apply(normalize_race_key)
    merged["normalized_race_key"] = merged["normalized_race_key"].fillna(merged["normalized_race_key_legacy"])

    configs = {
        "A_baseline": {
            "desc": "current approx_prob formula",
        },
        "B_place_additive": {
            "desc": "approx_prob + 0.2*first_win_feat + 0.3*pair_place_score",
        },
        "C_place_multiplicative": {
            "desc": "approx_prob * (1 + pair_place_score)",
        },
        "D_place_strong": {
            "desc": "approx_prob + 0.5*pair_place_score",
        },
    }

    per_config_ranks: dict[str, list[int | None]] = {k: [] for k in configs}
    per_config_winner_ranks: dict[str, list[int | None]] = {k: [] for k in configs}
    race_rows: list[dict[str, object]] = []

    for race_key, race_df in merged.groupby("normalized_race_key", sort=False):
        if pd.isna(race_key) or not race_key:
            continue
        actual_row = actual_lookup.get(race_key)
        if actual_row is None:
            continue

        actual_tri = str(actual_row["actual_trifecta"]).strip()
        parts = actual_tri.split("-")
        if len(parts) != 3:
            continue
        actual_first, actual_second, actual_third = parts

        race_df = race_df.copy()
        race_df["lane"] = pd.to_numeric(race_df["lane"], errors="coerce")
        race_df = race_df.dropna(subset=["lane"]).copy()
        if len(race_df) < 3:
            continue

        race_df["lane"] = race_df["lane"].astype(int)
        race_df["first_score"] = race_df["win_proba_norm"] + (0.2 * race_df[f"{WIN_FEAT}_scaled"])
        race_df["pair_lane_score"] = race_df[[f"{f}_scaled" for f in PLACE_FEATS]].mean(axis=1)

        lanes = race_df["lane"].tolist()
        probs = race_df.set_index("lane")["win_proba_norm"].to_dict()
        first_scores = race_df.set_index("lane")["first_score"].to_dict()
        pair_scores = race_df.set_index("lane")["pair_lane_score"].to_dict()

        first_pool = race_df.sort_values("win_proba_norm", ascending=False).head(top_n_win)["lane"].tolist()
        if not first_pool:
            continue

        candidate_rows: list[dict[str, object]] = []
        for first_lane, second_lane, third_lane in permutations(lanes, 3):
            if first_lane not in first_pool:
                continue

            p1 = float(probs.get(first_lane, 0.0))
            remain_after_first = sum(float(probs[l]) for l in lanes if l != first_lane)
            remain_after_second = sum(float(probs[l]) for l in lanes if l not in (first_lane, second_lane))
            if p1 <= 0 or remain_after_first <= EPS or remain_after_second <= EPS:
                continue

            p2 = float(probs[second_lane]) / remain_after_first
            p3 = float(probs[third_lane]) / remain_after_second
            approx_prob = min(p1 * p2 * p3, 1.0)
            if approx_prob <= 0:
                continue

            first_feat = float(race_df.loc[race_df["lane"] == first_lane, f"{WIN_FEAT}_scaled"].iloc[0])
            pair_score = float((pair_scores.get(second_lane, 0.0) + pair_scores.get(third_lane, 0.0)) / 2.0)
            formula_scores = build_formulas(first_feat, pair_score, approx_prob)

            candidate_rows.append(
                {
                    "trifecta": f"{first_lane}-{second_lane}-{third_lane}",
                    "first_lane": first_lane,
                    "second_lane": second_lane,
                    "third_lane": third_lane,
                    "approx_prob": approx_prob,
                    "first_win_proba": p1,
                    "second_win_proba": float(probs[second_lane]),
                    "third_win_proba": float(probs[third_lane]),
                    "first_score": float(first_scores.get(first_lane, 0.0)),
                    "pair_place_score": pair_score,
                    **formula_scores,
                }
            )

        if not candidate_rows:
            continue

        # Candidate count is the full permutation set for available boats.
        candidate_count = len(candidate_rows)
        actual_ranks_for_race: dict[str, int | None] = {}

        # Winner rank uses the same first-score idea across formulas.
        ranked_boats = sorted(
            ((lane, float(first_scores.get(lane, 0.0))) for lane in lanes),
            key=lambda x: x[1],
            reverse=True,
        )
        winner_rank = next((idx for idx, (lane, _) in enumerate(ranked_boats, start=1) if str(lane) == actual_first), None)

        race_rows.append(
            {
                "race_key": race_key,
                "actual_trifecta": actual_tri,
                "candidate_count": candidate_count,
                "winner_rank": winner_rank,
            }
        )

        for config_name in configs:
            ranked_candidates = sorted(candidate_rows, key=lambda r: float(r[config_name]), reverse=True)
            actual_rank = None
            for idx, row in enumerate(ranked_candidates, start=1):
                if str(row["trifecta"]).strip() == actual_tri:
                    actual_rank = idx
                    break
            per_config_ranks[config_name].append(actual_rank)
            per_config_winner_ranks[config_name].append(winner_rank)

    summary = {
        "input": {
            "proba_csv": str(PROBA_CSV),
            "features_csv": str(FEAT_CSV),
            "historical_csv": str(HIST_CSV),
            "top_n_win": top_n_win,
            "max_trifecta_combinations": max_trifecta_combinations,
            "candidate_space": "full_permutations_of_available_boats",
        },
        "shared": {
            "races_evaluated": int(len(race_rows)),
            "candidate_count_avg": round(float(pd.Series([r["candidate_count"] for r in race_rows]).mean()), 2) if race_rows else None,
            "candidate_count_max": int(pd.Series([r["candidate_count"] for r in race_rows]).max()) if race_rows else None,
            "candidate_count_min": int(pd.Series([r["candidate_count"] for r in race_rows]).min()) if race_rows else None,
            "actual_in_candidates_races": int(sum(1 for r in race_rows if r["actual_trifecta"])),
            "actual_not_in_candidates_races": 0,
        },
        "configs": {},
        "best": {},
    }

    for config_name, cfg in configs.items():
        tri_summary = summarize_ranks(per_config_ranks[config_name])
        win_summary = summarize_ranks(per_config_winner_ranks[config_name])
        summary["configs"][config_name] = {
            "desc": cfg["desc"],
            "trifecta_rank": tri_summary,
            "winner_rank": {
                "count": win_summary["count"],
                "mean": win_summary["mean"],
                "median": win_summary["median"],
                "rank_1_5": win_summary["hit_top5"],
                "rank_1_10": win_summary["hit_top10"],
                "exact_hit_count": win_summary["exact_hit_count"],
            },
        }

    baseline_median = summary["configs"]["A_baseline"]["trifecta_rank"]["median"]
    best_config = min(
        [k for k in configs.keys() if summary["configs"][k]["trifecta_rank"]["count"] > 0],
        key=lambda k: summary["configs"][k]["trifecta_rank"]["median"] if summary["configs"][k]["trifecta_rank"]["median"] is not None else float("inf"),
    )
    best_median = summary["configs"][best_config]["trifecta_rank"]["median"]
    summary["best"] = {
        "config": best_config,
        "baseline_median": baseline_median,
        "best_median": best_median,
        "improvement": round(float(baseline_median) - float(best_median), 2) if baseline_median is not None and best_median is not None else None,
    }

    OUT_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"[saved] {OUT_JSON}")


if __name__ == "__main__":
    main()
