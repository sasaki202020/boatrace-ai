# Benchmark Policy: 比較と改善の鉄則

モデルや特徴量を変更する際、客観的な良し悪しを判断するための基準を定義します。

## 1. Baseline (基準点) の固定
- **定義**: 何も手を加えない状態の `master_run.py` 実行結果。
- **保存**: `data/model_outputs/baseline/` に予測結果と評価指標を永続保存せよ。
- **鉄則**: 基準点がない改善は、ただの「変化」である。

## 2. 比較のルール (Comparison Rules)
- **1-Change per Step**: 一度に変更するのは「特徴量1つ」または「パラメータ1つ」に絞れ。複数同時に変えると、何が効いたか分からなくなる。
- **同一データ**: A/B テストを行う際は、必ず全く同じ `data/raw/` セットを使用せよ。

## 3. 評価指標 (KPI)
- **Primary**: 期待値 (EV) 1.0超のレースにおける的中率 (Hit Rate)。
- **Secondary**: 全レースの対数損失 (Log Loss)。
- **Business**: 擬似収支（回収率）。

## 4. 改善の採用基準
- 指標が **3% 以上** 改善し、かつ他の指標を大幅に悪化させない場合にのみ、その変更を `main` ブランチ（正式採用）へマージせよ。

## 5. test_metrics 保存ルール (before/after 比較の固定化)
- `data/model_outputs/test_metrics.json` は最新値（after）として上書き保存する。
- 同時に `metrics/history/test_metrics_<run_id_or_timestamp>.json` へ毎回スナップショットを保存する。
- `run_id` がある場合は `RUN_ID` 環境変数を優先してファイル名へ使う。
- `run_id` がない場合は UTC タイムスタンプ (`YYYYMMDDTHHMMSSZ`) を使う。
- 比較時は履歴から直前スナップショットを before、最新 `test_metrics.json` を after として扱う。
