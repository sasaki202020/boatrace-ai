from __future__ import annotations

import argparse
import json
import logging
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import itertools
import pandas as pd

from src.pipeline.boatrace_official_pipeline import JCD_TO_VENUE
from src.utils.race_id import canonical_race_id, canonical_race_key, normalize_race_id, split_race_id


logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_DIR = ROOT / "data"
DEFAULT_REPORT_DIR = ROOT / "reports"
DEFAULT_FEATURE_AVAILABILITY_PATH = ROOT / "data" / "metadata" / "feature_availability.csv"
DEFAULT_COMPAT_CANDIDATES_PATH = ROOT / "data" / "strategy_outputs" / "trifecta_candidates.csv"
DEFAULT_MODEL_NAME = "heuristic_trifecta_baseline"
DEFAULT_MODEL_VERSION = "v1"
LIVE_FEATURE_COLUMNS = [
    "win_proba",
    "win_proba_norm",
    "model_proba_raw",
    "model_win_proba_norm",
    "final_win_proba",
    "model_rank",
    "final_rank",
    "pred_rank_within_race",
    "class",
    "avg_st",
    "nat_win_rate",
    "local_win_rate",
    "motor_rate",
    "boat_rate",
    "exhibition_time",
    "weather",
    "wind_speed",
    "wave_height",
]
RESULT_PHASE_COLUMNS = {
    "finish_position",
    "is_win",
    "is_top2",
    "is_top3",
    "winning_trifecta",
    "payout_trifecta",
}
SCORE_FEATURE_WEIGHTS = {
    "rank_component": 0.36,
    "nat_win_rate": 0.14,
    "local_win_rate": 0.10,
    "avg_st": 0.12,
    "motor_rate": 0.10,
    "boat_rate": 0.08,
    "class": 0.06,
    "exhibition_time": 0.04,
}
CLASS_SCORE_MAP = {
    "A1": 1.0,
    "A2": 0.8,
    "B1": 0.55,
    "B2": 0.3,
}

VENUE_TO_JCD = {venue: f"{int(jcd):02d}" for jcd, venue in JCD_TO_VENUE.items()}


def _read_any(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in {".parquet", ".pq"}:
        try:
            return pd.read_parquet(path)
        except Exception as exc:
            raise RuntimeError(f"failed to read parquet: {path}") from exc
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "cp932"):
        try:
            return pd.read_csv(path, low_memory=False, encoding=encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    return pd.read_csv(path, low_memory=False)


def _load_frame(path: Path | None) -> pd.DataFrame:
    if path is None:
        return pd.DataFrame()
    path = Path(path)
    if not path.exists():
        return pd.DataFrame()
    if path.is_dir():
        frames = []
        for ext in ("*.csv", "*.parquet", "*.pq"):
            for file_path in sorted(path.glob(ext)):
                try:
                    frames.append(_read_any(file_path))
                except Exception as exc:
                    logger.warning("skip input file %s: %s", file_path, exc)
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True, sort=False)
    return _read_any(path)


def _normalize_date_text(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return pd.to_datetime(text, errors="raise").date().isoformat()
    except Exception:
        digits = "".join(ch for ch in text if ch.isdigit())
        if len(digits) >= 8:
            return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
        return None


def _normalize_int(value: object) -> int | None:
    if value is None or pd.isna(value):
        return None
    parsed = pd.to_numeric(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return int(parsed)


def _normalize_race_identity_value(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return normalize_race_id(text)
    except Exception:
        pass
    match = re.match(r"^(?P<date>\d{8})[_-](?P<venue>.+?)[_-](?P<race_no>\d{1,2})$", text)
    if match:
        venue = match.group("venue").strip()
        jcd = VENUE_TO_JCD.get(venue)
        if jcd is not None:
            try:
                return canonical_race_id(match.group("date"), jcd, match.group("race_no"))
            except Exception:
                return None
    return None


def _canonicalize_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    work = df.copy()

    if "race_id" in work.columns:
        work["race_id"] = work["race_id"].map(_normalize_race_identity_value)
    elif {"race_date", "jcd", "race_no"}.issubset(work.columns):
        work["race_id"] = work.apply(
            lambda row: canonical_race_id(row["race_date"], _normalize_int(row["jcd"]), _normalize_int(row["race_no"]))
            if _normalize_date_text(row.get("race_date")) is not None and _normalize_int(row.get("jcd")) is not None and _normalize_int(row.get("race_no")) is not None
            else None,
            axis=1,
        )
    elif "date" in work.columns and "jcd" in work.columns and "race_no" in work.columns:
        work["race_id"] = work.apply(
            lambda row: canonical_race_id(
                _normalize_date_text(row.get("date")),
                _normalize_int(row.get("jcd")),
                _normalize_int(row.get("race_no")),
            )
            if _normalize_date_text(row.get("date")) is not None and _normalize_int(row.get("jcd")) is not None and _normalize_int(row.get("race_no")) is not None
            else None,
            axis=1,
        )
    else:
        work["race_id"] = None

    if "race_date" not in work.columns:
        if "date" in work.columns:
            work["race_date"] = work["date"].map(_normalize_date_text)
        else:
            work["race_date"] = work["race_id"].map(lambda v: split_race_id(v)[0] if v else None)
    work["race_date"] = work["race_date"].map(_normalize_date_text)

    if "jcd" not in work.columns:
        work["jcd"] = work["race_id"].map(lambda v: split_race_id(v)[1] if v else None)
    work["jcd"] = pd.to_numeric(work["jcd"], errors="coerce").astype("Int64")

    if "race_no" not in work.columns:
        work["race_no"] = work["race_id"].map(lambda v: split_race_id(v)[2] if v else None)
    work["race_no"] = pd.to_numeric(work["race_no"], errors="coerce").astype("Int64")

    if "lane" in work.columns:
        work["lane"] = pd.to_numeric(work["lane"], errors="coerce").astype("Int64")

    work = work.dropna(subset=["race_id", "race_date", "jcd", "race_no", "lane"]).copy()
    work["jcd"] = work["jcd"].astype(int)
    work["race_no"] = work["race_no"].astype(int)
    work["lane"] = work["lane"].astype(int)
    work["race_key"] = work.apply(lambda row: canonical_race_key(row["race_date"], row["jcd"], row["race_no"]), axis=1)
    work = work.drop_duplicates(subset=["race_id", "lane"], keep="first").copy()
    return work


def _load_feature_availability(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(
            columns=[
                "feature_name",
                "source_table",
                "available_phase",
                "allowed_for_training",
                "allowed_for_live",
                "description",
            ]
        )
    return pd.read_csv(path, low_memory=False)


def _validate_feature_usage(frame: pd.DataFrame, availability: pd.DataFrame, *, live_only: bool) -> list[str]:
    problems: list[str] = []
    if frame.empty:
        return problems

    declared = set(availability.get("feature_name", pd.Series(dtype=object)).astype(str).tolist())
    live_allowed = set(
        availability.loc[
            availability.get("allowed_for_live", pd.Series(dtype=object)).astype(str).str.lower().isin({"true", "1", "yes"}),
            "feature_name",
        ].astype(str).tolist()
    )
    result_phase = set(
        availability.loc[
            availability.get("available_phase", pd.Series(dtype=object)).astype(str).str.lower().eq("result"),
            "feature_name",
        ].astype(str).tolist()
    )
    used = [col for col in frame.columns if col in declared]
    forbidden = [col for col in used if col in result_phase]
    if forbidden:
        problems.append("result_phase_used:" + ",".join(sorted(set(forbidden))))
    if live_only:
        live_forbidden = [col for col in used if col not in live_allowed]
        if live_forbidden:
            problems.append("live_forbidden_columns:" + ",".join(sorted(set(live_forbidden))))
    return problems


def _validate_no_odds_or_results(frame: pd.DataFrame) -> list[str]:
    if frame.empty:
        return []
    forbidden = []
    for col in frame.columns:
        name = str(col).lower()
        if name in RESULT_PHASE_COLUMNS or "odds" in name or name in {"ev", "adjusted_score", "gross_return"}:
            forbidden.append(col)
    return [f"forbidden_columns:{','.join(sorted(set(forbidden)))}"] if forbidden else []


def _normalize_component(series: pd.Series, *, higher_better: bool = True) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    if values.dropna().empty:
        return pd.Series([0.5] * len(series), index=series.index, dtype="float64")
    min_v = float(values.min())
    max_v = float(values.max())
    if pd.isna(min_v) or pd.isna(max_v) or abs(max_v - min_v) < 1e-12:
        return pd.Series([0.5] * len(series), index=series.index, dtype="float64")
    scaled = (values - min_v) / (max_v - min_v)
    scaled = scaled.clip(0.0, 1.0).fillna(0.5)
    if higher_better:
        return scaled.astype(float)
    return (1.0 - scaled).astype(float)


def _class_score(series: pd.Series) -> pd.Series:
    mapped = series.astype(str).str.strip().map(CLASS_SCORE_MAP)
    if mapped.dropna().empty:
        return pd.Series([0.5] * len(series), index=series.index, dtype="float64")
    return mapped.fillna(mapped.mean()).astype(float)


def _candidate_feature_frame(group: pd.DataFrame) -> pd.DataFrame:
    work = group.copy()
    if work.empty:
        return work

    if "pred_rank_within_race" in work.columns:
        work["pred_rank_within_race"] = pd.to_numeric(work["pred_rank_within_race"], errors="coerce")
    elif "final_rank" in work.columns:
        work["pred_rank_within_race"] = pd.to_numeric(work["final_rank"], errors="coerce")
    elif "model_rank" in work.columns:
        work["pred_rank_within_race"] = pd.to_numeric(work["model_rank"], errors="coerce")
    elif "win_proba_norm" in work.columns:
        work["pred_rank_within_race"] = pd.to_numeric(work["win_proba_norm"], errors="coerce").rank(method="first", ascending=False)
    else:
        work["pred_rank_within_race"] = pd.Series(range(1, len(work) + 1), index=work.index, dtype="float64")

    work["pred_rank_within_race"] = pd.to_numeric(work["pred_rank_within_race"], errors="coerce")
    work["rank_component"] = _normalize_component(work["pred_rank_within_race"], higher_better=False)
    for col in ["nat_win_rate", "avg_st", "motor_rate", "boat_rate", "local_win_rate", "exhibition_time"]:
        if col not in work.columns:
            work[f"{col}_component"] = 0.5
            continue
        if col in {"avg_st", "exhibition_time"}:
            work[f"{col}_component"] = _normalize_component(work[col], higher_better=False)
        else:
            work[f"{col}_component"] = _normalize_component(work[col], higher_better=True)
    if "class" in work.columns:
        work["class_component"] = _class_score(work["class"])
    else:
        work["class_component"] = 0.5

    weight_rows = []
    for _, row in work.iterrows():
        components = []
        for key, weight in SCORE_FEATURE_WEIGHTS.items():
            col = "class_component" if key == "class" else f"{key}_component" if key not in {"rank_component", "class"} else "rank_component"
            if col not in row.index:
                continue
            value = row.get(col)
            if pd.isna(value):
                continue
            components.append((float(value), float(weight)))
        if not components:
            lane_strength = 0.5
        else:
            total_weight = sum(weight for _, weight in components)
            lane_strength = sum(value * weight for value, weight in components) / total_weight if total_weight > 0 else 0.5
        weight_rows.append(lane_strength)
    work["lane_strength"] = pd.Series(weight_rows, index=work.index, dtype="float64").clip(0.0, 1.0)
    return work


def _score_race(group: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    work = group.copy()
    work["win_proba"] = pd.to_numeric(work["win_proba"], errors="coerce")
    total = float(work["win_proba"].sum())
    if total <= 0:
        logger.warning("race %s win_proba sum is non-positive; falling back to uniform probabilities", work["race_id"].iloc[0] if not work.empty else "-")
        work["win_proba"] = 1.0
        total = float(len(work))
    work["win_proba_norm"] = work["win_proba"] / total
    work = work.sort_values(["win_proba_norm", "lane"], ascending=[False, True], kind="mergesort").reset_index(drop=True)
    work["pred_rank_within_race"] = work.index + 1
    work = _candidate_feature_frame(work)
    lane_strength_map = work.set_index("lane")["lane_strength"].to_dict()
    prob_map = work.set_index("lane")["win_proba_norm"].to_dict()
    rank_map = work.set_index("lane")["pred_rank_within_race"].to_dict()
    top_lane = int(work.iloc[0]["lane"])

    rows: list[dict[str, Any]] = []
    for first_lane, second_lane, third_lane in itertools.permutations(work["lane"].tolist(), 3):
        if len({first_lane, second_lane, third_lane}) < 3:
            continue
        first_score = float(prob_map.get(first_lane, 0.0))
        second_score = float(lane_strength_map.get(second_lane, 0.0))
        third_score = float(lane_strength_map.get(third_lane, 0.0))
        candidate_score = first_score * second_score * third_score
        debug = {
            "first_prob": round(first_score, 6),
            "second_lane_strength": round(second_score, 6),
            "third_lane_strength": round(third_score, 6),
            "second_rank_component": round(float(work.loc[work["lane"] == second_lane, "rank_component"].iloc[0]), 6) if (work["lane"] == second_lane).any() else None,
            "third_rank_component": round(float(work.loc[work["lane"] == third_lane, "rank_component"].iloc[0]), 6) if (work["lane"] == third_lane).any() else None,
            "first_pred_rank": int(rank_map.get(first_lane, 0) or 0),
            "second_pred_rank": int(rank_map.get(second_lane, 0) or 0),
            "third_pred_rank": int(rank_map.get(third_lane, 0) or 0),
        }
        rows.append(
            {
                "race_date": work["race_date"].iloc[0],
                "jcd": int(work["jcd"].iloc[0]),
                "race_no": int(work["race_no"].iloc[0]),
                "race_id": work["race_id"].iloc[0],
                "race_key": work["race_key"].iloc[0],
                "first_lane": int(first_lane),
                "second_lane": int(second_lane),
                "third_lane": int(third_lane),
                "trifecta_key": f"{int(first_lane)}-{int(second_lane)}-{int(third_lane)}",
                "first_score": round(first_score, 6),
                "second_score": round(second_score, 6),
                "third_score": round(third_score, 6),
                "candidate_score": round(candidate_score, 10),
                "model_name": DEFAULT_MODEL_NAME,
                "model_version": DEFAULT_MODEL_VERSION,
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "score_debug_json": json.dumps(debug, ensure_ascii=False),
            }
        )

    cand = pd.DataFrame(rows)
    if cand.empty:
        return cand, {"race_id": work["race_id"].iloc[0], "generated": 0}
    cand = cand.sort_values(
        ["candidate_score", "first_score", "second_score", "third_score", "trifecta_key"],
        ascending=[False, False, False, False, True],
        kind="mergesort",
    ).reset_index(drop=True)
    cand["candidate_rank"] = cand.index + 1
    return cand, {
        "race_id": work["race_id"].iloc[0],
        "generated": int(len(cand)),
        "top_lane": top_lane,
        "top_first_prob": float(work["win_proba_norm"].iloc[0]),
        "top_score": float(cand["candidate_score"].iloc[0]),
        "lane_count": int(len(work)),
    }


def generate_trifecta_candidates(
    win_proba_path: Path | str,
    *,
    pre_race_features_path: Path | str | None = None,
    results_path: Path | str | None = None,
    feature_availability_path: Path = DEFAULT_FEATURE_AVAILABILITY_PATH,
    top_n: int = 10,
    out_dir: Path = DEFAULT_OUT_DIR,
    report_dir: Path = DEFAULT_REPORT_DIR,
    compat_candidates_path: Path = DEFAULT_COMPAT_CANDIDATES_PATH,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    out_dir = Path(out_dir)
    report_dir = Path(report_dir)
    report_root = report_dir / "trifecta_candidate_eval"
    report_root.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    availability = _load_feature_availability(feature_availability_path)
    win_df = _canonicalize_frame(_load_frame(Path(win_proba_path)))
    feat_df = _canonicalize_frame(_load_frame(Path(pre_race_features_path))) if pre_race_features_path else pd.DataFrame()
    if start_date:
        start_norm = _normalize_date_text(start_date)
        end_norm = _normalize_date_text(end_date or start_date)
        if start_norm is None or end_norm is None:
            raise ValueError("invalid date range")
        if not win_df.empty:
            win_df = win_df[win_df["race_date"].between(start_norm, end_norm, inclusive="both")].copy()
        if not feat_df.empty:
            feat_df = feat_df[feat_df["race_date"].between(start_norm, end_norm, inclusive="both")].copy()

    if win_df.empty:
        raise ValueError(f"win probability input is empty or missing: {win_proba_path}")

    issues = []
    issues.extend(_validate_no_odds_or_results(win_df))
    if not feat_df.empty:
        issues.extend(_validate_no_odds_or_results(feat_df))
        issues.extend(_validate_feature_usage(feat_df, availability, live_only=True))
    issues.extend(_validate_feature_usage(win_df, availability, live_only=True))
    if issues:
        raise ValueError("; ".join(issues))

    merge_keys = ["race_id", "race_date", "jcd", "race_no", "lane"]
    merged = win_df.copy()
    if not feat_df.empty:
        common = [col for col in feat_df.columns if col not in merge_keys]
        feat_subset = feat_df[merge_keys + common].copy()
        merged = merged.merge(feat_subset, on=merge_keys, how="left", suffixes=("", "_feat"))
        for col in common:
            feat_col = f"{col}_feat"
            if feat_col in merged.columns:
                if col in merged.columns:
                    merged[col] = merged[feat_col].combine_first(merged[col])
                else:
                    merged[col] = merged[feat_col]
                merged = merged.drop(columns=[feat_col])

    if "win_proba" not in merged.columns:
        if "final_win_proba" in merged.columns:
            merged["win_proba"] = merged["final_win_proba"]
        elif "win_proba_norm" in merged.columns:
            merged["win_proba"] = merged["win_proba_norm"]
        elif "model_win_proba_norm" in merged.columns:
            merged["win_proba"] = merged["model_win_proba_norm"]
        elif "model_proba_raw" in merged.columns:
            merged["win_proba"] = merged["model_proba_raw"]
        else:
            raise ValueError("win probability input must contain win_proba or compatible probability columns")

    required = ["race_id", "race_date", "jcd", "race_no", "lane", "win_proba"]
    missing = [col for col in required if col not in merged.columns]
    if missing:
        raise ValueError(f"missing required columns after canonicalization: {missing}")

    merged = merged.dropna(subset=["race_id", "race_date", "jcd", "race_no", "lane"]).copy()
    merged["jcd"] = merged["jcd"].astype(int)
    merged["race_no"] = merged["race_no"].astype(int)
    merged["lane"] = merged["lane"].astype(int)
    merged["race_key"] = merged.apply(lambda row: canonical_race_key(row["race_date"], row["jcd"], row["race_no"]), axis=1)
    merged = merged.sort_values(["race_date", "jcd", "race_no", "lane"], kind="mergesort").reset_index(drop=True)

    race_counts = merged.groupby("race_id")["lane"].nunique(dropna=True)
    valid_races = race_counts[race_counts == 6].index.astype(str).tolist()
    invalid_races = race_counts[race_counts != 6].index.astype(str).tolist()

    all_candidates: list[pd.DataFrame] = []
    race_summaries: list[dict[str, Any]] = []
    skip_reasons = Counter()
    for race_id, group in merged.groupby("race_id", sort=False):
        if len(group) < 6:
            skip_reasons["less_than_six_boats"] += 1
            logger.warning("skip race %s because lane count is %s", race_id, len(group))
            continue
        cand, race_summary = _score_race(group)
        if cand.empty:
            skip_reasons["empty_candidate_set"] += 1
            logger.warning("skip race %s because no candidates were generated", race_id)
            continue
        all_candidates.append(cand)
        race_summaries.append(race_summary)

    if all_candidates:
        full_candidates = pd.concat(all_candidates, ignore_index=True, sort=False)
    else:
        full_candidates = pd.DataFrame(
            columns=[
                "race_date",
                "jcd",
                "race_no",
                "race_id",
                "race_key",
                "first_lane",
                "second_lane",
                "third_lane",
                "trifecta_key",
                "first_score",
                "second_score",
                "third_score",
                "candidate_score",
                "candidate_rank",
                "model_name",
                "model_version",
                "created_at",
                "score_debug_json",
            ]
        )

    if not full_candidates.empty:
        full_candidates = full_candidates.sort_values(
            ["race_date", "jcd", "race_no", "candidate_rank"],
            kind="mergesort",
        ).reset_index(drop=True)
        topn_candidates = full_candidates[full_candidates["candidate_rank"].astype(int) <= int(top_n)].copy()
    else:
        topn_candidates = full_candidates.copy()

    suffix = "all"
    if not merged.empty and "race_date" in merged.columns:
        unique_dates = sorted([d for d in merged["race_date"].dropna().astype(str).unique().tolist() if d])
        if unique_dates:
            suffix = unique_dates[0] if len(unique_dates) == 1 else f"{unique_dates[0]}_to_{unique_dates[-1]}"

    predictions_dir = out_dir / "predictions"
    strategy_dir = out_dir / "strategy_outputs"
    predictions_dir.mkdir(parents=True, exist_ok=True)
    strategy_dir.mkdir(parents=True, exist_ok=True)

    full_path = predictions_dir / f"trifecta_candidates_full_{suffix}.csv"
    topn_path = predictions_dir / f"trifecta_candidates_topn_{suffix}.csv"
    compat_candidates_path = Path(compat_candidates_path)
    compat_candidates_path.parent.mkdir(parents=True, exist_ok=True)
    full_candidates.to_csv(full_path, index=False, encoding="utf-8-sig")
    topn_candidates.to_csv(topn_path, index=False, encoding="utf-8-sig")
    full_candidates.to_csv(compat_candidates_path, index=False, encoding="utf-8-sig")

    eval_summary: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "model_name": DEFAULT_MODEL_NAME,
        "model_version": DEFAULT_MODEL_VERSION,
        "input_summary": {
            "win_proba_rows": int(len(win_df)),
            "feature_rows": int(len(feat_df)),
            "merged_rows": int(len(merged)),
            "valid_races": int(len(valid_races)),
            "invalid_races": int(len(invalid_races)),
        },
        "generation_summary": {
            "generated_races": int(len(race_summaries)),
            "skipped_races": int(len(invalid_races)),
            "skip_reason_counts": dict(skip_reasons),
            "candidate_rows": int(len(full_candidates)),
            "topn_rows": int(len(topn_candidates)),
            "top_n": int(top_n),
        },
        "used_columns": {
            "prediction_columns": [col for col in ["win_proba", "win_proba_norm", "model_proba_raw", "model_win_proba_norm", "final_win_proba", "model_rank", "final_rank", "pred_rank_within_race"] if col in merged.columns],
            "feature_columns": [col for col in LIVE_FEATURE_COLUMNS if col in merged.columns],
            "merge_keys": merge_keys,
        },
        "output_paths": {
            "full": str(full_path),
            "topn": str(topn_path),
            "compat": str(compat_candidates_path),
        },
        "race_summaries": race_summaries[:20],
    }

    summary_path = report_root / "trifecta_candidate_summary.json"
    metrics_path = report_root / "trifecta_candidate_metrics.csv"
    venue_path = report_root / "trifecta_candidate_by_venue.csv"
    rank_cut_path = report_root / "trifecta_candidate_by_rank_cut.csv"
    used_columns_path = report_root / "used_columns.json"
    report_root.mkdir(parents=True, exist_ok=True)

    evaluation = evaluate_trifecta_candidates(full_candidates, results_path=results_path)
    if evaluation:
        eval_summary.update(evaluation)

    summary_path.write_text(json.dumps(eval_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    pd.DataFrame([{
        "metric": key,
        "value": value,
    } for key, value in (eval_summary.get("overall_metrics", {}) or {}).items()]).to_csv(metrics_path, index=False, encoding="utf-8-sig")
    pd.DataFrame(eval_summary.get("by_venue", []) or []).to_csv(venue_path, index=False, encoding="utf-8-sig")
    pd.DataFrame(eval_summary.get("by_rank_cut", []) or []).to_csv(rank_cut_path, index=False, encoding="utf-8-sig")
    used_columns_path.write_text(json.dumps(eval_summary["used_columns"], ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(eval_summary, ensure_ascii=False, indent=2))
    return eval_summary


def _build_truth_frame(results_df: pd.DataFrame) -> pd.DataFrame:
    if results_df.empty:
        return pd.DataFrame(columns=["race_id", "winning_trifecta", "race_key"])
    work = results_df.copy()
    if "winning_trifecta" not in work.columns:
        if {"finish_position", "lane"}.issubset(work.columns):
            work["finish_position"] = pd.to_numeric(work["finish_position"], errors="coerce")
            work["lane"] = pd.to_numeric(work["lane"], errors="coerce")
            work = work.dropna(subset=["race_id", "finish_position", "lane"]).copy()
            rows = []
            for race_id, group in work.groupby("race_id", sort=False):
                top3 = group[group["finish_position"].isin([1, 2, 3])].sort_values("finish_position")
                if len(top3) < 3:
                    continue
                rows.append(
                    {
                        "race_id": race_id,
                        "winning_trifecta": "-".join(top3["lane"].astype(int).astype(str).tolist()),
                    }
                )
            truth = pd.DataFrame(rows)
            if not truth.empty:
                truth["race_key"] = truth["race_id"].map(lambda v: canonical_race_key(*split_race_id(v)))
            return truth
        return pd.DataFrame(columns=["race_id", "winning_trifecta", "race_key"])

    work = work.dropna(subset=["race_id", "winning_trifecta"]).copy()
    work["race_id"] = work["race_id"].map(_normalize_race_identity_value)
    work = work.dropna(subset=["race_id"]).copy()
    if "race_key" not in work.columns:
        work["race_key"] = work["race_id"].map(lambda v: canonical_race_key(*split_race_id(v)))
    truth = work[["race_id", "race_key", "winning_trifecta"]].drop_duplicates("race_id").copy()
    return truth


def evaluate_trifecta_candidates(candidate_df: pd.DataFrame, *, results_path: Path | str | None) -> dict[str, Any]:
    if candidate_df.empty:
        return {
            "evaluation_available": False,
            "overall_metrics": {},
            "by_venue": [],
            "by_rank_cut": [],
        }

    if results_path is None:
        return {
            "evaluation_available": False,
            "overall_metrics": {},
            "by_venue": [],
            "by_rank_cut": [],
        }

    results_df = _canonicalize_frame(_load_frame(Path(results_path)))
    truth = _build_truth_frame(results_df)
    if truth.empty:
        return {
            "evaluation_available": False,
            "overall_metrics": {},
            "by_venue": [],
            "by_rank_cut": [],
        }

    cand = candidate_df.copy()
    cand["race_id"] = cand["race_id"].map(_normalize_race_identity_value)
    cand["trifecta_key"] = cand["trifecta_key"].astype(str)
    cand["candidate_rank"] = pd.to_numeric(cand["candidate_rank"], errors="coerce")
    cand = cand.dropna(subset=["race_id", "candidate_rank"]).copy()
    cand["candidate_rank"] = cand["candidate_rank"].astype(int)

    truth = truth.dropna(subset=["race_id", "winning_trifecta"]).copy()
    truth["race_id"] = truth["race_id"].map(_normalize_race_identity_value)
    truth = truth.dropna(subset=["race_id"]).copy()

    race_rows = []
    for race_id, truth_row in truth.groupby("race_id", sort=False):
        cand_group = cand[cand["race_id"] == race_id].sort_values("candidate_score", ascending=False, kind="mergesort").reset_index(drop=True)
        try:
            _, truth_jcd, _ = split_race_id(race_id)
        except Exception:
            truth_jcd = None
        if cand_group.empty:
            race_rows.append(
                {
                    "race_id": race_id,
                    "race_key": truth_row["race_key"].iloc[0],
                    "jcd": int(truth_jcd) if truth_jcd is not None else None,
                    "winning_trifecta": truth_row["winning_trifecta"].iloc[0],
                    "winning_rank": None,
                    "candidate_count": 0,
                    "evaluated": False,
                }
            )
            continue
        target = str(truth_row["winning_trifecta"].iloc[0]).strip()
        rank_series = cand_group.index[cand_group["trifecta_key"].astype(str) == target]
        winning_rank = int(rank_series[0] + 1) if len(rank_series) else None
        race_rows.append(
            {
                "race_id": race_id,
                "race_key": truth_row["race_key"].iloc[0],
                "jcd": int(truth_jcd) if truth_jcd is not None else None,
                "winning_trifecta": target,
                "winning_rank": winning_rank,
                "candidate_count": int(len(cand_group)),
                "evaluated": True,
            }
        )

    race_eval = pd.DataFrame(race_rows)
    if race_eval.empty:
        return {
            "evaluation_available": False,
            "overall_metrics": {},
            "by_venue": [],
            "by_rank_cut": [],
        }

    valid = race_eval[race_eval["winning_rank"].notna()].copy()
    eval_count = int(len(race_eval))
    ranked_count = int(len(valid))
    overall = {
        "evaluation_race_count": eval_count,
        "ranked_race_count": ranked_count,
        "candidate_coverage_rate": round(ranked_count / eval_count, 4) if eval_count else None,
        "hit@1": round(float((valid["winning_rank"] <= 1).mean()), 4) if ranked_count else 0.0,
        "hit@3": round(float((valid["winning_rank"] <= 3).mean()), 4) if ranked_count else 0.0,
        "hit@5": round(float((valid["winning_rank"] <= 5).mean()), 4) if ranked_count else 0.0,
        "hit@10": round(float((valid["winning_rank"] <= 10).mean()), 4) if ranked_count else 0.0,
        "mean_winning_rank": round(float(valid["winning_rank"].mean()), 3) if ranked_count else None,
        "avg_candidate_count": round(float(race_eval["candidate_count"].mean()), 2) if eval_count else None,
    }

    rank_cuts = []
    for k in [1, 3, 5, 10, 20]:
        rank_cuts.append(
            {
                "rank_cut": k,
                "hit_count": int((valid["winning_rank"] <= k).sum()),
                "hit_rate": round(float((valid["winning_rank"] <= k).mean()), 4) if ranked_count else 0.0,
                "evaluation_race_count": eval_count,
                "ranked_race_count": ranked_count,
            }
        )

    by_venue = []
    for jcd, group in race_eval.groupby("jcd", sort=True):
        gvalid = group[group["winning_rank"].notna()].copy()
        gcount = int(len(group))
        granked = int(len(gvalid))
        by_venue.append(
            {
                "jcd": int(jcd),
                "evaluation_race_count": gcount,
                "ranked_race_count": granked,
                "candidate_coverage_rate": round(granked / gcount, 4) if gcount else None,
                "hit@1": round(float((gvalid["winning_rank"] <= 1).mean()), 4) if granked else 0.0,
                "hit@3": round(float((gvalid["winning_rank"] <= 3).mean()), 4) if granked else 0.0,
                "hit@5": round(float((gvalid["winning_rank"] <= 5).mean()), 4) if granked else 0.0,
                "hit@10": round(float((gvalid["winning_rank"] <= 10).mean()), 4) if granked else 0.0,
                "mean_winning_rank": round(float(gvalid["winning_rank"].mean()), 3) if granked else None,
                "avg_candidate_count": round(float(group["candidate_count"].mean()), 2) if gcount else None,
            }
        )

    return {
        "evaluation_available": True,
        "overall_metrics": overall,
        "by_venue": by_venue,
        "by_rank_cut": rank_cuts,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate ranked trifecta candidates from win probabilities.")
    parser.add_argument("--win-proba-path", required=True, help="Win probability CSV/Parquet file or directory.")
    parser.add_argument("--pre-race-features-path", default=None, help="Optional pre-race features CSV/Parquet file or directory.")
    parser.add_argument("--results-path", default=None, help="Optional results/training dataset CSV/Parquet file or directory for evaluation.")
    parser.add_argument("--feature-availability-path", default=str(DEFAULT_FEATURE_AVAILABILITY_PATH))
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR))
    parser.add_argument("--compat-candidates-path", default=str(DEFAULT_COMPAT_CANDIDATES_PATH))
    parser.add_argument("--date", default=None, help="Optional target date in YYYY-MM-DD.")
    parser.add_argument("--start-date", default=None, help="Optional start date in YYYY-MM-DD.")
    parser.add_argument("--end-date", default=None, help="Optional end date in YYYY-MM-DD.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.date and (args.start_date or args.end_date):
        raise ValueError("use either --date or --start-date/--end-date")
    start_date = args.start_date or args.date
    end_date = args.end_date or args.date

    summary = generate_trifecta_candidates(
        args.win_proba_path,
        pre_race_features_path=args.pre_race_features_path,
        results_path=args.results_path,
        feature_availability_path=Path(args.feature_availability_path),
        top_n=int(args.top_n),
        out_dir=Path(args.out_dir),
        report_dir=Path(args.report_dir),
        compat_candidates_path=Path(args.compat_candidates_path),
        start_date=start_date,
        end_date=end_date,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
