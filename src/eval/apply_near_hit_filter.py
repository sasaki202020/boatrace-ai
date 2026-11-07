import json
import re
from pathlib import Path

import pandas as pd


SKIP_CSV = Path("data/strategy_outputs/skip_decisions.csv")
ROWS_CSV = Path("trifecta_rank_structure_rows.csv")
PROBA_CSV = Path("data/model_outputs/today_win_proba.csv")
FEAT_CSV = Path("data/features/today_features.csv")
HIST_CSV = Path("data/processed/historical_races.csv")
FILTER_JSON = Path("near_hit_filter_candidates.json")
OUT_JSON = Path("near_hit_filter_backtest.json")

STRICT_WINNER_RANK_LT = 2.0
STRICT_TOP1_MOTOR_LT = 32.23


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
    return pd.DataFrame(rows)


def main() -> None:
    skip_df = pd.read_csv(SKIP_CSV)
    rows_df = pd.read_csv(ROWS_CSV)
    proba_df = pd.read_csv(PROBA_CSV)
    feat_df = pd.read_csv(FEAT_CSV)
    hist_df = pd.read_csv(HIST_CSV)
    filter_json = json.loads(FILTER_JSON.read_text(encoding="utf-8"))

    baseline_buy = skip_df[skip_df["decision"] == "BUY"].copy()

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

    top3 = reconstruct_actual_trifecta(hist_df)
    top3["race_id"] = top3["race_id"].astype(str)
    top3["normalized_race_key_legacy"] = top3["race_id"].apply(normalize_race_key)
    top3["normalized_race_key"] = build_outcome_match_keys(top3["race_id"])
    top3["normalized_race_key"] = top3["normalized_race_key"].fillna(top3["normalized_race_key_legacy"])

    # race-level metrics for the strict filter
    race_filter_feats = {}
    for race_id, group in merged.groupby("race_id"):
        group = group.sort_values("win_proba_norm", ascending=False).reset_index(drop=True)
        top1 = group.iloc[0]
        row = rows_df[rows_df["race_id"] == str(race_id)]
        winner_rank = float(row["winner_rank"].iloc[0]) if len(row) > 0 else 99.0
        race_filter_feats[str(race_id)] = {
            "winner_rank": winner_rank,
            "top1_motor_2ren_rate": float(top1.get("motor_2ren_rate", 99.0)),
        }

    def eval_filter(cfg):
        exact_hits = 0
        near_hits = 0
        total_buy = 0
        buy_races = []

        for _, pred_row in baseline_buy.iterrows():
            race_id = str(pred_row["race_id"])
            ff = race_filter_feats.get(race_id)
            if ff is None:
                continue

            if not (ff["winner_rank"] < cfg["winner_rank_lt"] and ff["top1_motor_2ren_rate"] < cfg["top1_motor_2ren_rate_lt"]):
                continue

            total_buy += 1
            buy_races.append(race_id)

            race_key = merged.loc[merged["race_id"] == race_id, "normalized_race_key"].iloc[0]
            actual_row = top3[top3["normalized_race_key"] == race_key]
            if actual_row.empty:
                continue
            actual_tri = str(actual_row.iloc[0]["actual_trifecta"])
            pred_tri = str(pred_row.get("recommended_trifecta", "")).strip()
            if pred_tri == actual_tri:
                exact_hits += 1
            else:
                row_data = rows_df[rows_df["race_id"] == race_id]
                if len(row_data) > 0 and 2 <= float(row_data["trifecta_rank"].iloc[0]) <= 5:
                    near_hits += 1

        return {
            "total_buy": total_buy,
            "exact_hits": exact_hits,
            "near_hits": near_hits,
            "good_hits": exact_hits + near_hits,
            "exact_hitrate": round(exact_hits / total_buy, 4) if total_buy > 0 else 0.0,
            "good_hitrate": round((exact_hits + near_hits) / total_buy, 4) if total_buy > 0 else 0.0,
            "coverage_of_base": round(total_buy / len(baseline_buy), 4) if len(baseline_buy) > 0 else 0.0,
        }

    results = {"_baseline_no_filter": {"total_buy": len(baseline_buy), "exact_hits": 0, "exact_hitrate": 0.0, "note": "フィルタなし・現行"}}
    for name, cfg in filter_json["filter_candidates"] and {
        "strict": {"winner_rank_lt": STRICT_WINNER_RANK_LT, "top1_motor_2ren_rate_lt": STRICT_TOP1_MOTOR_LT},
        "relax_winner": {"winner_rank_lt": 3.0, "top1_motor_2ren_rate_lt": STRICT_TOP1_MOTOR_LT},
        "relax_motor": {"winner_rank_lt": STRICT_WINNER_RANK_LT, "top1_motor_2ren_rate_lt": 40.0},
        "relax_both": {"winner_rank_lt": 3.0, "top1_motor_2ren_rate_lt": 40.0},
        "winner_only": {"winner_rank_lt": STRICT_WINNER_RANK_LT, "top1_motor_2ren_rate_lt": 9999},
    }.items():
        results[name] = eval_filter(cfg)
        results[name]["conditions"] = cfg

    valid = {k: v for k, v in results.items() if k != "_baseline_no_filter" and v.get("total_buy", 0) >= 10}
    recommended = max(valid, key=lambda k: (valid[k]["exact_hitrate"], valid[k]["good_hitrate"])) if valid else None

    result = {
        "baseline_buy_count": len(baseline_buy),
        "filter_results": results,
        "recommended": recommended,
    }

    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(
        {
            "baseline_buy_count": len(baseline_buy),
            "recommended": recommended,
            "summary": {
                k: {
                    "total_buy": v.get("total_buy"),
                    "exact_hits": v.get("exact_hits"),
                    "exact_hitrate": v.get("exact_hitrate"),
                    "good_hitrate": v.get("good_hitrate"),
                }
                for k, v in results.items()
            },
        },
        ensure_ascii=False,
        indent=2,
    ))
    print(f"[saved] {OUT_JSON}")


if __name__ == "__main__":
    main()
