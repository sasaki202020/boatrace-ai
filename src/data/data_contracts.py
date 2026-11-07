from __future__ import annotations

"""Data contracts for win-model training frames.

This module defines the minimal canonical column set used by the win-model
training pipeline and helper utilities to resolve common source aliases.
"""

from dataclasses import dataclass
from typing import Iterable

import pandas as pd


CANONICAL_TRAIN_COLUMNS = [
    "race_id",
    "date",
    "venue",
    "race_number",
    "boat_no",
    "finish_position",
    "national_2ren_rate",
    "local_2ren_rate",
    "avg_st",
]

TRAIN_COLUMN_ALIASES: dict[str, list[str]] = {
    "race_number": ["race_no"],
    "boat_no": ["lane"],
    "avg_st": ["st", "start_display_st"],
}

TRAIN_NUMERIC_COLUMNS = [
    "race_number",
    "boat_no",
    "finish_position",
    "national_2ren_rate",
    "local_2ren_rate",
    "avg_st",
]

TRAIN_DATE_COLUMNS = ["date"]


@dataclass(frozen=True)
class ResolvedTrainingColumns:
    """Result of resolving canonical training columns."""

    frame: pd.DataFrame
    aliases_used: dict[str, str]
    missing_columns: list[str]


def resolve_training_columns(df: pd.DataFrame) -> ResolvedTrainingColumns:
    """Return a copy of *df* with common aliases renamed to canonical names."""

    out = df.copy()
    aliases_used: dict[str, str] = {}
    missing_columns: list[str] = []

    for canonical, aliases in TRAIN_COLUMN_ALIASES.items():
        if canonical in out.columns:
            continue
        for alias in aliases:
            if alias in out.columns:
                out = out.rename(columns={alias: canonical})
                aliases_used[canonical] = alias
                break

    for canonical in CANONICAL_TRAIN_COLUMNS:
        if canonical not in out.columns:
            missing_columns.append(canonical)

    return ResolvedTrainingColumns(frame=out, aliases_used=aliases_used, missing_columns=missing_columns)


def ensure_numeric_columns(df: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    """Convert listed columns to numeric where present, leaving others untouched."""

    out = df.copy()
    for col in columns:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    return out

