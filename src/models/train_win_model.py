# -*- coding: utf-8 -*-
import pandas as pd
import joblib
import json
import os
from datetime import datetime, timezone
from sklearn.linear_model import LogisticRegression, SGDClassifier
from sklearn.preprocessing import StandardScaler
from src.models.time_split import time_series_split
from src.models.metrics import compute_all_metrics
from pathlib import Path

class WinModelTrainer:
    """
    1着確率予測モデルを学習する。
    """
    def __init__(self, config_path=None):
        script_dir = Path(__file__).parent.absolute()
        base_dir = script_dir.parent.parent # boatrace-ai-mvp
        
        if config_path is None:
            config_path = base_dir / "config" / "model_config.json"
        
        with open(config_path, "r") as f:
            self.config = json.load(f)
            
        registry_path = base_dir / "config" / "feature_registry.json"
        with open(registry_path, "r", encoding="utf-8") as f:
            self.feature_registry = json.load(f)
            
        self.meta_cols = ["race_id", "lane", "date"]
        self.forbidden_cols = set(self.feature_registry.get("blocked", [])) | {"win_label"}
        self.winsorize_targets = ["boat_2ren_rate", "win_rate_diff_to_avg", "motor_2ren_rate"]
        self.large_data_threshold = int(self.config.get("large_data_threshold", 250_000))
        self.base_dir = base_dir

    def _select_feature_columns(self, feature_df: pd.DataFrame):
        candidate_cols = [
            c for c in feature_df.columns
            if c not in self.meta_cols and c not in self.forbidden_cols
        ]
        selected_cols = []
        for col in candidate_cols:
            if pd.api.types.is_numeric_dtype(feature_df[col]):
                selected_cols.append(col)
        return selected_cols

    def _save_metrics_with_history(self, metrics: dict):
        metrics_path = self.base_dir / self.config["output_paths"]["metrics_path"]
        history_dir = self.base_dir / self.config["output_paths"].get("metrics_history_dir", "metrics/history")
        snapshot_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        snapshot_path = history_dir / f"test_metrics_{snapshot_id}.json"

        metrics_path.parent.mkdir(parents=True, exist_ok=True)
        history_dir.mkdir(parents=True, exist_ok=True)

        with open(metrics_path, "w", encoding="utf-8") as f:
            json.dump(metrics, f, indent=4, ensure_ascii=False)
        with open(snapshot_path, "w", encoding="utf-8") as f:
            json.dump(metrics, f, indent=4, ensure_ascii=False)

        return str(metrics_path), str(snapshot_path)

    def _build_model(self, n_train_rows: int):
        from sklearn.pipeline import make_pipeline
        from sklearn.impute import SimpleImputer

        if n_train_rows >= self.large_data_threshold:
            model = make_pipeline(
                SimpleImputer(strategy="mean"),
                StandardScaler(),
                SGDClassifier(loss="log_loss", max_iter=2000, tol=1e-3, random_state=42, class_weight="balanced")
            )
            name = "SGDClassifier"
        else:
            model = make_pipeline(
                SimpleImputer(strategy="mean"),
                StandardScaler(),
                LogisticRegression(max_iter=5000, solver="saga", random_state=42, class_weight="balanced")
            )
            name = "LogisticRegression"
        return model, name

    def train(self, feature_path, label_path):
        feature_df = pd.read_csv(feature_path, low_memory=False)
        label_df = pd.read_csv(label_path, low_memory=False)

        if "finish_position" in label_df.columns:
            finish_num = pd.to_numeric(label_df["finish_position"], errors="coerce")
            label_df["win_label"] = (finish_num == 1).astype(int)

        key_cols = [col for col in self.meta_cols if col in feature_df.columns and col in label_df.columns]
        if len(key_cols) == len(self.meta_cols):
            label_cols = []
            for col in [*key_cols, self.config["time_split_col"], "win_label"]:
                if col in label_df.columns and col not in label_cols:
                    label_cols.append(col)
            merged = feature_df.merge(label_df[label_cols], on=key_cols, how="inner")
            feature_df = merged
            label_df = merged
        
        feature_cols = self._select_feature_columns(feature_df)
        X = feature_df[feature_cols]
        y = label_df["win_label"]
        dates = label_df[self.config["time_split_col"]]

        X_train, X_test, y_train, y_test = time_series_split(
            X, y, dates, test_days=self.config["test_size_days"]
        )

        model, model_name = self._build_model(len(X_train))
        model.fit(X_train, y_train)
        
        # Save
        model_out = self.base_dir / self.config["output_paths"]["model_path"]
        model_out.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"model": model, "feature_columns": feature_cols, "model_name": model_name}, model_out)
        
        y_prob = model.predict_proba(X_test)[:, 1]
        metrics = compute_all_metrics(y_test, y_prob)
        self._save_metrics_with_history(metrics)
        print(f"Model trained and saved. Rows: {len(X_train)}")
        return metrics

if __name__ == "__main__":
    trainer = WinModelTrainer()
    script_dir = Path(__file__).parent.absolute()
    base_dir = script_dir.parent.parent
    feat_path = base_dir / "data/features/train_features.csv"
    lab_path = base_dir / "data/processed/historical_races.csv"
    if feat_path.exists() and lab_path.exists():
        trainer.train(str(feat_path), str(lab_path))
