"""当日予想CLI。

指定日・指定レース場の出走表を取得し、保存済みモデルで各艇の
勝率・予想着順・単勝期待値を表示する(コンソール + CSV保存)。

使用例:
    python predict_today.py --date 2026-06-12 --place 桐生
    python predict_today.py --date 2026-06-12 --place 1 --race 12
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent))

from src.data_processing.database import BoatRaceDatabase
from src.data_processing.preprocessor import Preprocessor
from src.data_processing.real_data_fetcher import RealDataFetcher, resolve_place, PLACE_NAMES
from src.features.feature_engineer import FeatureEngineer
from src.models.prediction_system import PredictionSystem

BASE_DIR = Path(__file__).parent
OUTPUT_COLUMNS = [
    "race_id", "race_date", "course_id", "race_number", "lane",
    "racer_id", "name", "grade", "win_odds",
    "pred_proba", "pred_rank", "expected_value",
]


def load_config() -> dict:
    with open(BASE_DIR / "config" / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_features_with_history(today_df: pd.DataFrame, history_df: pd.DataFrame,
                                config: dict) -> pd.DataFrame:
    """過去データに当日分を連結して特徴量を計算し、当日分のみ返す。

    時系列特徴量(shift(1)系)は履歴がないと計算できないため、
    DBの過去レースに当日出走表を後置して一括計算する。
    当日分の結果列は NaN のままなので、shift(1) によって
    当日結果が特徴量へ混入することはない。
    """
    pre = Preprocessor()
    combined = pd.concat([history_df, today_df], ignore_index=True)
    combined = pre.fit_transform(combined)
    fe = FeatureEngineer(rolling_windows=config["features"]["rolling_windows"])
    combined = fe.create_features(combined)
    today_ids = set(today_df["race_id"])
    return combined[combined["race_id"].isin(today_ids)].copy()


def format_race_table(race_df: pd.DataFrame) -> str:
    lines = []
    rno = int(race_df["race_number"].iloc[0])
    lines.append(f"--- {rno}R ---")
    lines.append(f"{'枠':>2} {'選手名':<10} {'級':>3} {'勝率':>7} {'予想':>4} {'単勝':>6} {'期待値':>7}")
    for _, r in race_df.sort_values("lane").iterrows():
        odds = f"{r['win_odds']:.1f}" if pd.notna(r["win_odds"]) else "  -"
        ev = f"{r['expected_value']:.2f}" if pd.notna(r["expected_value"]) else "   -"
        name = (r["name"] or "?")[:10]
        lines.append(
            f"{int(r['lane']):>2} {name:<10} {r['grade'] or '-':>3} "
            f"{r['pred_proba']:>7.1%} {int(r['pred_rank']):>3}位 {odds:>6} {ev:>7}"
        )
    return "\n".join(lines)


def save_empty_prediction(date: str, jcd: int, config: dict) -> Path:
    """開催なし/取得失敗時も後続CIが判定できる空CSVを残す。"""
    out_dir = BASE_DIR / config["output"]["predictions_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"prediction_{date.replace('-', '')}_{jcd:02d}.csv"
    pd.DataFrame(columns=OUTPUT_COLUMNS).to_csv(
        out_path, index=False, encoding="utf-8-sig")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="当日レース予想")
    parser.add_argument("--date", required=True, help="予想対象日 YYYY-MM-DD")
    parser.add_argument("--place", required=True, help="レース場(場名または場コード)")
    parser.add_argument("--race", type=int, default=None,
                        help="レース番号(省略時は1〜12R全レース)")
    parser.add_argument("--model", default=None, help="モデルファイルのパス")
    args = parser.parse_args()

    config = load_config()
    jcd = resolve_place(args.place)
    model_path = Path(args.model) if args.model else BASE_DIR / config["output"]["model_path"]
    if not model_path.exists():
        sys.exit(f"モデルが見つかりません: {model_path}\n"
                 "先に python main.py で学習・保存してください。")

    f = config["fetcher"]
    fetcher = RealDataFetcher(
        base_url=f["base_url"],
        cache_dir=str(BASE_DIR / config["data"]["raw_cache_dir"]),
        min_interval_seconds=f["min_interval_seconds"],
        max_retries=f["max_retries"],
        backoff_base_seconds=f["backoff_base_seconds"],
        user_agent=f["user_agent"],
    )

    print(f"{args.date} {PLACE_NAMES[jcd]} の出走表を取得中...")
    race_numbers = [args.race] if args.race else None
    today = fetcher.fetch_day(args.date, jcd, include_results=False,
                              race_numbers=race_numbers)
    if today.empty:
        out_path = save_empty_prediction(args.date, jcd, config)
        print(f"空CSV保存: {out_path}")
        sys.exit("出走表を取得できませんでした(開催なし、または取得失敗)。")

    db = BoatRaceDatabase(str(BASE_DIR / config["data"]["database_path"]))
    history = db.load_dataframe(end_date=args.date, with_results_only=True)
    history = history[history["race_date"] < args.date]
    if history.empty:
        print("警告: DBに過去データがありません。時系列特徴量は中立値になります。")

    features = build_features_with_history(today, history, config)
    system = PredictionSystem.load(str(model_path))
    pred = system.predict_races(features)
    pred["expected_value"] = pred["pred_proba"] * pred["win_odds"]

    print(f"\n===== {args.date} {PLACE_NAMES[jcd]} 予想 =====")
    for race_id in sorted(pred["race_id"].unique()):
        print(format_race_table(pred[pred["race_id"] == race_id]))
        print()

    out_dir = BASE_DIR / config["output"]["predictions_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"prediction_{args.date.replace('-', '')}_{jcd:02d}.csv"
    pred[OUTPUT_COLUMNS].sort_values(["race_number", "lane"]).to_csv(
        out_path, index=False, encoding="utf-8-sig")
    print(f"CSV保存: {out_path}")


if __name__ == "__main__":
    main()
