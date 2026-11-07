import argparse
import csv
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd


THRESHOLDS = {
    "exact_hit_rate": 0.030,
    "top1_hit_rate": 0.250,
    "in_candidates_rate": 0.500,
    "roi": 1.000,
}

ODDS_BANDS = {
    "low": (0, 10),
    "mid": (10, 50),
    "high": (50, 200),
    "ultra": (200, float("inf")),
}

FAVORITE_THRESHOLD = 20.0
MIN_SAMPLE = 5


def warn(message: str) -> None:
    print(f"[warn] {message}")


def normalize_text(value) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def split_trifecta(value: str) -> list[str]:
    txt = normalize_text(value)
    if not txt:
        return []
    return [p.strip() for p in txt.split("-") if p.strip()]


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


def parse_date_series(series: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(series, errors="coerce")
    return parsed


def fill_missing_columns(df: pd.DataFrame, columns: list[str], default_value=np.nan) -> pd.DataFrame:
    out = df.copy()
    for col in columns:
        if col not in out.columns:
            out[col] = default_value
            warn(f"missing column in predictions: {col} (filled with default)")
    return out


def reconstruct_actual_trifecta(results: pd.DataFrame) -> pd.DataFrame:
    required = {"race_id", "lane", "finish_position"}
    missing = required - set(results.columns)
    if missing:
        raise ValueError(f"results file missing required columns: {sorted(missing)}")

    work = results.copy()
    work["race_id"] = work["race_id"].map(normalize_text)
    work["lane"] = pd.to_numeric(work["lane"], errors="coerce")
    work["finish_position"] = pd.to_numeric(work["finish_position"], errors="coerce")
    work["odds_trifecta"] = pd.to_numeric(work.get("odds_trifecta"), errors="coerce")
    if "odds_exacta" in work.columns:
        work["odds_exacta"] = pd.to_numeric(work.get("odds_exacta"), errors="coerce")
    if "odds_2rentan" in work.columns:
        work["odds_2rentan"] = pd.to_numeric(work.get("odds_2rentan"), errors="coerce")
    if "odds_quinella" in work.columns:
        work["odds_quinella"] = pd.to_numeric(work.get("odds_quinella"), errors="coerce")
    if "odds_2renpuku" in work.columns:
        work["odds_2renpuku"] = pd.to_numeric(work.get("odds_2renpuku"), errors="coerce")
    work = work.dropna(subset=["race_id", "lane", "finish_position"]).copy()

    race_rows: list[dict[str, object]] = []
    skipped_incomplete = 0

    for race_id, grp in work.groupby("race_id", sort=False):
        top3 = (
            grp[grp["finish_position"].isin([1, 2, 3])]
            .sort_values("finish_position")
            .copy()
        )
        if len(top3) < 3:
            skipped_incomplete += 1
            continue

        actual_trifecta = "-".join(top3["lane"].astype(int).astype(str).tolist())
        odds_series = top3["odds_trifecta"].dropna()
        official_odds = float(odds_series.iloc[0]) if len(odds_series) > 0 else np.nan
        first_row = grp.iloc[0].to_dict()
        official_exacta_odds = pd.to_numeric(
            first_row.get("odds_exacta", first_row.get("odds_2rentan")),
            errors="coerce",
        )
        official_quinella_odds = pd.to_numeric(
            first_row.get("odds_quinella", first_row.get("odds_2renpuku")),
            errors="coerce",
        )
        row = {
            "race_id": race_id,
            "date": first_row.get("date"),
            "jcd": first_row.get("jcd"),
            "venue": first_row.get("venue"),
            "race_no": first_row.get("race_no"),
            "weather": first_row.get("weather"),
            "grade": first_row.get("grade"),
            "wind_speed": first_row.get("wind_speed"),
            "wave_height": first_row.get("wave_height"),
            "actual_trifecta": actual_trifecta,
            "official_odds": official_odds,
            "official_exacta_odds": official_exacta_odds,
            "official_quinella_odds": official_quinella_odds,
            "result_available": True,
        }
        race_rows.append(row)

    if skipped_incomplete > 0:
        warn(f"skipped {skipped_incomplete} races with incomplete finish_position top3")

    race_df = pd.DataFrame(race_rows)
    if not race_df.empty:
        race_df["date"] = parse_date_series(race_df["date"])
        race_df["jcd"] = race_df["jcd"].map(normalize_text)
        race_df["venue"] = race_df["venue"].map(normalize_text)
        race_df["grade"] = race_df["grade"].map(normalize_text)
        race_df["weather"] = race_df["weather"].map(normalize_text)
        race_df["wind_speed"] = pd.to_numeric(race_df["wind_speed"], errors="coerce")
        race_df["wave_height"] = pd.to_numeric(race_df["wave_height"], errors="coerce")
        race_df["normalized_race_key_legacy"] = race_df["race_id"].apply(normalize_race_key)
        race_df["normalized_race_key"] = build_outcome_match_keys(race_df["race_id"])
        race_df["normalized_race_key"] = race_df["normalized_race_key"].fillna(race_df["normalized_race_key_legacy"])
    return race_df


def normalize_predictions(predictions: pd.DataFrame) -> pd.DataFrame:
    pred = predictions.copy()
    pred["race_id"] = pred["race_id"].map(normalize_text)
    pred["date"] = parse_date_series(pred["date"])
    pred["decision"] = pred["decision"].map(normalize_text).str.upper()
    pred["predicted_trifecta"] = pred["recommended_trifecta"].map(normalize_text)
    pred["first_win_proba"] = pd.to_numeric(pred["first_win_proba"], errors="coerce")
    pred["approx_prob"] = pd.to_numeric(pred["approx_prob"], errors="coerce")
    pred["odds"] = pd.to_numeric(pred["odds"], errors="coerce")
    pred["ev"] = pd.to_numeric(pred["ev"], errors="coerce")
    pred["reason"] = pred.get("reason", "").map(normalize_text) if "reason" in pred.columns else ""
    pred["ticket_count"] = np.where(pred["decision"].eq("BUY"), 1, 0)
    pred["is_buy"] = pred["decision"].eq("BUY")
    pred["normalized_race_key_legacy"] = pred["race_id"].apply(normalize_race_key)
    pred["normalized_race_key"] = pred["race_id"].apply(prediction_match_key)
    pred["normalized_race_key"] = pred["normalized_race_key"].fillna(pred["normalized_race_key_legacy"])
    pred = pred.drop_duplicates(subset=["race_id"], keep="last").copy()
    return pred


def apply_window(predictions: pd.DataFrame, window: str) -> tuple[pd.DataFrame, dict[str, object]]:
    work = predictions.copy()
    work = work.dropna(subset=["date"]).copy()
    if len(work) == 0:
        return work, {"window": window, "date_min": None, "date_max": None, "description": "no valid dates"}

    latest_date = work["date"].dt.normalize().max()
    if window == "all":
        filtered = work.copy()
        start_date = work["date"].dt.normalize().min()
    elif window == "recent30":
        start_date = latest_date - timedelta(days=29)
        filtered = work[work["date"].dt.normalize() >= start_date].copy()
    elif window == "recent60":
        start_date = latest_date - timedelta(days=59)
        filtered = work[work["date"].dt.normalize() >= start_date].copy()
    else:
        raise ValueError(f"unsupported window: {window}")

    info = {
        "window": window,
        "date_min": start_date.strftime("%Y-%m-%d") if pd.notna(start_date) else None,
        "date_max": latest_date.strftime("%Y-%m-%d") if pd.notna(latest_date) else None,
        "description": f"{window} ({start_date.strftime('%Y-%m-%d')} - {latest_date.strftime('%Y-%m-%d')})"
        if pd.notna(start_date)
        else window,
    }
    return filtered, info


def load_inputs(pred_path: Path, results_path: Path) -> pd.DataFrame:
    pred = pd.read_csv(pred_path, low_memory=False)
    results = pd.read_csv(results_path, low_memory=False)

    pred_required = {"race_id", "date", "decision", "recommended_trifecta"}
    missing_pred = pred_required - set(pred.columns)
    if missing_pred:
        raise ValueError(f"predictions file missing required columns: {sorted(missing_pred)}")

    pred = fill_missing_columns(pred, ["first_win_proba", "approx_prob", "odds", "ev", "reason"])
    pred = normalize_predictions(pred)

    outcomes = reconstruct_actual_trifecta(results)
    outcomes = outcomes.sort_values("race_id").drop_duplicates(subset=["normalized_race_key"])
    merged = pred.merge(outcomes, on="normalized_race_key", how="left", suffixes=("", "_result"))
    merged["result_available"] = merged["actual_trifecta"].notna()
    merged["official_odds"] = pd.to_numeric(merged.get("official_odds"), errors="coerce")
    merged["official_exacta_odds"] = pd.to_numeric(merged.get("official_exacta_odds"), errors="coerce")
    merged["official_quinella_odds"] = pd.to_numeric(merged.get("official_quinella_odds"), errors="coerce")
    merged["odds"] = pd.to_numeric(merged.get("odds"), errors="coerce")
    merged["jcd"] = pd.to_numeric(merged.get("jcd"), errors="coerce").astype("Int64")
    merged["venue"] = merged.get("venue", "").map(normalize_text)
    merged["weather"] = merged.get("weather", "").map(normalize_text)
    merged["grade"] = merged.get("grade", "").map(normalize_text)
    merged["wind_speed"] = pd.to_numeric(merged.get("wind_speed"), errors="coerce")
    merged["wave_height"] = pd.to_numeric(merged.get("wave_height"), errors="coerce")
    merged = merged[merged["result_available"]].copy()
    merged["official_odds"] = pd.to_numeric(merged["official_odds"], errors="coerce")

    return merged


def odds_band_label(odds: float | int | None) -> str:
    if pd.isna(odds):
        return "unknown"
    value = float(odds)
    for label, (low, high) in ODDS_BANDS.items():
        if low <= value < high:
            return label
    return "unknown"


def classify_loss_type(row: pd.Series) -> str:
    if bool(row["is_buy"]) and bool(row["exact_hit"]):
        return "hit"
    if not bool(row["is_buy"]):
        return "skip"
    odds = row.get("settled_odds")
    if pd.isna(odds) or float(odds) >= FAVORITE_THRESHOLD:
        return "miss_long"
    return "miss_fav"


def calc_summary_metrics(df: pd.DataFrame) -> dict[str, object]:
    work = df.copy()
    work["official_odds"] = pd.to_numeric(work.get("official_odds"), errors="coerce")
    work["odds"] = pd.to_numeric(work.get("odds"), errors="coerce")
    # official odds を優先し、欠損時は prediction odds を利用
    work["settled_odds"] = work["official_odds"].fillna(work["odds"])
    work["ticket_count"] = np.where(work["is_buy"], 1, 0)
    work["trifecta_match"] = work["predicted_trifecta"].astype(str).eq(work["actual_trifecta"].astype(str))
    pred_parts = work["predicted_trifecta"].astype(str).map(split_trifecta)
    act_parts = work["actual_trifecta"].astype(str).map(split_trifecta)
    work["top1_hit"] = work["is_buy"] & pred_parts.map(lambda x: x[0] if len(x) >= 1 else "").eq(
        act_parts.map(lambda x: x[0] if len(x) >= 1 else "")
    )
    work["in_candidates_hit"] = work["is_buy"] & pred_parts.map(set).eq(act_parts.map(set))
    work["exact_hit"] = work["is_buy"] & work["trifecta_match"]
    work["roi_contribution"] = np.where(
        work["exact_hit"] & work["is_buy"],
        work["settled_odds"].fillna(0.0),
        0.0,
    )
    work["odds_band"] = work["settled_odds"].apply(odds_band_label)
    work["loss_type"] = work.apply(classify_loss_type, axis=1)

    buy_mask = work["is_buy"].astype(bool)
    skip_mask = ~buy_mask
    exact_mask = work["exact_hit"].astype(bool)

    buy_race_count = int(buy_mask.sum())
    skip_race_count = int(skip_mask.sum())
    exact_hit_count = int((buy_mask & exact_mask).sum())
    top1_hit_count = int(work["top1_hit"].sum())
    in_candidates_count = int(work["in_candidates_hit"].sum())
    total_stake = float(buy_race_count)
    total_return = float(work.loc[buy_mask & exact_mask, "settled_odds"].fillna(0.0).sum())
    roi = round(total_return / total_stake, 4) if total_stake > 0 else None
    exact_hit_rate = round(exact_hit_count / buy_race_count, 4) if buy_race_count > 0 else None
    top1_hit_rate = round(top1_hit_count / buy_race_count, 4) if buy_race_count > 0 else None
    in_candidates_rate = round(in_candidates_count / buy_race_count, 4) if buy_race_count > 0 else None
    avg_ticket_count = round(float(work.loc[buy_mask, "ticket_count"].mean()), 4) if buy_race_count > 0 else None

    skip_but_hit_mask = skip_mask & work["trifecta_match"].astype(bool)
    buy_but_miss_mask = buy_mask & (~exact_mask)
    loss_favorite_mask = buy_but_miss_mask & (work["official_odds"] < FAVORITE_THRESHOLD)
    loss_longshot_mask = buy_but_miss_mask & (~loss_favorite_mask)

    loss_patterns = {
        "skip_but_hit_count": int(skip_but_hit_mask.sum()),
        "skip_but_hit_rate": round(float(skip_but_hit_mask.sum() / skip_race_count), 4) if skip_race_count > 0 else None,
        "skip_but_hit_avg_odds": round(float(work.loc[skip_but_hit_mask, "settled_odds"].mean()), 4)
        if skip_but_hit_mask.any()
        else None,
        "buy_but_miss_count": int(buy_but_miss_mask.sum()),
        "buy_but_miss_rate": round(float(buy_but_miss_mask.sum() / buy_race_count), 4) if buy_race_count > 0 else None,
        "buy_but_miss_avg_odds": round(float(work.loc[buy_but_miss_mask, "settled_odds"].mean()), 4)
        if buy_but_miss_mask.any()
        else None,
        "loss_favorite_count": int(loss_favorite_mask.sum()),
        "loss_favorite_rate": round(float(loss_favorite_mask.sum() / buy_but_miss_mask.sum()), 4)
        if buy_but_miss_mask.sum() > 0
        else None,
        "loss_longshot_count": int(loss_longshot_mask.sum()),
        "loss_longshot_rate": round(float(loss_longshot_mask.sum() / buy_but_miss_mask.sum()), 4)
        if buy_but_miss_mask.sum() > 0
        else None,
    }

    band_metrics: dict[str, float | None] = {}
    for band in ODDS_BANDS:
        band_mask = buy_mask & (work["odds_band"] == band)
        band_buy_count = int(band_mask.sum())
        if band_buy_count == 0:
            band_metrics[f"roi_{band}"] = None
            band_metrics[f"hit_rate_{band}"] = None
            continue
        band_hits = int((band_mask & exact_mask).sum())
        band_return = float(work.loc[band_mask & exact_mask, "settled_odds"].fillna(0.0).sum())
        band_metrics[f"roi_{band}"] = round(band_return / float(band_buy_count), 4)
        band_metrics[f"hit_rate_{band}"] = round(band_hits / float(band_buy_count), 4)

    pass_flags = {
        "exact_hit_rate": bool(exact_hit_rate is not None and exact_hit_rate >= THRESHOLDS["exact_hit_rate"]),
        "top1_hit_rate": bool(top1_hit_rate is not None and top1_hit_rate >= THRESHOLDS["top1_hit_rate"]),
        "in_candidates_rate": bool(in_candidates_rate is not None and in_candidates_rate >= THRESHOLDS["in_candidates_rate"]),
        "roi": bool(roi is not None and roi >= THRESHOLDS["roi"]),
    }

    race_level = work.copy()
    race_level["roi_contribution"] = race_level["roi_contribution"].round(4)
    race_level["loss_type"] = race_level["loss_type"].astype(str)

    summary = {
        "race_count": int(len(work)),
        "buy_race_count": buy_race_count,
        "skip_race_count": skip_race_count,
        "exact_hit_count": exact_hit_count,
        "exact_hit_rate": exact_hit_rate,
        "top1_hit_count": top1_hit_count,
        "top1_hit_rate": top1_hit_rate,
        "in_candidates_count": in_candidates_count,
        "in_candidates_rate": in_candidates_rate,
        "roi": roi,
        "avg_ticket_count": avg_ticket_count,
        "loss_patterns": loss_patterns,
        "roi_by_odds_band": {band: band_metrics[f"roi_{band}"] for band in ODDS_BANDS},
        "hit_rate_by_odds_band": {band: band_metrics[f"hit_rate_{band}"] for band in ODDS_BANDS},
        "pass": pass_flags,
        "notes": [
            "exact_hit is strict order match, top1_hit is first-place-only match, in_candidates_hit is orderless 3-lane match.",
        ],
    }
    return summary, race_level


def format_metric(value) -> str:
    if isinstance(value, (float, np.floating)):
        return f"{float(value):.4f}"
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "-"
    return str(value)


def calc_breakdown(df: pd.DataFrame, group_col: str, min_sample: int = MIN_SAMPLE) -> pd.DataFrame:
    if group_col not in df.columns or df[group_col].dropna().empty:
        warn(f"skip breakdown for missing/empty column: {group_col}")
        return pd.DataFrame(columns=[group_col, "sample_size"])

    rows = []
    for key, group in df.groupby(group_col, dropna=False):
        sample_size = int(len(group))
        summary, _ = calc_summary_metrics(group)
        row = {
            group_col: key if pd.notna(key) else "(missing)",
            "sample_size": sample_size,
            "buy_race_count": summary["buy_race_count"],
            "skip_race_count": summary["skip_race_count"],
            "exact_hit_rate": summary["exact_hit_rate"],
            "top1_hit_rate": summary["top1_hit_rate"],
            "in_candidates_rate": summary["in_candidates_rate"],
            "roi": summary["roi"],
            "avg_ticket_count": summary["avg_ticket_count"],
            "skip_but_hit_rate": summary["loss_patterns"]["skip_but_hit_rate"],
            "buy_but_miss_rate": summary["loss_patterns"]["buy_but_miss_rate"],
            "loss_favorite_rate": summary["loss_patterns"]["loss_favorite_rate"],
            "loss_longshot_rate": summary["loss_patterns"]["loss_longshot_rate"],
            "roi_low": summary["roi_by_odds_band"]["low"],
            "roi_mid": summary["roi_by_odds_band"]["mid"],
            "roi_high": summary["roi_by_odds_band"]["high"],
            "roi_ultra": summary["roi_by_odds_band"]["ultra"],
            "hit_rate_low": summary["hit_rate_by_odds_band"]["low"],
            "hit_rate_mid": summary["hit_rate_by_odds_band"]["mid"],
            "hit_rate_high": summary["hit_rate_by_odds_band"]["high"],
            "hit_rate_ultra": summary["hit_rate_by_odds_band"]["ultra"],
        }
        if sample_size < min_sample:
            warn(f"sample size < {min_sample} for {group_col}={key}; metrics will be marked with '-'")
            for metric_col in list(row.keys()):
                if metric_col not in {group_col, "sample_size"}:
                    row[metric_col] = "-"
        rows.append(row)

    return pd.DataFrame(rows).sort_values(["sample_size", group_col], ascending=[False, True])


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def append_experiments_log(log_path: Path, row: dict[str, object]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.exists():
        existing = pd.read_csv(log_path)
        updated = pd.concat([existing, pd.DataFrame([row])], ignore_index=True)
    else:
        updated = pd.DataFrame([row])
    updated.to_csv(log_path, index=False, encoding="utf-8-sig")


def render_console_report(run_id: str, window_info: dict[str, object], summary: dict[str, object]) -> None:
    ok = "[OK]"
    warn_tag = "[WARN]"
    print("=" * 40)
    print(f"  評価レポート: {run_id}")
    print(f"  期間: {window_info['description']}")
    print(f"  対象レース: {summary['race_count']}  BUY: {summary['buy_race_count']}  SKIP: {summary['skip_race_count']}")
    print("=" * 40)

    print()
    print("【基本指標】")
    print(f"  exact_hit_rate    : {format_metric(summary['exact_hit_rate'])}  {ok if summary['pass']['exact_hit_rate'] else warn_tag} (目標: {THRESHOLDS['exact_hit_rate']:.3f}以上)")
    print(f"  top1_hit_rate     : {format_metric(summary['top1_hit_rate'])}  {ok if summary['pass']['top1_hit_rate'] else warn_tag} (目標: {THRESHOLDS['top1_hit_rate']:.3f}以上)")
    print(f"  in_candidates_rate: {format_metric(summary['in_candidates_rate'])}  {ok if summary['pass']['in_candidates_rate'] else warn_tag} (目標: {THRESHOLDS['in_candidates_rate']:.3f}以上)")
    print(f"  ROI               : {format_metric(summary['roi'])}  {ok if summary['pass']['roi'] else warn_tag} (目標: {THRESHOLDS['roi']:.3f}以上)")
    print(f"  平均買い目数      : {format_metric(summary['avg_ticket_count'])}点")

    print()
    print("【負けパターン】")
    lp = summary["loss_patterns"]
    print(
        f"  取りこぼし(SKIP_BUT_HIT) : {lp['skip_but_hit_count']}件 / {summary['skip_race_count']}件中 "
        f"({format_metric(lp['skip_but_hit_rate'])})  平均オッズ: {format_metric(lp['skip_but_hit_avg_odds'])}倍"
    )
    print(
        f"  空振り(BUY_BUT_MISS)     : {lp['buy_but_miss_count']}件 / {summary['buy_race_count']}件中 "
        f"({format_metric(lp['buy_but_miss_rate'])})  平均オッズ: {format_metric(lp['buy_but_miss_avg_odds'])}倍"
    )
    print(
        f"  人気負け                  : {lp['loss_favorite_count']}件 ({format_metric(lp['loss_favorite_rate'])})"
    )
    print(
        f"  穴負け                    : {lp['loss_longshot_count']}件 ({format_metric(lp['loss_longshot_rate'])})"
    )

    print()
    print("【オッズ帯別ROI】")
    for band in ODDS_BANDS:
        roi = summary["roi_by_odds_band"][band]
        hit_rate = summary["hit_rate_by_odds_band"][band]
        band_label = {
            "low": "10倍未満",
            "mid": "10〜50倍",
            "high": "50〜200倍",
            "ultra": "200倍以上",
        }[band]
        state = ok if isinstance(roi, (int, float)) and roi >= 1.0 else (warn_tag if roi is not None else "-")
        print(f"  {band_label:<10}: ROI {format_metric(roi)}  hit_rate {format_metric(hit_rate)}  {state}")

    print()
    print("【要確認条件（ROI < 0.90）】")
    print("  - breakdown outputs should be checked per group for small-sample warnings")
    print(f"  詳細: reports/experiments/{run_id}/")
    print("=" * 40)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate BUY/SKIP experiments against historical results")
    parser.add_argument("--predictions", required=True, help="Path to skip_decisions.csv")
    parser.add_argument("--results", required=True, help="Path to historical_races.csv")
    parser.add_argument("--run-id", required=True, help="Experiment run id")
    parser.add_argument("--window", choices=["recent30", "recent60", "all"], default="all")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output dir under reports/experiments/<run_id>/",
    )
    args = parser.parse_args()

    pred_path = Path(args.predictions)
    results_path = Path(args.results)
    if not pred_path.exists():
        raise FileNotFoundError(f"predictions file not found: {pred_path}")
    if not results_path.exists():
        raise FileNotFoundError(f"results file not found: {results_path}")

    output_dir = Path(args.output_dir) if args.output_dir else Path("reports/experiments") / args.run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = Path("reports/experiments/experiments_log.csv")

    merged = load_inputs(pred_path, results_path)
    if len(merged) == 0:
        raise ValueError("no evaluable races found after merging predictions and results")

    merged, window_info = apply_window(merged, args.window)
    if len(merged) == 0:
        raise ValueError(f"no races left after applying window {args.window}")

    if len(merged) < 10:
        warn(f"evaluation window has only {len(merged)} races; results may be unstable")

    summary, race_level = calc_summary_metrics(merged)
    summary_out = {
        "run_id": args.run_id,
        "evaluation_window": args.window,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        **summary,
    }

    summary_csv_row = {
        "run_id": args.run_id,
        "window": args.window,
        "generated_at": summary_out["generated_at"],
        "race_count": summary["race_count"],
        "buy_race_count": summary["buy_race_count"],
        "skip_race_count": summary["skip_race_count"],
        "exact_hit_count": summary["exact_hit_count"],
        "exact_hit_rate": summary["exact_hit_rate"],
        "top1_hit_count": summary["top1_hit_count"],
        "top1_hit_rate": summary["top1_hit_rate"],
        "in_candidates_count": summary["in_candidates_count"],
        "in_candidates_rate": summary["in_candidates_rate"],
        "roi": summary["roi"],
        "avg_ticket_count": summary["avg_ticket_count"],
        "skip_but_hit_count": summary["loss_patterns"]["skip_but_hit_count"],
        "skip_but_hit_rate": summary["loss_patterns"]["skip_but_hit_rate"],
        "buy_but_miss_count": summary["loss_patterns"]["buy_but_miss_count"],
        "buy_but_miss_rate": summary["loss_patterns"]["buy_but_miss_rate"],
        "loss_favorite_count": summary["loss_patterns"]["loss_favorite_count"],
        "loss_favorite_rate": summary["loss_patterns"]["loss_favorite_rate"],
        "loss_longshot_count": summary["loss_patterns"]["loss_longshot_count"],
        "loss_longshot_rate": summary["loss_patterns"]["loss_longshot_rate"],
    }
    for band in ODDS_BANDS:
        summary_csv_row[f"roi_{band}"] = summary["roi_by_odds_band"][band]
        summary_csv_row[f"hit_rate_{band}"] = summary["hit_rate_by_odds_band"][band]
    summary_csv_row.update({f"pass_{k}": v for k, v in summary["pass"].items()})

    summary_json_path = output_dir / "summary.json"
    summary_csv_path = output_dir / "summary.csv"
    race_level_path = output_dir / "race_level.csv"
    summary_json_path.write_text(json.dumps(summary_out, ensure_ascii=False, indent=2), encoding="utf-8")
    pd.DataFrame([summary_csv_row]).to_csv(summary_csv_path, index=False, encoding="utf-8-sig")

    race_level = race_level.copy()
    race_level["odds_band"] = race_level["odds_band"].astype(str)
    race_level["loss_type"] = race_level["loss_type"].astype(str)
    if "date" in race_level.columns:
        race_level["date"] = pd.to_datetime(race_level["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    race_level_out = race_level[
        [
            "race_id",
            "date",
            "decision",
            "predicted_trifecta",
            "actual_trifecta",
            "top1_hit",
            "in_candidates_hit",
            "exact_hit",
            "official_odds",
            "roi_contribution",
            "odds_band",
            "loss_type",
            "jcd",
            "venue",
            "weather",
            "ticket_count",
        ]
    ].copy()
    race_level_out.to_csv(race_level_path, index=False, encoding="utf-8-sig")

    breakdown_specs = [
        ("breakdown_by_jcd.csv", "jcd"),
        ("breakdown_by_grade.csv", "grade"),
        ("breakdown_by_weather.csv", "weather"),
        ("breakdown_by_weekday_weekend.csv", "weekday_weekend"),
        ("breakdown_by_day_night.csv", "day_night"),
        ("breakdown_by_wind_speed_bin.csv", "wind_speed_bin"),
        ("breakdown_by_wave_height_bin.csv", "wave_height_bin"),
        ("breakdown_by_odds_band.csv", "odds_band"),
    ]

    breakdown_outputs = {}
    work_for_breakdown = race_level.copy()
    work_for_breakdown["weekday_weekend"] = np.where(
        pd.to_datetime(work_for_breakdown["date"], errors="coerce").dt.dayofweek < 5,
        "weekday",
        "weekend",
    )
    date_parsed = pd.to_datetime(work_for_breakdown["date"], errors="coerce")
    has_time_info = False
    if date_parsed.notna().any():
        time_components = date_parsed.dt.hour.fillna(0) + date_parsed.dt.minute.fillna(0) + date_parsed.dt.second.fillna(0)
        has_time_info = bool((time_components != 0).any())
    if has_time_info:
        work_for_breakdown["day_night"] = np.where(
            pd.to_datetime(work_for_breakdown["date"], errors="coerce").dt.hour < 17,
            "day",
            "night",
        )
    else:
        work_for_breakdown["day_night"] = pd.NA
        warn("day_night breakdown skipped because no time information is available in date column")

    work_for_breakdown["wind_speed_bin"] = pd.NA
    if "wind_speed" in work_for_breakdown.columns and work_for_breakdown["wind_speed"].notna().any():
        ws = pd.to_numeric(work_for_breakdown["wind_speed"], errors="coerce")
        work_for_breakdown["wind_speed_bin"] = pd.cut(
            ws,
            bins=[-np.inf, 3, 6, np.inf],
            labels=["calm", "moderate", "strong"],
            right=False,
        )

    work_for_breakdown["wave_height_bin"] = pd.NA
    if "wave_height" in work_for_breakdown.columns and work_for_breakdown["wave_height"].notna().any():
        wh = pd.to_numeric(work_for_breakdown["wave_height"], errors="coerce")
        work_for_breakdown["wave_height_bin"] = pd.cut(
            wh,
            bins=[-np.inf, 1, 2, np.inf],
            labels=["flat", "choppy", "rough"],
            right=False,
        )

    for file_name, col in breakdown_specs:
        breakdown_df = calc_breakdown(work_for_breakdown, col)
        breakdown_path = output_dir / file_name
        breakdown_df.to_csv(breakdown_path, index=False, encoding="utf-8-sig")
        breakdown_outputs[file_name] = str(breakdown_path)

    append_experiments_log(
        log_path,
        {
            "run_id": args.run_id,
            "window": args.window,
            "exact_hit_rate": summary["exact_hit_rate"],
            "top1_hit_rate": summary["top1_hit_rate"],
            "in_candidates_rate": summary["in_candidates_rate"],
            "roi": summary["roi"],
            "avg_ticket_count": summary["avg_ticket_count"],
            "skip_but_hit_rate": summary["loss_patterns"]["skip_but_hit_rate"],
            "buy_but_miss_rate": summary["loss_patterns"]["buy_but_miss_rate"],
            "loss_favorite_rate": summary["loss_patterns"]["loss_favorite_rate"],
            "loss_longshot_rate": summary["loss_patterns"]["loss_longshot_rate"],
            "generated_at": summary_out["generated_at"],
        },
    )

    render_console_report(args.run_id, window_info, summary)
    print(f"\n[saved] {summary_json_path}")
    print(f"[saved] {summary_csv_path}")
    print(f"[saved] {race_level_path}")
    for file_name, path in breakdown_outputs.items():
        print(f"[saved] {path}")
    print(f"[saved] {log_path}")


if __name__ == "__main__":
    main()
