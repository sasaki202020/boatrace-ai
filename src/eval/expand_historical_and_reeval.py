import json
from pathlib import Path

import pandas as pd


HIST_CSV = Path("data/processed/historical_races.csv")
ROWS_CSV = Path("trifecta_rank_structure_rows.csv")
SKIP_CSV = Path("data/strategy_outputs/skip_decisions.csv")
OUT_PLAN = Path("data_expansion_plan.json")
OUT_CRANK = Path("candidate_rank_filter_result.json")


def main() -> None:
    hist_df = pd.read_csv(HIST_CSV)
    rows_df = pd.read_csv(ROWS_CSV)
    skip_df = pd.read_csv(SKIP_CSV)

    date_col = next((c for c in hist_df.columns if "date" in c.lower()), None)

    plan = {
        "current": {
            "rows": int(len(hist_df)),
            "race_count": int(hist_df["race_id"].nunique()) if "race_id" in hist_df.columns else None,
            "date_col": date_col,
            "date_range": {
                "min": str(hist_df[date_col].min()) if date_col else None,
                "max": str(hist_df[date_col].max()) if date_col else None,
            },
        },
        "target": {
            "exact_hit_needed": 50,
            "current_exact_hit": int((rows_df["trifecta_rank"] == 1).sum()),
            "current_race_count": int(len(rows_df)),
            "scale_factor": round(50 / max(int((rows_df["trifecta_rank"] == 1).sum()), 1), 1),
            "estimated_races_needed": round(len(rows_df) * 50 / max(int((rows_df["trifecta_rank"] == 1).sum()), 1)),
        },
        "data_sources": {
            "raw_dir": "data/raw",
            "processed_dir": "data/processed",
        },
    }

    raw_dir = Path("data/raw")
    raw_files = sorted(raw_dir.glob("**/*.csv")) if raw_dir.exists() else []
    plan["available_raw_files"] = {
        "count": len(raw_files),
        "sample": [str(f) for f in raw_files[:5]],
        "oldest": str(raw_files[0]) if raw_files else None,
        "newest": str(raw_files[-1]) if raw_files else None,
    }

    OUT_PLAN.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[PART A] データ拡張方針")
    print(json.dumps(plan, ensure_ascii=False, indent=2))

    rows_df = rows_df.copy()
    skip_df = skip_df.copy()
    rows_df["race_id"] = rows_df["race_id"].astype(str)
    skip_df["race_id"] = skip_df["race_id"].astype(str)

    merged = skip_df.merge(rows_df[["race_id", "winner_rank", "trifecta_rank"]], on="race_id", how="left")
    merged["exact"] = (merged["trifecta_rank"] == 1).astype(int)
    merged["near"] = merged["trifecta_rank"].between(2, 5).astype(int)
    merged["good"] = ((merged["trifecta_rank"] > 0) & (merged["trifecta_rank"] <= 5)).astype(int)

    rank_configs = {
        "winner_rank_1": merged["winner_rank"] == 1,
        "winner_rank_le2": merged["winner_rank"] <= 2,
        "winner_rank_le3": merged["winner_rank"] <= 3,
        "tri_rank_le5": merged["trifecta_rank"] <= 5,
        "tri_rank_le10": merged["trifecta_rank"] <= 10,
        "winner1_tri_le10": (merged["winner_rank"] == 1) & (merged["trifecta_rank"] <= 10),
        "winner_le2_tri_le20": (merged["winner_rank"] <= 2) & (merged["trifecta_rank"] <= 20),
    }

    crank_results = {}
    baseline_buy = merged[merged["decision"] == "BUY"]
    for config_name, mask in rank_configs.items():
        filtered = merged[mask]
        currently_buy = filtered[filtered["decision"] == "BUY"]
        crank_results[config_name] = {
            "total_matching": int(mask.sum()),
            "currently_buy": int(len(currently_buy)),
            "exact_in_matching": int(filtered["exact"].sum()),
            "near_in_matching": int(filtered["near"].sum()),
            "good_in_matching": int(filtered["good"].sum()),
            "exact_hitrate": round(float(filtered["exact"].mean()), 4) if len(filtered) > 0 else 0.0,
            "good_hitrate": round(float(filtered["good"].mean()), 4) if len(filtered) > 0 else 0.0,
            "coverage_of_base": round(len(filtered) / len(baseline_buy), 4) if len(baseline_buy) > 0 else 0.0,
            "new_buys_added": int(len(filtered) - len(currently_buy)),
        }

    valid = {k: v for k, v in crank_results.items() if v["total_matching"] >= 20}
    recommended = (
        max(valid, key=lambda k: (valid[k]["exact_hitrate"], valid[k]["good_hitrate"]))
        if valid
        else None
    )

    crank_summary = {
        "total_races": int(len(merged)),
        "current_buy": int((merged["decision"] == "BUY").sum()),
        "exact_total": int(merged["exact"].sum()),
        "configs": crank_results,
        "recommended": recommended,
        "warning": "tri_rank is posthoc and cannot be used as a live BUY condition; treat as diagnostic only.",
    }
    OUT_CRANK.write_text(json.dumps(crank_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n[PART C] candidate_rank フィルタ")
    print(
        json.dumps(
            {
                "recommended": recommended,
                "summary": {
                    k: {
                        "total_matching": v["total_matching"],
                        "exact_hitrate": v["exact_hitrate"],
                        "good_hitrate": v["good_hitrate"],
                        "exact_in_matching": v["exact_in_matching"],
                    }
                    for k, v in crank_results.items()
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print(f"[saved] {OUT_PLAN}")
    print(f"[saved] {OUT_CRANK}")


if __name__ == "__main__":
    main()
