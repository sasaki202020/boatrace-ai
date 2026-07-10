from sklearn.metrics import log_loss, roc_auc_score, accuracy_score

def compute_all_metrics(y_true, y_prob):
    """
    モデル評価に必要な全メトリクスを計算。
    """
    y_pred = (y_prob > 0.5).astype(int)
    metrics = {
        "log_loss": float(log_loss(y_true, y_prob, labels=[0, 1])),
        "accuracy": float(accuracy_score(y_true, y_pred)),
    }

    if len(set(y_true)) > 1:
        metrics["auc_roc"] = float(roc_auc_score(y_true, y_prob))
    else:
        metrics["auc_roc"] = None

    return metrics
