# Candidate Trace Contract

## Goal

候補 1 件を、予測 -> frozen ledger -> settlement まで同じ追跡キーで追える状態にする。
この契約は監査専用であり、`BUY` / `EV` / 投票 / 本番購入には接続しない。

## Trace scope

本監査の主対象は `paperDecision in {PAPER, WATCH}` の候補行である。
`SKIP` 行は trace 補助としては残すが、canonical trace の中心ではない。

## Canonical candidate key

### Stable key

`predictionHash` は候補単位で重複しうる。したがって **candidateId を正本キー** とする。

```text
candidateId = sha1(raceDate + "|" + raceId + "|" + combination + "|" + predictionHash)
```

### Join rules

- prediction -> frozen ledger: `candidateId`
- frozen ledger -> settlement: `raceId`
- settlement の照合では `actualTrifecta` と `combination` も確認する
- `predictionHash` 単独では跨日衝突があるため、正本キーにしない

## Canonical field contract

| field | source | legacy handling | strict note |
|---|---|---|---|
| `candidateId` | `raceDate` + `raceId` + `combination` + `predictionHash` | required | duplicate 禁止 |
| `raceId` | `prediction_sheet.json` / raw row | required | なし |
| `raceDate` | audit 対象日 | required | なし |
| `venueCode` | `jcd` | required | なし |
| `raceNo` | `raceNo` / `race_no` | required | なし |
| `combination` | `combo` | required | なし |
| `snapshotId` | current repo では未永続 | `legacy_unknown` | null 可 |
| `snapshotHash` | current repo では未永続 | `legacy_unknown` | null 可 |
| `snapshotCapturedAt` | current repo では未永続 | `legacy_unknown` または null | strict 評価外 |
| `featureVersion` | current repo では未永続 | `legacy_unknown` | null 可 |
| `featureHash` | current repo では未永続 | `legacy_unknown` | null 可 |
| `modelVersion` | `daily_report.json` の prediction row | `legacy_unknown` 可 | なし |
| `calibratorVersion` | current repo では未永続 | `legacy_unknown` | null 可 |
| `predictionHash` | prediction row / frozen row | required | frozen 側と一致確認 |
| `rawProbability` | `approxProb` / `prob` | null 可 | なし |
| `calibratedProbability` | explicit calibrator が無い場合は null | null 可 | なし |
| `odds` | prediction row / daily report row | null 可 | なし |
| `oddsCapturedAt` | current repo では未永続 | `legacy_unknown` または null | `oddsCapturedAt < deadlineAt` のみ有効 |
| `deadlineAt` | `deadline` | required | なし |
| `marketProbability` | odds からの派生値 | null 可 | なし |
| `estimatedEdge` | `edge` / `expectedValue` | null 可 | なし |
| `policyVersion` | current repo では未永続 | `legacy_unknown` | infer しない |
| `policyDecision` | `paperDecision` | required | BUY 判定ではない |
| `guardDecision` | `finalDecision` | required | guard の最終判断 |
| `guardReason` | `stopReason` / `reason` | required | なし |
| `frozenBetId` | current repo では未永続 | `legacy_unknown` | null 可 |
| `frozenAt` | current repo では未永続 | `legacy_unknown` または null | strict 評価外 |
| `resultCombination` | `actualTrifecta` | required | なし |
| `payout` | `payoutAmount` / `trifectaPayout` | null 可 | なし |
| `settlementStatus` | `resultStatus` / `settleStatus` | required | なし |
| `settledAt` | current repo では未永続 | `legacy_unknown` または null | strict 評価外 |

## Source of truth

### Monitoring

- `reports/monitoring/paper_validation_summary.json`
- `reports/monitoring/candidate_quality_review.json`

この 2 つは候補数と readiness の authoritative な監視ソースとする。
raw スキャン数と gate 数がずれる場合は、監視 summary を優先する。

### Prediction

- `reports/predictions/YYYY-MM-DD/prediction_sheet.json`

候補の予測正本。以下のフィールドを追う。

- `paperDecision`
- `finalDecision`
- `stopReason`
- `oddsStatus`
- `approxProb`
- `realOdds`
- `expectedValue`
- `riskFlag`
- `confidenceRank`
- `predictionHash`

### Frozen ledger

- `reports/predictions/YYYY-MM-DD/frozen_bets.json`

このリポジトリでは `data/frozen_bets` が無いので、監査用の frozen ledger は `reports/predictions/.../frozen_bets.json` を使う。
上書きはしない。

### Settlement

- `reports/daily/YYYY-MM-DD/daily_report.json`
- fallback: `reports/daily/YYYY-MM-DD/daily_evaluation_race_results.csv`

候補単位の最終照合は `raceId` ベースで行う。
`actualTrifecta`, `hit`, `resultStatus`, `settledOdds`, `payout`, `pnl` を確認する。

## Trace statuses

候補行の trace status は次のいずれかにする。

- `complete`
- `result_unconfirmed`
- `missing_prediction_sheet`
- `missing_frozen_bets`
- `missing_settlement`
- `hash_mismatch`
- `race_mismatch`
- `combo_mismatch`

## Missing reason categories

trace の欠損理由は次のカテゴリに畳み込む。

- `legacy_field_missing`
- `frozen_not_created`
- `no_shadow_decision`
- `no_settlement`
- `scope_mismatch`
- `prediction_hash_mismatch`
- `unknown`

## Contract rules

- prediction と frozen ledger は `candidateId` で照合する
- frozen ledger 内の重複 hash は `candidateId` で防ぐ
- settlement は `raceId` で照合する
- `result_available=False` / `resultStatus != ok` の行は `result_unconfirmed` とする
- 結果がない候補を勝手に `hit` 扱いしない
- `predictionHash` を推測で補完しない
- `frozen_bets` を再生成・上書きしない
- `result` を再計算しない
- `BUY` / `EV` / 投票ロジックには接続しない

## Monitoring alignment

- `paperValidationReady=True` は候補準備完了を意味する
- `paperEligibleCandidateCount` は authoritative な readiness 指標であり、raw スキャン件数と一致しないことがある
- raw スキャン件数、traced 件数、authoritative count はレポートで分けて表示する
- `candidateIdDuplicateCount=0` を維持する

## Quality classification

監査レポートは次の 3 区分で品質を示す。

- `trace_ready`
  - `candidateIdDuplicateCount=0`
  - `missingPredictionSheetRows=0`
  - `missingFrozenBetsRows=0`
  - `missingSettlementRows=0`
  - `canonicalMissingCounts` がすべて 0
- `trace_warning`
  - candidateId は一意だが、legacy 欠損や settlement 欠損が残る
- `trace_blocked`
  - candidateId 重複、prediction sheet 欠損、frozen 欠損がある
  - または join の正本が壊れている

## Output contract

監査出力は次の 3 つを作る。

- `reports/monitoring/candidate_trace_rows.csv`
- `reports/monitoring/candidate_trace_audit.json`
- `reports/monitoring/candidate_trace_audit.md`
