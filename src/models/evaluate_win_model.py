import pandas as pd
import json
import os
from datetime import datetime
from sklearn.metrics import log_loss, roc_auc_score

def _resolve_metrics_snapshot_id():
    run_id = os.environ.get("RUN_ID")
    if run_id:
        return str(run_id).replace(" ", "_").replace(":", "-")
    return datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

def _save_metrics_with_history(metrics, output_path, history_dir="metrics/history"):
    snapshot_id = _resolve_metrics_snapshot_id()
    snapshot_path = os.path.join(history_dir, f"test_metrics_{snapshot_id}.json")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    os.makedirs(history_dir, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=4, ensure_ascii=False)
    with open(snapshot_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=4, ensure_ascii=False)

    return snapshot_path

def calculate_metrics(test_results_path, output_path):
    """
    テスト予測結果から精度指標を算出する。
    """
    df = pd.read_csv(test_results_path)
    y_true = df["actual_win"]
    y_prob = df["win_proba_raw"]
    
    metrics = {
        "log_loss": log_loss(y_true, y_prob),
        "auc_roc": roc_auc_score(y_true, y_prob)
    }
    
    # Top-1 Accuracy (最も確率が高い艇が実際に1着だった割合)
    # race_id ごとのTop-1を計算するにはデータの形式調整が必要だが、
    # 簡易版として binary accuracy (0.5閾値) ではなく、モデルの汎用性能を出す
    
    snapshot_path = _save_metrics_with_history(metrics, output_path)
        
    print(f"Metrics saved to {output_path}")
    print(f"Metrics history saved to {snapshot_path}")
    return metrics

if __name__ == "__main__":
    if os.path.exists("data/model_outputs/test_predictions.csv"):
        calculate_metrics(
            "data/model_outputs/test_predictions.csv",
            "data/model_outputs/test_metrics.json"
        )
