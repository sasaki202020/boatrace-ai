import pandas as pd
import os
from datetime import datetime

class ReportGenerator:
    """
    予測結果と評価結果を統合し、実戦的な Markdown レポートを作成する。
    """
    def generate(self, race_info, value_bets, metrics):
        date_str = datetime.now().strftime("%Y-%m-%d")
        report_path = f"reports/{date_str}_race_analysis.md"
        
        report = f"""# BoatRace-AI-MVP 予測レポート ({date_str})

## 1. レース概要
- {race_info}

## 2. 予測結論
"""
        if value_bets:
            report += "### 【結論：買い推奨】\n期待値の高い買い目が検出されました。\n\n"
            report += "| 買い目 | 確率 | オッズ(想定) | 期待値 |\n"
            report += "| :--- | :--- | :--- | :--- |\n"
            for bet in value_bets[:5]:
                report += f"| {bet['combo']} | {bet['prob']:.2%} | {bet['odds']} | {bet['ev']:.2f} |\n"
        else:
            report += "### 【結論：見送り (SKIP)】\n期待値が閾値を超える買い目がないため、見送りを推奨します。\n"

        report += f"""
## 3. モデル評価指標
- **Logloss**: {metrics['logloss']:.4f}
- **Accuracy**: {metrics['accuracy']:.2%}

## 4. 分析コメント (ReportAgent)
- 1号艇の1着確率が非常に高く、本命サイドの決着が予想されます。
- 中穴狙いの期待値は低いため、絞って買うか、無理な勝負は避けるべきです。
"""
        
        os.makedirs("reports", exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report)
            
        print(f"Report generated: {report_path}")
        return report_path

if __name__ == "__main__":
    # サンプルデータで生成テスト
    gen = ReportGenerator()
    gen.generate(
        "2024-01-01 桐生 1R",
        [{"combo": "1-2-3", "prob": 0.45, "odds": 5.2, "ev": 2.34}],
        {"logloss": 0.35, "accuracy": 0.85}
    )
