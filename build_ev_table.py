from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime
from itertools import permutations
from pathlib import Path
from typing import Any

import pandas as pd


DEFAULT_CONFIG = {
    "proba": {
        "race_id": ["race_id", "race_key"],
        "date": ["date"],
        "lane": ["lane"],
        "trifecta": ["trifecta"],
        "approx_prob": ["approx_prob", "win_proba_norm", "calibrated_hit_prob"],
        "first_win_proba": ["first_win_proba", "win_proba_norm"],
        "second_win_proba": ["second_win_proba"],
        "third_win_proba": ["third_win_proba"],
    },
    "odds": {
        "race_id": ["race_id", "race_key"],
        "trifecta": ["trifecta"],
        "odds": ["odds", "official_odds", "settled_odds"],
    },
    "result": {
        "race_id": ["race_id", "normalized_race_key"],
        "date": ["date"],
        "combo": ["combo", "actual_trifecta"],
        "payout": ["payout", "settled_odds", "official_odds"],
        "hit": ["hit"],
    },
    "thresholds": {
        "buy_ev": 1.0,
        "strong_ev": 1.2,
        "risk_odds": 1000.0,
        "risk_low_prob": 0.01,
        "calibration_first_weight": 0.2,
        "calibration_place_weight": 0.3,
    },
}


def read_csv_any_encoding(path: Path) -> pd.DataFrame:
    last_error = None
    for encoding in ("utf-8-sig", "utf-8", "cp932"):
        try:
            return pd.read_csv(path, encoding=encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    return pd.read_csv(path)


def load_json_config(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return json.loads(json.dumps(DEFAULT_CONFIG))
    with path.open("r", encoding="utf-8") as f:
        loaded = json.load(f)
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    for section in ("proba", "odds", "result", "thresholds"):
        merged[section].update(loaded.get(section, {}))
    return merged


def rename_by_aliases(df: pd.DataFrame, aliases: dict[str, list[str]]) -> pd.DataFrame:
    rename_map: dict[str, str] = {}
    for canonical, names in aliases.items():
        if canonical in df.columns:
            continue
        for name in names:
            if name in df.columns:
                rename_map[name] = canonical
                break
    return df.rename(columns=rename_map)


def to_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def to_bool(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    return series.astype(str).str.strip().str.lower().isin({"1", "true", "yes", "y", "t"})


def normalize_text(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def normalize_race_key(race_id: object) -> str | None:
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


def prediction_match_key(race_id: object) -> str | None:
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


def build_outcome_match_keys(race_ids: pd.Series) -> pd.Series:
    parsed = race_ids.apply(normalize_race_key)
    return pd.Series(parsed, index=race_ids.index, dtype=object)


def parse_trifecta(text: object) -> tuple[int, int, int] | None:
    m = re.fullmatch(r"\s*(\d)-(\d)-(\d)\s*", str(text or ""))
    if not m:
        return None
    return tuple(int(x) for x in m.groups())


def value_band(ev: float | None) -> str:
    if ev is None or pd.isna(ev):
        return "UNKNOWN"
    if ev >= 3.0:
        return "STRONG"
    if ev >= 1.2:
        return "BUY"
    if ev >= 1.0:
        return "WATCH"
    return "SKIP"


def derive_actual_results(result_df: pd.DataFrame) -> pd.DataFrame:
    if result_df.empty:
        return pd.DataFrame()

    work = result_df.copy()
    if "date" in work.columns:
        work["date"] = pd.to_datetime(work["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    if "combo" not in work.columns and "actual_trifecta" in work.columns:
        work["combo"] = work["actual_trifecta"]
    if "hit" in work.columns:
        work["hit"] = to_bool(work["hit"])

    if "normalized_race_key" in work.columns:
        work["normalized_race_key"] = work["normalized_race_key"].map(normalize_text)
        key_cols = ["normalized_race_key"]
    else:
        key_cols = [c for c in ["race_id"] if c in work.columns]
    if key_cols:
        group_cols = key_cols
    elif {"date", "race_no"}.issubset(work.columns):
        group_cols = ["date", "race_no"]
    else:
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    for key, grp in work.groupby(group_cols, dropna=False):
        if not isinstance(key, tuple):
            key = (key,)
        rec: dict[str, Any] = {group_cols[i]: key[i] for i in range(len(group_cols))}
        if "race_id" in grp.columns:
            rec["race_id"] = normalize_text(grp["race_id"].iloc[0])
        grp = grp.copy()
        if "lane" in grp.columns:
            grp["lane"] = to_numeric(grp["lane"])
            # Result files may contain repeated lane rows for the same race.
            # Keep one row per lane before deriving the finish order.
            grp = grp.drop_duplicates(subset=["lane"], keep="first")
        grp["rank_num"] = to_numeric(grp.get("rank", pd.Series(dtype=float)))
        grp = grp.sort_values(["rank_num", "lane"] if "lane" in grp.columns else ["rank_num"], na_position="last")
        finish = grp["lane"].astype("Int64").dropna().tolist()
        if len(finish) >= 3:
            rec["actual_trifecta"] = f"{int(finish[0])}-{int(finish[1])}-{int(finish[2])}"
        else:
            rec["actual_trifecta"] = ""
        combo_vals = grp.get("combo")
        if combo_vals is not None:
            combo_vals = combo_vals.astype(str).replace({"nan": ""})
            combo = next((v for v in combo_vals.tolist() if v), "")
        else:
            combo = ""
        rec["combo"] = combo
        payout_vals = grp.get("payout")
        if payout_vals is not None:
            payout_num = to_numeric(payout_vals).dropna()
            rec["payout"] = float(payout_num.iloc[0]) if not payout_num.empty else None
        else:
            rec["payout"] = None
        if "hit" in grp.columns:
            hit_vals = grp["hit"].dropna()
            rec["hit"] = bool(hit_vals.iloc[0]) if not hit_vals.empty else False
        else:
            rec["hit"] = bool(rec["actual_trifecta"] and rec["combo"] and rec["actual_trifecta"] == rec["combo"])
        rows.append(rec)

    out = pd.DataFrame(rows)
    if "date" in out.columns:
        out["date"] = pd.to_datetime(out["date"], errors="coerce")
    if "normalized_race_key" not in out.columns and "race_id" in out.columns:
        out["normalized_race_key_legacy"] = out["race_id"].apply(normalize_race_key)
        out["normalized_race_key"] = out["race_id"].apply(prediction_match_key)
        out["normalized_race_key"] = out["normalized_race_key"].fillna(out["normalized_race_key_legacy"])
    return out


def build_from_candidate_table(proba_df: pd.DataFrame, odds_df: pd.DataFrame) -> pd.DataFrame:
    work = proba_df.copy()
    work = rename_by_aliases(work, DEFAULT_CONFIG["proba"])
    odds = rename_by_aliases(odds_df.copy(), DEFAULT_CONFIG["odds"])

    if "trifecta" not in work.columns:
        raise ValueError("proba input must contain 'trifecta' or lane-level columns that can be expanded")

    if "approx_prob" not in work.columns and "win_proba_norm" in work.columns:
        work["approx_prob"] = work["win_proba_norm"]
    if "first_win_proba" not in work.columns and "win_proba_norm" in work.columns:
        work["first_win_proba"] = work["win_proba_norm"]
    if "second_win_proba" not in work.columns:
        work["second_win_proba"] = pd.NA
    if "third_win_proba" not in work.columns:
        work["third_win_proba"] = pd.NA

    merged = work.merge(
        odds[["race_id", "trifecta", "odds"]].drop_duplicates(["race_id", "trifecta"]),
        on=["race_id", "trifecta"],
        how="left",
        suffixes=("", "_odds"),
    )
    merged["normalized_race_key_legacy"] = merged["race_id"].apply(normalize_race_key)
    merged["normalized_race_key"] = merged["race_id"].apply(prediction_match_key)
    merged["normalized_race_key"] = merged["normalized_race_key"].fillna(merged["normalized_race_key_legacy"])
    return merged


def build_from_lane_table(proba_df: pd.DataFrame, odds_df: pd.DataFrame) -> pd.DataFrame:
    work = rename_by_aliases(proba_df.copy(), DEFAULT_CONFIG["proba"])
    odds = rename_by_aliases(odds_df.copy(), DEFAULT_CONFIG["odds"])

    lane_prob_col = None
    for cand in ("approx_prob", "win_proba_norm", "win_proba_raw"):
        if cand in work.columns:
            lane_prob_col = cand
            break
    if lane_prob_col is None:
        raise ValueError("proba input must contain lane-level probability column")
    if "lane" not in work.columns:
        raise ValueError("lane-level proba input must contain 'lane'")

    if "date" in work.columns:
        work["date"] = pd.to_datetime(work["date"], errors="coerce").dt.strftime("%Y-%m-%d")

    lane_probs = work[["race_id", "lane", lane_prob_col] + (["date"] if "date" in work.columns else [])].copy()
    lane_probs["lane"] = to_numeric(lane_probs["lane"]).astype("Int64")
    lane_probs[lane_prob_col] = to_numeric(lane_probs[lane_prob_col])

    rows: list[dict[str, Any]] = []
    for race_id, grp in lane_probs.groupby("race_id", dropna=False):
        prob_map = {int(r.lane): float(r[lane_prob_col]) for _, r in grp.dropna(subset=["lane", lane_prob_col]).iterrows()}
        date_val = grp["date"].iloc[0] if "date" in grp.columns else ""
        race_odds = odds[odds["race_id"] == race_id].copy()
        if race_odds.empty:
            continue
        for _, odd_row in race_odds.iterrows():
            trifecta = str(odd_row.get("trifecta", "")).strip()
            parsed = parse_trifecta(trifecta)
            if parsed is None:
                continue
            a, b, c = parsed
            p1 = prob_map.get(a)
            p2 = prob_map.get(b)
            p3 = prob_map.get(c)
            if p1 is None or p2 is None or p3 is None:
                continue
            approx_prob = float(p1 * p2 * p3)
            rows.append(
                {
                    "race_id": race_id,
                    "date": date_val,
                    "trifecta": trifecta,
                    "first_lane": a,
                    "second_lane": b,
                    "third_lane": c,
                    "first_win_proba": p1,
                    "second_win_proba": p2,
                    "third_win_proba": p3,
                    "approx_prob_base": approx_prob,
                    "main_score": approx_prob,
                    "approx_prob_raw": approx_prob,
                    "approx_prob": approx_prob,
                    "conditional_mode": True,
                    "odds": odd_row.get("odds"),
                }
            )

    out = pd.DataFrame(rows)
    if not out.empty:
        out["normalized_race_key_legacy"] = out["race_id"].apply(normalize_race_key)
        out["normalized_race_key"] = out["race_id"].apply(prediction_match_key)
        out["normalized_race_key"] = out["normalized_race_key"].fillna(out["normalized_race_key_legacy"])
    return out


def add_calibration_columns(df: pd.DataFrame, thresholds: dict[str, Any]) -> pd.DataFrame:
    out = df.copy()
    if "approx_prob_raw" not in out.columns:
        out["approx_prob_raw"] = out.get("approx_prob", pd.NA)
    out["approx_prob_raw"] = to_numeric(out["approx_prob_raw"])

    if "first_win_feat" not in out.columns:
        if "win_score_scaled" in out.columns:
            out["first_win_feat"] = to_numeric(out["win_score_scaled"])
        elif "first_win_proba" in out.columns:
            out["first_win_feat"] = to_numeric(out["first_win_proba"])
        else:
            out["first_win_feat"] = 0.0
    else:
        out["first_win_feat"] = to_numeric(out["first_win_feat"])
    out["first_win_feat"] = out["first_win_feat"].fillna(0.0)

    if "pair_place_score" not in out.columns:
        if "place_score_scaled" in out.columns:
            out["pair_place_score"] = to_numeric(out["place_score_scaled"])
        else:
            out["pair_place_score"] = 0.0
    else:
        out["pair_place_score"] = to_numeric(out["pair_place_score"])
    out["pair_place_score"] = out["pair_place_score"].fillna(0.0)

    first_w = float(thresholds.get("calibration_first_weight", 0.2))
    place_w = float(thresholds.get("calibration_place_weight", 0.3))
    out["calibrated_approx_prob"] = out["approx_prob_raw"] + (first_w * out["first_win_feat"]) + (place_w * out["pair_place_score"])
    return out


def enrich_ev_table(df: pd.DataFrame, thresholds: dict[str, Any], prob_mode: str) -> pd.DataFrame:
    out = df.copy()
    out["odds"] = to_numeric(out.get("odds", pd.Series(dtype=float)))
    if "approx_prob" not in out.columns:
        out["approx_prob"] = pd.NA
    out["approx_prob"] = to_numeric(out["approx_prob"])
    if "first_win_proba" in out.columns:
        out["first_win_proba"] = to_numeric(out["first_win_proba"])
    if "second_win_proba" in out.columns:
        out["second_win_proba"] = to_numeric(out["second_win_proba"])
    if "third_win_proba" in out.columns:
        out["third_win_proba"] = to_numeric(out["third_win_proba"])

    out = add_calibration_columns(out, thresholds)
    prob_mode = str(prob_mode or "raw").strip().lower()
    if prob_mode not in {"raw", "calibrated"}:
        raise ValueError("--prob-mode must be either 'raw' or 'calibrated'")
    selected_prob_col = "approx_prob_raw" if prob_mode == "raw" else "calibrated_approx_prob"
    if selected_prob_col not in out.columns:
        selected_prob_col = "approx_prob"
    out["approx_prob"] = to_numeric(out[selected_prob_col])

    out["gross_return"] = out["approx_prob"] * out["odds"]
    out["net_ev"] = out["gross_return"] - 1.0
    out["ev"] = out["net_ev"]
    out["value_band"] = out["ev"].map(value_band)
    out["risk_flag"] = (
        out["odds"].isna()
        | out["approx_prob"].isna()
        | (out["odds"] >= float(thresholds.get("risk_odds", 1000.0)))
        | (out["approx_prob"] <= float(thresholds.get("risk_low_prob", 0.01)))
    )
    out["sort_score"] = out["approx_prob"].fillna(0.0) * out["odds"].fillna(0.0) * 1_000_000.0
    out["has_real_odds"] = out["odds"].notna()
    out["odds_source"] = out["has_real_odds"].map(lambda v: "real" if v else "missing")
    out["calibrated_hit_prob"] = out["calibrated_approx_prob"]
    out["calibration_method"] = prob_mode
    out["calibration_source_col"] = selected_prob_col
    out["strategy_mode"] = "NORMAL"
    if "first_win_proba" in out.columns:
        out["first_place_prob"] = out["first_win_proba"]
    else:
        out["first_place_prob"] = pd.NA
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Build EV table from probability candidates and trifecta odds")
    parser.add_argument(
        "--proba",
        default="data/strategy_outputs/trifecta_candidates.csv",
        help="Probability CSV path (candidate table with trifecta column)",
    )
    parser.add_argument(
        "--odds",
        default="data/odds/today_trifecta_odds.csv",
        help="Trifecta odds CSV path",
    )
    parser.add_argument("--result", help="Optional result CSV path")
    parser.add_argument("--config", default="column_config.json", help="Column config JSON path")
    parser.add_argument("--output-dir", default="data/strategy_outputs", help="Output directory")
    parser.add_argument(
        "--prob-mode",
        choices=("raw", "calibrated"),
        default="raw",
        help="Use raw approx_prob or calibration-adjusted approx_prob for EV calculations",
    )
    args = parser.parse_args()

    config_path = Path(args.config)
    config = load_json_config(config_path if config_path.exists() else None)

    proba_path = Path(args.proba)
    odds_path = Path(args.odds)
    result_path = Path(args.result) if args.result else None
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    proba_df = read_csv_any_encoding(proba_path)
    odds_df = read_csv_any_encoding(odds_path)
    result_df = read_csv_any_encoding(result_path) if result_path and result_path.exists() else pd.DataFrame()

    proba_df = rename_by_aliases(proba_df, config["proba"])
    odds_df = rename_by_aliases(odds_df, config["odds"])
    if not result_df.empty:
        result_df = rename_by_aliases(result_df, config["result"])

    if "trifecta" in proba_df.columns:
        ev_df = build_from_candidate_table(proba_df, odds_df)
    else:
        raise ValueError(
            "build_ev_table expects candidate-table input with a trifecta column for this flow; "
            "lane-level recomputation is disabled."
        )

    ev_df = enrich_ev_table(ev_df, config.get("thresholds", {}), args.prob_mode)

    actual_df = derive_actual_results(result_df)
    if not actual_df.empty and "normalized_race_key" in actual_df.columns and "normalized_race_key" in ev_df.columns:
        actual_cols = [c for c in ["race_id", "actual_trifecta", "combo", "payout", "hit", "normalized_race_key"] if c in actual_df.columns]
        ev_df = ev_df.merge(
            actual_df[actual_cols].drop_duplicates("normalized_race_key"),
            on="normalized_race_key",
            how="left",
            suffixes=("", "_result"),
        )
        if "actual_trifecta" in ev_df.columns:
            ev_df["hit"] = ev_df["trifecta"].eq(ev_df["actual_trifecta"])
            ev_df["result_available"] = ev_df["actual_trifecta"].notna() & ev_df["actual_trifecta"].astype(str).ne("")
        elif "combo" in ev_df.columns:
            ev_df["hit"] = ev_df["trifecta"].eq(ev_df["combo"])
            ev_df["result_available"] = ev_df["combo"].notna() & ev_df["combo"].astype(str).ne("")
    else:
        ev_df["actual_trifecta"] = ""
        ev_df["hit"] = False
        ev_df["result_available"] = False

    if "date" in ev_df.columns:
        ev_df["date"] = pd.to_datetime(ev_df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        ev_df = ev_df.sort_values(["date", "race_id", "ev"], ascending=[True, True, False], na_position="last")
    else:
        ev_df = ev_df.sort_values(["race_id", "ev"], ascending=[True, False], na_position="last")
    ev_df = ev_df.reset_index(drop=True)

    dated_name = "ev_table.csv"
    if "date" in ev_df.columns and ev_df["date"].notna().any():
        date_min = str(ev_df["date"].dropna().min()).replace("-", "")
        date_max = str(ev_df["date"].dropna().max()).replace("-", "")
        dated_name = f"ev_table_{date_min}_{date_max}.csv"
    out_csv = output_dir / dated_name
    out_csv_alt = output_dir / "ev_table.csv"
    ev_df.to_csv(out_csv, index=False)
    if out_csv != out_csv_alt:
        ev_df.to_csv(out_csv_alt, index=False)

    summary = {
        "schema_version": "1.0.0",
        "generated_at": datetime.now().astimezone().replace(microsecond=0).isoformat(),
        "proba_input": str(proba_path),
        "odds_input": str(odds_path),
        "result_input": str(result_path) if result_path else "",
        "prob_mode": args.prob_mode,
        "rows": int(len(ev_df)),
        "races": int(ev_df["race_id"].nunique()) if "race_id" in ev_df.columns else 0,
        "result_available_rows": int(ev_df["result_available"].sum()) if "result_available" in ev_df.columns else 0,
        "hit_rows": int(ev_df["hit"].sum()) if "hit" in ev_df.columns else 0,
        "buy_rows": int((ev_df["ev"] >= float(config["thresholds"]["buy_ev"])).sum()),
        "strong_rows": int((ev_df["ev"] >= float(config["thresholds"]["strong_ev"])).sum()),
        "outputs": {
            "ev_table": str(out_csv_alt),
            "ev_table_dated": str(out_csv),
        },
    }
    out_json = output_dir / "build_ev_table_meta.json"
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
