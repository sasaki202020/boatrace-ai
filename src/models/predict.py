import pandas as pd
import numpy as np
import pickle
import json
import os

class OddsPredictor:
    """
    保存済みモデルを用いて1着確率を出し、3連単期待値を計算する。
    """
    def __init__(self, model_path="models/baseline_model.pkl"):
        with open(model_path, "rb") as f:
            data = pickle.load(f)
            self.model = data["model"]
            self.features = data["features"]

    def predict_probabilities(self, df):
        X = df[self.features]
        probs = self.model.predict_proba(X)[:, 1] # 1着になる確率
        return probs

    def calculate_triplet_probabilities(self, probs):
        """
        1着確率から3連単(1-2-3)確率を計算（簡易案分）
        """
        tr_probs = []
        n = len(probs)
        for i in range(n):
            for j in range(n):
                if i == j: continue
                p2_adj = probs[j] / (1 - probs[i])
                for k in range(n):
                    if k == i or k == j: continue
                    p3_adj = probs[k] / (1 - probs[i] - probs[j])
                    
                    tr_probs.append({
                        "combo": f"{i+1}-{j+1}-{k+1}",
                        "prob": probs[i] * p2_adj * p3_adj
                    })
        return tr_probs

if __name__ == "__main__":
    # テスト
    df = pd.read_csv("data/processed/features.csv")
    predictor = OddsPredictor()
    win_probs = predictor.predict_probabilities(df)
    print("Win Probabilities:", win_probs)
    
    triplet_probs = predictor.calculate_triplet_probabilities(win_probs)
    print("Top triplet combos:", sorted(triplet_probs, key=lambda x: x['prob'], reverse=True)[:5])
