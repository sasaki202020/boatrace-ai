from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import math
import os
import random
from dataclasses import dataclass
from itertools import product
from pathlib import Path

import pandas as pd

from src.eval.evaluate_experiments import apply_window as apply_date_window
from src.eval.evaluate_experiments import calc_summary_metrics, load_inputs
from src.strategy.evaluate_ev_and_skip import StrategyEvaluator
from src.strategy.generate_trifecta_candidates import TrifectaGenerator


ROOT = Path(__file__).resolve().parents[2]
WIN_PROBA_PATH = ROOT / "data" / "model_outputs" / "train_win_proba.csv"
RESULTS_PATH = ROOT / "data" / "processed" / "historical_races.csv"
ODDS_ROOT = ROOT / "data" / "odds"
OUT_DIR = ROOT / "reports" / "param_search"
TMP_DIR = OUT_DIR / "_tmp"


RISK_PRESETS: dict[str, dict[str, int]] = {
    "mild": {
        "NO_REAL_ODDS": 1,
        "LOW_CONFIDENCE": 1,
        "HIGH_ODDS_VOLATILE": 1,
        "DATA_MISSING": 1,
        "LOW_SAMPLE_MODEL": 1,
        "STALE_ODDS": 1,
    },
    "standard": {
        "NO_REAL_ODDS": 2,
        "LOW_CONFIDENCE": 2,
        "HIGH_ODDS_VOLATILE": 1,
        "DATA_MISSING": 3,
        "LOW_SAMPLE_MODEL": 2,
        "STALE_ODDS": 2,
    },
    "medium": {
        "NO_REAL_ODDS": 2,
        "LOW_CONFIDENCE": 2,
        "HIGH_ODDS_VOLATILE": 1,
        "DATA_MISSING": 3,
        "LOW_SAMPLE_MODEL": 2,
        "STALE_ODDS": 2,
    },
    "strict": {
        "NO_REAL_ODDS": 3,
        "LOW_CONFIDENCE": 3,
        "HIGH_ODDS_VOLATILE": 2,
        "DATA_MISSING": 4,
        "LOW_SAMPLE_MODEL": 3,
        "STALE_ODDS": 3,
    },
}


@dataclass(frozen=True)
class ConfigSpec:
    candidate_generation_mode: str
    score_mode: str
    first_prob_relative_threshold: float
    min_win_proba: float
    min_ev: float
    high_ev_watch_threshold: float
    rescue_enabled: bool
    risk_preset: str

    def as_dict(self) -> dict[str, object]:
        return {
            "candidate_generation_mode": self.candidate_generation_mode,
            "score_mode": self.score_mode,
            "first_prob_relative_threshold": self.first_prob_relative_threshold,
            "min_win_proba": self.min_win_proba,
            "min_ev": self.min_ev,
            "high_ev_watch_threshold": self.high_ev_watch_threshold,
            "rescue_enabled": self.rescue_enabled,
            "risk_preset": self.risk_preset,
        }


def _read_csv_safe(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except Exception:
        return pd.DataFrame()


def _truthy(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    return series.astype(str).str.strip().str.lower().isin({"true", "1", "yes", "y"})


def _available_odds_file(date_str: str) -> Path | None:
    day_dir = ODDS_ROOT / date_str
    if not day_dir.exists():
        return None
    for name in ["trifecta_odds.csv", "all_trifecta_odds.csv", "today_trifecta_odds.csv"]:
        candidate = day_dir / name
        if candidate.exists() and candidate.stat().st_size > 0:
            try:
                df = pd.read_csv(candidate, nrows=1, low_memory=False)
                if not df.empty:
                    return candidate
            except Exception:
                continue
    return None


def build_combined_odds(selected_dates: list[str], out_path: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    seen: set[str] = set()
    for date_str in selected_dates:
        odds_file = _available_odds_file(date_str)
        if odds_file is None:
            continue
        try:
            df = pd.read_csv(odds_file, low_memory=False)
        except Exception:
            continue
        if df.empty:
            continue
        df = df.copy()
        if "combo" in df.columns and "trifecta" not in df.columns:
            df["trifecta"] = df["combo"]
        if "trifecta" not in df.columns:
            continue
        if "odds" not in df.columns:
            continue
        keep_cols = ["race_id", "trifecta", "odds"]
        for col in [
            "odds_source",
            "fetched_at",
            "source_url",
            "odds_fetch_status",
            "odds_fetch_used_cache",
            "odds_missing_odds_cells",
            "odds_target_source",
            "odds_status",
            "odds_last_fetched_at",
            "odds_provider",
            "odds_raw_status",
            "odds_fetch_status_normalized",
            "odds_fetch_status_reason",
            "odds_fetch_failed_reason",
        ]:
            if col in df.columns:
                keep_cols.append(col)
        df = df[keep_cols].copy()
        df["race_id"] = df["race_id"].astype(str).str.strip()
        df["trifecta"] = df["trifecta"].astype(str).str.strip()
        key = df["race_id"].astype(str) + "|" + df["trifecta"].astype(str)
        df = df.loc[~key.isin(seen)].copy()
        seen.update(key.astype(str).tolist())
        frames.append(df)

    if not frames:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("", encoding="utf-8")
        return pd.DataFrame(columns=["race_id", "trifecta", "odds"])

    combined = pd.concat(frames, ignore_index=True, sort=False)
    combined = combined.drop_duplicates(subset=["race_id", "trifecta"], keep="last").reset_index(drop=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(out_path, index=False)
    return combined


def _normalize_series(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    valid = s.dropna()
    if valid.empty:
        return pd.Series([0.5] * len(s), index=s.index, dtype=float)
    lo = float(valid.min())
    hi = float(valid.max())
    if math.isclose(lo, hi):
        return pd.Series([0.5] * len(s), index=s.index, dtype=float)
    return ((s - lo) / (hi - lo)).fillna(0.0).clip(0.0, 1.0)


def _stability_scale(series: pd.Series) -> pd.Series:
    if not isinstance(series, pd.Series):
        series = pd.Series([series])
    s = pd.to_numeric(series, errors="coerce").fillna(0.0)
    return 1.0 / (1.0 + s)


def _coerce_numeric_series(frame: pd.DataFrame, column: str, default: float = 0.0) -> pd.Series:
    if column in frame.columns:
        return pd.to_numeric(frame[column], errors="coerce").fillna(default)
    return pd.Series([default] * len(frame), index=frame.index, dtype=float)


def _series_or_default(frame: pd.DataFrame, column: str, default: object) -> pd.Series:
    if column in frame.columns:
        return frame[column]
    return pd.Series([default] * len(frame), index=frame.index)


def _safe_int(value: object, default: int = 0) -> int:
    try:
        numeric = pd.to_numeric(value, errors="coerce")
        if pd.isna(numeric):
            return default
        return int(numeric)
    except Exception:
        return default


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        numeric = pd.to_numeric(value, errors="coerce")
        if pd.isna(numeric):
            return default
        return float(numeric)
    except Exception:
        return default


def _daily_metrics(merged: pd.DataFrame) -> dict[str, float | int | None]:
    if merged.empty:
        return {
            "day_count": 0,
            "profitable_day_count": 0,
            "profitable_day_ratio": None,
            "max_drawdown_like_proxy": None,
            "buy_count_std_by_day": None,
        }
    work = merged.copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce")
    work = work.dropna(subset=["date"])
    if work.empty:
        return {
            "day_count": 0,
            "profitable_day_count": 0,
            "profitable_day_ratio": None,
            "max_drawdown_like_proxy": None,
            "buy_count_std_by_day": None,
        }
    work["is_buy"] = _truthy(work["is_buy"])
    if "hit" in work.columns:
        work["hit"] = _truthy(work["hit"])
    elif "exact_hit" in work.columns:
        work["hit"] = _truthy(work["exact_hit"])
    elif "trifecta_match" in work.columns:
        work["hit"] = _truthy(work["trifecta_match"])
    else:
        work["hit"] = False
    work["settled_odds"] = _coerce_numeric_series(work, "settled_odds", 0.0)
    daily = work.groupby(work["date"].dt.normalize()).agg(
        buy_count=("is_buy", "sum"),
        hit_count=("hit", "sum"),
        returned_amount=("settled_odds", lambda s: float(s[work.loc[s.index, "is_buy"] & work.loc[s.index, "hit"]].sum())),
    )
    daily["invested_amount"] = daily["buy_count"].astype(float)
    daily["roi"] = daily.apply(
        lambda r: float(r["returned_amount"]) / float(r["invested_amount"]) if float(r["invested_amount"]) > 0 else 0.0,
        axis=1,
    )
    daily["pnl"] = daily["returned_amount"] - daily["invested_amount"]
    day_count = int(len(daily))
    profitable_day_count = int((daily["roi"] > 1.0).sum())
    buy_count_std = float(daily["buy_count"].std(ddof=0)) if day_count > 1 else 0.0
    equity = daily["pnl"].cumsum()
    drawdown = equity - equity.cummax()
    max_drawdown_like_proxy = float(abs(drawdown.min())) if not drawdown.empty else 0.0
    return {
        "day_count": day_count,
        "profitable_day_count": profitable_day_count,
        "profitable_day_ratio": float(profitable_day_count / day_count) if day_count > 0 else None,
        "max_drawdown_like_proxy": max_drawdown_like_proxy,
        "buy_count_std_by_day": buy_count_std,
    }


def _metric_bundle(merged: pd.DataFrame) -> dict[str, object]:
    summary, _race_level = calc_summary_metrics(merged)
    daily = _daily_metrics(merged)
    work = merged.copy()
    work["decision"] = work["decision"].astype(str).str.upper()
    work["is_buy"] = _truthy(work["is_buy"]) if "is_buy" in work.columns else work["decision"].eq("BUY")
    if "hit" in work.columns:
        work["hit"] = _truthy(work["hit"])
    elif "exact_hit" in work.columns:
        work["hit"] = _truthy(work["exact_hit"])
    elif "trifecta_match" in work.columns:
        work["hit"] = _truthy(work["trifecta_match"])
    else:
        work["hit"] = False
    work["ev"] = _coerce_numeric_series(work, "ev", 0.0)
    work["adjusted_score"] = _coerce_numeric_series(work, "adjusted_score", 0.0)
    work["risk_penalty"] = _coerce_numeric_series(work, "risk_penalty", 0.0)
    work["high_ev_suspect_flag"] = _truthy(work["high_ev_suspect_flag"]) if "high_ev_suspect_flag" in work.columns else False
    work["rescue_applied"] = _truthy(work["rescue_applied"]) if "rescue_applied" in work.columns else False
    total_stake = int(work["is_buy"].sum())
    hit_count = int((work["is_buy"] & work["hit"]).sum())
    returned_amount = float(work.loc[work["is_buy"] & work["hit"], "settled_odds"].fillna(0.0).sum()) if "settled_odds" in work.columns else 0.0
    roi = float(summary.get("roi")) if summary.get("roi") is not None else None
    hit_rate = float(summary.get("exact_hit_rate")) if summary.get("exact_hit_rate") is not None else None
    avg_ev = float(work["ev"].mean()) if len(work) else None
    avg_adjusted_score = float(work["adjusted_score"].mean()) if len(work) else None
    suspect_high_ev_count = int(work["high_ev_suspect_flag"].sum()) if "high_ev_suspect_flag" in work.columns else 0
    rescue_applied_count = int(work["rescue_applied"].sum()) if "rescue_applied" in work.columns else 0
    races_covered = int(work.loc[work["decision"].isin(["BUY", "WATCH", "PENDING"]), "race_id"].nunique())
    low_prob_count = int(work.get("low_prob_flag", pd.Series(False, index=work.index)).astype(bool).sum()) if "low_prob_flag" in work.columns else 0
    low_odds_count = int(work.get("low_odds_flag", pd.Series(False, index=work.index)).astype(bool).sum()) if "low_odds_flag" in work.columns else 0
    missing_odds_count = int(work.get("missing_odds_flag", pd.Series(False, index=work.index)).astype(bool).sum()) if "missing_odds_flag" in work.columns else 0
    risk_penalty_count = int(work.get("risk_penalty_flag", pd.Series(False, index=work.index)).astype(bool).sum()) if "risk_penalty_flag" in work.columns else 0
    avg_reasons_per_buy = None
    if "decision_reasons" in work.columns and total_stake > 0:
        avg_reasons_per_buy = float(
            work.loc[work["is_buy"], "decision_reasons"].astype(str).map(lambda s: len([x for x in s.split(" / ") if x.strip()])).mean()
        )
    return {
        "total_candidates": int(len(work)),
        "buy_count": total_stake,
        "watch_count": int((work["decision"] == "WATCH").sum()),
        "skip_count": int((work["decision"] == "SKIP").sum()),
        "pending_count": int((work["decision"] == "PENDING").sum()),
        "hit_count": hit_count,
        "invested_amount": float(total_stake),
        "returned_amount": returned_amount,
        "roi": roi,
        "hit_rate": hit_rate,
        "avg_ev": avg_ev,
        "avg_adjusted_score": avg_adjusted_score,
        "suspect_high_ev_count": suspect_high_ev_count,
        "rescue_applied_count": rescue_applied_count,
        "races_covered": races_covered,
        "low_prob_count": low_prob_count,
        "low_odds_count": low_odds_count,
        "missing_odds_count": missing_odds_count,
        "risk_penalty_count": risk_penalty_count,
        "avg_reasons_per_buy": avg_reasons_per_buy,
        **daily,
    }


def _build_stage1_grid(current_buy_min_prob: float, current_buy_min_ev: float, current_high_ev_threshold: float) -> list[ConfigSpec]:
    candidate_modes = ["legacy", "expanded"]
    score_modes = ["approx", "unified_score"]
    first_prob_thresholds = [0.55, 0.65, 0.75, 0.85]
    min_win_probs = [0.01, 0.03, 0.05, 0.07, max(0.01, round(current_buy_min_prob, 3))]
    min_win_probs = sorted({round(v, 3) for v in min_win_probs})
    min_evs = [0.35, 0.45, 0.55, 0.65, max(0.35, round(current_buy_min_ev, 3))]
    min_evs = sorted({round(v, 3) for v in min_evs})
    high_ev_thresholds = [2.0, 2.5, 3.0]
    rescue_enabled_values = [True, False]
    risk_presets = ["mild", "medium"]

    grid: list[ConfigSpec] = []
    for candidate_mode, score_mode, fthr, min_prob, min_ev, hev, rescue_enabled, risk_preset in product(
        candidate_modes,
        score_modes,
        first_prob_thresholds,
        min_win_probs,
        min_evs,
        high_ev_thresholds,
        rescue_enabled_values,
        risk_presets,
    ):
        grid.append(
            ConfigSpec(
                candidate_generation_mode=candidate_mode,
                score_mode=score_mode,
                first_prob_relative_threshold=float(fthr),
                min_win_proba=float(min_prob),
                min_ev=float(min_ev),
                high_ev_watch_threshold=float(hev),
                rescue_enabled=bool(rescue_enabled),
                risk_preset=risk_preset,
            )
        )
    return grid


def _build_stage2_grid(stage1_ranked: pd.DataFrame) -> list[ConfigSpec]:
    if stage1_ranked.empty:
        return _build_stage1_grid(0.02, 0.4, 2.5)
    work = stage1_ranked.copy()
    for col in ["tuning_buy_count", "holdout_buy_count", "ranking_score"]:
        if col in work.columns:
            work[col] = pd.to_numeric(work[col], errors="coerce")
    signal_rows = work[
        (pd.to_numeric(_series_or_default(work, "tuning_buy_count", 0), errors="coerce").fillna(0) > 0)
        | (pd.to_numeric(_series_or_default(work, "holdout_buy_count", 0), errors="coerce").fillna(0) > 0)
    ].copy()
    if signal_rows.empty:
        signal_rows = work.head(8).copy()
    seed_rows = signal_rows.head(8).copy()

    candidate_modes = sorted(set(seed_rows.get("candidate_generation_mode", pd.Series(["legacy"])).astype(str).tolist()) or ["legacy"])
    score_modes = sorted(set(seed_rows.get("score_mode", pd.Series(["approx"])).astype(str).tolist()) or ["approx"])
    rescue_values = sorted({bool(v) for v in seed_rows.get("rescue_enabled", pd.Series([True])).tolist()}) or [True]
    risk_presets = sorted({str(v) for v in seed_rows.get("risk_preset", pd.Series(["mild"])).tolist()}) or ["mild"]
    risk_presets = [rp for rp in risk_presets if rp in RISK_PRESETS] or ["mild", "medium"]

    def _collect(col: str, fallback: list[float], lo: float, hi: float) -> list[float]:
        values = set(fallback)
        if col in seed_rows.columns:
            for raw in pd.to_numeric(seed_rows[col], errors="coerce").dropna().tolist():
                for delta in (-0.05, -0.02, 0.0, 0.02, 0.05):
                    values.add(round(min(hi, max(lo, float(raw) + delta)), 3))
        return sorted(values)

    first_prob_thresholds = _collect("first_prob_relative_threshold", [0.55, 0.60, 0.65, 0.70], 0.50, 0.90)
    min_win_probs = _collect("min_win_proba", [0.01, 0.02, 0.03, 0.04, 0.05], 0.005, 0.12)
    min_evs = _collect("min_ev", [0.35, 0.40, 0.45, 0.50, 0.60], 0.20, 1.50)
    high_ev_thresholds = _collect("high_ev_watch_threshold", [2.0, 2.5, 3.0], 1.5, 5.0)

    grid: list[ConfigSpec] = []
    for candidate_mode, score_mode, fthr, min_prob, min_ev, hev, rescue_enabled, risk_preset in product(
        candidate_modes,
        score_modes,
        first_prob_thresholds,
        min_win_probs,
        min_evs,
        high_ev_thresholds,
        rescue_values,
        risk_presets,
    ):
        grid.append(
            ConfigSpec(
                candidate_generation_mode=candidate_mode,
                score_mode=score_mode,
                first_prob_relative_threshold=float(fthr),
                min_win_proba=float(min_prob),
                min_ev=float(min_ev),
                high_ev_watch_threshold=float(hev),
                rescue_enabled=bool(rescue_enabled),
                risk_preset=str(risk_preset),
            )
        )
    return grid


def _pick_configs(grid: list[ConfigSpec], max_configs: int, seed: int) -> list[ConfigSpec]:
    if len(grid) <= max_configs:
        return grid
    rng = random.Random(seed)
    selected: list[ConfigSpec] = []
    covered: set[tuple[str, str]] = set()
    for spec in grid:
        key = (spec.candidate_generation_mode, spec.score_mode)
        if key in covered:
            continue
        selected.append(spec)
        covered.add(key)
        if len(selected) >= max_configs:
            return selected[:max_configs]
    if len(selected) < max_configs:
        rest = [spec for spec in grid if spec not in selected]
        sample = rng.sample(rest, k=min(len(rest), max_configs - len(selected)))
        selected.extend(sample)
    return selected[:max_configs]


def _split_dates(available_dates: list[pd.Timestamp], tuning_days: int, holdout_days: int) -> tuple[list[str], list[str]]:
    ordered = sorted({d.normalize() for d in available_dates if pd.notna(d)})
    if not ordered:
        return [], []
    total_needed = tuning_days + holdout_days
    if len(ordered) < total_needed:
        holdout_days = max(1, min(holdout_days, len(ordered) // 3 or 1))
        tuning_days = max(1, len(ordered) - holdout_days)
    selected = ordered[-(tuning_days + holdout_days) :]
    tuning = selected[:tuning_days]
    holdout = selected[tuning_days:]
    return [d.strftime("%Y-%m-%d") for d in tuning], [d.strftime("%Y-%m-%d") for d in holdout]


def _format_window(dates: list[str]) -> str:
    ordered = sorted(str(d) for d in dates if d is not None)
    if not ordered:
        return "empty"
    return f"{ordered[0]}..{ordered[-1]}"


def _json_default(value: object) -> object:
    if isinstance(value, (pd.Timestamp, pd.Timedelta)):
        return value.isoformat()
    if isinstance(value, (pd.Series, pd.Index)):
        return value.tolist()
    if isinstance(value, set):
        return sorted(value)
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _stable_hash(payload: dict[str, object]) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=_json_default)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:20]


def _config_hash(stage: str, spec: ConfigSpec, run_context: dict[str, object]) -> str:
    payload = {
        "stage": stage,
        "spec": spec.as_dict(),
        "run_context": run_context,
    }
    return _stable_hash(payload)


def _cache_root(out_dir: Path) -> Path:
    return out_dir / "cache"


def _common_cache_paths(cache_root: Path, kind: str, cache_key: str) -> tuple[Path, Path]:
    base = cache_root / "common" / kind / cache_key
    return base.with_suffix(".csv"), base.with_suffix(".json")


def _stage_cache_paths(cache_root: Path, stage: str, cache_key: str) -> tuple[Path, Path]:
    base = cache_root / stage / cache_key
    return base.with_suffix(".csv"), base.with_suffix(".json")


def _load_cached_stage_result(csv_path: Path, json_path: Path) -> tuple[dict[str, object], pd.DataFrame] | None:
    if not csv_path.exists() or not json_path.exists():
        return None
    try:
        row = json.loads(json_path.read_text(encoding="utf-8"))
        frame = pd.read_csv(csv_path, low_memory=False) if csv_path.stat().st_size > 0 else pd.DataFrame()
        return row, frame
    except Exception:
        return None


def _save_cached_stage_result(csv_path: Path, json_path: Path, row: dict[str, object], frame: pd.DataFrame) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    if frame is None or frame.empty:
        csv_path.write_text("", encoding="utf-8")
    else:
        frame.to_csv(csv_path, index=False)
    json_path.write_text(json.dumps(row, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")


def _read_jsonl_keys(path: Path) -> set[str]:
    if not path.exists():
        return set()
    keys: set[str] = set()
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            data = json.loads(line)
            key = str(data.get("cache_key") or data.get("config_key") or "")
            if key:
                keys.add(key)
    except Exception:
        return set()
    return keys


def _append_jsonl(path: Path, row: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=_json_default) + "\n")


def _evaluate_config(
    spec: ConfigSpec,
    *,
    stage: str,
    config_path: Path,
    filtered_win_path: Path,
    results_path: Path,
    odds_path: Path,
    tuning_dates: set[str],
    holdout_dates: set[str],
    race_boat_counts: dict[str, int],
    cache_root: Path,
    run_context: dict[str, object],
    prune_policy: dict[str, object] | None = None,
    candidate_cache: dict[tuple[str, float], pd.DataFrame],
    ev_cache: dict[tuple[str, float, str, float], pd.DataFrame],
) -> tuple[dict[str, object], pd.DataFrame]:
    stage_cache_key = _config_hash(stage, spec, run_context)
    stage_csv, stage_json = _stage_cache_paths(cache_root, stage, stage_cache_key)
    cached = _load_cached_stage_result(stage_csv, stage_json)
    if cached is not None:
        cached_row, cached_skip = cached
        cached_row["cache_hit"] = True
        cached_row["stage"] = stage
        return cached_row, cached_skip

    candidate_key = (spec.candidate_generation_mode, float(spec.first_prob_relative_threshold))
    candidate_cache_key = _stable_hash({"candidate_key": candidate_key, "run_context": run_context})
    candidate_csv, candidate_json = _common_cache_paths(cache_root, "candidates", candidate_cache_key)
    if candidate_key in candidate_cache:
        candidates = candidate_cache[candidate_key].copy()
    elif candidate_csv.exists() and candidate_json.exists():
        candidates = pd.read_csv(candidate_csv, low_memory=False)
        candidate_cache[candidate_key] = candidates.copy()
    else:
        generator = TrifectaGenerator(config_path=str(config_path))
        generator.candidate_generation_mode = spec.candidate_generation_mode
        generator.first_prob_relative_threshold = spec.first_prob_relative_threshold
        candidates = generator.generate(str(filtered_win_path), ignore_race_candidate_limit=True)
        if candidates is None or candidates.empty:
            return (
                {
                    **spec.as_dict(),
                    "error": "no_candidates",
                },
                pd.DataFrame(),
            )
        candidate_cache[candidate_key] = candidates.copy()
        candidate_csv.parent.mkdir(parents=True, exist_ok=True)
        candidates.to_csv(candidate_csv, index=False)
        candidate_json.write_text(
            json.dumps({"candidate_key": candidate_key, "stage": stage}, ensure_ascii=False, indent=2, default=_json_default),
            encoding="utf-8",
        )

    evaluator = StrategyEvaluator(config_path=str(config_path))
    evaluator.use_unified_score = spec.score_mode == "unified_score"
    evaluator.rescue_enabled = spec.rescue_enabled
    evaluator.high_ev_watch_threshold = spec.high_ev_watch_threshold
    evaluator.buy_min_approx_prob = spec.min_win_proba
    evaluator.buy_min_ev = spec.min_ev
    evaluator.buy_config["min_win_proba"] = spec.min_win_proba
    evaluator.buy_config["min_ev"] = spec.min_ev
    evaluator.buy_config["high_ev_watch_threshold"] = spec.high_ev_watch_threshold
    evaluator.buy_config["rescue_enabled"] = spec.rescue_enabled
    evaluator.buy_config["use_unified_score"] = spec.score_mode == "unified_score"
    evaluator.buy_config["low_odds_threshold"] = 20.0
    evaluator.risk_penalties = dict(RISK_PRESETS[spec.risk_preset])

    candidates_path = TMP_DIR / f"{spec.candidate_generation_mode}_{spec.score_mode}_{spec.first_prob_relative_threshold}_{spec.min_win_proba}_{spec.min_ev}_{spec.high_ev_watch_threshold}_{int(spec.rescue_enabled)}_{spec.risk_preset}.csv"
    candidates_path.parent.mkdir(parents=True, exist_ok=True)
    candidates.to_csv(candidates_path, index=False)

    ev_key = (spec.candidate_generation_mode, float(spec.first_prob_relative_threshold), spec.score_mode, float(spec.high_ev_watch_threshold))
    ev_cache_key = _stable_hash({"ev_key": ev_key, "run_context": run_context})
    ev_csv, ev_json = _common_cache_paths(cache_root, "ev", ev_cache_key)
    if ev_key in ev_cache:
        ev_df = ev_cache[ev_key].copy()
    elif ev_csv.exists() and ev_json.exists():
        ev_df = pd.read_csv(ev_csv, low_memory=False)
        ev_cache[ev_key] = ev_df.copy()
    else:
        ev_df = evaluator.build_ev_analysis(str(candidates_path), odds_path=str(odds_path))
        ev_cache[ev_key] = ev_df.copy()
        ev_csv.parent.mkdir(parents=True, exist_ok=True)
        ev_df.to_csv(ev_csv, index=False)
        ev_json.write_text(
            json.dumps({"ev_key": ev_key, "stage": stage}, ensure_ascii=False, indent=2, default=_json_default),
            encoding="utf-8",
        )
    skip_df = evaluator.build_skip_decisions(
        ev_df,
        race_boat_counts=race_boat_counts,
        race_card_path=str(filtered_win_path),
        ignore_day_mode=True,
        ignore_daily_candidate_limit=True,
        ignore_race_candidate_limit=True,
        ignore_hard_guards=True,
        ignore_priority_gates=True,
    )

    predictions_path = TMP_DIR / f"{candidates_path.stem}_predictions.csv"
    skip_df.to_csv(predictions_path, index=False)
    merged = load_inputs(predictions_path, results_path).copy()
    merged["date"] = pd.to_datetime(merged["date"], errors="coerce")
    merged = merged.dropna(subset=["date"]).copy()
    merged["date_str"] = merged["date"].dt.strftime("%Y-%m-%d")

    tuning = merged[merged["date_str"].isin(tuning_dates)].copy()
    tuning_skip = skip_df[skip_df["date"].astype(str).isin(tuning_dates)].copy()
    prune_policy = prune_policy or {}
    if stage == "stage1" and bool(prune_policy.get("enabled", False)):
        tuning_buy_count = int((tuning_skip["decision"].astype(str).str.upper() == "BUY").sum()) if not tuning_skip.empty else 0
        tuning_watch_count = int((tuning_skip["decision"].astype(str).str.upper() == "WATCH").sum()) if not tuning_skip.empty else 0
        tuning_races_covered = int(tuning_skip.loc[tuning_skip["decision"].isin(["BUY", "WATCH", "PENDING"]), "race_id"].nunique()) if not tuning_skip.empty else 0
        if (
            tuning_buy_count == 0
            and tuning_races_covered <= int(prune_policy.get("min_races_covered", 2))
            and tuning_watch_count <= int(prune_policy.get("max_watch_count", 1))
        ):
            pruned_row: dict[str, object] = {
                **spec.as_dict(),
                "stage": stage,
                "cache_key": stage_cache_key,
                "cache_hit": False,
                "pruned": True,
                "prune_reason": "weak_tuning_pilot",
                "ignore_day_mode": True,
                "ignore_daily_candidate_limit": True,
                "ignore_race_candidate_limit": True,
                "combined_dates": len(sorted(set(merged["date_str"]))),
                "tuning_period": _format_window(tuning_dates),
                "holdout_period": _format_window(holdout_dates),
                "tuning_dates": json.dumps(sorted(tuning_dates), ensure_ascii=False),
                "holdout_dates": json.dumps(sorted(holdout_dates), ensure_ascii=False),
                "candidate_rows": int(len(candidates)),
                "selected_rows": int(len(skip_df)),
                "tuning_total_candidates": int(len(tuning_skip)),
                "tuning_buy_count": tuning_buy_count,
                "tuning_watch_count": tuning_watch_count,
                "tuning_skip_count": int((tuning_skip["decision"].astype(str).str.upper() == "SKIP").sum()) if not tuning_skip.empty else 0,
                "tuning_pending_count": int((tuning_skip["decision"].astype(str).str.upper() == "PENDING").sum()) if not tuning_skip.empty else 0,
                "tuning_hit_count": 0,
                "tuning_invested_amount": float(tuning_buy_count),
                "tuning_returned_amount": 0.0,
                "tuning_roi": None,
                "tuning_hit_rate": None,
                "tuning_avg_ev": float(tuning_skip["ev"].mean()) if "ev" in tuning_skip.columns and not tuning_skip.empty else None,
                "tuning_avg_adjusted_score": float(tuning_skip["adjusted_score"].mean()) if "adjusted_score" in tuning_skip.columns and not tuning_skip.empty else None,
                "tuning_suspect_high_ev_count": int(tuning_skip.get("high_ev_suspect_flag", pd.Series(False, index=tuning_skip.index)).astype(bool).sum()) if not tuning_skip.empty else 0,
                "tuning_rescue_applied_count": int(tuning_skip.get("rescue_applied", pd.Series(False, index=tuning_skip.index)).astype(bool).sum()) if not tuning_skip.empty else 0,
                "tuning_races_covered": tuning_races_covered,
                "tuning_low_prob_count": int(tuning_skip.get("low_prob_flag", pd.Series(False, index=tuning_skip.index)).astype(bool).sum()) if not tuning_skip.empty else 0,
                "tuning_low_odds_count": int(tuning_skip.get("low_odds_flag", pd.Series(False, index=tuning_skip.index)).astype(bool).sum()) if not tuning_skip.empty else 0,
                "tuning_missing_odds_count": int(tuning_skip.get("missing_odds_flag", pd.Series(False, index=tuning_skip.index)).astype(bool).sum()) if not tuning_skip.empty else 0,
                "tuning_risk_penalty_count": int(tuning_skip.get("risk_penalty_flag", pd.Series(False, index=tuning_skip.index)).astype(bool).sum()) if not tuning_skip.empty else 0,
                "tuning_avg_reasons_per_buy": None,
                "tuning_day_count": 1,
                "tuning_profitable_day_count": 0,
                "tuning_profitable_day_ratio": 0.0,
                "tuning_max_drawdown_like_proxy": 0.0,
                "tuning_buy_count_std_by_day": 0.0,
            }
            pruned_row["holdout_total_candidates"] = None
            pruned_row["holdout_buy_count"] = None
            pruned_row["holdout_watch_count"] = None
            pruned_row["holdout_skip_count"] = None
            pruned_row["holdout_pending_count"] = None
            pruned_row["holdout_hit_count"] = None
            pruned_row["holdout_invested_amount"] = None
            pruned_row["holdout_returned_amount"] = None
            pruned_row["holdout_roi"] = None
            pruned_row["holdout_hit_rate"] = None
            pruned_row["holdout_avg_ev"] = None
            pruned_row["holdout_avg_adjusted_score"] = None
            pruned_row["holdout_suspect_high_ev_count"] = None
            pruned_row["holdout_rescue_applied_count"] = None
            pruned_row["holdout_races_covered"] = None
            pruned_row["holdout_low_prob_count"] = None
            pruned_row["holdout_low_odds_count"] = None
            pruned_row["holdout_missing_odds_count"] = None
            pruned_row["holdout_risk_penalty_count"] = None
            pruned_row["holdout_avg_reasons_per_buy"] = None
            pruned_row["holdout_day_count"] = 0
            pruned_row["holdout_profitable_day_count"] = 0
            pruned_row["holdout_profitable_day_ratio"] = None
            pruned_row["holdout_max_drawdown_like_proxy"] = None
            pruned_row["holdout_buy_count_std_by_day"] = None
            _save_cached_stage_result(stage_csv, stage_json, pruned_row, skip_df)
            return pruned_row, skip_df

    holdout = merged[merged["date_str"].isin(holdout_dates)].copy()

    tuning_metrics = _metric_bundle(tuning)
    holdout_metrics = _metric_bundle(holdout)

    def with_prefix(prefix: str, metrics: dict[str, object]) -> dict[str, object]:
        return {f"{prefix}{k}": v for k, v in metrics.items()}

    row: dict[str, object] = {
        **spec.as_dict(),
        "ignore_day_mode": True,
        "ignore_daily_candidate_limit": True,
        "ignore_race_candidate_limit": True,
        "combined_dates": len(sorted(set(merged["date_str"]))),
        "tuning_period": _format_window(tuning_dates),
        "holdout_period": _format_window(holdout_dates),
        "tuning_dates": json.dumps(sorted(tuning_dates), ensure_ascii=False),
        "holdout_dates": json.dumps(sorted(holdout_dates), ensure_ascii=False),
        "candidate_rows": int(len(candidates)),
        "selected_rows": int(len(skip_df)),
    }
    row.update(with_prefix("tuning_", tuning_metrics))
    row.update(with_prefix("holdout_", holdout_metrics))
    row["stage"] = stage
    row["cache_key"] = stage_cache_key
    row["cache_hit"] = False
    row["pruned"] = False
    row["prune_reason"] = ""

    _save_cached_stage_result(stage_csv, stage_json, row, skip_df)
    return row, skip_df


def _rank_configs(df: pd.DataFrame, minimum_required_buys: int) -> pd.DataFrame:
    work = df.copy()
    default_zero_cols = [
        "tuning_buy_count",
        "tuning_roi",
        "tuning_profitable_day_ratio",
        "tuning_hit_rate",
        "tuning_races_covered",
        "tuning_buy_count_std_by_day",
        "holdout_buy_count",
        "holdout_roi",
        "holdout_profitable_day_ratio",
        "holdout_hit_rate",
        "holdout_races_covered",
        "holdout_buy_count_std_by_day",
    ]
    for col in default_zero_cols:
        if col not in work.columns:
            work[col] = 0.0
    work["tuning_buy_count_stability"] = _stability_scale(work["tuning_buy_count_std_by_day"] if "tuning_buy_count_std_by_day" in work.columns else 0.0).reindex(work.index, fill_value=0.5)
    work["holdout_buy_count_stability"] = _stability_scale(work["holdout_buy_count_std_by_day"] if "holdout_buy_count_std_by_day" in work.columns else 0.0).reindex(work.index, fill_value=0.5)
    work["norm_tuning_roi"] = _normalize_series(work["tuning_roi"])
    work["norm_tuning_profitable_day_ratio"] = _normalize_series(work["tuning_profitable_day_ratio"])
    work["norm_tuning_hit_rate"] = _normalize_series(work["tuning_hit_rate"])
    work["norm_tuning_races_covered"] = _normalize_series(work["tuning_races_covered"])
    work["norm_tuning_buy_count_stability"] = _normalize_series(work["tuning_buy_count_stability"])
    work["norm_holdout_roi"] = _normalize_series(work["holdout_roi"])
    work["norm_holdout_profitable_day_ratio"] = _normalize_series(work["holdout_profitable_day_ratio"])
    work["norm_holdout_hit_rate"] = _normalize_series(work["holdout_hit_rate"])
    work["norm_holdout_races_covered"] = _normalize_series(work["holdout_races_covered"])
    work["norm_holdout_buy_count_stability"] = _normalize_series(work["holdout_buy_count_stability"])

    work["tuning_ranking_score"] = (
        0.40 * work["norm_tuning_roi"]
        + 0.20 * work["norm_tuning_profitable_day_ratio"]
        + 0.15 * work["norm_tuning_hit_rate"]
        + 0.15 * work["norm_tuning_races_covered"]
        + 0.10 * work["norm_tuning_buy_count_stability"]
    )
    work["holdout_ranking_score"] = (
        0.40 * work["norm_holdout_roi"]
        + 0.20 * work["norm_holdout_profitable_day_ratio"]
        + 0.15 * work["norm_holdout_hit_rate"]
        + 0.15 * work["norm_holdout_races_covered"]
        + 0.10 * work["norm_holdout_buy_count_stability"]
    )
    work["ranking_score"] = 0.7 * work["tuning_ranking_score"] + 0.3 * work["holdout_ranking_score"]

    tuning_buy = pd.to_numeric(work["tuning_buy_count"], errors="coerce").fillna(0).astype(int)
    holdout_buy = pd.to_numeric(work["holdout_buy_count"], errors="coerce").fillna(0).astype(int)
    tuning_roi = pd.to_numeric(work["tuning_roi"], errors="coerce").fillna(-1.0)
    holdout_roi = pd.to_numeric(work["holdout_roi"], errors="coerce").fillna(-1.0)
    holdout_pass = pd.to_numeric(_series_or_default(work, "holdout_pass", False), errors="coerce").fillna(0).astype(bool)

    work.loc[tuning_buy < minimum_required_buys, "ranking_score"] -= 0.30
    work.loc[holdout_buy < minimum_required_buys, "ranking_score"] -= 0.45
    work.loc[holdout_buy == 0, "ranking_score"] -= 0.35
    work.loc[holdout_roi <= 0.0, "ranking_score"] -= 0.40
    work.loc[tuning_roi <= 0.0, "ranking_score"] -= 0.15
    work.loc[~holdout_pass, "ranking_score"] -= 0.55
    work.loc[tuning_buy == 0, "ranking_score"] -= 0.20
    work.loc[holdout_buy == 0, "holdout_ranking_score"] -= 0.20
    work.loc[tuning_buy == 0, "tuning_ranking_score"] -= 0.10

    work["holdout_pass"] = (
        holdout_pass
        & (holdout_roi >= 1.0)
        & (holdout_buy >= minimum_required_buys)
    )
    work = work.sort_values(["holdout_pass", "ranking_score", "tuning_roi"], ascending=[False, False, False]).reset_index(drop=True)
    return work


def _write_top_configs_md(df: pd.DataFrame, out_path: Path) -> None:
    lines: list[str] = [
        "# Top Configs",
        "",
        "## Ranking rule",
        "- 0.40 * tuning ROI",
        "- 0.20 * tuning profitable-day ratio",
        "- 0.15 * tuning hit rate",
        "- 0.15 * tuning races covered",
        "- 0.10 * tuning buy-count stability",
        "- Holdout must not collapse to be considered operationally usable",
        "",
    ]
    top = df.head(10).copy()
    for idx, row in top.iterrows():
        lines.append(f"## {idx + 1}. {row['candidate_generation_mode']} / {row['score_mode']} / rescue={bool(row['rescue_enabled'])}")
        lines.append(f"- ranking_score: {float(row['ranking_score']):.4f}")
        lines.append(f"- tuning_roi: {row['tuning_roi']}")
        lines.append(f"- holdout_roi: {row['holdout_roi']}")
        lines.append(f"- tuning_buy_count: {_safe_int(row.get('tuning_buy_count', 0))}")
        lines.append(f"- holdout_buy_count: {_safe_int(row.get('holdout_buy_count', 0))}")
        lines.append(f"- tuning_profitable_day_ratio: {row['tuning_profitable_day_ratio']}")
        lines.append(f"- holdout_profitable_day_ratio: {row['holdout_profitable_day_ratio']}")
        lines.append(f"- holdout_pass: {bool(row['holdout_pass'])}")
        strength = []
        weakness = []
        if pd.notna(row.get("holdout_roi")) and float(row["holdout_roi"]) >= 1.0:
            strength.append("holdout ROI is above break-even")
        if pd.notna(row.get("tuning_hit_rate")) and float(row["tuning_hit_rate"]) >= 0.03:
            strength.append("tuning hit rate is stable")
        if _safe_int(row.get("tuning_buy_count", 0)) >= 5:
            strength.append("enough tuning buys for signal")
        if _safe_int(row.get("tuning_buy_count", 0)) < 5:
            weakness.append("tuning buy count is too small")
        if pd.notna(row.get("holdout_roi")) and float(row["holdout_roi"]) < 1.0:
            weakness.append("holdout ROI is below break-even")
        if pd.notna(row.get("holdout_profitable_day_ratio")) and float(row["holdout_profitable_day_ratio"]) < 0.5:
            weakness.append("holdout profitable-day ratio is weak")
        lines.append(f"- strengths: {', '.join(strength) if strength else 'none'}")
        lines.append(f"- weaknesses: {', '.join(weakness) if weakness else 'none'}")
        lines.append(
            f"- operational_fit: {'candidate for real use' if bool(row['holdout_pass']) and _safe_int(row.get('tuning_buy_count', 0)) >= 5 else 'use only as a reference'}"
        )
        lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")


def _write_best_configs_md(stage1_ranked: pd.DataFrame | None, stage2_ranked: pd.DataFrame | None, out_path: Path) -> None:
    lines: list[str] = [
        "# Best Configs",
        "",
        "## Ranking rule",
        "- holdout pass is required",
        "- holdout buy_count=0 gets a strong penalty",
        "- holdout ROI<=0 gets a strong penalty",
        "- buy_count below minimum_required_buys gets a penalty",
        "",
    ]
    if stage1_ranked is not None and not stage1_ranked.empty:
        lines.extend(["## Stage1 Top 10", ""])
        for idx, row in stage1_ranked.head(10).reset_index(drop=True).iterrows():
            lines.append(f"### {idx + 1}. {row['candidate_generation_mode']} / {row['score_mode']} / rescue={bool(row['rescue_enabled'])}")
            lines.append(f"- ranking_score: {float(row['ranking_score']):.4f}")
            lines.append(f"- tuning_buy_count: {_safe_int(row.get('tuning_buy_count', 0))}")
            lines.append(f"- holdout_buy_count: {_safe_int(row.get('holdout_buy_count', 0))}")
            lines.append(f"- tuning_roi: {row.get('tuning_roi')}")
            lines.append(f"- holdout_roi: {row.get('holdout_roi')}")
            lines.append(f"- holdout_pass: {bool(row.get('holdout_pass', False))}")
            lines.append("")
    if stage2_ranked is not None and not stage2_ranked.empty:
        lines.extend(["## Stage2 Top 5", ""])
        for idx, row in stage2_ranked.head(5).reset_index(drop=True).iterrows():
            lines.append(f"### {idx + 1}. {row['candidate_generation_mode']} / {row['score_mode']} / rescue={bool(row['rescue_enabled'])}")
            lines.append(f"- ranking_score: {float(row['ranking_score']):.4f}")
            lines.append(f"- tuning_buy_count: {_safe_int(row.get('tuning_buy_count', 0))}")
            lines.append(f"- holdout_buy_count: {_safe_int(row.get('holdout_buy_count', 0))}")
            lines.append(f"- tuning_roi: {row.get('tuning_roi')}")
            lines.append(f"- holdout_roi: {row.get('holdout_roi')}")
            lines.append(f"- holdout_pass: {bool(row.get('holdout_pass', False))}")
            lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")


def _stage_config_key(stage: str, spec: ConfigSpec, run_context: dict[str, object]) -> str:
    return _config_hash(stage, spec, run_context)


def _evaluate_config_task(payload: dict[str, object]) -> dict[str, object]:
    spec = ConfigSpec(**payload["spec"])  # type: ignore[arg-type]
    row, _skip_df = _evaluate_config(
        spec,
        stage=str(payload["stage"]),
        config_path=Path(payload["config_path"]),
        filtered_win_path=Path(payload["filtered_win_path"]),
        results_path=Path(payload["results_path"]),
        odds_path=Path(payload["odds_path"]),
        tuning_dates=set(payload["tuning_dates"]),  # type: ignore[arg-type]
        holdout_dates=set(payload["holdout_dates"]),  # type: ignore[arg-type]
        race_boat_counts={str(k): int(v) for k, v in dict(payload["race_boat_counts"]).items()},  # type: ignore[arg-type]
        cache_root=Path(payload["cache_root"]),
        run_context=dict(payload["run_context"]),  # type: ignore[arg-type]
        prune_policy=dict(payload.get("prune_policy", {})),
        candidate_cache={},
        ev_cache={},
    )
    return row


def _load_run_state(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_run_state(path: Path, state: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")


def _run_stage(
    *,
    stage: str,
    specs: list[ConfigSpec],
    search_space_size: int,
    args: argparse.Namespace,
    run_context: dict[str, object],
    filtered_win_path: Path,
    results_path: Path,
    odds_path: Path,
    tuning_dates: list[str],
    holdout_dates: list[str],
    race_boat_counts: dict[str, int],
    cache_root: Path,
    completed_path: Path,
    run_state_path: Path,
    resume_keys: set[str],
    workers: int,
    prune_policy: dict[str, object] | None = None,
) -> tuple[pd.DataFrame, dict[ConfigSpec, pd.DataFrame]]:
    pending: list[ConfigSpec] = []
    skipped_from_resume = 0
    for spec in specs:
        key = _stage_config_key(stage, spec, run_context)
        if key in resume_keys:
            skipped_from_resume += 1
            continue
        pending.append(spec)

    rows: list[dict[str, object]] = []
    skip_frames: dict[ConfigSpec, pd.DataFrame] = {}
    payload_base = {
        "stage": stage,
        "config_path": args.config_path,
        "filtered_win_path": str(filtered_win_path),
        "results_path": str(results_path),
        "odds_path": str(odds_path),
        "tuning_dates": list(tuning_dates),
        "holdout_dates": list(holdout_dates),
        "race_boat_counts": race_boat_counts,
        "cache_root": str(cache_root),
        "run_context": run_context,
        "prune_policy": prune_policy or {},
    }

    def _handle_row(spec: ConfigSpec, row: dict[str, object], skip_df: pd.DataFrame) -> None:
        row = dict(row)
        row["stage"] = stage
        row["cache_key"] = _stage_config_key(stage, spec, run_context)
        row["config_key"] = row["cache_key"]
        rows.append(row)
        if not skip_df.empty:
            skip_frames[spec] = skip_df
        _append_jsonl(
            completed_path,
            {
                "stage": stage,
                "config_key": row["config_key"],
                "cache_key": row["cache_key"],
                "status": "pruned" if bool(row.get("pruned", False)) else "done",
                "cache_hit": bool(row.get("cache_hit", False)),
                "pruned": bool(row.get("pruned", False)),
                "prune_reason": row.get("prune_reason", ""),
                "candidate_generation_mode": spec.candidate_generation_mode,
                "score_mode": spec.score_mode,
                "first_prob_relative_threshold": spec.first_prob_relative_threshold,
                "min_win_proba": spec.min_win_proba,
                "min_ev": spec.min_ev,
                "high_ev_watch_threshold": spec.high_ev_watch_threshold,
                "rescue_enabled": spec.rescue_enabled,
                "risk_preset": spec.risk_preset,
            },
        )

    if workers <= 1 or len(pending) <= 1:
        for spec in pending:
            row, skip_df = _evaluate_config(
                spec,
                stage=stage,
                config_path=Path(args.config_path),
                filtered_win_path=filtered_win_path,
                results_path=results_path,
                odds_path=odds_path,
                tuning_dates=set(tuning_dates),
                holdout_dates=set(holdout_dates),
                race_boat_counts=race_boat_counts,
                cache_root=cache_root,
                run_context=run_context,
                prune_policy=prune_policy,
                candidate_cache={},
                ev_cache={},
            )
            _handle_row(spec, row, skip_df)
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_map = {
                executor.submit(_evaluate_config_task, {**payload_base, "spec": spec.as_dict()}): spec
                for spec in pending
            }
            for future in as_completed(future_map):
                spec = future_map[future]
                try:
                    row = future.result()
                except Exception as exc:
                    row = {**spec.as_dict(), "stage": stage, "error": type(exc).__name__, "error_message": str(exc)}
                    skip_df = pd.DataFrame()
                else:
                    cache_key = str(row.get("cache_key") or _stage_config_key(stage, spec, run_context))
                    cached = _load_cached_stage_result(
                        *_stage_cache_paths(cache_root, stage, cache_key)
                    )
                    skip_df = cached[1] if cached is not None else pd.DataFrame()
                _handle_row(spec, row, skip_df)

    df = pd.DataFrame(rows)
    if df.empty:
        return df, skip_frames

    ranked = _rank_configs(df, minimum_required_buys=args.minimum_required_buys)
    stage_csv = args.output_dir and Path(args.output_dir) / f"{stage}_results.csv"
    stage_json = args.output_dir and Path(args.output_dir) / f"{stage}_summary.json"
    if stage_csv:
        ranked.to_csv(stage_csv, index=False)
    if stage_json:
        summary = {
            "generated_at": pd.Timestamp.now().isoformat(),
            "stage": stage,
            "run_context": run_context,
            "search_space_size": int(search_space_size),
            "selected_configs": len(specs),
            "evaluated_configs": len(ranked),
            "skipped_from_resume": skipped_from_resume,
            "minimum_required_buys": args.minimum_required_buys,
            "best": ranked.iloc[0].to_dict() if not ranked.empty else {},
            "top10": ranked.head(10).to_dict(orient="records"),
        }
        Path(stage_json).write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")

    state = _load_run_state(run_state_path)
    state.setdefault("run_context", run_context)
    state.setdefault("stages", {})
    state["stages"][stage] = {
        "completed": True,
        "completed_at": pd.Timestamp.now().isoformat(),
        "evaluated_configs": len(ranked),
        "skipped_from_resume": skipped_from_resume,
        "best_config_key": str(ranked.iloc[0].get("config_key", "")) if not ranked.empty else "",
        "best_rank": 1 if not ranked.empty else None,
    }
    _save_run_state(run_state_path, state)
    return ranked, skip_frames


def main() -> None:
    parser = argparse.ArgumentParser(description="Parameter search for boatrace strategy modes.")
    parser.add_argument("--win-proba-path", default=str(WIN_PROBA_PATH))
    parser.add_argument("--results-path", default=str(RESULTS_PATH))
    parser.add_argument("--config-path", default="config/strategy_config.json")
    parser.add_argument("--tuning-days", type=int, default=6)
    parser.add_argument("--holdout-days", type=int, default=2)
    parser.add_argument("--max-configs", type=int, default=24)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-dir", default=str(OUT_DIR))
    parser.add_argument("--minimum-required-buys", type=int, default=5)
    parser.add_argument("--max-races-per-day", type=int, default=0)
    parser.add_argument("--workers", type=int, default=max(1, min(2, (os.cpu_count() or 1))))
    parser.add_argument("--resume", action="store_true", default=False)
    parser.add_argument("--export-best-debug", action="store_true", default=True)
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_root = _cache_root(out_dir)
    cache_root.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    run_state_path = out_dir / "run_state.json"
    completed_path = out_dir / "completed_configs.jsonl"
    stage1_csv = out_dir / "stage1_results.csv"
    stage1_json = out_dir / "stage1_summary.json"
    stage2_csv = out_dir / "stage2_results.csv"
    stage2_json = out_dir / "stage2_summary.json"
    best_md = out_dir / "best_configs.md"
    stage1_md = out_dir / "stage1_top_configs.md"
    stage2_md = out_dir / "stage2_top_configs.md"

    if not args.resume:
        for path in [run_state_path, completed_path, stage1_csv, stage1_json, stage2_csv, stage2_json, best_md, stage1_md, stage2_md]:
            try:
                if path.exists():
                    path.unlink()
            except Exception:
                pass

    win = pd.read_csv(args.win_proba_path, low_memory=False)
    win["date"] = pd.to_datetime(win["date"], errors="coerce")
    win = win.dropna(subset=["date"]).copy()
    results = pd.read_csv(args.results_path, low_memory=False)
    results["date"] = pd.to_datetime(results["date"], errors="coerce")

    available_dates = sorted(
        {
            d.normalize()
            for d in win["date"].dropna().tolist()
            if _available_odds_file(pd.Timestamp(d).strftime("%Y%m%d")) is not None
        }
    )
    tuning_dates, holdout_dates = _split_dates(available_dates, tuning_days=args.tuning_days, holdout_days=args.holdout_days)
    if not tuning_dates or not holdout_dates:
        raise RuntimeError("Not enough date coverage with available odds files to build tuning/holdout splits.")

    selected_dates = set(tuning_dates) | set(holdout_dates)
    win = win[win["date"].dt.strftime("%Y-%m-%d").isin(selected_dates)].copy()
    results = results[results["date"].dt.strftime("%Y-%m-%d").isin(selected_dates)].copy()

    if args.max_races_per_day and args.max_races_per_day > 0:
        selected_race_ids: set[str] = set()
        for date_str, group in win.groupby(win["date"].dt.strftime("%Y-%m-%d"), sort=True):
            if date_str not in selected_dates:
                continue
            race_ids = sorted(group["race_id"].dropna().astype(str).unique().tolist())
            if len(race_ids) > args.max_races_per_day:
                rng = random.Random(f"{args.seed}:{date_str}")
                race_ids = rng.sample(race_ids, k=args.max_races_per_day)
            selected_race_ids.update(race_ids)
        win = win[win["race_id"].astype(str).isin(selected_race_ids)].copy()
        results = results[results["race_id"].astype(str).isin(selected_race_ids)].copy()

    filtered_win_path = TMP_DIR / "filtered_win_proba.csv"
    filtered_win_path.parent.mkdir(parents=True, exist_ok=True)
    win.to_csv(filtered_win_path, index=False)

    combined_odds_path = TMP_DIR / "combined_odds.csv"
    build_combined_odds([d.replace("-", "") for d in sorted(selected_dates)], combined_odds_path)

    race_boat_counts = (
        pd.to_numeric(win.groupby("race_id")["lane"].nunique(), errors="coerce").fillna(0).astype(int).to_dict()
        if "lane" in win.columns
        else {}
    )
    race_boat_counts = {str(k): int(v) for k, v in race_boat_counts.items()}

    run_context = {
        "win_proba_path": str(args.win_proba_path),
        "results_path": str(args.results_path),
        "config_path": str(args.config_path),
        "selected_dates": sorted(selected_dates),
        "tuning_dates": tuning_dates,
        "holdout_dates": holdout_dates,
        "seed": int(args.seed),
        "max_races_per_day": int(args.max_races_per_day),
        "tuning_days": int(args.tuning_days),
        "holdout_days": int(args.holdout_days),
    }

    state = _load_run_state(run_state_path)
    completed_keys = _read_jsonl_keys(completed_path) if args.resume else set()
    if args.resume and state.get("run_context") and state.get("run_context") != run_context:
        print("[warn] resume context mismatch; starting a fresh run with existing cache")
        state = {}
        completed_keys = set()
        for path in [run_state_path, completed_path, stage1_csv, stage1_json, stage2_csv, stage2_json, best_md, stage1_md, stage2_md]:
            try:
                if path.exists():
                    path.unlink()
            except Exception:
                pass
    stage1_done = bool(state.get("stages", {}).get("stage1", {}).get("completed", False))
    stage2_done = bool(state.get("stages", {}).get("stage2", {}).get("completed", False))

    base_evaluator = StrategyEvaluator(config_path=str(args.config_path))
    stage1_grid = _build_stage1_grid(base_evaluator.buy_min_approx_prob, base_evaluator.buy_min_ev, base_evaluator.high_ev_watch_threshold)
    stage1_specs = _pick_configs(stage1_grid, max_configs=args.max_configs, seed=args.seed)
    stage1_ranked = pd.DataFrame()
    stage2_ranked = pd.DataFrame()

    if not stage1_done or not args.resume:
        stage1_ranked, _stage1_skip = _run_stage(
            stage="stage1",
            specs=stage1_specs,
            search_space_size=len(stage1_grid),
            args=args,
            run_context=run_context,
            filtered_win_path=filtered_win_path,
            results_path=Path(args.results_path),
            odds_path=combined_odds_path,
            tuning_dates=tuning_dates,
            holdout_dates=holdout_dates,
            race_boat_counts=race_boat_counts,
            cache_root=cache_root,
            completed_path=completed_path,
            run_state_path=run_state_path,
            resume_keys=completed_keys if args.resume else set(),
            workers=max(1, int(args.workers)),
            prune_policy={"enabled": True, "min_races_covered": 2, "max_watch_count": 1},
        )
        if not stage1_ranked.empty:
            _write_top_configs_md(stage1_ranked, stage1_md)

    stage1_source = stage1_ranked
    if stage1_source.empty and stage1_csv.exists():
        stage1_source = pd.read_csv(stage1_csv, low_memory=False)
    if not stage2_done and not stage1_source.empty:
        stage2_grid = _build_stage2_grid(stage1_source)
        stage2_specs = _pick_configs(stage2_grid, max_configs=args.max_configs, seed=args.seed)
        stage2_ranked, _stage2_skip = _run_stage(
            stage="stage2",
            specs=stage2_specs,
            search_space_size=len(stage2_grid),
            args=args,
            run_context=run_context,
            filtered_win_path=filtered_win_path,
            results_path=Path(args.results_path),
            odds_path=combined_odds_path,
            tuning_dates=tuning_dates,
            holdout_dates=holdout_dates,
            race_boat_counts=race_boat_counts,
            cache_root=cache_root,
            completed_path=completed_path,
            run_state_path=run_state_path,
            resume_keys=completed_keys if args.resume else set(),
            workers=max(1, int(args.workers)),
            prune_policy={"enabled": False},
        )
        if not stage2_ranked.empty:
            _write_top_configs_md(stage2_ranked, stage2_md)

    if stage1_csv.exists():
        stage1_ranked = pd.read_csv(stage1_csv, low_memory=False)
    if stage2_csv.exists():
        stage2_ranked = pd.read_csv(stage2_csv, low_memory=False)

    _write_best_configs_md(stage1_ranked if not stage1_ranked.empty else None, stage2_ranked if not stage2_ranked.empty else None, best_md)

    final_ranked = stage2_ranked if not stage2_ranked.empty else stage1_ranked
    if final_ranked.empty:
        raise RuntimeError("No parameter search rows were produced.")
    print(f"[saved] {stage1_csv}")
    print(f"[saved] {stage1_json}")
    print(f"[saved] {stage2_csv}")
    print(f"[saved] {stage2_json}")
    print(f"[saved] {best_md}")
    print(f"[saved] {run_state_path}")
    print(f"[saved] {completed_path}")
    cols = [
        "candidate_generation_mode",
        "score_mode",
        "first_prob_relative_threshold",
        "min_win_proba",
        "min_ev",
        "high_ev_watch_threshold",
        "rescue_enabled",
        "risk_preset",
        "ranking_score",
        "tuning_roi",
        "holdout_roi",
        "tuning_buy_count",
        "holdout_buy_count",
        "holdout_pass",
    ]
    available_cols = [c for c in cols if c in final_ranked.columns]
    print(final_ranked.head(5)[available_cols].to_string(index=False))


if __name__ == "__main__":
    main()
