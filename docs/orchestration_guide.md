# Orchestration Guide: 自動運用の手引き

毎日「ボタン一つ」で予測からアーカイブまでを完遂させるためのガイドです。

## ⚙️ 仕組み
`src/orchestration/orchestrate_daily.py` が以下のステップを順次に実行します：
1. **Ingest**: `data/raw/official/` の当日データを処理。
2. **Predict**: 学習済みモデルで1着確率を予測。
3. **Strategy**: 3連単候補生成と期待値判定。
4. **Report**: Markdown形式の投資レポートを出力。
5. **Archive**: 成果物を `archive/年/月/日/` フォルダへ自動コピー。

## 🚀 使い方
ルートディレクトリにある **`run_today.bat`** をダブルクリックするだけです。

## ⚠️ トラブル時の対応
自動実行が途中で止まった場合：
1. ターミナルに表示されているエラー（PythonのTraceback）を確認します。
2. 止まった段階（Gate名）を特定します。
3. [docs/stabilization_log.md](file:///c:/Users/goo10/競艇/boatrace-ai-mvp/docs/stabilization_log.md) に記録し、修正を行ってください。

## 🛡️ 安全設計
このスクリプトは **「再学習」をあえて含めていません。** 
再学習は `master_run.py` または `train_win_model.py` で意図的に（週1回など）行うべき、という方針に基づいています。
