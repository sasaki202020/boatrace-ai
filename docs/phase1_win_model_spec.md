# Phase 1 Win Model Spec

## 目的
Phase 1 の目的は、1着モデルを downstream の official predictor として固定し、Phase 2 以降が参照できる安定した入出力契約を作ること。

この Phase では、`core` を regression baseline として保持しつつ、`core_relative` を official predictor として固定する。
以後の 2着条件付きモデル、3着条件付きモデル、三連単再構成は、この 1着出力を前提に進める。

## 前提
- 三連単条件付きモデル化の全体方針は既に固定済み。
- `official baseline` は `core` のまま保持する。
- `relative features` の採否判断は完了している。
- 本仕様は学習や実装ではなく、Phase 1 の作業分解と契約固定を目的とする。

## 1. 入力データ
### 学習入力
- `data/processed/trainable_win_training_data.csv`

### 参照する設定
- `config/feature_sets/win_baseline_core.json`
- `config/feature_sets/win_baseline_extended.json`
- `config/model_pipeline/trifecta_conditional_plan.json`
- `reports/model_eval/win_baseline_decision.json`
- `reports/model_eval/win_relative_decision.json`

## 2. 採用 feature set
### Regression baseline
- `win_baseline_core`
- 用途: 既存の正式 baseline として比較基準を固定する

### Official predictor for Phase 1
- `core_relative`
- 構成:
  - `win_baseline_core` の feature
  - race 内 relative features
    - `national_2ren_rate_rank_in_race`
    - `national_2ren_rate_diff_from_race_mean`
    - `national_2ren_rate_z_in_race`
    - `local_2ren_rate_rank_in_race`
    - `local_2ren_rate_diff_from_race_mean`
    - `local_2ren_rate_z_in_race`
    - `avg_st_rank_in_race`
    - `avg_st_advantage_vs_mean`
    - `avg_st_advantage_z_in_race`

### 採用の扱い
- `core` は regression baseline として残す。
- `core_relative` は Phase 1 の official predictor として固定する。
- `extended` は official predictor には採用しない。

## 3. target 定義
- `target_win = 1 if finish_position == 1 else 0`
- 1着以外は `0`
- レース単位では 6艇中 1艇のみ `1` を持つことを前提とする

## 4. train / valid / test 分割方針
### 分割原則
- ランダム分割は禁止
- `date` による時系列分割を使う
- `race_id` 単位で分割し、同一レースの艇が split をまたがないようにする

### 推奨構成
- train: 過去の大部分
- valid: test 直前の期間
- test: 直近の未学習期間

### 実装メモ
- 具体的な日付境界は run 時に固定し、split manifest として保存する
- split manifest には `train_start`, `train_end`, `valid_start`, `valid_end`, `test_start`, `test_end` を含める

## 5. モデル種別
- LightGBM binary classification
- 目的は `P(win)` の推定
- Phase 1 ではランキングと確率校正を重視し、複雑なアンサンブルは使わない

## 6. 推論時の正規化方法
### raw probability
- モデルの出力を `p_raw` とする

### race 内正規化
- `p_win_norm_i = p_raw_i / sum(p_raw within same race)`
- race 内 6艇の合計が `1.0` になるよう補正する

### ranking
- `p_win_norm` の降順で race 内順位を付与する
- `rank_within_race = 1` が最上位候補

## 7. calibration の扱い
- Phase 1 では calibration を評価対象に含める
- `log loss` と `calibration error` を主指標として監視する
- raw 出力のまま使うか、後処理の calibration を入れるかは Phase 1 の採用判定で決める
- ただし、Phase 1 の official predictor は race 内正規化後の確率を最終出力として返す

## 8. 評価指標
最低限、次を記録する。
- `log loss`
- `Brier score`
- `calibration error`
- `top1 accuracy`
- `top1 win rate`
- `top3 hit rate`

## 9. 成果物
### 必須成果物
- 学習済み 1着モデル
- split manifest
- feature set snapshot
- evaluation report
- prediction export

### 想定ファイル
- `models/win_model_phase1/*.joblib`
- `reports/model_eval/win_model_phase1*.json`
- `reports/model_eval/win_model_phase1*.csv`

## 10. 採用判定条件
Phase 1 の official predictor として固定する条件は次のとおり。
- `core_relative` が `core` に対して test の `log loss` を悪化させない
- `Brier score` が悪化しない
- `calibration error` が許容範囲
- `top1 accuracy` が同等以上、または実運用上の改善が説明できる
- race 内正規化後の確率が安定している

## 11. Phase 2 への出力仕様
Phase 2 は 1着固定後の 2着条件付きモデルを学習する。
Phase 1 は、以下の列を少なくとも渡せることを前提にする。

### 必須出力列
- `race_id`
- `boat_no`
- `win_proba_raw`
- `win_proba_norm`
- `rank_within_race`
- `target_win`
- `date`
- `jcd`
- `race_number`

### 付加情報
- `model_name`
- `feature_set_name`
- `split_name`
- `calibrated_flag`

## 12. Phase 2 へ渡す契約
Phase 2 は、Phase 1 の出力を使って次をできる必要がある。
- 1着候補を固定して 2着条件付き分布を作る
- race 内順位を参照して除外・残存艇を分ける
- 1着確率を前提にした条件付き計算を再現する

## 13. 実装上の注意
- constant 列や全欠損列は学習時に自動除外する
- ただし feature set 定義そのものは別管理にする
- 相対特徴は `build_relative_features.py` を通じて生成する
- ここで selector は触らない
- ここで 2着 / 3着モデルも作らない

## 14. 非目標
- 三連単の完成
- selector の再設計
- 閾値チューニング
- 2着 / 3着モデルの実装
- extended の正式採用

## 15. Phase 1 の完了条件
- official predictor が `core_relative` として明文化されている
- regression baseline が `core` として残っている
- Phase 2 が必要とする出力インターフェースが明確である
- 時系列分割、正規化、評価指標、成果物が固定されている
