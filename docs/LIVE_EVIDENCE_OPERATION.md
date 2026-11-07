# Live Evidence Operation

## Purpose

This document defines the Phase E live evidence gate for architecture v2.
It hardens candidate-level traceability and keeps production adoption blocked
until live shadow evidence is large, settled, and traceable.

This is monitoring only. It does not change prediction logic, BUY thresholds,
EV calculation, calibration, hard guards, champion switching, or voting.

## Forward-only candidate metadata

Newly written live/paper candidates now persist these fields when the writer
has the value at creation time:

- candidateId
- modelVersion
- calibratorVersion
- policyVersion
- predictionHash
- snapshotHash
- featureVersion
- rawProbability
- calibratedProbability
- odds
- oddsCapturedAt
- deadlineAt
- policyDecision
- guardDecision
- guardReason
- frozenAt

Legacy rows are not backfilled with guessed values. Missing legacy fields stay
blank or `legacy_unknown` and are reported by the trace and evidence gates.

## Write path

The audited path is:

1. `reports/predictions/YYYY-MM-DD/prediction_sheet.json`
2. `reports/predictions/YYYY-MM-DD/frozen_bets.json`
3. `data/predictions/YYYYMMDD/frozen_bets_all.json`
4. `reports/daily/YYYY-MM-DD/daily_report.json`
5. `reports/daily/YYYY-MM-DD/daily_evaluation_race_results.csv`
6. `reports/monitoring/candidate_trace_rows.csv`
7. `reports/monitoring/live_evidence_gate.json`

The metadata writer is forward-only and runs before frozen records are written.
It prohibits duplicate `candidateId` inside each written race payload.

## Strict evidence definition

A candidate counts as strict live evidence only when all conditions are true:

- `candidateId` is present and unique
- `modelVersion`, `policyVersion`, `predictionHash`, and `frozenAt` are present
- `oddsCapturedAt < deadlineAt`
- official result settlement is present

Rows with missing metadata, missing odds time, deadline violations, or pending
settlement remain visible but do not count toward strict profitability evidence.

## Gate criteria

`productionAdoptionAllowed` is always `False` in this phase.

The gate remains blocked unless all of these are satisfied:

- observationDays >= 60
- settledCandidateCount >= 500
- traceCoverage >= 0.95
- preDeadlineOddsCoverage >= 0.95
- settlementCoverage >= 0.98
- no severe drift signal
- no positive-profit concentration above 25%
- ROI and confidence evidence are calculable

## Commands

```powershell
py -3.13 scripts\build_candidate_trace_audit.py
py -3.13 scripts\build_live_evidence_gate.py
```

Focused verification:

```powershell
py -3.13 -m pytest tests/test_candidate_metadata.py tests/test_build_live_evidence_gate.py tests/test_build_candidate_trace_audit.py -q
py -3.13 -m py_compile src\pipeline\candidate_metadata.py scripts\build_live_evidence_gate.py
```

## Interpretation

Current paper validation readiness is not live profitability proof.
The next phase should continue collecting live shadow observations and use this
gate to explain why production adoption remains blocked.
