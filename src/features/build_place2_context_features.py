from __future__ import annotations

"""Build Phase 2 place2 context features from the fixed Phase 1 win predictor."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

from src.features.build_relative_features import add_race_relative_features


@dataclass(frozen=True)
class Place2ContextBuildSummary:
    """Summary of place2 context construction."""

    input_race_count: int
    output_context_count: int
    dropped_race_count: int
    dropped_race_ids: list[str]


def _numeric_coerce(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    out = frame.copy()
    for col in columns:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    return out


def load_phase1_bundle(bundle_path: Path) -> dict[str, Any]:
    bundle = joblib.load(bundle_path)
    if not isinstance(bundle, dict) or "model" not in bundle or "feature_columns" not in bundle:
        raise ValueError(f"invalid phase1 bundle: {bundle_path}")
    return bundle


def predict_phase1_probabilities(frame: pd.DataFrame, bundle_path: Path) -> pd.DataFrame:
    """Attach Phase 1 win probability outputs using the fixed official predictor."""

    bundle = load_phase1_bundle(bundle_path)
    model = bundle["model"]
    feature_columns = list(bundle["feature_columns"])

    missing = [col for col in feature_columns if col not in frame.columns]
    if missing:
        raise ValueError(f"phase1 prediction frame missing required columns: {missing}")

    work = _numeric_coerce(frame.copy(), feature_columns)
    out = work.copy()
    out["win_proba_raw"] = model.predict_proba(work[feature_columns])[:, 1]
    out["win_proba_norm"] = out.groupby("race_id")["win_proba_raw"].transform(
        lambda s: s / s.sum() if float(s.sum()) > 0 else 0.0
    )
    out["win_proba_norm"] = out["win_proba_norm"].fillna(0.0)
    out["rank_within_race"] = out.groupby("race_id")["win_proba_norm"].rank(
        method="first", ascending=False
    )
    return out


def build_place2_context_frame(
    frame: pd.DataFrame,
    *,
    phase1_bundle_path: Path,
    split_name: str,
) -> tuple[pd.DataFrame, Place2ContextBuildSummary]:
    """Expand a race-level frame into 2nd-place conditional contexts."""

    if "race_id" not in frame.columns:
        raise ValueError("frame must contain race_id")
    if "finish_position" not in frame.columns:
        raise ValueError("frame must contain finish_position")

    rel_frame = add_race_relative_features(frame)
    pred_frame = predict_phase1_probabilities(rel_frame, phase1_bundle_path)

    rows: list[dict[str, Any]] = []
    dropped_race_ids: list[str] = []
    for race_id, race_df in pred_frame.groupby("race_id", dropna=False):
        race_work = race_df.copy()
        if len(race_work) != 6:
            dropped_race_ids.append(str(race_id))
            continue
        win_rows = race_work[race_work["finish_position"] == 1].copy()
        place2_rows = race_work[race_work["finish_position"] == 2].copy()
        if len(win_rows) != 1 or len(place2_rows) != 1:
            dropped_race_ids.append(str(race_id))
            continue

        first = win_rows.iloc[0]
        context_id = f"{race_id}__fp{int(first['boat_no']):02d}"
        first_rank = float(first["rank_within_race"]) if pd.notna(first["rank_within_race"]) else np.nan
        first_raw = float(first["win_proba_raw"]) if pd.notna(first["win_proba_raw"]) else np.nan
        first_norm = float(first["win_proba_norm"]) if pd.notna(first["win_proba_norm"]) else np.nan

        remaining = race_work[race_work["boat_no"] != first["boat_no"]].copy()
        if len(remaining) != 5:
            dropped_race_ids.append(str(race_id))
            continue

        remaining = remaining.sort_values("win_proba_norm", ascending=False, kind="mergesort").copy()
        remaining["candidate_rank_within_remaining_field"] = range(1, len(remaining) + 1)
        for _, candidate in remaining.iterrows():
            row = candidate.to_dict()
            row["split_name"] = split_name
            row["place2_context_id"] = context_id
            row["fixed_first_place_boat_no"] = int(first["boat_no"])
            row["fixed_first_place_source"] = "actual_first_place"
            row["fixed_first_place_rank_within_race"] = first_rank
            row["fixed_first_place_win_proba_raw"] = first_raw
            row["fixed_first_place_win_proba_norm"] = first_norm
            row["candidate_boat_no"] = int(candidate["boat_no"])
            row["candidate_rank_within_race_before_fix"] = float(candidate["rank_within_race"]) if pd.notna(candidate["rank_within_race"]) else np.nan
            row["candidate_win_proba_raw"] = float(candidate["win_proba_raw"]) if pd.notna(candidate["win_proba_raw"]) else np.nan
            row["candidate_win_proba_norm"] = float(candidate["win_proba_norm"]) if pd.notna(candidate["win_proba_norm"]) else np.nan
            row["candidate_win_rank_within_race"] = float(candidate["rank_within_race"]) if pd.notna(candidate["rank_within_race"]) else np.nan
            row["candidate_phase1_margin_to_fixed_first"] = (
                row["candidate_win_proba_norm"] - first_norm
                if pd.notna(row["candidate_win_proba_norm"]) and pd.notna(first_norm)
                else np.nan
            )
            row["remaining_field_size"] = 5
            row["target_place2"] = int(candidate["finish_position"] == 2)
            rows.append(row)

    out = pd.DataFrame(rows)
    if out.empty:
        summary = Place2ContextBuildSummary(
            input_race_count=int(frame["race_id"].nunique()),
            output_context_count=0,
            dropped_race_count=int(frame["race_id"].nunique()),
            dropped_race_ids=sorted(dict.fromkeys(dropped_race_ids)),
        )
        return out, summary

    out["place2_context_id"] = out["place2_context_id"].astype(str)
    out["split_name"] = out["split_name"].astype(str)
    out["model_name"] = "place2_model_phase2_core_relative"
    out["feature_set_name"] = "phase2_place2_conditional"
    out["phase1_model_name"] = str(Path(phase1_bundle_path).name).replace(".joblib", "")
    out["phase1_feature_set_name"] = "win_baseline_core_relative"
    out["calibrated_flag"] = False
    out["fixed_first_place_boat_no"] = pd.to_numeric(out["fixed_first_place_boat_no"], errors="coerce")
    out["fixed_first_place_rank_within_race"] = pd.to_numeric(
        out["fixed_first_place_rank_within_race"], errors="coerce"
    ).round().astype("Int64")
    out["candidate_boat_no"] = pd.to_numeric(out["candidate_boat_no"], errors="coerce")
    out["candidate_rank_within_race_before_fix"] = pd.to_numeric(
        out["candidate_rank_within_race_before_fix"], errors="coerce"
    ).round().astype("Int64")
    out["candidate_win_rank_within_race"] = pd.to_numeric(
        out["candidate_win_rank_within_race"], errors="coerce"
    ).round().astype("Int64")
    out["remaining_field_size"] = pd.to_numeric(out["remaining_field_size"], errors="coerce")
    out["target_place2"] = pd.to_numeric(out["target_place2"], errors="coerce").astype(int)
    out["candidate_rank_within_remaining_field"] = out.groupby("place2_context_id")["candidate_win_proba_norm"].rank(
        method="first", ascending=False
    )
    out["candidate_rank_within_remaining_field"] = out["candidate_rank_within_remaining_field"].astype(int)

    summary = Place2ContextBuildSummary(
        input_race_count=int(frame["race_id"].nunique()),
        output_context_count=int(out["place2_context_id"].nunique()),
        dropped_race_count=int(len(set(dropped_race_ids))),
        dropped_race_ids=sorted(dict.fromkeys(dropped_race_ids)),
    )
    return out, summary
