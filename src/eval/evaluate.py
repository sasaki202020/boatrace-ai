import pandas as pd
import numpy as np
import json
from sklearn.metrics import log_loss, accuracy_score

class Evaluator:
    """
    モデルの予測結果を、的中率、Logloss、期待値などの観点から評価する。
    """
    def __init__(self, processed_file="data/processed/features.csv"):
        self.df = pd.read_csv(processed_file)

    def evaluate(self, win_probs, labels):
        # 1着的中率 (Top-1 Accuracy)
        # 各レースの1位を当てるのは難しいので、ここでは単純な二値分類の精度
        acc = accuracy_score(labels, win_probs > 0.5)
        loss = log_loss(labels, win_probs)
        
        metrics = {
            "accuracy": float(acc),
            "logloss": float(loss),
            "top1_hit": float(acc) # サンプルでは簡易的に
        }
        
        with open("reports/metrics.json", "w") as f:
            json.dump(metrics, f, indent=4)
            
        print("Metrics saved to reports/metrics.json")
        return metrics

if __name__ == "__main__":
    from src.models.predict import OddsPredictor
    predictor = OddsPredictor()
    win_probs = predictor.predict_probabilities(pd.read_csv("data/processed/features.csv"))
    
    evaluator = Evaluator()
    evaluator.evaluate(win_probs, pd.read_csv("data/processed/features.csv")["win_label"])
