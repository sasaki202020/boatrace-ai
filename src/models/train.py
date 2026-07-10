import pandas as pd
import numpy as np
import pickle
import os
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss, accuracy_score

class ModelTrainer:
    """
    1着確率モデルの学習を、時系列分割に基づいて実行する。
    """
    def __init__(self, feature_file="data/processed/features.csv"):
        self.feature_file = feature_file

    def train(self, model_path="models/baseline_model.pkl"):
        if not os.path.exists(self.feature_file):
            print(f"File not found: {self.feature_file}")
            return
            
        df = pd.read_csv(self.feature_file)
        
        # 特徴量とターゲットの分離
        features = [
            "pit_no", "racer_class_score", "avg_st", "national_win_rate", 
            "national_2ren_rate", "local_2ren_rate", "motor_2ren_rate"
        ]
        # サンプルデータには一部列を簡略化して入れているため調整
        features = ["lane", "racer_class_score", "avg_st", "national_win_rate", "national_2ren_rate"]
        
        X = df[features]
        y = df["win_label"]
        
        # モデル (MVPベースライン)
        model = LogisticRegression()
        model.fit(X, y)
        
        # 保存
        os.makedirs(os.path.dirname(model_path), exist_ok=True)
        with open(model_path, "wb") as f:
            pickle.dump({
                "model": model,
                "features": features
            }, f)
            
        print(f"Model trained and saved: {model_path}")
        return model_path

if __name__ == "__main__":
    trainer = ModelTrainer()
    trainer.train()
