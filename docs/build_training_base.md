# build_training_base

`src/pipeline/build_training_base.py` は、raw の公式データを正規化して学習ベーステーブルを作るための CLI である。

## 入力

- `data/raw/official/web_entries/**/syusso_pages/*.html`
- `data/raw/official/entries/*.TXT`
- `data/raw/official/results/*.TXT`
- `raw/B/*.txt`
- `raw/K/*.txt`
- `data/csv/program/program.csv`
- `data/csv/result/result.csv`

## 出力

- `data/processed/normalized_entries.csv`
- `data/processed/normalized_pre_race.csv`
- `data/processed/normalized_results.csv`
- `data/processed/pre_race_features.csv`
- `data/processed/training_dataset.csv`
- `data/metadata/feature_availability.csv`

## 実行例

```powershell
py -m src.pipeline.build_training_base --date 2026-04-07
py -m src.pipeline.build_training_base --start-date 2026-04-01 --end-date 2026-04-07
```

## 検査ルール

- `race_date / jcd / race_no / lane` の null は禁止
- `race_key + lane` の重複はエラー
- 1レース6艇でない場合は警告を出し、出力は 6艇そろったレースに限定する
- `pre_race_features` に `result` phase の列が入ったらエラー
- `allowed_for_live=false` の列が live 出力に入ったらエラー

## 使い分け

- `normalized_entries`
  - 出走表の正規化結果
- `normalized_pre_race`
  - ライブ前提で使う直前情報込みの表
- `normalized_results`
  - 結果ラベルの正規化結果
- `pre_race_features`
  - 学習/本番共通の特徴量
- `training_dataset`
  - 学習用の結合済みデータ

