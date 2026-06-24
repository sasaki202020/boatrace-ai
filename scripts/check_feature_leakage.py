"""特徴量リーク検証スクリプト。

モデルの FEATURE_COLUMNS に未来情報(結果情報)が混入していないか確認する。

使い方:
    python scripts/check_feature_leakage.py

DBパスは config/config.yaml の data.database_path から読む。
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from src.data_processing.database import BoatRaceDatabase
from src.data_processing.preprocessor import Preprocessor
from src.features.feature_engineer import FeatureEngineer, FEATURE_COLUMNS

# ------------------------------------------------------------------
# 結果列(レース終了後にしか判明しない情報) → FEATURE_COLUMNS に入ってはいけない
# ------------------------------------------------------------------
FORBIDDEN_COLUMNS = {"win", "finish_position", "place", "show", "start_time"}

# ------------------------------------------------------------------
# shift(1)を通しているはずの時系列特徴量
# ------------------------------------------------------------------
SHIFT1_FEATURES = {
    "win_rate_3", "win_rate_5", "win_rate_10",
    "show_rate_5", "avg_finish_5", "avg_st_5",
    "lane_win_rate", "course_win_rate",
}


def run_checks(db_path: Path) -> int:
    """特徴量リーク検証を実行する。

    問題が見つかれば 1 を返す。正常なら 0 を返す。
    DBファイルが存在しない or 空の場合は FileNotFoundError を送出する。
    """
    if not db_path.exists():
        raise FileNotFoundError(
            f"DBが見つかりません: {db_path}\n"
            "       check_feature_leakage.py はDBに実データが必要です。\n"
            "       まず main.py --source real --fetch でデータを取得してください。"
        )

    issues = 0

    # ------------------------------------------------------------------
    # 1. DB ロード
    # ------------------------------------------------------------------
    print("=" * 60)
    print("【DBロード】")
    db = BoatRaceDatabase(str(db_path))
    raw = db.load_dataframe(with_results_only=True)
    if raw.empty:
        raise FileNotFoundError(
            "DBに結果付きレコードがありません。\n"
            "       まず main.py --source real --fetch でデータを取得してください。"
        )
    n_races = raw["race_id"].nunique()
    print(f"  レース数: {n_races:,}  行数: {len(raw):,}")

    # ------------------------------------------------------------------
    # 2. 前処理 + 特徴量生成
    # ------------------------------------------------------------------
    print()
    print("【前処理・特徴量生成】")
    pre = Preprocessor()
    df = pre.fit_transform(raw)
    fe = FeatureEngineer()
    df = fe.create_features(df)
    print(f"  処理後 行数: {len(df):,}  FEATURE_COLUMNS 数: {len(FEATURE_COLUMNS)}")

    # ------------------------------------------------------------------
    # 3. FEATURE_COLUMNS に禁止列が入っていないか
    # ------------------------------------------------------------------
    print()
    print("【禁止列チェック(結果情報が特徴量に混入していないか)】")
    leaked = FORBIDDEN_COLUMNS & set(FEATURE_COLUMNS)
    if leaked:
        print(f"  [NG] 禁止列が FEATURE_COLUMNS に含まれています: {sorted(leaked)}")
        print("       shift(1) を通さずに結果情報を使っている可能性があります。")
        issues += 1
    else:
        for col in sorted(FORBIDDEN_COLUMNS):
            print(f"  [OK] {col:<20} FEATURE_COLUMNS に含まれていない")

    # exhibition_time は出走表・直前情報で公開される → 使用正当
    print()
    if "exhibition_time" in FEATURE_COLUMNS:
        print("  [OK] exhibition_time: レース前に公開される情報のため直接使用は正当")

    # ------------------------------------------------------------------
    # 4. shift(1)系特徴量がデータフレームに存在するか
    # ------------------------------------------------------------------
    print()
    print("【shift(1)系特徴量の存在確認】")
    cols_in_df = set(df.columns)
    for feat in sorted(SHIFT1_FEATURES):
        if feat in cols_in_df:
            print(f"  [OK] {feat}")
        else:
            print(f"  [NG] {feat} が見つかりません ← 特徴量生成に問題がある可能性")
            issues += 1

    # ------------------------------------------------------------------
    # 5. 簡易時系列整合性チェック
    #    win_rate_3 は「過去3レースの1着率」= 当該レースの win とは別の値のはず。
    #    完全一致率が高いと shift(1) が効いていない疑いがある。
    # ------------------------------------------------------------------
    print()
    print("【簡易時系列整合性チェック】")
    experienced = df[df["races_count"] >= 5].copy()
    if len(experienced) < 10:
        print(
            f"  データ不足のためスキップ"
            f"(races_count >= 5 の行が {len(experienced)} 件、最低10件必要)"
        )
    else:
        match_rate = (
            experienced["win_rate_3"].round(4) == experienced["win"].astype(float).round(4)
        ).mean()
        print(f"  win_rate_3 と win の完全一致率: {match_rate:.4f}")
        if match_rate > 0.5:
            print(
                "  [WARN] 一致率が高すぎます。"
                "shift(1)が正しく機能していない可能性があります。"
            )
            issues += 1
        else:
            print("  [OK] 一致率は低く、shift(1)が正常に機能していると推定されます。")

    # ------------------------------------------------------------------
    # 6. 結果サマリ
    # ------------------------------------------------------------------
    print()
    print("=" * 60)
    if issues:
        print(f"[FAIL] {issues} 件の問題を検出しました。")
    else:
        print("[OK] 特徴量リーク検証完了。問題なし。")
    return issues


def main() -> None:
    config_path = _ROOT / "config" / "config.yaml"
    if not config_path.exists():
        print(f"[ERROR] config.yaml が見つかりません: {config_path}", file=sys.stderr)
        sys.exit(1)

    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    db_path = _ROOT / config["data"]["database_path"]

    try:
        sys.exit(run_checks(db_path))
    except FileNotFoundError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
