# Architecture V2 Walk-Forward Validation

## Purpose

baselineとchallengerを、同じ日付範囲・同じtrain/validation/holdout境界で再評価する。
production model bundleは上書きせず、評価用modelは一時領域でのみ作る。

## Method

- expanding windowを使う。
- 既定は3 folds、validation 30日、holdout 30日。
- foldを過去へ30日ずつずらし、各foldの終了日より後のデータは読まない。
- coreとchallengerは同じsplitを使う。
- random splitは禁止する。

## Metrics

- log loss
- Brier score
- calibration error
- top-1 accuracy
- top-1 win rate

モデル比較とpolicy/live評価は別に扱う。model holdout期間とcandidate trace期間が重ならない場合、モデル比較が正常でもcross-layer validationはwarningにする。

## Quality

- `validation_ready`: 全foldでsplitが一致し、leakage候補がなく、candidate trace期間との重なりがある。
- `validation_warning`: model比較は有効だが、policy/live traceとの期間重なりがない。
- `validation_blocked`: split不一致、結果由来feature疑い、またはfold生成不能。

## Output

- `reports/model_eval/architecture_v2_walk_forward_validation.json`
- `reports/model_eval/architecture_v2_walk_forward_validation.csv`
- `reports/model_eval/architecture_v2_walk_forward_validation.md`

BUY/EV、policy threshold、frozen_bets、settlement、本番modelは変更しない。
