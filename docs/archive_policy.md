# Archive Policy: データ保存・整理規定

「何があったか」を後から検証できるよう、日次の成果物を永続保存するためのルールです。

## 1. 保存すべき項目
毎日、運用終了後に以下のファイルを `archive/YYYYMMDD/` にコピーせよ。
- **Raw**: 当日投入した公式データ
- **Proba**: `data/model_outputs/today_win_proba.csv`
- **Decision**: `data/strategy_outputs/today_strategy_decisions.csv`
- **Report**: `reports/YYYYMMDD_daily_report.md`
- **Log**: 実行時のターミナル出力またはログファイル

## 2. ディレクトリ構造
```text
archive/
└── 2026/
    └── 03/
        ├── 11/ (フォルダ)
        └── 12/ (フォルダ)
```

## 3. 保存の目的
- **事後検証 (Audit)**: 後日、レース結果と照らし合わせて的中精度を算出するため。
- **再学習データ**: 蓄積された `Raw` データを将来的に `historical_races.csv` に統合するため。

## 4. 自動化の推奨
手動でのコピーは漏れが発生するため、将来的に `master_run.py` の最後に `shutil.copytree` によるアーカイブ機能を追加することを推奨する。
