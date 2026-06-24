"""1着予測モデル: XGBoost + LightGBM + RandomForest のスタッキングアンサンブル。

- ベース学習器3種の予測確率を入力に、ロジスティック回帰のメタ学習器で統合。
- 学習/テスト分割は必ず時系列順(race_date 順で末尾を テストに使う)。
"""
from __future__ import annotations

import joblib
import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from xgboost import XGBClassifier

from src.features.feature_engineer import FEATURE_COLUMNS


def time_series_split(df: pd.DataFrame, test_ratio: float = 0.2) -> tuple[pd.DataFrame, pd.DataFrame]:
    """レース単位で時系列分割する(同一レースの艇が train/test に跨がらない)。"""
    race_order = (
        df[["race_id", "race_date"]]
        .drop_duplicates()
        .sort_values(["race_date", "race_id"])["race_id"]
        .tolist()
    )
    n_test = max(1, int(len(race_order) * test_ratio))
    test_ids = set(race_order[-n_test:])
    train = df[~df["race_id"].isin(test_ids)].copy()
    test = df[df["race_id"].isin(test_ids)].copy()
    return train, test


class PredictionSystem:
    """スタッキングアンサンブルによる1着予測。"""

    def __init__(self, config: dict | None = None):
        cfg = config or {}
        rs = cfg.get("random_state", 42)
        xgb_cfg = cfg.get("xgboost", {})
        lgbm_cfg = cfg.get("lightgbm", {})
        rf_cfg = cfg.get("random_forest", {})
        self.base_models = {
            "xgb": XGBClassifier(
                n_estimators=xgb_cfg.get("n_estimators", 300),
                max_depth=xgb_cfg.get("max_depth", 5),
                learning_rate=xgb_cfg.get("learning_rate", 0.05),
                eval_metric="logloss", random_state=rs, n_jobs=-1,
            ),
            "lgbm": LGBMClassifier(
                n_estimators=lgbm_cfg.get("n_estimators", 300),
                max_depth=lgbm_cfg.get("max_depth", 5),
                learning_rate=lgbm_cfg.get("learning_rate", 0.05),
                random_state=rs, n_jobs=-1, verbose=-1,
            ),
            "rf": RandomForestClassifier(
                n_estimators=rf_cfg.get("n_estimators", 200),
                max_depth=rf_cfg.get("max_depth", 8),
                random_state=rs, n_jobs=-1,
            ),
        }
        self.meta_model = LogisticRegression(max_iter=1000)
        self.feature_columns = list(FEATURE_COLUMNS)
        self.fitted = False

    # ------------------------------------------------------------------
    def fit(self, train_df: pd.DataFrame) -> "PredictionSystem":
        """時系列を保ったホールドアウトでメタ学習器を学習する。

        train の前半でベース学習器 → 後半のベース予測でメタ学習器を学習し、
        最後にベース学習器を train 全体で再学習する。
        """
        X = train_df[self.feature_columns].to_numpy(dtype=float)
        y = train_df["win"].to_numpy(dtype=int)

        holdout_start = int(len(X) * 0.75)
        X_a, y_a = X[:holdout_start], y[:holdout_start]
        X_b, y_b = X[holdout_start:], y[holdout_start:]

        meta_features = []
        for model in self.base_models.values():
            model.fit(X_a, y_a)
            meta_features.append(model.predict_proba(X_b)[:, 1])
        self.meta_model.fit(np.column_stack(meta_features), y_b)

        # ベース学習器は全trainで再学習して本番に備える
        for model in self.base_models.values():
            model.fit(X, y)
        self.fitted = True
        return self

    def predict_proba(self, df: pd.DataFrame) -> np.ndarray:
        """各行(=各艇)の1着確率を返す。"""
        if not self.fitted:
            raise RuntimeError("モデルが未学習です。fit() を先に呼んでください。")
        X = df[self.feature_columns].to_numpy(dtype=float)
        meta = np.column_stack(
            [m.predict_proba(X)[:, 1] for m in self.base_models.values()]
        )
        return self.meta_model.predict_proba(meta)[:, 1]

    def predict_races(self, df: pd.DataFrame) -> pd.DataFrame:
        """レース内で確率を正規化し、予想着順を付与した DataFrame を返す。"""
        out = df.copy()
        out["pred_proba_raw"] = self.predict_proba(df)
        out["pred_proba"] = out.groupby("race_id")["pred_proba_raw"].transform(
            lambda s: s / s.sum() if s.sum() > 0 else 1 / len(s)
        )
        out["pred_rank"] = (
            out.groupby("race_id")["pred_proba"].rank(method="first", ascending=False).astype(int)
        )
        return out

    # ------------------------------------------------------------------
    def evaluate(self, test_df: pd.DataFrame) -> dict:
        """1着的中率(レース単位)と AUC(艇単位)を返す。"""
        pred = self.predict_races(test_df)
        winners = pred[pred["pred_rank"] == 1]
        hit_rate = float(winners["win"].mean()) if len(winners) else 0.0
        auc = float(roc_auc_score(pred["win"], pred["pred_proba_raw"])) \
            if pred["win"].nunique() > 1 else float("nan")
        return {
            "n_races": int(pred["race_id"].nunique()),
            "hit_rate": hit_rate,
            "auc": auc,
            "random_baseline": 1 / 6,
        }

    # ------------------------------------------------------------------
    def save(self, path: str) -> None:
        joblib.dump(
            {
                "base_models": self.base_models,
                "meta_model": self.meta_model,
                "feature_columns": self.feature_columns,
            },
            path,
        )

    @classmethod
    def load(cls, path: str) -> "PredictionSystem":
        payload = joblib.load(path)
        obj = cls()
        obj.base_models = payload["base_models"]
        obj.meta_model = payload["meta_model"]
        obj.feature_columns = payload["feature_columns"]
        obj.fitted = True
        return obj
