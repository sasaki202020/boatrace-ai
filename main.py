"""競艇AI予想システム 一括実行スクリプト。

学習 → 予測 → ROI検証 → モデル保存 をまとめて実行する。

使用例:
    python main.py --quick                 # ダミーデータで高速動作確認
    python main.py --source dummy          # ダミーデータでフル実行
    python main.py --source real           # DB内の実データで実行
    python main.py --source real --fetch --start 2026-05-01 --end 2026-05-31 --places 桐生 住之江
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent))

from src.data_processing.database import BoatRaceDatabase
from src.data_processing.dummy_generator import DummyDataGenerator
from src.data_processing.preprocessor import Preprocessor
from src.evaluation.backtester import Backtester
from src.features.feature_engineer import FeatureEngineer
from src.models.prediction_system import PredictionSystem, time_series_split

BASE_DIR = Path(__file__).parent


def load_config() -> dict:
    with open(BASE_DIR / "config" / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_data(args, config) -> "pd.DataFrame":
    if args.source == "dummy":
        d = config["data"]["dummy"]
        n_races = 200 if args.quick else d["n_races"]
        gen = DummyDataGenerator(n_races=n_races, n_racers=d["n_racers"], seed=d["seed"])
        print(f"ダミーデータ生成: {n_races}レース")
        return gen.generate()

    db = BoatRaceDatabase(str(BASE_DIR / config["data"]["database_path"]))
    if args.fetch:
        from src.data_processing.real_data_fetcher import RealDataFetcher
        f = config["fetcher"]
        fetcher = RealDataFetcher(
            base_url=f["base_url"],
            cache_dir=str(BASE_DIR / config["data"]["raw_cache_dir"]),
            min_interval_seconds=f["min_interval_seconds"],
            max_retries=f["max_retries"],
            backoff_base_seconds=f["backoff_base_seconds"],
            user_agent=f["user_agent"],
        )
        if not (args.start and args.end and args.places):
            sys.exit("--fetch には --start/--end/--places が必要です")
        print(f"実データ取得: {args.start}〜{args.end} {args.places}")
        total_saved = 0
        for d in pd.date_range(args.start, args.end):
            for place in args.places:
                day_df = fetcher.fetch_day(d.strftime("%Y-%m-%d"), place)
                if not day_df.empty:
                    saved = db.save_dataframe(day_df)
                    total_saved += saved
                    print(f"  {d.strftime('%Y-%m-%d')} {place}: {saved}行保存")
        print(f"DBへ保存(合計): {total_saved}行")

    df = db.load_dataframe(with_results_only=True)
    print(f"DBから読込: {df['race_id'].nunique() if len(df) else 0}レース / {len(df)}行")
    if df.empty:
        sys.exit("DBに実データがありません。--fetch で取得するか、"
                 "fetcher で事前にデータを収集してください。")
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description="競艇AI予想システム")
    parser.add_argument("--source", choices=["dummy", "real"], default="dummy",
                        help="データソース(既定: dummy)")
    parser.add_argument("--quick", action="store_true",
                        help="少量データ・軽量モデルで高速動作確認")
    parser.add_argument("--fetch", action="store_true",
                        help="実データを公式サイトから取得してDBへ保存(--source real 用)")
    parser.add_argument("--start", help="取得開始日 YYYY-MM-DD")
    parser.add_argument("--end", help="取得終了日 YYYY-MM-DD")
    parser.add_argument("--places", nargs="+", help="取得対象レース場(場名または場コード)")
    args = parser.parse_args()

    config = load_config()
    if args.quick:
        for m in ("xgboost", "lightgbm", "random_forest"):
            config["model"][m]["n_estimators"] = 50

    # 1) データ読込
    raw = load_data(args, config)

    # 2) 前処理 + 特徴量
    pre = Preprocessor()
    df = pre.fit_transform(raw)
    fe = FeatureEngineer(rolling_windows=config["features"]["rolling_windows"])
    df = fe.create_features(df)

    # 3) 時系列分割 → 学習
    train, test = time_series_split(df, test_ratio=config["model"]["test_ratio"])
    print(f"学習: {train['race_id'].nunique()}レース / テスト: {test['race_id'].nunique()}レース")
    system = PredictionSystem(config["model"])
    system.fit(train)

    # 4) 評価
    metrics = system.evaluate(test)
    print("\n=== テスト評価 ===")
    print(f"対象レース数 : {metrics['n_races']}")
    print(f"1着的中率    : {metrics['hit_rate']:.1%}(ランダム基準 {metrics['random_baseline']:.1%})")
    print(f"AUC          : {metrics['auc']:.3f}")

    # 5) 回収率バックテスト
    bt_cfg = config["backtest"]
    backtester = Backtester(unit_stake=bt_cfg["unit_stake"],
                            ev_threshold=bt_cfg["ev_threshold"],
                            confidence_threshold=bt_cfg["confidence_threshold"])
    pred = system.predict_races(test)
    print("\n" + Backtester.format_report(backtester.run(pred)))

    # 6) モデル保存
    model_path = BASE_DIR / config["output"]["model_path"]
    model_path.parent.mkdir(parents=True, exist_ok=True)
    system.save(str(model_path))
    print(f"\nモデル保存: {model_path}")


if __name__ == "__main__":
    main()
