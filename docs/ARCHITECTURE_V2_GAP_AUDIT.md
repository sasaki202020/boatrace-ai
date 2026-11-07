# Architecture V2 Gap Audit

対象リポジトリ: `C:\Users\goo10\競艇\boatrace-ai-mvp`

目的:
- 現在の実装を v2 のレイヤー構造に対応づける
- どこまでが実装済みで、どこが未分離・未完成かを明確にする
- BUY / EV / 投票 / 本番接続を増やさず、監査観点だけを固定する

## 現在地

- live shadow operation は継続中
- `paperValidationReady=True`
- `liveSettledBetCount=0`
- `liveSettlementCoverage=null`
- `canTuneWithLiveOnly=False`
- `canTuneWithBackfill=False`
- `phase6_gate_status` 相当の live tuning はまだ未到達
- つまり、paper 側の蓄積は進んでいるが、live tuning の開始条件は満たしていない

## v2 レイヤー対応表

| v2 layer | 現在の実装 | 状態 | コメント |
| --- | --- | --- | --- |
| 1. raw official data | `src/ingest/official_txt_parser.py` | 実装済み | B/K TXT から公式データを抽出する入口。raw の事実取得層。 |
| 2. canonical race snapshot | `src/normalize/race_snapshot.py` | 実装済みだが永続監査は弱い | `RaceSnapshot` を組み立てるが、snapshot lineage の永続追跡はまだ薄い。 |
| 3. feature pipeline | `src/features/build_features.py`, `src/features/build_relative_features.py` | 実装済み | pre-race 特徴量と race-relative 特徴量はある。欠損方針も docs 化済み。 |
| 4. probability model | `src/models/predict_win_proba.py`, `src/models/train_win_model_baseline.py`, `src/models/evaluate_win_model_baseline.py` | 実装済み | baseline / challenger の比較は可能。レース内正規化もあり。 |
| 5. probability calibration | `src/eval/train_probability_calibrator.py`, `src/strategy/probability_calibration_features.py` | 実装済み | calibrator はあるが、policy との境界はまだ強くない。 |
| 6. odds join | `src/odds/fetch_daily_trifecta_odds.py` | 実装済み | odds3t を取得・解析する層は存在する。取得時刻の証跡が重要。 |
| 7. shadow policy | `src/strategy/evaluate_ev_and_skip.py` | 部分実装 | EV / BUY / watch / rescue が 1 クラスに密結合している。v2 で最も分離したい層。 |
| 8. frozen ledger | `data/frozen_bets/**`, `src/pipeline/run_daily_pre_race` 系 | 実運用あり | 日次の frozen_bets はあるが、監査用の lineage はもっと明示化したい。 |
| 9. settlement | `src/pipeline/run_daily_post_race`, `src/evaluation/live_operation_summary.py`, `src/evaluation/paper_validation_summary.py` | 実装済み | settlement / paper validation / live operation summary はある。 |
| 10. evaluation / monitoring | `src/pipeline/health_check.py`, `src/pipeline/ops_goal_board.py`, `src/evaluation/tuning_gate.py` | 実装済み | 監視はあるが、レイヤー横断の traceability はまだ粗い。 |

## 何が足りないか

### 1. レイヤー境界の明文化

現状は機能自体は揃っているが、以下が 1 つの監査線としては弱い。

- raw -> snapshot -> feature -> model -> calibration -> odds -> policy -> frozen ledger -> settlement -> monitoring
- 各層の入力・出力ファイル・失敗時の扱い
- どの層が欠けたら「欠損」なのか「ブロック」なのか

### 2. snapshot lineage の永続監査

`RaceSnapshot` はあるが、監査用に

- どの raw TXT
- どの parse バージョン
- どの feature バージョン
- どの model / calibrator
- どの odds snapshot
- どの frozen_bets
- どの settlement

を 1 つの追跡可能な ID で繋ぐ仕組みがまだ弱い。

### 3. policy と calibration の分離

`src/strategy/evaluate_ev_and_skip.py` の中で、

- calibration
- EV
- BUY
- watch
- rescue

がまとまっている。

これは実装上は便利だが、監査上は

- どこで確率が作られたか
- どこで閾値が掛かったか
- どこで BUY に昇格したか

が見えにくい。

### 4. live tuning readiness の不足

現在の状態では

- `liveSettledBetCount=0`
- `liveSettlementCoverage=null`
- `canTuneWithLiveOnly=False`

なので、live tuning 開始条件は満たしていない。
paper validation は進んでいるが、live 確認はまだ足りない。

### 5. candidate traceability の不足

`paperEligibleCandidateCount` や `predictionHashMissingDays` は監視されているが、候補 1 件単位で

- raw 起点
- feature 起点
- model 出力
- calibration 出力
- odds join
- policy decision
- frozen_bets
- settlement

を 1 本の trace に落とす監査線はまだ薄い。

## baseline

baseline は次の 2 系統で確認できる。

- `win_baseline_core`
- `win_baseline_core_relative`

根拠ファイル:

- `docs/feature_inventory.md`
- `docs/feature_tiering.md`
- `docs/benchmark_policy.md`
- `src/models/win_baseline_common.py`
- `src/models/evaluate_win_model_baseline.py`

要点:

- 比較基準は固定されている
- time-series split / train-valid-test の考え方もある
- ただし「基準点の結果を 1 枚の lineage で追える」状態までは未完成

## walk-forward

walk-forward の考え方はある。

根拠:

- `src/models/time_split.py`
- `src/models/win_baseline_common.py`

要点:

- 日付順 split は実装済み
- ただし model / calibration / policy / settlement をまたいだ walk-forward の監査線はまだ不足

## calibration

calibration は実装済み。

根拠:

- `src/eval/train_probability_calibrator.py`
- `src/strategy/probability_calibration_features.py`
- `docs/calibration_policy.md`

要点:

- 生確率と補正確率は区別されている
- Brier / log loss の評価も可能
- ただし calibration と policy が同じクラスで扱われているため、境界は監査上もっと明示化したい

## candidate traceability

現状は部分実装。

良い点:

- `RaceSnapshot` がある
- `paper_validation_summary` / `live_operation_summary` / `tuning_gate` がある
- `predictionHashMissingDays` を監視している

不足点:

- 単一 candidate の end-to-end trace が docs から追いにくい
- `predictionHash` がどの層で付与されたかの説明が弱い
- raw / feature / model / policy / settlement の ID 連結を 1 枚にまとめていない

### paperEligibleCandidateCount=105 の追跡

確認できた根拠:

- `reports/monitoring/paper_validation_summary.json`
- `reports/monitoring/paper_validation_gate.json`

確認できた値:

- `paperEligibleCandidateCount=105`
- `paperValidationReady=True`
- `predictionHashMissingDays=0`
- `liveSettledBetCount=0`

追跡できたもの:

- candidate count の集計結果
- ready / blocker の gate 判定
- `predictionHashMissingDays` の監視値

まだ弱いもの:

- candidate ごとの `policyVersion`
- candidate ごとの `modelVersion`
- candidate ごとの `odds` 取得時刻

### policyVersion / modelVersion / odds時刻

- `modelVersion` は daily report 側で `baseline_rule_v1` が確認できる
- `policyVersion` は repo 横断検索で明示的な永続フィールドを確認できなかった
- `odds時刻` は odds 取得コード側に fetch/parse の時刻概念はあるが、候補 1 件ごとの監査用 canonical field としては未固定

### RaceYosouView.jsx

- repo 内検索では `RaceYosouView.jsx` は見つからなかった
- UI 側は `src/web/static/*.html` と `src/web/app.py` ベースで構成されている

## gate provisional mapping

### DataReady

概ね次が揃う状態。

- raw official data の取得
- parse
- `dailyIssueClassification=ready`
- 日次の最低限の input あり

### FeatureReady

概ね次が揃う状態。

- feature pipeline が動く
- relative feature が作れる
- 欠損方針が明確

### ModelReady

概ね次が揃う状態。

- baseline モデルが学習 / 評価可能
- calibration artifact が作れる

### PolicyReady

まだ live policy の開始条件は満たしていない。

- `canTuneWithLiveOnly=False`
- `canTuneWithBackfill=False`
- BUY / EV / watch の境界が 1 クラスに集約されている

### LiveShadowReady

未到達。

- `liveSettledBetCount=0`
- `liveSettlementCoverage=null`
- live tuning の gate は閉じたまま

## 最大リスク

**最大リスクは、calibration と BUY / EV policy が同居していることによる、閾値・確率・ decision の混線。**

理由:

- 確率を直したつもりで policy が変わる
- policy を直したつもりで calibration が変わる
- その結果、trace が追えない

副次リスク:

- live tuning の開始条件が満たされていないのに、paper 指標だけで前進したと誤認すること
- candidate traceability が弱いまま gate を通そうとすること

## top 5 priorities

1. raw -> snapshot -> feature -> model -> calibration -> odds -> policy -> frozen_bets -> settlement の trace 線を 1 本にまとめる
2. `evaluate_ev_and_skip.py` の policy / calibration / BUY / watch / rescue を監査上は分離して見えるようにする
3. `predictionHash` と `frozen_bets` と settlement の対応を candidate 単位で追えるようにする
4. `paperValidationReady` と `canTuneWithLiveOnly` の意味を docs と監視で同じ言葉に揃える
5. baseline / walk-forward / calibration の評価結果を、同じ date range と split で再現できる形に固定する

## 次の実行順

### A. 候補単位の追跡を完成

- 目的: 1 candidate を raw / feature / model / calibration / odds / policy / frozen_bets / settlement まで 1 本で追えるようにする
- 現状: `predictionHash` と settlement は追えるが、candidate ごとの `policyVersion` と `odds時刻` が弱い
- 優先修正: trace 用 canonical field を増やし、日次レポートだけでなく候補単位の audit artifact を作る

### B. モデルと売買ルールを完全分離

- 目的: 確率推定と BUY / EV / watch / rescue の判定を別レイヤーにする
- 現状: `src/strategy/evaluate_ev_and_skip.py` に policy と calibration が密結合している
- 優先修正: 監査上の責務を分け、同じクラス内にあっても出力アーティファクトは分離する

### C. 同一期間で walk-forward 再検証

- 目的: baseline / challenger / calibration / policy を同じ期間・同じ split で再評価する
- 現状: `time_split.py` と baseline 評価はあるが、レイヤー横断の再検証は docs で未固定
- 優先修正: train / valid / holdout の期間定義を固定し、再実行可能な比較表を作る

### D. live shadow で収益性と安定性を証明

- 目的: 締切前オッズを使った shadow で、ROI と安定性を検証する
- 現状: live operation / tuning gate はあるが、`liveSettledBetCount=0` で開始条件未達
- 優先修正: paper validation ではなく live shadow の settled 件数と coverage を増やす

### 実行順の判断

- 最初にやるのは A
- A が固まらないと B 以降の検証が曖昧になる
- C は A/B の追跡が固まってからでないと再現性が弱い
- D は live 指標が十分に溜まってから判定する

## phase / gate の解釈

- paper validation は ready に近い
- live tuning は blocked
- 本番 BUY / EV / 投票はまだ禁止
- 収益化判断はまだしない

## 今回の結論

この repo は、

- raw ingestion
- feature
- model
- calibration
- odds
- settlement
- monitoring

の部品は揃っている。

ただし v2 の観点では、

- traceability
- policy / calibration 分離
- live tuning readiness

がまだ弱い。

つまり、**部品はあるが、層の境界がまだ監査しやすい形にはなっていない**。

## 変更ファイル

- `docs/ARCHITECTURE_V2_GAP_AUDIT.md`

## BUY / EV / vote

- BUY 判定: 変更なし
- EV 判定: 変更なし
- 投票: 変更なし
- 本番接続: なし
