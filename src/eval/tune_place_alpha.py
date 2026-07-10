import json
import re
from itertools import permutations
from pathlib import Path

import pandas as pd


PROBA_CSV = Path("data/model_outputs/today_win_proba.csv")
FEAT_CSV = Path("data/features/today_features.csv")
HIST_CSV = Path("data/processed/historical_races.csv")
CONFIG_CSV = Path("config/strategy_config.json")
OUT_JSON = Path("place_alpha_tuning.json")

WIN_FEAT = "national_win_rate"
PLACE_FEATS = ["local_2ren_rate", "national_2ren_rate", "boat_2ren_rate"]
WIN_BETA = 0.2
ALPHA_GRID = [0.0, 0.05, 0.10, 0.15, 0.20, 0.30]
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


def load_candidate_generation_config() -> dict[str, object]:
    if not CONFIG_CSV.exists():
        return {}
    config = json.loads(CONFIG_CSV.read_text(encoding="utf-8"))
    return dict(config.get("candidate_generation", {}))


def scale_series(s: pd.Series) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce")
    if s.dropna().empty:
        return pd.Series(0.0, index=s.index)
    col_min = float(s.min())
    col_max = float(s.max())
    return ((s - col_min) / (col_max - col_min + 1e-9)).fillna(0.0)


def reconstruct_actual_trifecta(hist_df: pd.DataFrame) -> pd.DataFrame:
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


def select_top60_per_first_pattern(candidate_rows: list[dict[str, object]], min_per_first=12, fill_mode="global"):
    lanes = sorted({int(row["first_lane"]) for row in candidate_rows})
    if not lanes:
        return []

    selected = []
    selected_keys = set()
    grouped = {lane: [] for lane in lanes}
    for row in candidate_rows:
        grouped[int(row["first_lane"])].append(row)

    for lane in lanes:
        for row in grouped[lane][:max(int(min_per_first), 0)]:
            key = str(row["trifecta"])
            if key in selected_keys:
                continue
            selected.append(row)
            selected_keys.add(key)
            if len(selected) >= 60:
                return selected[:60]

    if str(fill_mode) == "lane_round_robin":
        lane_idx = 0
        cursor = {lane: max(int(min_per_first), 0) for lane in lanes}
        while len(selected) < 60:
            lane = lanes[lane_idx % len(lanes)]
            lane_idx += 1
            idx = cursor[lane]
            if idx >= len(grouped[lane]):
                if all(cursor[l] >= len(grouped[l]) for l in lanes):
                    break
                continue
            row = grouped[lane][idx]
            cursor[lane] += 1
            key = str(row["trifecta"])
            if key in selected_keys:
                continue
            selected.append(row)
            selected_keys.add(key)
    else:
        for row in candidate_rows:
            key = str(row["trifecta"])
            if key in selected_keys:
                continue
            selected.append(row)
            selected_keys.add(key)
            if len(selected) >= 60:
                break

    return selected[:60]


def main() -> None:
    proba_df = pd.read_csv(PROBA_CSV)
    feat_df = pd.read_csv(FEAT_CSV)
    hist_df = pd.read_csv(HIST_CSV)
    candidate_cfg = load_candidate_generation_config()

    top_n_win = int(candidate_cfg.get("top_n_win", 6))
    max_trifecta_combinations = int(candidate_cfg.get("max_trifecta_combinations", 60))
    selection_mode = str(candidate_cfg.get("selection_mode", "baseline_top60"))
    min_per_first = int(candidate_cfg.get("per_first_min_per_first", 12))
    fill_mode = str(candidate_cfg.get("per_first_fill_mode", "global"))

    proba_df["race_id"] = proba_df["race_id"].map(normalize_text)
    feat_df["race_id"] = feat_df["race_id"].map(normalize_text)
    hist_df["race_id"] = hist_df["race_id"].map(normalize_text)

    merged = proba_df.merge(feat_df, on=["race_id", "lane"], how="left", suffixes=("", "_feat"))
    merged["lane"] = pd.to_numeric(merged["lane"], errors="coerce")
    merged["win_proba_norm"] = pd.to_numeric(merged["win_proba_norm"], errors="coerce").fillna(0.0)

    for col in [WIN_FEAT] + PLACE_FEATS:
        if col not in merged.columns:
            merged[f"{col}_scaled"] = 0.0
        else:
            merged[f"{col}_scaled"] = scale_series(merged[col])
    merged["place_avg_scaled"] = merged[[f"{f}_scaled" for f in PLACE_FEATS]].fillna(0.0).mean(axis=1)

    actual = reconstruct_actual_trifecta(hist_df)
    if actual.empty:
        raise ValueError("no actual trifectas reconstructed from historical results")
    actual_lookup = actual.set_index("normalized_race_key")[["actual_trifecta"]].to_dict(orient="index")

    race_id_to_key = merged.drop_duplicates("race_id")["race_id"].to_frame()
    race_id_to_key["normalized_race_key"] = race_id_to_key["race_id"].apply(prediction_match_key)
    race_id_to_key["normalized_race_key_legacy"] = race_id_to_key["race_id"].apply(normalize_race_key)
    race_id_to_key["normalized_race_key"] = race_id_to_key["normalized_race_key"].fillna(
        race_id_to_key["normalized_race_key_legacy"]
    )
    race_key_map = dict(zip(race_id_to_key["race_id"], race_id_to_key["normalized_race_key"]))

    # winner_rank is measured on lane-level score to verify 1着 degradation does not happen.
    base_winner_scores = (
        merged.assign(winner_lane_score=lambda d: d["win_proba_norm"] + (WIN_BETA * d["national_win_rate_scaled"]))
        .groupby("race_id")
    )

    alpha_results: dict[str, dict[str, object]] = {}

    for alpha in ALPHA_GRID:
        tri_ranks: list[int | None] = []
        winner_ranks: list[int | None] = []
        exact_count = 0
        selected_counts = []

        for race_id, race_df in merged.groupby("race_id", sort=False):
            race_key = race_key_map.get(race_id)
            actual_row = actual_lookup.get(race_key) if race_key else None
            if actual_row is None:
                continue

            actual_tri = str(actual_row["actual_trifecta"]).strip()
            parts = actual_tri.split("-")
            if len(parts) != 3:
                continue
            w1, w2, w3 = parts

            race_df = race_df.copy()
            race_df["lane"] = pd.to_numeric(race_df["lane"], errors="coerce")
            race_df = race_df.dropna(subset=["lane"]).copy()
            race_df["lane"] = race_df["lane"].astype(int)
            race_df["win_proba_norm"] = race_df["win_proba_norm"] / max(float(race_df["win_proba_norm"].sum()), EPS)

            sorted_boats = race_df.sort_values("win_proba_norm", ascending=False)
            top_boats = sorted_boats.head(top_n_win)["lane"].tolist()
            lanes = race_df["lane"].tolist()
            probs = race_df.set_index("lane")["win_proba_norm"].to_dict()
            win_scores = race_df.set_index("lane")[f"{WIN_FEAT}_scaled"].to_dict()
            place_scores = race_df.set_index("lane")["place_avg_scaled"].to_dict()

            candidate_rows = []
            for first_lane, second_lane, third_lane in permutations(lanes, 3):
                if first_lane not in top_boats:
                    continue
                p1 = float(probs.get(first_lane, 0.0))
                remain_after_first = sum(float(probs[l]) for l in lanes if l != first_lane)
                remain_after_second = sum(float(probs[l]) for l in lanes if l not in (first_lane, second_lane))
                if p1 <= 0 or remain_after_first <= EPS or remain_after_second <= EPS:
                    continue
                p2 = float(probs[second_lane]) / remain_after_first
                p3 = float(probs[third_lane]) / remain_after_second
                base_prob = p1 * p2 * p3
                if base_prob <= 0:
                    continue
                win_score = float(win_scores.get(first_lane, 0.0))
                place_score = float((place_scores.get(second_lane, 0.0) + place_scores.get(third_lane, 0.0)) / 2.0)
                approx_prob = base_prob + (WIN_BETA * win_score) + (alpha * place_score)
                candidate_rows.append(
                    {
                        "trifecta": f"{first_lane}-{second_lane}-{third_lane}",
                        "first_lane": first_lane,
                        "second_lane": second_lane,
                        "third_lane": third_lane,
                        "approx_prob": approx_prob,
                    }
                )

            if not candidate_rows:
                continue

            candidate_rows.sort(key=lambda x: float(x["approx_prob"]), reverse=True)
            if selection_mode == "per_first_m12_global":
                candidate_rows = select_top60_per_first_pattern(
                    candidate_rows,
                    min_per_first=min_per_first,
                    fill_mode=fill_mode,
                )
            else:
                candidate_rows = candidate_rows[:max_trifecta_combinations]

            selected_counts.append(len(candidate_rows))
            actual_rank = None
            for idx, row in enumerate(candidate_rows, start=1):
                if str(row["trifecta"]).strip() == actual_tri:
                    actual_rank = idx
                    break
            tri_ranks.append(actual_rank)
            if actual_rank == 1:
                exact_count += 1

            lane_scores = {
                int(lane): float(row_score)
                for lane, row_score in zip(
                    race_df["lane"].tolist(),
                    (race_df["win_proba_norm"] + (WIN_BETA * race_df["national_win_rate_scaled"]) + (alpha * race_df["place_avg_scaled"])).tolist(),
                )
            }
            winner_rank = None
            if int(w1) in lane_scores:
                sorted_lanes = sorted(lane_scores.items(), key=lambda x: x[1], reverse=True)
                winner_rank = next((i for i, (lane, _) in enumerate(sorted_lanes, start=1) if str(lane) == w1), None)
            winner_ranks.append(winner_rank)

        tr = pd.Series([r for r in tri_ranks if r is not None and r > 0], dtype=float)
        wr = pd.Series([r for r in winner_ranks if r is not None and r > 0], dtype=float)

        alpha_results[str(alpha)] = {
            "trifecta_rank": {
                "count": int(len(tr)),
                "mean": round(float(tr.mean()), 2) if not tr.empty else None,
                "median": round(float(tr.median()), 2) if not tr.empty else None,
                "in_top5": int((tr <= 5).sum()) if not tr.empty else 0,
                "in_top10": int((tr <= 10).sum()) if not tr.empty else 0,
                "exact": int(exact_count),
                "not_found": int(sum(r is None or r <= 0 for r in tri_ranks)),
            },
            "winner_rank_median": round(float(wr.median()), 2) if not wr.empty else None,
            "winner_rank_mean": round(float(wr.mean()), 2) if not wr.empty else None,
            "winner_rank_top5_rate": round(float((wr <= 5).mean()), 4) if not wr.empty else None,
            "candidate_count_avg": round(float(pd.Series(selected_counts).mean()), 2) if selected_counts else None,
            "candidate_count_max": int(pd.Series(selected_counts).max()) if selected_counts else None,
            "candidate_count_min": int(pd.Series(selected_counts).min()) if selected_counts else None,
        }

    baseline_median = alpha_results["0.0"]["trifecta_rank"]["median"]
    best_alpha = None
    best_key = None
    for alpha in ALPHA_GRID:
        key = str(alpha)
        row = alpha_results[key]
        median = row["trifecta_rank"]["median"]
        winner_median = row["winner_rank_median"]
        if median is None or winner_median is None:
            continue
        if median <= baseline_median and winner_median <= 3.5:
            if best_key is None:
                best_key = key
                best_alpha = alpha
            else:
                cur_top10 = row["trifecta_rank"]["in_top10"]
                best_top10 = alpha_results[best_key]["trifecta_rank"]["in_top10"]
                if cur_top10 > best_top10:
                    best_key = key
                    best_alpha = alpha

    if best_key is None:
        recommended = {
            "alpha": 0.0,
            "reason": "no alpha improved median without worsening winner rank; keep baseline",
        }
    else:
        recommended = {
            "alpha": best_alpha,
            "reason": "best alpha by top10 among candidates with median <= baseline and winner median <= 3.5",
        }

    result = {
        "input": {
            "proba_csv": str(PROBA_CSV),
            "features_csv": str(FEAT_CSV),
            "historical_csv": str(HIST_CSV),
            "top_n_win": top_n_win,
            "max_trifecta_combinations": max_trifecta_combinations,
            "selection_mode": selection_mode,
            "per_first_min_per_first": min_per_first,
            "per_first_fill_mode": fill_mode,
            "win_beta": WIN_BETA,
            "alpha_grid": ALPHA_GRID,
        },
        "alpha_comparison": alpha_results,
        "recommended_alpha": recommended,
    }

    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"[saved] {OUT_JSON}")


if __name__ == "__main__":
    main()
