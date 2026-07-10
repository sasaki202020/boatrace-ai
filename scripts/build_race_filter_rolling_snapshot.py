from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
from src.ingest.official_txt_parser import OfficialTxtParser


def _norm_key_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "date" in out.columns:
        out["date"] = out["date"].astype(str).str.strip()
    for col in ["jcd", "race_no"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce").astype("Int64")
            out = out.dropna(subset=[col]).copy()
            out[col] = out[col].astype(int).astype(str)
    return out


def load_daily_snapshot(day_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, str]:
    feat_path = day_dir / "today_features.csv"
    proba_path = day_dir / "today_win_proba.csv"
    date_token = day_dir.name.replace("-", "")
    result_csv = Path("data/raw/official/parsed") / f"K{date_token[2:]}.csv"
    result_txt = Path("data/raw/official/results") / f"K{date_token[2:]}.TXT"
    if not feat_path.exists() or not proba_path.exists():
        raise FileNotFoundError(f"missing daily snapshot files in {day_dir}")
    feat_df = pd.read_csv(feat_path)
    proba_df = pd.read_csv(proba_path)
    if result_csv.exists():
        result_df = pd.read_csv(result_csv)
        source = result_csv.name
    elif result_txt.exists():
        parser = OfficialTxtParser()
        parsed = parser.parse(str(result_txt), raw_kind="kse_txt")
        result_df = parsed["dataframe"].copy()
        source = result_txt.name
    else:
        raise FileNotFoundError(f"missing result csv/txt for {day_dir.name}")
    return feat_df, proba_df, result_df, source


def build_actual_results(result_df: pd.DataFrame) -> pd.DataFrame:
    required = {"date", "jcd", "race_no", "lane", "finish_position"}
    missing = required - set(result_df.columns)
    if missing:
        raise ValueError(f"result csv missing columns: {sorted(missing)}")

    df = _norm_key_frame(result_df)
    df["lane"] = pd.to_numeric(df["lane"], errors="coerce")
    df["finish_position_num"] = pd.to_numeric(df["finish_position"], errors="coerce")
    df["odds_trifecta_num"] = pd.to_numeric(df.get("odds_trifecta", 0.0), errors="coerce").fillna(0.0)
    df = df.dropna(subset=["lane", "finish_position_num"]).copy()
    df["lane"] = df["lane"].astype(int)
    df["finish_position_num"] = df["finish_position_num"].astype(int)

    rows: list[dict[str, object]] = []
    for (date, jcd, race_no), grp in df.groupby(["date", "jcd", "race_no"], sort=False):
        grp = grp.sort_values("finish_position_num")
        if len(grp) < 3:
            continue
        top3 = grp[grp["finish_position_num"].isin([1, 2, 3])].sort_values("finish_position_num")
        if len(top3) < 3:
            continue
        actual_trifecta = "-".join(top3["lane"].astype(int).astype(str).tolist())
        odds_value = float(grp.loc[grp["odds_trifecta_num"] > 0, "odds_trifecta_num"].head(1).sum())
        rows.append(
            {
                "date": str(date),
                "jcd": str(jcd),
                "race_no": str(race_no),
                "actual_trifecta": actual_trifecta,
                "result_available": True,
                "official_odds": odds_value,
                "settled_odds": odds_value,
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a multi-day snapshot for race filter analysis.")
    parser.add_argument("--daily-root", default="reports/daily")
    parser.add_argument("--out-dir", default="reports/race_filter_rolling_snapshot")
    parser.add_argument("--dates", nargs="*", default=None)
    args = parser.parse_args()

    daily_root = Path(args.daily_root)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    selected_dirs = []
    for day_dir in sorted(daily_root.iterdir()):
        if not day_dir.is_dir():
            continue
        if args.dates and day_dir.name not in set(args.dates):
            continue
        if (day_dir / "today_features.csv").exists() and (day_dir / "today_win_proba.csv").exists():
            selected_dirs.append(day_dir)

    if not selected_dirs:
        raise SystemExit("no daily snapshots found")

    feat_frames: list[pd.DataFrame] = []
    proba_frames: list[pd.DataFrame] = []
    result_frames: list[pd.DataFrame] = []
    manifest_days: list[dict[str, object]] = []
    skipped_days: list[dict[str, object]] = []
    for day_dir in selected_dirs:
        try:
            feat_df, proba_df, result_df, source_name = load_daily_snapshot(day_dir)
        except FileNotFoundError as exc:
            skipped_days.append({"date": day_dir.name, "reason": str(exc)})
            continue
        feat_frames.append(feat_df)
        proba_frames.append(proba_df)
        result_frames.append(result_df)
        manifest_days.append(
            {
                "date": day_dir.name,
                "features_rows": int(len(feat_df)),
                "proba_rows": int(len(proba_df)),
                "result_source": source_name,
                "result_rows": int(len(result_df)),
            }
        )

    selected_dates = {day["date"] for day in manifest_days}
    combined_features = _norm_key_frame(pd.concat(feat_frames, ignore_index=True))
    combined_proba = _norm_key_frame(pd.concat(proba_frames, ignore_index=True))

    race_meta = combined_features[["race_id", "date", "jcd", "race_no"]].drop_duplicates("race_id").copy()
    actual_results = build_actual_results(pd.concat(result_frames, ignore_index=True))
    actual_results = actual_results[actual_results["date"].isin(selected_dates)].copy()
    combined_backtest = race_meta.merge(
        actual_results,
        on=["date", "jcd", "race_no"],
        how="left",
    )
    combined_backtest["result_available"] = combined_backtest["result_available"].fillna(False)
    combined_backtest["official_odds"] = pd.to_numeric(combined_backtest["official_odds"], errors="coerce").fillna(0.0)
    combined_backtest["settled_odds"] = pd.to_numeric(combined_backtest["settled_odds"], errors="coerce").fillna(0.0)

    combined_features.to_csv(out_dir / "today_features.csv", index=False)
    combined_proba.to_csv(out_dir / "today_win_proba.csv", index=False)
    combined_backtest.to_csv(out_dir / "backtest_race_results.csv", index=False)

    manifest = {
        "daily_root": str(daily_root),
        "selected_dates": sorted(selected_dates),
        "days": manifest_days,
        "skipped_days": skipped_days,
        "combined_rows": {
            "features": int(len(combined_features)),
            "proba": int(len(combined_proba)),
            "backtest": int(len(combined_backtest)),
            "actual_results": int(len(actual_results)),
        },
        "output_dir": str(out_dir),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
