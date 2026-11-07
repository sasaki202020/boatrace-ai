import argparse
import json
from pathlib import Path

import pandas as pd
from sklearn.isotonic import IsotonicRegression

from src.strategy.probability_calibration_features import available_calibration_feature_columns


ROOT = Path(__file__).resolve().parents[2]
CALIBRATION_ARTIFACT = ROOT / "models" / "probability_calibrator.json"
DEFAULT_BACKTEST_RACES = ROOT / "reports" / "backtest_race_results.csv"
DEFAULT_BACKTEST_RACES_LATEST = ROOT / "reports" / "ops" / "backtest_race_results_latest.csv"
DEFAULT_FEATURE_GAP_PATH = ROOT / "reports" / "calibrated_ev_feature_gaps.csv"
DEFAULT_COMPARE_PATH = ROOT / "reports" / "calibrated_ev_summary.json"


def to_bool_series(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    return series.astype(str).str.strip().str.lower().isin({"true", "1", "yes", "y"})


def build_truth(backtest_df: pd.DataFrame) -> pd.DataFrame:
    required = {"race_id", "actual_trifecta", "official_odds", "result_available", "decision"}
    missing = required - set(backtest_df.columns)
    if missing:
        raise ValueError(f"backtest_race_results missing columns: {sorted(missing)}")

    truth = (
        backtest_df[["race_id", "actual_trifecta", "official_odds", "result_available", "decision"]]
        .drop_duplicates(subset=["race_id"])
        .copy()
    )
    truth["official_odds"] = pd.to_numeric(truth["official_odds"], errors="coerce")
    truth["result_available"] = to_bool_series(truth["result_available"])
    truth["decision"] = truth["decision"].astype(str).str.upper()
    return truth


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _resolve_backtest_path(path: Path) -> Path:
    if path.exists():
        return path
    if DEFAULT_BACKTEST_RACES_LATEST.exists():
        return DEFAULT_BACKTEST_RACES_LATEST
    return path


def attach_labels(ev_df: pd.DataFrame, truth_df: pd.DataFrame) -> pd.DataFrame:
    work = ev_df.merge(truth_df, on="race_id", how="left")
    work["approx_prob"] = pd.to_numeric(work["approx_prob"], errors="coerce")
    work["odds"] = pd.to_numeric(work["odds"], errors="coerce")
    work["hit"] = work["result_available"] & work["trifecta"].astype(str).eq(work["actual_trifecta"].astype(str))
    return work


def _calibration_gap_rows(work: pd.DataFrame, feature_columns: list[str]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    base_prob = pd.to_numeric(work["approx_prob"], errors="coerce").fillna(0.0)
    cal_prob = pd.to_numeric(work["calibrated_prob"], errors="coerce").fillna(0.0)
    hit = pd.to_numeric(work["hit"], errors="coerce").fillna(0).astype(int)

    for feature in feature_columns:
        if feature not in work.columns:
            continue
        series = pd.to_numeric(work[feature], errors="coerce")
        valid_mask = series.notna()
        if valid_mask.sum() == 0:
            continue
        feature_df = work.loc[valid_mask, ["race_id"]].copy()
        feature_df["feature"] = feature
        feature_df["feature_value"] = series.loc[valid_mask].astype(float)
        feature_df["approx_prob"] = base_prob.loc[valid_mask].astype(float).to_numpy()
        feature_df["calibrated_prob"] = cal_prob.loc[valid_mask].astype(float).to_numpy()
        feature_df["hit"] = hit.loc[valid_mask].astype(int).to_numpy()
        try:
            n_bins = min(5, int(feature_df["feature_value"].nunique()))
            if n_bins >= 2:
                feature_df["feature_bin"] = pd.qcut(
                    feature_df["feature_value"],
                    q=n_bins,
                    duplicates="drop",
                )
            else:
                feature_df["feature_bin"] = "all"
        except Exception:
            feature_df["feature_bin"] = "all"

        grouped = feature_df.groupby("feature_bin", dropna=False)
        for bin_label, grp in grouped:
            count = int(len(grp))
            if count <= 0:
                continue
            raw_gap = float((grp["approx_prob"] - grp["hit"]).mean())
            cal_gap = float((grp["calibrated_prob"] - grp["hit"]).mean())
            raw_abs_gap = float((grp["approx_prob"] - grp["hit"]).abs().mean())
            cal_abs_gap = float((grp["calibrated_prob"] - grp["hit"]).abs().mean())
            rows.append(
                {
                    "feature": feature,
                    "feature_bin": str(bin_label),
                    "count": count,
                    "avg_feature_value": float(grp["feature_value"].mean()),
                    "min_feature_value": float(grp["feature_value"].min()),
                    "max_feature_value": float(grp["feature_value"].max()),
                    "avg_raw_prob": float(grp["approx_prob"].mean()),
                    "avg_calibrated_prob": float(grp["calibrated_prob"].mean()),
                    "avg_hit_rate": float(grp["hit"].mean()),
                    "raw_gap": raw_gap,
                    "calibrated_gap": cal_gap,
                    "raw_abs_gap": raw_abs_gap,
                    "calibrated_abs_gap": cal_abs_gap,
                    "abs_gap_improvement": raw_abs_gap - cal_abs_gap,
                }
            )
    if not rows:
        return pd.DataFrame(
            columns=[
                "feature",
                "feature_bin",
                "count",
                "avg_feature_value",
                "min_feature_value",
                "max_feature_value",
                "avg_raw_prob",
                "avg_calibrated_prob",
                "avg_hit_rate",
                "raw_gap",
                "calibrated_gap",
                "raw_abs_gap",
                "calibrated_abs_gap",
                "abs_gap_improvement",
            ]
        )
    return pd.DataFrame(rows).sort_values(
        ["feature", "count", "abs_gap_improvement"],
        ascending=[True, False, False],
        kind="mergesort",
    ).reset_index(drop=True)


def _feature_gap_summary(feature_gap_rows: pd.DataFrame) -> list[dict[str, object]]:
    if feature_gap_rows.empty:
        return []
    grouped = feature_gap_rows.groupby("feature", dropna=False)
    summary_rows: list[dict[str, object]] = []
    for feature, grp in grouped:
        total = int(grp["count"].sum())
        if total <= 0:
            continue
        weight = grp["count"] / total
        summary_rows.append(
            {
                "feature": str(feature),
                "bin_count": int(len(grp)),
                "sample_count": total,
                "avg_raw_abs_gap": float((grp["raw_abs_gap"] * weight).sum()),
                "avg_calibrated_abs_gap": float((grp["calibrated_abs_gap"] * weight).sum()),
                "abs_gap_improvement": float((grp["abs_gap_improvement"] * weight).sum()),
                "avg_raw_gap": float((grp["raw_gap"] * weight).sum()),
                "avg_calibrated_gap": float((grp["calibrated_gap"] * weight).sum()),
            }
        )
    return sorted(summary_rows, key=lambda r: (r["abs_gap_improvement"], r["sample_count"]), reverse=True)


def fit_isotonic(work: pd.DataFrame) -> IsotonicRegression:
    train = work[work["result_available"]].dropna(subset=["approx_prob"]).copy()
    x = train["approx_prob"].to_numpy()
    y = train["hit"].astype(int).to_numpy()
    model = IsotonicRegression(y_min=0.0, y_max=1.0, increasing=True, out_of_bounds="clip")
    model.fit(x, y)
    return model


def evaluate_selection(selected: pd.DataFrame) -> dict:
    work = selected.copy()
    work["official_odds"] = pd.to_numeric(work["official_odds"], errors="coerce")
    work["odds"] = pd.to_numeric(work["odds"], errors="coerce")
    work["settled_odds"] = work["official_odds"].fillna(work["odds"])
    work["hit"] = to_bool_series(work["hit"])
    count = int(len(work))
    hit_count = int(work["hit"].sum())
    hit_rate = (hit_count / count) if count > 0 else None
    roi = float((work["hit"].astype(int) * work["settled_odds"].fillna(0.0)).sum() / count) if count > 0 else None
    avg_odds = float(work["settled_odds"].mean()) if count > 0 else None
    return {
        "count": count,
        "hit_count": hit_count,
        "hit_rate": hit_rate,
        "roi": roi,
        "avg_odds": avg_odds,
    }


def _calibration_sources(calibrator_artifact: dict | None) -> tuple[str, str, str]:
    base_prob_col = "approx_prob"
    calibration_method = "fallback"
    if isinstance(calibrator_artifact, dict):
        base_prob_col = str(calibrator_artifact.get("base_prob_col") or base_prob_col)
        calibration_method = str(calibrator_artifact.get("method") or calibration_method)
    selected_source = "calibrated_prob" if calibration_method.lower() != "fallback" else base_prob_col
    return base_prob_col, selected_source, calibration_method


def main():
    parser = argparse.ArgumentParser(description="Compare EV ranking with isotonic-calibrated probabilities")
    parser.add_argument("--ev-analysis", default="data/strategy_outputs/ev_analysis.csv")
    parser.add_argument("--backtest-races", default=str(DEFAULT_BACKTEST_RACES))
    parser.add_argument("--out-candidates", default="reports/calibrated_ev_candidates.csv")
    parser.add_argument("--out-comparison", default="reports/calibrated_ev_comparison.csv")
    parser.add_argument("--out-diff", default="reports/calibrated_ev_topdiff.csv")
    parser.add_argument("--out-summary", default="reports/calibrated_ev_summary.json")
    parser.add_argument("--out-feature-gaps", default=str(DEFAULT_FEATURE_GAP_PATH))
    parser.add_argument(
        "--require-official-odds",
        action="store_true",
        help="Limit evaluation to rows with official_odds present (focus on real-odds subset)",
    )
    args = parser.parse_args()

    ev_path = Path(args.ev_analysis)
    bt_path = _resolve_backtest_path(Path(args.backtest_races))
    if not ev_path.exists():
        raise FileNotFoundError(f"ev analysis not found: {ev_path}")
    if not bt_path.exists():
        raise FileNotFoundError(f"backtest races not found: {bt_path}")

    calibrator_artifact = _load_json(CALIBRATION_ARTIFACT)
    ev_df = pd.read_csv(ev_path)
    bt_df = pd.read_csv(bt_path)
    truth_df = build_truth(bt_df)
    work = attach_labels(ev_df, truth_df)

    if args.require_official_odds:
        work = work[work["official_odds"].notna()].copy()
        bt_df = bt_df[bt_df["official_odds"].notna()].copy()
        truth_df = build_truth(bt_df)

    feature_columns: list[str] = []
    if isinstance(calibrator_artifact, dict):
        feature_columns = [str(c) for c in calibrator_artifact.get("feature_columns", []) if str(c).strip()]
    if not feature_columns:
        feature_columns = available_calibration_feature_columns(work)

    valid = work[work["result_available"]].copy()
    if valid.empty:
        out_candidates = Path(args.out_candidates)
        out_comp = Path(args.out_comparison)
        out_diff = Path(args.out_diff)
        out_summary = Path(args.out_summary)
        out_feature_gaps = Path(args.out_feature_gaps)
        for p in [out_candidates, out_comp, out_diff, out_summary, out_feature_gaps]:
            p.parent.mkdir(parents=True, exist_ok=True)
        raw_source, selected_source, calibration_method = _calibration_sources(calibrator_artifact if isinstance(calibrator_artifact, dict) else None)
        empty_candidates = work[["race_id", "trifecta", "approx_prob", "odds"]].copy()
        empty_candidates["calibrated_prob"] = empty_candidates["approx_prob"]
        empty_candidates["ev_old"] = empty_candidates["approx_prob"] * empty_candidates["odds"]
        empty_candidates["ev_cal"] = empty_candidates["approx_prob"] * empty_candidates["odds"]
        empty_candidates["hit"] = work["hit"]
        empty_candidates["result_available"] = work["result_available"]
        empty_candidates["official_odds"] = work["official_odds"]
        empty_candidates["decision"] = work["decision"]
        empty_candidates["risk_flag"] = work["risk_flag"]
        candidate_cols = [
            "race_id",
            "trifecta",
            "approx_prob",
            "calibrated_prob",
            "odds",
            "ev_old",
            "ev_cal",
            "hit",
            "result_available",
            "official_odds",
            "decision",
            "risk_flag",
        ]
        empty_candidates[candidate_cols].to_csv(out_candidates, index=False)
        pd.DataFrame(
            columns=[
                "scope",
                "method",
                "count",
                "hit_count",
                "hit_rate",
                "roi",
                "avg_odds",
            ]
        ).to_csv(out_comp, index=False)
        pd.DataFrame(
            columns=[
                "race_id",
                "old_top_trifecta",
                "ev_old",
                "approx_prob",
                "calibrated_prob",
                "cal_top_trifecta",
                "ev_cal",
                "cal_top_approx_prob",
                "cal_top_calibrated_prob",
                "changed",
            ]
        ).to_csv(out_diff, index=False)
        pd.DataFrame(
            columns=[
                "feature",
                "feature_bin",
                "count",
                "avg_feature_value",
                "min_feature_value",
                "max_feature_value",
                "avg_raw_prob",
                "avg_calibrated_prob",
                "avg_hit_rate",
                "raw_gap",
                "calibrated_gap",
                "raw_abs_gap",
                "calibrated_abs_gap",
                "abs_gap_improvement",
            ]
        ).to_csv(out_feature_gaps, index=False)
        summary = {
            "status": "NO_RESULTS",
            "method": "isotonic_regression",
            "default_probability_path": {
                "raw_source": raw_source,
                "selected_source": selected_source,
                "calibration_method": calibration_method,
                "base_prob_col": raw_source,
            },
            "input_rows": int(len(work)),
            "result_available_races": 0,
            "top_pick_changed_races": 0,
            "top_pick_changed_rate": None,
            "comparison": [],
            "feature_columns": feature_columns,
            "feature_gap_summary": [],
            "feature_gap_table_path": str(out_feature_gaps),
            "comparison_table_path": str(out_comp),
            "topdiff_table_path": str(out_diff),
            "note": "result_available rows were not available; comparison emitted as empty report",
        }
        out_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"candidates saved: {out_candidates}")
        print(f"comparison saved: {out_comp}")
        print(f"top-diff saved: {out_diff}")
        print(f"feature-gap saved: {out_feature_gaps}")
        print(f"summary saved: {out_summary}")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    model = fit_isotonic(work)
    work["calibrated_prob"] = model.predict(work["approx_prob"].fillna(0.0).to_numpy())
    work["ev_old"] = work["approx_prob"] * work["odds"]
    work["ev_cal"] = work["calibrated_prob"] * work["odds"]
    work["raw_abs_gap"] = (work["approx_prob"] - work["hit"].astype(int)).abs()
    work["calibrated_abs_gap"] = (work["calibrated_prob"] - work["hit"].astype(int)).abs()

    old_top = valid.sort_values(["race_id", "ev_old"], ascending=[True, False]).groupby("race_id", as_index=False).first()
    cal_top = valid.sort_values(["race_id", "ev_cal"], ascending=[True, False]).groupby("race_id", as_index=False).first()

    buy_races = set(truth_df[truth_df["decision"] == "BUY"]["race_id"].astype(str).tolist())
    old_top_buy = old_top[old_top["race_id"].astype(str).isin(buy_races)].copy()
    cal_top_buy = cal_top[cal_top["race_id"].astype(str).isin(buy_races)].copy()

    comparison_rows = [
        {"scope": "all_result_available_races", "method": "old_ev", **evaluate_selection(old_top)},
        {"scope": "all_result_available_races", "method": "calibrated_ev", **evaluate_selection(cal_top)},
        {"scope": "current_buy_races", "method": "old_ev", **evaluate_selection(old_top_buy)},
        {"scope": "current_buy_races", "method": "calibrated_ev", **evaluate_selection(cal_top_buy)},
    ]
    comparison_df = pd.DataFrame(comparison_rows)

    diff = old_top[["race_id", "trifecta", "ev_old", "approx_prob", "calibrated_prob"]].rename(
        columns={"trifecta": "old_top_trifecta"}
    ).merge(
        cal_top[["race_id", "trifecta", "ev_cal", "approx_prob", "calibrated_prob"]].rename(
            columns={
                "trifecta": "cal_top_trifecta",
                "approx_prob": "cal_top_approx_prob",
                "calibrated_prob": "cal_top_calibrated_prob",
            }
        ),
        on="race_id",
        how="inner",
    )
    diff["changed"] = diff["old_top_trifecta"].astype(str) != diff["cal_top_trifecta"].astype(str)

    feature_gap_rows = _calibration_gap_rows(valid, feature_columns)
    feature_gap_summary = _feature_gap_summary(feature_gap_rows)

    out_candidates = Path(args.out_candidates)
    out_comp = Path(args.out_comparison)
    out_diff = Path(args.out_diff)
    out_summary = Path(args.out_summary)
    out_feature_gaps = Path(args.out_feature_gaps)
    for p in [out_candidates, out_comp, out_diff, out_summary, out_feature_gaps]:
        p.parent.mkdir(parents=True, exist_ok=True)

    candidate_cols = [
        "race_id", "trifecta", "approx_prob", "calibrated_prob", "odds", "ev_old", "ev_cal",
        "hit", "result_available", "official_odds", "decision", "risk_flag"
    ]
    work[candidate_cols].to_csv(out_candidates, index=False)
    comparison_df.to_csv(out_comp, index=False)
    diff.to_csv(out_diff, index=False)
    feature_gap_rows.to_csv(out_feature_gaps, index=False)

    base_prob_col, selected_probability_source, calibration_method = _calibration_sources(calibrator_artifact if isinstance(calibrator_artifact, dict) else None)

    summary = {
        "method": "isotonic_regression",
        "default_probability_path": {
            "raw_source": base_prob_col,
            "selected_source": selected_probability_source,
            "calibration_method": calibration_method,
            "base_prob_col": base_prob_col,
        },
        "input_rows": int(len(work)),
        "result_available_races": int(valid["race_id"].nunique()),
        "top_pick_changed_races": int(diff["changed"].sum()),
        "top_pick_changed_rate": float(diff["changed"].mean()) if len(diff) else None,
        "comparison": comparison_rows,
        "feature_columns": feature_columns,
        "feature_gap_summary": feature_gap_summary,
        "feature_gap_table_path": str(out_feature_gaps),
        "comparison_table_path": str(out_comp),
        "topdiff_table_path": str(out_diff),
    }
    out_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"candidates saved: {out_candidates}")
    print(f"comparison saved: {out_comp}")
    print(f"top-diff saved: {out_diff}")
    print(f"feature-gap saved: {out_feature_gaps}")
    print(f"summary saved: {out_summary}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
