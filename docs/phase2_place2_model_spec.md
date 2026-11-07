# Phase 2 Place2 Model Spec

## 目的
Phase 2 の目的は、`P(2着 = j | 1着 = i)` を学習し、1着を固定した条件付き2着分布を安定して返せるようにすること。

この Phase では、Phase 1 の official predictor である `core_relative` の出力を前提に、2着候補の条件付き確率を作る。
Phase 3 では、この出力を使って 1着+2着 の中間評価を行う。

## 前提
- Phase 1 の official predictor は `core_relative`。
- 1着モデルの出力契約は既に固定済み。
- Phase 2 は 1着を固定した条件付き問題として扱う。
- 本仕様は 3着モデルや selector には広げない。

## 1. 学習単位
### 条件付きコンテキスト
- 1つの学習単位は `(race_id, fixed_first_place_boat_no)` の組み合わせ。
- この組み合わせを `place2_context_id` と呼ぶ。
- 各コンテキストには、1着固定後の残り 5 艇が候補として並ぶ。

### 行単位
- 1コンテキストにつき、候補艇ごとに 1 行を作る。
- したがって 1 レースあたり通常 5 行の学習サンプルを持つ。

### 正例の定義
- そのコンテキストで実際に 2着だった艇が正例 `1`。
- 残りの候補艇は負例 `0`。

## 2. 入力データ
### 基本入力
- `data/processed/trainable_win_training_data.csv`
- Phase 1 の official predictor 出力
  - `race_id`
  - `boat_no`
  - `win_proba_raw`
  - `win_proba_norm`
  - `rank_within_race`

### 参照する設定・成果物
- `config/model_pipeline/phase1_win_model.json`
- `config/model_pipeline/trifecta_conditional_plan.json`
- `reports/model_eval/win_relative_decision.json`

### Phase 1 出力の扱い
- Phase 2 の学習・評価で使う Phase 1 由来特徴は、リークしない形で用意する。
- 学習時は、各 race について target を見ない out-of-fold もしくは time split 安全な Phase 1 出力を使う。
- 推論時は、固定済み Phase 1 official predictor の出力を使う。

## 3. target_place2 定義
- `target_place2 = 1 if candidate_boat_no` がそのコンテキストの実際の 2着艇なら `1`、それ以外は `0`
- 1つのコンテキスト内では正例は 1 つだけ

## 4. 1着艇固定後の残り艇集合の扱い
### 学習時
- 各 race で実際の 1着艇を固定する
- その 1着艇を候補集合から除外する
- 残った 5艇を 2着候補として扱う

### 推論時
- Phase 1 の出力から固定 1着艇を決める
- 1着候補を固定した後、残り 5艇の条件付き 2着確率を出す
- Phase 3 で必要なら topK の 1着候補に対して同じ処理を繰り返せるようにする

### 除外条件
- 同一 race の boat が重複している場合は除外
- 6艇未満の race は原則として除外
- Phase 1 出力と結合できない race は除外し、ログに残す

## 5. 採用 feature set
### ベース feature set
- `win_baseline_core_relative`
- 理由: Phase 1 の official predictor と整合し、2着条件付き学習の基盤にできるため

### 追加する条件付き feature
- `place2_context_id`
- `fixed_first_place_boat_no`
- `fixed_first_place_rank_within_race`
- `fixed_first_place_win_proba_raw`
- `fixed_first_place_win_proba_norm`
- `candidate_phase1_rank_within_race`
- `candidate_phase1_win_proba_raw`
- `candidate_phase1_win_proba_norm`
- `candidate_phase1_margin_to_fixed_first`
- `remaining_field_size`

### 候補艇側の feature
- `boat_no`
- `jcd`
- `race_number`
- `win_baseline_core_relative` 由来の race-relative features

### 採用の扱い
- `core` は Phase 2 の比較基準として保持する
- Phase 2 の本命入力は `core_relative` 系と Phase 1 由来特徴の組み合わせとする

## 6. train / valid / test 分割方針
### 分割原則
- ランダム分割は禁止
- `date` による時系列分割を使う
- 分割は race_id 単位で行い、同一 race が split をまたがないようにする

### 追加ルール
- Phase 2 のコンテキスト展開は split 後に行う
- つまり、race を先に train / valid / test に分け、その後に 1着固定コンテキストを生成する
- これにより同一 race の候補が split をまたがる leakage を避ける

## 7. モデル種別
- LightGBM binary classification
- 1コンテキスト内の候補行を binary classification で学習する
- 予測後にコンテキスト内で正規化して条件付き分布にする

## 8. 推論時の正規化方法
### raw probability
- 各候補行について `p_place2_raw` を出す

### コンテキスト内正規化
- `p_place2_norm_k = p_place2_raw_k / sum(p_place2_raw within same place2_context_id)`
- 1つの `place2_context_id` で候補 5艇の合計が `1.0` になるよう補正する

### ranking
- `p_place2_norm` の降順でコンテキスト内順位を付与する
- `rank_within_remaining_field = 1` が最上位候補

## 9. calibration の扱い
- Phase 2 でも calibration を評価対象に含める
- `log loss` と `calibration error` を主に監視する
- 必要なら後処理 calibration を検討するが、コンテキスト内正規化を壊さないことを条件とする
- Phase 2 の official 出力はコンテキスト内正規化後の確率とする

## 10. 評価指標
最低限、次を記録する。
- `log loss`
- `Brier score`
- `calibration error`
- `top1 accuracy`
- `top3 hit rate`
- `context coverage`

### 補助指標
- `naive baseline gap`
- `candidate ranking stability`

## 11. Naive baseline
- 1着固定後の残り 5艇を一様分布とする
- つまり各候補の naive probability は `1 / 5`
- Phase 2 は、この naive baseline より低い損失と安定した calibration を目指す

## 12. 成果物
### 必須成果物
- 学習済み 2着条件付きモデル
- split manifest
- Phase 1 由来特徴の作成記録
- evaluation report
- context-level prediction export

### 想定ファイル
- `models/phase2_place2/*.joblib`
- `reports/model_eval/phase2_place2_model*.json`
- `reports/model_eval/phase2_place2_model*.csv`

## 13. 採用判定条件
Phase 2 を次の段階へ進める条件は次のとおり。
- naive baseline を `log loss` で上回る
- `Brier score` が悪化しない
- `calibration error` が許容範囲
- `top1 accuracy` と `top3 hit rate` が再現可能
- コンテキスト内正規化が安定している
- Phase 3 が必要とする中間出力契約が満たせる

## 14. Phase 3 への出力仕様
Phase 3 は、Phase 2 の 1着固定後の 2着候補出力をそのまま使う。
Phase 2 は、少なくとも以下の列を出力できる必要がある。

### 必須出力列
- `race_id`
- `place2_context_id`
- `fixed_first_place_boat_no`
- `fixed_first_place_source`
- `fixed_first_place_rank_within_race`
- `fixed_first_place_win_proba_raw`
- `fixed_first_place_win_proba_norm`
- `candidate_boat_no`
- `candidate_rank_within_race_before_fix`
- `candidate_rank_within_remaining_field`
- `p_place2_raw`
- `p_place2_norm`
- `target_place2`
- `date`
- `jcd`
- `race_number`
- `model_name`
- `feature_set_name`
- `split_name`
- `calibrated_flag`

### 付加情報
- `remaining_field_size`
- `phase1_model_name`
- `phase1_feature_set_name`

## 15. Phase 3 での利用前提
- Phase 3 は Phase 2 の `place2_context_id` を単位に 1着+2着を連結する
- `fixed_first_place_boat_no` と `candidate_boat_no` を組にして順序制約を作る
- 2着出力は、1着候補が変わっても同じ列契約で扱える必要がある

## 16. 実装上の注意
- Phase 1 出力の利用はリークしないことを優先する
- constant 列や全欠損列は自動除外する
- race size が 6 でないものは原則除外する
- selector は触らない
- 3着モデルもまだ作らない

## 17. 非目標
- 3着条件付きモデルの実装
- 三連単再構成
- selector の再設計
- 閾値チューニング
- external odds を使った最適化

## 18. Phase 2 の完了条件
- 1着固定後の 2着条件付き学習単位が明文化されている
- Phase 1 由来特徴の使い方が leakage-safe に定義されている
- Phase 3 が必要とする出力インターフェースが明確である
- 時系列 split、正規化、評価指標、成果物が固定されている
