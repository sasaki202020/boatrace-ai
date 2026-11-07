# Live Evidence Burn-in

## 目的

Phase E の candidate trace / frozen ledger / settlement について、
新規候補が `prediction -> odds -> freeze -> settlement` まで素通りするかを
監視だけで判定する。

この文書と対応スクリプトは監視専用であり、`BUY` / `EV` / 投票 /
本番購入には接続しない。

## 正本

- `reports/monitoring/candidate_trace_audit.json`
- `reports/monitoring/candidate_trace_rows.csv`
- `reports/monitoring/live_evidence_gate.json`
- `reports/monitoring/live_operation_summary.json`

## 状態の考え方

### shadowLifecycleState

legacy 行も含めた見え方。

legacyLifecycleState の別名として残す互換フィールド。

- `awaiting_first_candidate`
- `first_candidate_traced`
- `first_candidate_frozen`
- `first_candidate_settled`

### legacyLifecycleState

legacy 候補だけの状態。

- `awaiting_first_candidate`
- `first_candidate_traced`
- `first_candidate_frozen`
- `first_candidate_settled`

### strictLifecycleState

新規候補だけを strict evidence として判定する。

- `awaiting_first_candidate`
- `first_candidate_traced`
- `first_candidate_frozen`
- `first_candidate_settled`
- `burn_in_ready`

### overallLifecycleState

- `legacy_only_flow`
- `burn_in_warning`
- `burn_in_ready`

### burnInState

- `burn_in_warning`
- `burn_in_ready`

`burn_in_ready` の条件は次のとおり。

- strict settled 候補が 10 件以上
- strict metadata coverage が `1.0`
- duplicate candidateId が `0`
- deadline violation が `0`
- strict settlement join failure が `0`

legacy 側の join failure は `legacyWarnings` に残すが、strict burn-in の永久 blocker にしない。

## 使い方

```powershell
py -3.13 scripts\build_live_evidence_burn_in.py
py -3.13 scripts\build_live_evidence_burn_in.py --dry-run
```

## 出力

- `reports/monitoring/live_evidence_burn_in.json`
- `reports/monitoring/live_evidence_burn_in.csv`
- `reports/monitoring/live_evidence_burn_in.md`

## 出力に含めるもの

- `legacyCandidateCount`
- `strictCandidateCount`
- `legacySettlementJoinFailedCount`
- `strictSettlementJoinFailedCount`
- `legacySettledCandidateCount`
- `strictSettledCandidateCount`
- `legacyTraceCoverage`
- `strictMetadataCoverage`
- `forwardPathAudit`
- `watchdog`
- `currentBlockingStage`

## 読み方

- `strictLifecycleState=awaiting_first_candidate`
  - 新規 strict 候補はまだ来ていない
- `shadowLifecycleState=first_candidate_settled`
  - legacy を含む trace / freeze / settlement の流れは見えている
- `burn_in_warning`
  - strict evidence はまだ不十分
- `burn_in_ready`
  - strict burn-in 条件を満たした

## 注意

- legacy 行は推測補完しない
- production adoption はまだ許可しない
- `reports/monitoring/*` は Git 対象外
- forward path audit は `connected` / `conditional` / `disconnected` / `not_applicable` で示す
- live burn-in に直接関係しない経路は `not_applicable` として残してよい
