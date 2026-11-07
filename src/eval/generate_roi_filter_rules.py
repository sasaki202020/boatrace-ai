from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
BACKTEST_PATH = ROOT / "reports" / "backtest_race_results.csv"
PRED_PATH = ROOT / "data" / "strategy_outputs" / "skip_decisions.csv"
TODAY_RACES_PATH = ROOT / "data" / "processed" / "today_races.csv"
OUT_RULES = ROOT / "data" / "strategy_outputs" / "roi_filter_rules.json"
OUT_REPORT = ROOT / "reports" / "roi_filter_analysis.csv"

MIN_SAMPLE_COUNT = 30
MIN_ROI = 1.0

PROB_METRICS = ["first_place_prob", "calibrated_hit_prob", "approx_prob"]
PROB_EDGES = [round(x / 10, 1) for x in range(0, 11)]
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

RACE_PREFIX_TO_VENUE = {
    "B": "大村",
    "K": "唐津",
    "S": "下関",
}

VENUE_FIX = {
    "‘å‘º": "大村",
    "“‚’Ã": "唐津",
    "‰ºŠÖ": "下関",
}


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

    if "venue_name" not in work.columns:
        work["venue_name"] = ""

    if "venue" in work.columns:
        work["venue_name"] = work["venue_name"].where(work["venue_name"].astype(str).str.len() > 0, work["venue"].astype(str))

    work["venue_name"] = work["venue_name"].map(_normalize_venue_name)
    work.loc[work["venue_name"].eq(""), "venue_name"] = work.loc[work["venue_name"].eq(""), "race_id"].map(_venue_from_race_id)
    work.loc[work["venue_name"].eq(""), "venue_name"] = "不明"

    if "race_no" not in work.columns:
        work["race_no"] = pd.NA

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


def _bin_label(left: float, right: float, digits: int = 1, is_last: bool = False) -> str:
    if digits == 0:
        l_text = str(int(left))
        r_text = str(int(right)) if not is_last else f"{int(left)}+"
        return f"{l_text}-{r_text}" if not is_last else f"{l_text}+"
    left_text = f"{left:.1f}"
    if is_last:
        return f"{left_text}+"
    return f"{left_text}-{right:.1f}"


def _build_bins(edges: list[float], digits: int = 1) -> list[str]:
    labels: list[str] = []
    for idx in range(len(edges) - 1):
        labels.append(_bin_label(edges[idx], edges[idx + 1], digits=digits, is_last=idx == len(edges) - 2))
    return labels


def _assign_bins(series: pd.Series, edges: list[float], digits: int = 1) -> pd.Series:
    labels = _build_bins(edges, digits=digits)
    if len(labels) == 0:
        return pd.Series(["unknown"] * len(series), index=series.index)
    work = pd.to_numeric(series, errors="coerce")
    binned = pd.cut(work, bins=edges, labels=labels, include_lowest=True, right=True)
    return binned.astype(str).replace("nan", "unknown")


def _aggregate_bins(df: pd.DataFrame, value_col: str, edges: list[float], digits: int = 1) -> pd.DataFrame:
    work = df.copy()
    work = work.dropna(subset=[value_col, "settled_odds", "hit"]).copy()
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
    work = df.copy()
    work = work.dropna(subset=["venue_name", "settled_odds", "hit"]).copy()
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
    grouped["allowed"] = (grouped["count"] >= MIN_SAMPLE_COUNT) & (grouped["roi"] > MIN_ROI)
    grouped["dimension"] = "venue_name"
    grouped = grouped.rename(columns={"venue_name": "bin"})
    return grouped[["dimension", "bin", "count", "hits", "hit_rate", "avg_odds", "avg_settled_odds", "total_return", "roi", "allowed"]]


def _serialize_rows(df: pd.DataFrame) -> list[dict]:
    return json.loads(df.replace({pd.NA: None}).where(pd.notna(df), None).to_json(orient="records", force_ascii=False))


def _choose_prob_metric(df: pd.DataFrame) -> tuple[str, pd.DataFrame, dict]:
    candidates: list[tuple[str, pd.DataFrame, dict]] = []
    for metric in PROB_METRICS:
        if metric not in df.columns:
            continue
        table = _aggregate_bins(df, metric, PROB_EDGES, digits=1)
        allowed = table[table["allowed"]].copy()
        score = {
            "allowed_bin_count": int(len(allowed)),
            "allowed_volume": int(allowed["count"].sum()) if not allowed.empty else 0,
            "allowed_roi_mean": float(allowed["roi"].mean()) if not allowed.empty else 0.0,
            "max_roi": float(table["roi"].max()) if not table.empty else 0.0,
        }
        candidates.append((metric, table, score))

    if not candidates:
        empty = pd.DataFrame(columns=["dimension", "bin", "count", "hits", "hit_rate", "avg_odds", "avg_settled_odds", "total_return", "roi", "allowed"])
        return "first_place_prob", empty, {"allowed_bin_count": 0, "allowed_volume": 0, "allowed_roi_mean": 0.0, "max_roi": 0.0}

    candidates.sort(
        key=lambda item: (
            item[2]["allowed_volume"],
            item[2]["allowed_bin_count"],
            item[2]["allowed_roi_mean"],
            item[2]["max_roi"],
        ),
        reverse=True,
    )
    return candidates[0]


def main() -> None:
    df = _load_data()
    if df.empty:
        raise RuntimeError("no rows available for ROI filter analysis")

    prob_metric, prob_table, prob_score = _choose_prob_metric(df)
    odds_table = _aggregate_bins(df, "odds", ODDS_EDGES, digits=0)
    places_table = _aggregate_places(df)

    allowed_prob_bins = prob_table.loc[prob_table["allowed"], "bin"].tolist()
    allowed_odds_bins = odds_table.loc[odds_table["allowed"], "bin"].tolist()
    allowed_places = places_table.loc[places_table["allowed"], "bin"].tolist()

    report_rows = pd.concat([prob_table.assign(metric=prob_metric), odds_table.assign(metric="odds"), places_table.assign(metric="venue")], ignore_index=True)
    report_rows = report_rows[["metric", "dimension", "bin", "count", "hits", "hit_rate", "avg_odds", "avg_settled_odds", "total_return", "roi", "allowed"]]
    OUT_REPORT.parent.mkdir(parents=True, exist_ok=True)
    report_rows.to_csv(OUT_REPORT, index=False)

    payload = {
        "strategy_mode": "ROI_FILTER",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
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
        "report_csv": str(OUT_REPORT.relative_to(ROOT)),
    }

    OUT_RULES.parent.mkdir(parents=True, exist_ok=True)
    OUT_RULES.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(
        {
            "prob_metric": prob_metric,
            "allowed_prob_bins": allowed_prob_bins,
            "allowed_odds_bins": allowed_odds_bins,
            "allowed_places": allowed_places,
            "prob_metric_score": prob_score,
            "report_csv": str(OUT_REPORT.relative_to(ROOT)),
            "rules_json": str(OUT_RULES.relative_to(ROOT)),
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
