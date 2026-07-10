from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd


logger = logging.getLogger(__name__)

RELATIVE_FEATURE_COLUMNS = [
    "national_2ren_rate_rank_in_race",
    "national_2ren_rate_diff_from_race_mean",
    "national_2ren_rate_z_in_race",
    "local_2ren_rate_rank_in_race",
    "local_2ren_rate_diff_from_race_mean",
    "local_2ren_rate_z_in_race",
    "avg_st_rank_in_race",
    "avg_st_advantage_vs_mean",
    "avg_st_advantage_z_in_race",
]


@dataclass(frozen=True)
class RelativeFeatureSummary:
    """Relative feature generation summary."""

    added_columns: list[str]
    warning_messages: list[str]


def _resolve_boat_column(df: pd.DataFrame) -> str:
    for candidate in ("boat_no", "lane", "boat", "boat_number"):
        if candidate in df.columns:
            return candidate
    raise ValueError("df must contain one of: boat_no, lane, boat, boat_number")


def _numeric_series(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype="float64")
    return pd.to_numeric(df[column], errors="coerce")


def _safe_std(series: pd.Series) -> pd.Series:
    std = series.transform(lambda s: s.std(ddof=0))
    return pd.to_numeric(std, errors="coerce")


def _safe_rank(series: pd.Series, *, ascending: bool) -> pd.Series:
    return series.rank(method="min", ascending=ascending)


def _safe_z_from_centered(centered_value: pd.Series, std: pd.Series) -> pd.Series:
    z = pd.Series(np.nan, index=centered_value.index, dtype="float64")
    valid_value = centered_value.notna()
    valid_scale = std.notna() & (std != 0)
    usable = valid_value & valid_scale
    z.loc[usable] = centered_value.loc[usable] / std.loc[usable]
    z.loc[valid_value & ~valid_scale] = 0.0
    return z


def _maybe_warn_race_sizes(df: pd.DataFrame, race_key: str) -> list[str]:
    warning_messages: list[str] = []
    if race_key not in df.columns:
        return warning_messages

    race_sizes = df.groupby(race_key, dropna=False).size()
    abnormal = race_sizes[race_sizes != 6]
    if abnormal.empty:
        return warning_messages

    sample_keys = [str(idx) for idx in abnormal.index[:5].tolist()]
    msg = (
        f"relative feature build: {len(abnormal)} races have size != 6 "
        f"(sample={sample_keys})"
    )
    logger.warning(msg)
    warning_messages.append(msg)
    return warning_messages


def _add_metric_features(
    df: pd.DataFrame,
    *,
    race_key: str,
    metric_col: str,
    rank_col: str,
    diff_col: str,
    z_col: str,
    higher_is_better: bool,
    advantage: bool = False,
) -> list[str]:
    added: list[str] = []
    if race_key not in df.columns or metric_col not in df.columns:
        df[rank_col] = np.nan
        df[diff_col] = np.nan
        df[z_col] = np.nan
        return added

    metric = _numeric_series(df, metric_col)
    work = pd.DataFrame({race_key: df[race_key], metric_col: metric})
    grouped_metric = work.groupby(race_key, dropna=False)[metric_col]
    race_mean = grouped_metric.transform("mean")
    race_std = _safe_std(grouped_metric)

    if advantage:
        diff = race_mean - metric
    else:
        diff = metric - race_mean

    rank = metric.groupby(df[race_key], dropna=False).transform(
        lambda s: _safe_rank(s, ascending=not higher_is_better)
    )
    if advantage:
        z = _safe_z_from_centered(diff, race_std)
    else:
        z = _safe_z_from_centered(metric - race_mean, race_std)

    df[rank_col] = rank
    df[diff_col] = diff
    df[z_col] = z
    added.extend([rank_col, diff_col, z_col])
    return added


def add_race_relative_features(df: pd.DataFrame, race_key: str = "race_id") -> pd.DataFrame:
    """
    race_id 単位の相対特徴を追加する。

    Parameters
    ----------
    df:
        1レース×1艇の縦持ち DataFrame。
    race_key:
        レースを識別するキー列名。

    Returns
    -------
    pd.DataFrame
        相対特徴を追加したコピー。
    """
    if race_key not in df.columns:
        raise ValueError(f"df must contain race key column: {race_key}")

    out = df.copy()
    warning_messages = _maybe_warn_race_sizes(out, race_key)
    boat_col = _resolve_boat_column(out)
    boat_numeric = _numeric_series(out, boat_col)
    out[boat_col] = boat_numeric

    added_columns: list[str] = []

    added_columns.extend(
        _add_metric_features(
            out,
            race_key=race_key,
            metric_col="national_2ren_rate",
            rank_col="national_2ren_rate_rank_in_race",
            diff_col="national_2ren_rate_diff_from_race_mean",
            z_col="national_2ren_rate_z_in_race",
            higher_is_better=True,
        )
    )
    added_columns.extend(
        _add_metric_features(
            out,
            race_key=race_key,
            metric_col="local_2ren_rate",
            rank_col="local_2ren_rate_rank_in_race",
            diff_col="local_2ren_rate_diff_from_race_mean",
            z_col="local_2ren_rate_z_in_race",
            higher_is_better=True,
        )
    )
    added_columns.extend(
        _add_metric_features(
            out,
            race_key=race_key,
            metric_col="avg_st",
            rank_col="avg_st_rank_in_race",
            diff_col="avg_st_advantage_vs_mean",
            z_col="avg_st_advantage_z_in_race",
            higher_is_better=False,
            advantage=True,
        )
    )

    out.attrs["relative_feature_columns"] = added_columns
    out.attrs["relative_feature_warnings"] = warning_messages
    out.attrs["relative_feature_boat_column"] = boat_col
    return out


def list_relative_feature_columns() -> list[str]:
    """Return the relative feature column names added by this module."""
    return list(RELATIVE_FEATURE_COLUMNS)
