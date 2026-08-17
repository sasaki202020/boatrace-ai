# OOF Decision Protocol

## Status

- Protocol status: `FROZEN_PRE_EVALUATION`
- OOF execution status: `BLOCKED_WAITING_FOR_EXTERNAL_DATA`
- Evaluation mode: `PRE_FLIGHT_ONLY`
- Production adoption: `false`
- Personal adoption: `false`
- ROI, BUY, EV and betting evaluation: not part of this protocol

この文書は、`tree_15` と `course_and_start_exhibition` challenger の比較条件を
結果を見る前に固定するための研究契約である。readiness が条件を満たしても、
本評価を自動実行または自動採用してはならない。

## Fixed Models

- Champion: frozen `tree_15`
- Baseline 1: `baseline_lane1`
- Baseline 2: `baseline_tree15`
- Challenger: `challenger_tree15_course_start`
- Feature group: `course_and_start_exhibition`
- Challenger family: tree_15 champion logitへの固定された正則化 residual 補正1つだけ
- Missing policy: course/startが完全な場合だけ challenger を適用し、それ以外は
  `baseline_tree15`へ deterministic fallback する
- No sequential coefficient tuning, threshold tuning, or additional challenger search

使用するmodel、feature、係数、seed、前処理、fallback理由は評価開始前に
artifactへ固定する。結果確定後に変更して再評価してはならない。

## Cohort

評価対象は、次をすべて満たす同一race cohortだけとする。

- prediction、feature snapshot、settlementがrace単位で結合できる
- feature snapshotがrace deadline前に取得されている
- timestamp、provenance、schemaの検証に成功している
- result、winner、payout、対象raceの実STまたは確定進入をfeatureとして使用していない
- duplicate、schema drift、parser failure、time-order violationがない

未結合race、締切後snapshot、leakage拒否raceは評価に再投入しない。
過去predictionを結果確定後に再生成してはならない。

## Split And Preprocessing

- Split: chronological expanding-window 5-fold
- Group boundary: `targetDate`; 同一日をtrainとvalidationに分割しない
- Random split: prohibited
- Same race in multiple folds: prohibited
- Preprocessing fit: train races only
- Calibration fit: train races only
- Validation result: outer validationでは一度だけ使用し、再調整に使わない
- Bootstrap block: `targetDate` block; race単位の独立再標本化は採用しない

各foldは、過去trainから未来validationへ一方向に進む。validationを見て
係数、欠損処理、fold境界、採用条件を変更してはならない。

## Historical Frozen Snapshot

以下はprotocolをfreezeした時点の履歴スナップショットである。現在のreadiness、
残件数、fold会計として再利用してはならない。

```text
totalEligibleRaceCount = 305
initialTrainRaceCount  = 21
gapExcludedRaceCount   = 0
validationRaceCount    = 284
otherExcludedRaceCount = 0
```

```text
305 = 21 + 0 + 284 + 0
```

validation foldは `65, 59, 60, 60, 40` raceである。21件は未説明の欠損ではなく、
fold評価に入れない初期学習期間である。

## Current Readiness Accounting

現在のOOF readinessは、過去Markdownの数値を転記せず、正規のlocal forward evidence
から読み取り専用で再計算する。

```text
py -3 scripts/build_oof_data_readiness_v1.py --data-root <canonical-data-root>
```

出力は `<canonical-data-root>/reports/feature_forward_v1/` 配下の
`oof_readiness_latest.json` と `oof_readiness_latest.md` である。reportは
`forwardCollectionDays`、settled/OOF race、OOF date、fold、mature coverage、
lifecycle/hash integrity、blocked reasonsを記録する。readiness処理はモデル評価、
challenger選択、ネットワーク取得、BUY、EV、投票、production/prospective書込みを行わない。

手動取り込みの入力契約は
[MANUAL_INGEST_FORMAT.md](MANUAL_INGEST_FORMAT.md) を参照する。

## Metrics And Decision Rules

Primary metric:

- race log loss

Secondary metrics:

- multiclass Brier
- Top-1 accuracy
- ECE
- calibration slope/intercept
- date-block bootstrap 95% CI
- venue、month、predicted boat、feature coverage別安定性

Challengerを `PERSONAL_OFFLINE_CHALLENGER` とするには、以下をすべて満たす。

- log lossが5 fold中4 fold以上で改善
- aggregate log loss差のdate-block bootstrap 95% CI上限が0未満
- Brierが4 fold以上で悪化せず、重大な悪化がない
- Top-1がtree_15より悪化しない
- ECEの悪化が+0.005以内で、重大な校正崩れがない
- 最悪foldのlog loss悪化が+0.002以内
- 特定venue、month、predicted boat、coverage segmentへの依存がない
- leakage、duplicate、時系列違反、hash不一致が0
- deterministic rerunが完全一致

条件未達なら `NO_CHALLENGER_FOUND` とし、`tree_15`を維持する。点推定だけ改善し、
CIが0をまたぐ場合は採用せず `INCONCLUSIVE` とする。

## Readiness Gates

### Coverage denominator

coverage gateは日中の途中集計ではなく、対象日の全capture windowが通過した後の
`mature selected race`を分母にする。capture window未到達のraceは、未取得失敗
として分母へ含めない。

```text
matureCaptureCoverage =
  valid pre-deadline capture
  / selected race whose capture window has passed
```

判定時点はend-of-day、または対象cohortの全capture window通過後に固定する。
raw capture coverageは診断用に別表示してよいが、80% gateの合否には使用しない。

### Diagnostic readiness

以下は評価を許可する条件ではなく、データが壊れていないかを確認するための
preflight条件である。

- forward collection 30日以上
- feature-settled race 500件以上
- coverage 80%以上
- 各validation fold 75件以上
- OOF対象日25日以上、対象race 375件以上
- `newUnknownCount = 0`
- `terminalConflictCount = 0`
- `leakageCount = 0`
- `hashChainValid = true`
- `productionRelevantFailureCount = 0`

診断条件を満たしても、metric計算・候補選択・採用判定は自動開始しない。

### Decision readiness

本評価の実行には、次をすべて満たす必要がある。

- forward collection 30日以上
- feature-settled race 1,500件以上
- coverage 80%以上
- 各validation fold 250件以上
- OOF対象日25日以上、対象race 1,250件以上
- `newUnknownCount = 0`
- `terminalConflictCount = 0`
- `leakageCount = 0`
- `hashChainValid = true`
- `productionRelevantFailureCount = 0`
- 固定cohort digest、model hash、schema hash、spec hashが一致
- 明示的な研究実行承認がある

30日または1,500件の片方だけでは不十分とする。条件到達後も実行は
明示承認待ちであり、`--force`、`--skip-gate`等の迂回は作らない。

## Immutable Lineage

このprotocolは次の既存machine-readable specと対応する。

- Spec: `config/feature_forward_v1/oof_evaluation_spec.json`
- Current spec SHA-256: `aa50c8a4529472b53cc8bb833cfc87f251b46474bd983a5b4882d9f6e992809b`
- Current preflight snapshot: `37d9cff9f426b7cc5e0de03a716cc6229f2a051fc5ab9515b681532e6b97fa63`
- Current ledger HWM: `7011`
- Current code commit: `01b145ff1d8c05c5066ba0891e5b93d8eb11ef1e`
- Frozen tree_15 SHA-256: `a2f11bf69c1b4b7ea47cca847dbe0a46f076f7c08d3361ba9e30b43f12d65da0`
- Feature schema SHA-256: `a3853bdbdb75d13d4a596928c13eaa034307b14a5a8c534d31fca9acdab623dd`

現在の研究worktreeはdirtyであるため、上記code commitだけでは完全な再現性を
表さない。既存のreproducibility manifestとtracked patch hashを併用し、
commit、status、diff、untracked manifest、spec hashを評価artifactへ保存する。
dirty状態を理由にproductionまたはprospectiveへ接続してはならない。

## Isolation Rules

- 現行tree_15 prediction、settlement、parallel shadow、anchor、ledgerは変更しない
- challengerのOOF結果をproductionまたはpersonal predictionへ自動接続しない
- `productionAdoptionAllowed` は常に `false`
- BUY、EV、odds、betting、paymentは評価対象外
- 実データ、raw B/K、racer identity、prediction probabilityを外部公開しない
- OOF resultをprospective historyへ書き込まない

候補が条件を満たしても、次段階は新規forward raceでのchampion-challenger
parallel shadowであり、tree_15の置換ではない。

## Stop Conditions

次のいずれかが発生した場合、外部・production書込みをせず停止する。

- model、schema、spec、cohort digestの不一致
- result leakage、timestamp違反、duplicate、hash chain異常
- fold間またはrace間の重複
- deterministic rerun不一致
- 固定対象以外のchallenger追加要求
- 既存prospective履歴への変更要求

停止状態は、条件を満たすまで `BLOCKED_WAITING_FOR_EXTERNAL_DATA` または
`INCONCLUSIVE` として記録し、失敗を隠すために閾値を緩和しない。
