"""Shadow profitability analysis for daily prediction artifacts.

This module is intentionally observational. It does not place bets, change
research BUY rules, or promote a strategy from a single good-looking slice.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

STRATEGIES = ("top1_win", "top2_win", "value_filter")
DEFAULT_MIN_SAMPLE = 100
DEFAULT_MIN_DAYS = 3
DEFAULT_MIN_ROI_PCT = 105.0


@dataclass(frozen=True)
class AnalysisPaths:
    daily_root: Path
    output_dir: Path


def _as_number(series: pd.Series | object, default: float = 0.0) -> pd.Series:
    if isinstance(series, pd.Series):
        return pd.to_numeric(series, errors="coerce").fillna(default)
    return pd.Series(dtype=float)


def _prob_column(frame: pd.DataFrame) -> str:
    if "pred_prob" in frame.columns:
        return "pred_prob"
    if "pred_proba" in frame.columns:
        return "pred_proba"
    return "pred_prob"


def _settled_mask(frame: pd.DataFrame) -> pd.Series:
    if "result_status" not in frame.columns:
        return pd.Series([True] * len(frame), index=frame.index)
    return frame["result_status"].astype(str).isin(["settled", "final", "partial_result"])


def load_settlements(daily_root: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    if not daily_root.exists():
        return pd.DataFrame()
    for path in sorted(daily_root.glob("*/settlement.csv")):
        try:
            frame = pd.read_csv(path, dtype={"race_id": str})
        except Exception:
            continue
        if frame.empty:
            continue
        frame["artifact_date"] = path.parent.name
        frames.append(frame)
    if not frames:
        return pd.DataFrame()
    data = pd.concat(frames, ignore_index=True, sort=False)
    if "race_date" not in data.columns:
        data["race_date"] = data["artifact_date"]
    return data


def selected_rows(frame: pd.DataFrame, strategy: str, ev_threshold: float = 1.0) -> pd.DataFrame:
    data = frame.copy()
    if data.empty:
        return data
    prob_col = _prob_column(data)
    data["pred_rank"] = _as_number(data.get("pred_rank"))
    data[prob_col] = _as_number(data.get(prob_col))
    data["expected_value"] = _as_number(data.get("expected_value"))
    if strategy == "top1_win":
        return data[data["pred_rank"] == 1].copy()
    if strategy == "top2_win":
        return data[data["pred_rank"].isin([1, 2])].copy()
    if strategy == "value_filter":
        return data[(data["expected_value"] >= ev_threshold) & data[prob_col].notna()].copy()
    raise ValueError(f"unknown strategy: {strategy}")


def metric_summary(frame: pd.DataFrame, strategy: str,
                   min_sample: int = DEFAULT_MIN_SAMPLE,
                   ev_threshold: float = 1.0) -> dict:
    selected = selected_rows(frame, strategy, ev_threshold=ev_threshold)
    if selected.empty:
        return {
            "strategy": strategy,
            "bets": 0,
            "hit_count": 0,
            "hit_rate": None,
            "roi_pct": None,
            "profit_units": 0.0,
            "sample_status": "no_sample",
        }
    selected["win"] = _as_number(selected.get("win"))
    selected["win_odds"] = _as_number(selected.get("win_odds"), default=float("nan"))
    valid = selected[selected["win_odds"].notna()].copy()
    bets = int(len(valid))
    if bets == 0:
        return {
            "strategy": strategy,
            "bets": 0,
            "hit_count": 0,
            "hit_rate": None,
            "roi_pct": None,
            "profit_units": 0.0,
            "sample_status": "no_odds",
        }
    payout_units = float((valid["win"] * valid["win_odds"]).sum())
    hit_count = int(valid["win"].sum())
    return {
        "strategy": strategy,
        "bets": bets,
        "hit_count": hit_count,
        "hit_rate": float(hit_count / bets),
        "roi_pct": float(payout_units / bets * 100.0),
        "profit_units": float(payout_units - bets),
        "sample_status": "enough_sample" if bets >= min_sample else "insufficient_sample",
    }


def _bucket(series: pd.Series | object, bins: list[float], labels: list[str]) -> pd.Series:
    numeric = _as_number(series, default=float("nan"))
    return pd.cut(numeric, bins=bins, labels=labels, include_lowest=True).astype("string").fillna("missing")


def build_slices(frame: pd.DataFrame, min_sample: int = DEFAULT_MIN_SAMPLE,
                 ev_threshold: float = 1.0) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    rows: list[dict] = []
    prob_col = _prob_column(frame)
    for strategy in STRATEGIES:
        selected = selected_rows(frame, strategy, ev_threshold=ev_threshold)
        if selected.empty:
            continue
        selected = selected.copy()
        selected["prob_bucket"] = _bucket(
            selected.get(prob_col),
            [0, 0.12, 0.16, 0.20, 0.30, 1.01],
            ["<=0.12", "0.12-0.16", "0.16-0.20", "0.20-0.30", ">0.30"],
        )
        selected["odds_bucket"] = _bucket(
            selected.get("win_odds"),
            [0, 1.5, 3, 5, 10, 30, 9999],
            ["<=1.5", "1.5-3", "3-5", "5-10", "10-30", ">30"],
        )
        selected["ev_bucket"] = _bucket(
            selected.get("expected_value"),
            [-999, 0.8, 1.0, 1.2, 1.6, 9999],
            ["<0.8", "0.8-1.0", "1.0-1.2", "1.2-1.6", ">1.6"],
        )
        for column in ("course_id", "race_number", "lane", "prob_bucket", "odds_bucket", "ev_bucket"):
            if column not in selected.columns:
                continue
            for value, group in selected.groupby(column, dropna=False):
                summary = metric_summary(group, strategy, min_sample=min_sample, ev_threshold=ev_threshold)
                summary.update({
                    "slice_type": column,
                    "slice_value": str(value),
                    "sample_status": "enough_sample"
                    if summary["bets"] >= min_sample else "insufficient_sample",
                    "unique_days": int(group["artifact_date"].astype(str).nunique())
                    if "artifact_date" in group.columns else 0,
                })
                rows.append(summary)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(
        ["strategy", "slice_type", "roi_pct", "bets"],
        ascending=[True, True, False, False],
    )


def recommend_actions(strategy_summary: list[dict], slices: pd.DataFrame,
                      min_sample: int = DEFAULT_MIN_SAMPLE,
                      min_days: int = DEFAULT_MIN_DAYS,
                      min_roi_pct: float = DEFAULT_MIN_ROI_PCT) -> dict:
    candidates: list[dict] = []
    if not slices.empty:
        data = slices.copy()
        data["roi_pct"] = pd.to_numeric(data["roi_pct"], errors="coerce")
        data["bets"] = pd.to_numeric(data["bets"], errors="coerce").fillna(0)
        data["unique_days"] = pd.to_numeric(data.get("unique_days"), errors="coerce").fillna(0)
        qualified = data[
            (data["bets"] >= min_sample)
            & (data["unique_days"] >= min_days)
            & (data["roi_pct"] >= min_roi_pct)
        ].copy()
        if not qualified.empty:
            candidates = qualified.sort_values(
                ["roi_pct", "bets"], ascending=[False, False]
            ).head(10).to_dict(orient="records")

    profitable_strategy = [
        row for row in strategy_summary
        if row.get("sample_status") == "enough_sample"
        and row.get("roi_pct") is not None
        and row["roi_pct"] >= min_roi_pct
    ]
    live_allowed = bool(candidates and profitable_strategy)
    return {
        "decision": "paper_trade_only" if not live_allowed else "candidate_found_requires_manual_review",
        "live_betting_allowed": live_allowed,
        "reason": (
            "No strategy and slice both satisfy minimum sample, cross-day, and ROI gates."
            if not live_allowed else
            "A candidate passed numeric gates, but still requires manual review before paper-trade use."
        ),
        "candidate_slices": candidates,
        "required_gates": {
            "min_sample": int(min_sample),
            "min_days": int(min_days),
            "min_roi_pct": float(min_roi_pct),
        },
    }


def bankroll_guard(bankroll_yen: int | None, live_allowed: bool) -> dict:
    if bankroll_yen is None or bankroll_yen <= 0:
        return {
            "bankroll_yen": None,
            "unit_stake_yen": 0,
            "max_daily_loss_yen": 0,
            "rule": "Bankroll was not provided. Use paper trading only.",
        }
    max_daily_loss = max(100, int(bankroll_yen * 0.01 // 100 * 100))
    unit = 0 if not live_allowed else max(100, int(bankroll_yen * 0.001 // 100 * 100))
    return {
        "bankroll_yen": int(bankroll_yen),
        "unit_stake_yen": int(unit),
        "max_daily_loss_yen": int(max_daily_loss),
        "rule": (
            "Live betting is blocked by profitability gates; record-only mode."
            if not live_allowed else
            "If manually approved, stop for the day when max_daily_loss_yen is reached."
        ),
    }


def top_slices(slices: pd.DataFrame, limit: int = 10) -> list[dict]:
    if slices.empty:
        return []
    data = slices.copy()
    data["roi_pct"] = pd.to_numeric(data["roi_pct"], errors="coerce")
    data["bets"] = pd.to_numeric(data["bets"], errors="coerce").fillna(0)
    return data.sort_values(["sample_status", "roi_pct", "bets"],
                            ascending=[True, False, False]).head(limit).to_dict(orient="records")


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_markdown_report(payload: dict) -> str:
    lines = [
        "# Profitability Analysis",
        "",
        "This report is observational. It is for research and paper-trade purposes only.",
        "",
        f"- decision: {payload['recommendation']['decision']}",
        f"- live_betting_allowed: {payload['recommendation']['live_betting_allowed']}",
        f"- reason: {payload['recommendation']['reason']}",
        "",
        "## Strategy Summary",
        "| strategy | bets | hit_rate | roi_pct | profit_units | sample |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for row in payload["current"]["strategy_summary"]:
        hit = "" if row["hit_rate"] is None else f"{row['hit_rate']:.3f}"
        roi = "" if row["roi_pct"] is None else f"{row['roi_pct']:.1f}"
        lines.append(
            f"| {row['strategy']} | {row['bets']} | {hit} | {roi} | "
            f"{row['profit_units']:.1f} | {row['sample_status']} |"
        )
    lines.extend([
        "",
        "## Top Shadow Slices",
        "| strategy | slice | value | bets | days | hit_rate | roi_pct | sample |",
        "|---|---|---|---:|---:|---:|---:|---|",
    ])
    for row in payload["top_slices"]:
        hit = "" if row.get("hit_rate") is None else f"{row['hit_rate']:.3f}"
        roi = "" if row.get("roi_pct") is None else f"{row['roi_pct']:.1f}"
        lines.append(
            f"| {row['strategy']} | {row['slice_type']} | {row['slice_value']} | "
            f"{int(row['bets'])} | {int(row.get('unique_days', 0))} | {hit} | {roi} | {row['sample_status']} |"
        )
    guard = payload["bankroll_guard"]
    lines.extend([
        "",
        "## Bankroll Guard",
        f"- bankroll_yen: {guard['bankroll_yen']}",
        f"- unit_stake_yen: {guard['unit_stake_yen']}",
        f"- max_daily_loss_yen: {guard['max_daily_loss_yen']}",
        f"- rule: {guard['rule']}",
        "",
        "## Next Actions",
        "- Keep recording daily predictions and settlements.",
        "- Do not promote any strategy until numeric gates pass across multiple dates.",
        "- Prefer reducing bad bets over increasing the number of bets.",
    ])
    return "\n".join(lines) + "\n"


def run_analysis(paths: AnalysisPaths,
                 min_sample: int = DEFAULT_MIN_SAMPLE,
                 min_days: int = DEFAULT_MIN_DAYS,
                 min_roi_pct: float = DEFAULT_MIN_ROI_PCT,
                 bankroll_yen: int | None = None,
                 ev_threshold: float = 1.0) -> dict:
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    current = load_settlements(paths.daily_root)
    settled = current[_settled_mask(current)].copy() if not current.empty else current
    summary = [
        metric_summary(settled, strategy, min_sample=min_sample, ev_threshold=ev_threshold)
        for strategy in STRATEGIES
    ]
    slices = build_slices(settled, min_sample=min_sample, ev_threshold=ev_threshold)
    recommendation = recommend_actions(
        summary,
        slices,
        min_sample=min_sample,
        min_days=min_days,
        min_roi_pct=min_roi_pct,
    )
    payload = {
        "analysis_status": "shadow_only",
        "current": {
            "daily_root": str(paths.daily_root),
            "days": int(current["artifact_date"].astype(str).nunique()) if "artifact_date" in current.columns else 0,
            "rows": int(len(current)),
            "settled_rows": int(len(settled)),
            "strategy_summary": summary,
        },
        "recommendation": recommendation,
        "bankroll_guard": bankroll_guard(bankroll_yen, recommendation["live_betting_allowed"]),
        "top_slices": top_slices(slices),
        "notes": [
            "Hit rate alone is insufficient; ROI and drawdown control determine survivability.",
            "This output is for validation and does not alter prediction or BUY logic.",
        ],
    }
    write_json(paths.output_dir / "profitability_summary.json", payload)
    if not slices.empty:
        slices.to_csv(paths.output_dir / "profitability_slices.csv", index=False, encoding="utf-8-sig")
    (paths.output_dir / "profitability_report.md").write_text(build_markdown_report(payload), encoding="utf-8")
    return payload
