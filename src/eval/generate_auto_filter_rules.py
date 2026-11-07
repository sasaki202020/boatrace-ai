from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
BACKTEST_PATH = ROOT / "reports" / "backtest_race_results.csv"
PRED_PATH = ROOT / "data" / "strategy_outputs" / "skip_decisions.csv"
TODAY_RACES_PATH = ROOT / "data" / "processed" / "today_races.csv"
OUT_RULES = ROOT / "data" / "strategy_outputs" / "auto_filter_rules.json"
OUT_REPORT = ROOT / "reports" / "auto_filter_analysis.csv"

MIN_SAMPLE_COUNT = 30
MIN_ROI = 1.0
DEFAULT_RECENT_DAYS = 35
DEFAULT_RECENT_RACES = 500
MIN_RECENT_ROWS = 300
PROB_METRIC_CANDIDATES = [
    "calibrated_hit_prob",
    "first_place_prob",
    "approx_prob",
]
PROB_EDGES = [round(x / 20, 2) for x in range(0, 21)]
ODDS_EDGES = [0, 20, 50, 100, 200, 500, 1000, 999999]

JCD_TO_VENUE = {
    "01": "桐生",
    "02": "戸田",
    "03": "江戸川",
    "04": "平和島",
    "05": "多摩川",
    "06": "浜名湖",
    "07": "蒲郡",
    "08": "常滑",
    "09": "津",
    "10": "三国",
    "11": "びわこ",
    "12": "住之江",
    "13": "尼崎",
    "14": "鳴門",
    "15": "丸亀",
    "16": "児島",
    "17": "宮島",
    "18": "徳山",
    "19": "下関",
    "20": "若松",
    "21": "芦屋",
    "22": "福岡",
    "23": "唐津",
    "24": "大村",
}

RACE_PREFIX_TO_VENUE = {"B": "大村", "K": "唐津", "S": "下関"}

VENUE_FIX = {"‘å‘º": "大村", "“‚’Ã": "唐津", "‰ºŠÖ": "下関"}


def _to_bool_series(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    return series.astype(str).str.strip().str.lower().isin({"1", "true", "yes", "y"})


def _normalize_jcd(v: object) -> str:
    digits = "".join(ch for ch in str(v or "") if ch.isdigit())
    return digits[-2:].zfill(2) if digits else ""


def _venue_from_race_id(race_id: object) -> str:
    text = str(race_id or "")
    if "-" not in text:
        return ""
    mid = text.split("-")[1] if len(text.split("-")) >= 2 else ""
    prefix = mid[:1].upper()
    return RACE_PREFIX_TO_VENUE.get(prefix, "")


def _normalize_venue_name(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return VENUE_FIX.get(text, text)


def _load_venue_meta() -> pd.DataFrame:
    if not TODAY_RACES_PATH.exists():
        return pd.DataFrame(columns=["race_id", "venue_name"])
    df = pd.read_csv(TODAY_RACES_PATH, low_memory=False)
    if "race_id" not in df.columns:
        return pd.DataFrame(columns=["race_id", "venue_name"])
    work = df.copy()
    if "jcd" in work.columns:
        work["jcd"] = work["jcd"].map(_normalize_jcd)
        work["venue_name"] = work["jcd"].map(JCD_TO_VENUE).fillna("")
    else:
        work["venue_name"] = ""
    if "venue" in work.columns:
        work["venue_name"] = work["venue_name"].where(work["venue_name"].astype(str).str.len() > 0, work["venue"].astype(str))
    work["venue_name"] = work["venue_name"].map(_normalize_venue_name)
    work.loc[work["venue_name"].eq(""), "venue_name"] = work.loc[work["venue_name"].eq(""), "race_id"].map(_venue_from_race_id)
    work.loc[work["venue_name"].eq(""), "venue_name"] = "不明"
    meta = work[["race_id", "venue_name"]].dropna(subset=["race_id"]).copy()
    meta["venue_name"] = meta["venue_name"].astype(str).str.strip()
    meta = meta.drop_duplicates("race_id")
    return meta


def _load_data() -> pd.DataFrame:
    if not BACKTEST_PATH.exists():
        raise FileNotFoundError(f"backtest file not found: {BACKTEST_PATH}")
    if not PRED_PATH.exists():
        raise FileNotFoundError(f"prediction file not found: {PRED_PATH}")

    backtest = pd.read_csv(BACKTEST_PATH, low_memory=False)
    preds = pd.read_csv(PRED_PATH, low_memory=False)

    required_backtest = {"race_id", "hit", "settled_odds", "result_available"}
    missing_backtest = required_backtest - set(backtest.columns)
    if missing_backtest:
        raise ValueError(f"backtest missing columns: {sorted(missing_backtest)}")

    join_cols = ["race_id"]
    pred_cols = [c for c in ["first_place_prob", "calibrated_hit_prob", "approx_prob", "strategy_mode", "odds_source", "has_real_odds"] if c in preds.columns]
    preds = preds[join_cols + pred_cols].drop_duplicates("race_id")

    meta = _load_venue_meta()
    df = backtest.merge(preds, on="race_id", how="left", suffixes=("", "_pred"))
    if not meta.empty:
        df = df.merge(meta, on="race_id", how="left")
    else:
        df["venue_name"] = ""

    df["result_available"] = _to_bool_series(df["result_available"])
    df = df[df["result_available"]].copy()
    df["hit"] = _to_bool_series(df["hit"])

    for col in ["settled_odds", "odds", "ev", "first_place_prob", "calibrated_hit_prob", "approx_prob"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df["venue_name"] = df["venue_name"].fillna("").astype(str).str.strip()
    df.loc[df["venue_name"].eq(""), "venue_name"] = df.loc[df["venue_name"].eq(""), "race_id"].map(_venue_from_race_id)
    df.loc[df["venue_name"].eq(""), "venue_name"] = "不明"
    return df


def _pick_date_column(df: pd.DataFrame) -> str | None:
    for col in df.columns:
        if "date" in str(col).lower():
            return col
    return None


def _select_recent_window(
    df: pd.DataFrame,
    recent_days: int | None = DEFAULT_RECENT_DAYS,
    recent_races: int | None = DEFAULT_RECENT_RACES,
    min_rows: int = MIN_RECENT_ROWS,
) -> tuple[pd.DataFrame, dict]:
    work = df.copy()
    date_col = _pick_date_column(work)
    window_info = {
        "mode": "full",
        "recent_days": recent_days,
        "recent_races": recent_races,
        "min_rows": min_rows,
        "date_col": date_col,
        "source_rows": int(len(work)),
        "source_races": int(work["race_id"].nunique()) if "race_id" in work.columns else None,
        "selected_rows": int(len(work)),
        "selected_races": int(work["race_id"].nunique()) if "race_id" in work.columns else None,
        "window_start": None,
        "window_end": None,
        "selected_by": "full",
    }

    if work.empty:
        return work, window_info

    if date_col:
        work[date_col] = pd.to_datetime(work[date_col], errors="coerce")
        dated = work.dropna(subset=[date_col]).copy()
    else:
        dated = work.copy()

    selected = dated
    if recent_days and date_col and not dated.empty:
        end_dt = pd.to_datetime(dated[date_col].max(), errors="coerce")
        if not pd.isna(end_dt):
            start_dt = end_dt - pd.Timedelta(days=max(int(recent_days) - 1, 0))
            candidate = dated[dated[date_col] >= start_dt].copy()
            if len(candidate) >= min_rows:
                selected = candidate
                window_info.update(
                    {
                        "mode": "recent",
                        "selected_by": "recent_days",
                        "window_start": start_dt.isoformat(),
                        "window_end": end_dt.isoformat(),
                        "selected_rows": int(len(candidate)),
                        "selected_races": int(candidate["race_id"].nunique()) if "race_id" in candidate.columns else None,
                    }
                )
    if window_info["selected_by"] == "full" and recent_races and "race_id" in dated.columns:
        sort_cols = [date_col] if date_col else []
        if not sort_cols:
            sort_cols = ["race_id"]
        candidate = (
            dated.sort_values(sort_cols, ascending=True)
            .drop_duplicates("race_id", keep="last")
            .sort_values(sort_cols, ascending=False)
            .head(int(recent_races))
            .sort_values(sort_cols, ascending=True)
            .copy()
        )
        if not candidate.empty:
            selected = candidate
            start_value = candidate[date_col].min() if date_col else None
            end_value = candidate[date_col].max() if date_col else None
            window_info.update(
                {
                    "mode": "recent",
                    "selected_by": "recent_races",
                    "window_start": None if pd.isna(start_value) else str(start_value),
                    "window_end": None if pd.isna(end_value) else str(end_value),
                    "selected_rows": int(len(candidate)),
                    "selected_races": int(candidate["race_id"].nunique()) if "race_id" in candidate.columns else None,
                }
            )

    if window_info["selected_by"] == "full":
        if date_col and not dated.empty:
            window_info.update(
                {
                    "window_start": None if pd.isna(dated[date_col].min()) else str(dated[date_col].min()),
                    "window_end": None if pd.isna(dated[date_col].max()) else str(dated[date_col].max()),
                }
            )
        selected = work

    selected = selected.copy()
    if date_col and date_col in selected.columns:
        selected = selected.sort_values(date_col, ascending=True).reset_index(drop=True)
    return selected, window_info


def _window_label(window_info: dict) -> str:
    mode = str(window_info.get("selected_by") or "full")
    if mode == "recent_days":
        days = window_info.get("recent_days")
        return f"直近{days}日"
    if mode == "recent_races":
        races = window_info.get("recent_races")
        return f"直近{races}レース"
    return "全期間"


def _build_auto_filter_artifacts(df: pd.DataFrame, window_info: dict) -> tuple[dict, pd.DataFrame]:
    prob_metric, prob_table, prob_score = _choose_prob_metric(df)
    odds_table = _aggregate_bins(df, "odds", ODDS_EDGES, digits=0)
    places_table = _aggregate_places(df)

    allowed_prob_bins = prob_table.loc[prob_table["allowed"], "bin"].tolist()
    allowed_odds_bins = odds_table.loc[odds_table["allowed"], "bin"].tolist()
    allowed_places = places_table.loc[places_table["allowed"], "bin"].tolist()
    enabled = bool(allowed_prob_bins and allowed_odds_bins and allowed_places)

    report_rows = pd.concat(
        [
            prob_table.assign(metric=prob_metric),
            odds_table.assign(metric="odds"),
            places_table.assign(metric="venue"),
        ],
        ignore_index=True,
    )
    report_rows = report_rows[["metric", "dimension", "bin", "count", "hits", "hit_rate", "avg_odds", "avg_settled_odds", "total_return", "roi", "allowed"]]

    payload = {
        "strategy_mode": "AUTO_FILTER",
        "enabled": enabled,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "window": window_info,
        "window_label": _window_label(window_info),
        "min_sample_count": MIN_SAMPLE_COUNT,
        "min_roi": MIN_ROI,
        "prob_metric": prob_metric,
        "prob_bin_edges": PROB_EDGES,
        "odds_bin_edges": ODDS_EDGES,
        "allowed_prob_bins": allowed_prob_bins,
        "allowed_odds_bins": allowed_odds_bins,
        "allowed_places": allowed_places,
        "prob_metric_score": prob_score,
        "prob_table": _serialize_rows(prob_table),
        "odds_table": _serialize_rows(odds_table),
        "venue_table": _serialize_rows(places_table),
    }
    return payload, report_rows


def _bin_label(left: float, right: float, digits: int = 2, is_last: bool = False) -> str:
    if digits == 0:
        return f"{int(left)}+" if is_last else f"{int(left)}-{int(right)}"
    return f"{left:.2f}+" if is_last else f"{left:.2f}-{right:.2f}"


def _build_bins(edges: list[float], digits: int = 2) -> list[str]:
    labels: list[str] = []
    for idx in range(len(edges) - 1):
        labels.append(_bin_label(edges[idx], edges[idx + 1], digits=digits, is_last=idx == len(edges) - 2))
    return labels


def _assign_bins(series: pd.Series, edges: list[float], digits: int = 2) -> pd.Series:
    labels = _build_bins(edges, digits=digits)
    work = pd.to_numeric(series, errors="coerce")
    if not labels:
        return pd.Series(["unknown"] * len(series), index=series.index)
    binned = pd.cut(work, bins=edges, labels=labels, include_lowest=True, right=True)
    return binned.astype(str).replace("nan", "unknown")


def _aggregate_bins(df: pd.DataFrame, value_col: str, edges: list[float], digits: int = 2) -> pd.DataFrame:
    work = df.copy().dropna(subset=[value_col, "settled_odds", "hit"]).copy()
    work["bin"] = _assign_bins(work[value_col], edges, digits=digits)
    labels = _build_bins(edges, digits=digits)
    work["payout"] = work["settled_odds"] * work["hit"].astype(int)
    grouped = (
        work.groupby("bin", as_index=False)
        .agg(
            count=("race_id", "count"),
            hits=("hit", "sum"),
            avg_odds=("odds", "mean"),
            avg_settled_odds=("settled_odds", "mean"),
            total_return=("payout", "sum"),
        )
    )
    grouped["bin"] = pd.Categorical(grouped["bin"], categories=labels, ordered=True)
    grouped = grouped.sort_values("bin").reset_index(drop=True)
    grouped["hit_rate"] = grouped["hits"] / grouped["count"]
    grouped["roi"] = grouped["total_return"] / grouped["count"]
    grouped["allowed"] = (grouped["count"] >= MIN_SAMPLE_COUNT) & (grouped["roi"] > MIN_ROI)
    grouped["dimension"] = value_col
    return grouped[["dimension", "bin", "count", "hits", "hit_rate", "avg_odds", "avg_settled_odds", "total_return", "roi", "allowed"]]


def _aggregate_places(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy().dropna(subset=["venue_name", "settled_odds", "hit"]).copy()
    work["payout"] = work["settled_odds"] * work["hit"].astype(int)
    grouped = (
        work.groupby("venue_name", as_index=False)
        .agg(
            count=("race_id", "count"),
            hits=("hit", "sum"),
            avg_odds=("odds", "mean"),
            avg_settled_odds=("settled_odds", "mean"),
            total_return=("payout", "sum"),
        )
        .sort_values("venue_name")
        .reset_index(drop=True)
    )
    grouped["hit_rate"] = grouped["hits"] / grouped["count"]
    grouped["roi"] = grouped["total_return"] / grouped["count"]
    grouped["allowed"] = (grouped["count"] >= MIN_SAMPLE_COUNT * 2 - 10) & (grouped["roi"] > MIN_ROI)
    grouped["dimension"] = "venue_name"
    grouped = grouped.rename(columns={"venue_name": "bin"})
    return grouped[["dimension", "bin", "count", "hits", "hit_rate", "avg_odds", "avg_settled_odds", "total_return", "roi", "allowed"]]


def _serialize_rows(df: pd.DataFrame) -> list[dict]:
    return json.loads(df.replace({pd.NA: None}).where(pd.notna(df), None).to_json(orient="records", force_ascii=False))


def _choose_prob_metric(df: pd.DataFrame) -> tuple[str, pd.DataFrame, dict]:
    candidates: list[dict] = []
    for metric in PROB_METRIC_CANDIDATES:
        if metric not in df.columns:
            continue
        table = _aggregate_bins(df, metric, PROB_EDGES, digits=2)
        allowed = table[table["allowed"]].copy()
        if allowed.empty:
            score = {
                "allowed_bin_count": 0,
                "allowed_volume": 0,
                "allowed_hits": 0,
                "allowed_roi_mean": 0.0,
                "max_roi": float(table["roi"].max()) if not table.empty else 0.0,
                "selection_score": 0.0,
            }
        else:
            allowed_volume = int(allowed["count"].sum())
            allowed_hits = int(allowed["hits"].sum())
            allowed_roi_mean = float(allowed["roi"].mean())
            max_roi = float(table["roi"].max()) if not table.empty else 0.0
            # 低サンプルの見かけROIより、件数とヒット数を少し強めに評価する
            selection_score = (
                allowed_roi_mean * (1.0 + np.log1p(allowed_volume))
                + 0.35 * np.log1p(allowed_hits)
                + 0.05 * max_roi
            )
            score = {
                "allowed_bin_count": int(len(allowed)),
                "allowed_volume": allowed_volume,
                "allowed_hits": allowed_hits,
                "allowed_roi_mean": allowed_roi_mean,
                "max_roi": max_roi,
                "selection_score": float(selection_score),
            }
        candidates.append(
            {
                "metric": metric,
                "table": table,
                "score": score,
            }
        )

    if not candidates:
        empty = pd.DataFrame(columns=["dimension", "bin", "count", "hits", "hit_rate", "avg_odds", "avg_settled_odds", "total_return", "roi", "allowed"])
        return "approx_prob", empty, {"allowed_bin_count": 0, "allowed_volume": 0, "allowed_hits": 0, "allowed_roi_mean": 0.0, "max_roi": 0.0, "selection_score": 0.0}

    candidates.sort(
        key=lambda item: (
            item["score"].get("selection_score", 0.0),
            item["score"].get("allowed_volume", 0),
            item["score"].get("allowed_hits", 0),
            item["score"].get("allowed_roi_mean", 0.0),
        ),
        reverse=True,
    )

    best = candidates[0]
    return best["metric"], best["table"], best["score"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate AUTO_FILTER rules from backtest results.")
    parser.add_argument("--recent-days", type=int, default=DEFAULT_RECENT_DAYS)
    parser.add_argument("--recent-races", type=int, default=DEFAULT_RECENT_RACES)
    parser.add_argument("--min-rows", type=int, default=MIN_RECENT_ROWS)
    parser.add_argument("--source", choices=["full", "recent"], default="full")
    parser.add_argument("--output-rules", default=str(OUT_RULES))
    parser.add_argument("--output-report", default=str(OUT_REPORT))
    args = parser.parse_args(argv)

    df = _load_data()
    if args.source == "recent":
        df, window_info = _select_recent_window(
            df,
            recent_days=args.recent_days,
            recent_races=args.recent_races,
            min_rows=args.min_rows,
        )
    else:
        df, window_info = _select_recent_window(df, recent_days=None, recent_races=None, min_rows=args.min_rows)

    if df.empty:
        raise RuntimeError("no rows available for auto filter analysis")

    payload, report_rows = _build_auto_filter_artifacts(df, window_info)
    out_rules = Path(args.output_rules)
    out_report = Path(args.output_report)
    payload["report_csv"] = str(out_report.relative_to(ROOT))
    payload["rules_scope"] = window_info
    report_rows = report_rows.copy()
    report_rows["window_label"] = payload["window_label"]
    out_report.parent.mkdir(parents=True, exist_ok=True)
    report_rows.to_csv(out_report, index=False)

    out_rules.parent.mkdir(parents=True, exist_ok=True)
    out_rules.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(
        {
            "prob_metric": payload["prob_metric"],
            "allowed_prob_bins": payload["allowed_prob_bins"],
            "allowed_odds_bins": payload["allowed_odds_bins"],
            "allowed_places": payload["allowed_places"],
            "enabled": payload["enabled"],
            "window_label": payload["window_label"],
            "window": payload["window"],
            "prob_metric_score": payload["prob_metric_score"],
            "report_csv": str(out_report.relative_to(ROOT)),
            "rules_json": str(out_rules.relative_to(ROOT)),
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
