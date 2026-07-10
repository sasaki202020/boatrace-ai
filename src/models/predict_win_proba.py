import pandas as pd
import joblib
import json
import os
import glob
from pathlib import Path

class WinProbabilityPredictor:
    """
    学習済みモデルを用いて1着確率を予測する。
    """
    def __init__(self, config_path="config/model_config.json"):
        with open(config_path, "r") as f:
            self.config = json.load(f)
        
        model_path = self.config["output_paths"]["model_path"]
        if os.path.exists(model_path):
            model_bundle = joblib.load(model_path)
            if isinstance(model_bundle, dict) and "model" in model_bundle:
                self.model = model_bundle["model"]
                self.feature_columns = model_bundle.get("feature_columns", [])
                self.winsorize_bounds = model_bundle.get("winsorize_bounds", {})
            else:
                self.model = model_bundle
                self.feature_columns = []
                self.winsorize_bounds = {}
            self.model_loaded = True
        else:
            print(f"CRITICAL: Model file not found at {model_path}. Please run train_win_model.py first.")
            self.model_loaded = False
            self.winsorize_bounds = {}
        # Re-rank settings (offline validated)
        self.rerank_features = ["national_win_rate", "local_2ren_rate"]
        self.rerank_beta = 0.2

    @staticmethod
    def _rank_within_race(df: pd.DataFrame, prob_col: str) -> pd.Series:
        if "race_id" not in df.columns or prob_col not in df.columns:
            return pd.Series([pd.NA] * len(df), index=df.index, dtype="Int64")
        ranked = df.groupby("race_id")[prob_col].rank(method="first", ascending=False)
        return ranked.fillna(0).astype("Int64")

    def _apply_rerank(self, results: pd.DataFrame, source_df: pd.DataFrame) -> pd.DataFrame:
        if "race_id" not in results.columns or "win_proba_norm" not in results.columns:
            return results

        missing = [f for f in self.rerank_features if f not in source_df.columns]
        if missing:
            return results

        work = results.copy()
        # Add tiebreak features from source rows (same row order as results)
        for feat in self.rerank_features:
            work[feat] = pd.to_numeric(source_df[feat], errors="coerce")
            fmin = work[feat].min()
            fmax = work[feat].max()
            work[f"{feat}_scaled"] = (work[feat] - fmin) / (fmax - fmin + 1e-9)
            work[f"{feat}_scaled"] = work[f"{feat}_scaled"].fillna(0.0)

        work["rerank_score"] = pd.to_numeric(work["win_proba_norm"], errors="coerce").fillna(0.0)
        for feat in self.rerank_features:
            work["rerank_score"] = work["rerank_score"] + self.rerank_beta * work[f"{feat}_scaled"]

        # Keep output spec: overwrite win_proba_norm and renormalize per race
        work["win_proba_norm"] = work.groupby("race_id")["rerank_score"].transform(
            lambda x: x / x.sum() if x.sum() > 0 else 0.0
        )
        return work.drop(columns=[c for c in work.columns if c in self.rerank_features or c.endswith("_scaled") or c == "rerank_score"])

    def predict(self, feature_path):
        if not self.model_loaded:
            return None
        df = pd.read_csv(feature_path)
        
        # メタデータ列（学習に使わない列）の分離
        meta_cols = ["race_id", "lane", "date", "win_label", "finish_position"]
        available_meta = [c for c in meta_cols if c in df.columns]
        
        if not self.feature_columns:
            raise ValueError("Model bundle does not contain feature_columns")
        missing_cols = [c for c in self.feature_columns if c not in df.columns]
        if missing_cols:
            raise ValueError(f"Missing required feature columns for prediction: {missing_cols}")

        # Apply same winsorize bounds used in training
        for col, b in (self.winsorize_bounds or {}).items():
            if col not in df.columns:
                continue
            q01 = b.get("q01")
            q99 = b.get("q99")
            if q01 is None or q99 is None:
                continue
            df.loc[:, col] = pd.to_numeric(df[col], errors="coerce").clip(lower=float(q01), upper=float(q99))

        X = df[self.feature_columns]
        
        # 1. Raw Probability
        y_prob = self.model.predict_proba(X)[:, 1]
        
        results = df[available_meta].copy()
        results["model_proba_raw"] = y_prob
        results["win_proba_raw"] = y_prob
        if "race_id" in results.columns:
            results["model_win_proba_norm"] = results.groupby("race_id")["win_proba_raw"].transform(
                lambda x: x / x.sum() if x.sum() > 0 else 0
            )
            results["model_rank"] = self._rank_within_race(results, "model_win_proba_norm")
        else:
            results["model_win_proba_norm"] = results["win_proba_raw"]
            results["model_rank"] = pd.Series([pd.NA] * len(results), index=results.index, dtype="Int64")
        
        # 2. Race-level Normalization (Sum to 1.0)
        if "race_id" in results.columns:
            results["win_proba_norm"] = results.groupby("race_id")["win_proba_raw"].transform(
                lambda x: x / x.sum() if x.sum() > 0 else 0
            )
            # Inline re-ranking: win_proba_norm + beta * scaled(tiebreak features)
            results = self._apply_rerank(results, df)
            results["final_win_proba"] = results["win_proba_norm"]
            results["final_rank"] = self._rank_within_race(results, "final_win_proba")
            results["rerank_delta"] = (results["final_rank"] - results["model_rank"]).fillna(0).astype("Int64")
            results["rerank_applied"] = results["rerank_delta"].astype(float).ne(0.0)
        else:
            results["win_proba_norm"] = results["win_proba_raw"]
            results["final_win_proba"] = results["win_proba_norm"]
            results["final_rank"] = results["model_rank"]
            results["rerank_delta"] = pd.Series([0] * len(results), index=results.index, dtype="Int64")
            results["rerank_applied"] = False
            
        return results

if __name__ == "__main__":
    predictor = WinProbabilityPredictor()
    feature_files = glob.glob("data/features/*.csv")
    out_dir = Path("data/model_outputs")
    out_dir.mkdir(parents=True, exist_ok=True)
    
    for f in feature_files:
        if "summary" in f:
            continue
        preds = predictor.predict(f)
        if preds is None:
            continue
        in_name = Path(f).name
        output_name = in_name.replace("_features.csv", "_win_proba.csv")
        out_path = out_dir / output_name
        preds.to_csv(out_path, index=False)
        if "rerank_delta" in preds.columns:
            moved = int((pd.to_numeric(preds["rerank_delta"], errors="coerce").fillna(0) != 0).sum())
            max_delta = int(pd.to_numeric(preds["rerank_delta"], errors="coerce").abs().fillna(0).max())
            print(f"Predictions saved to {out_path.as_posix()} (rerank_changed={moved}, max_rank_delta={max_delta})")
        else:
            print(f"Predictions saved to {out_path.as_posix()}")
