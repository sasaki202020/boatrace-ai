# Offline Model v5 Parallel Shadow Runbook

## Scope

This document describes a future, separately approved champion/challenger comparison. It does not connect v5 to the current prospective controller, prediction package, ledger, anchor, settlement, production predictor, BUY, EV, betting, or payment paths.

## Fixed roles

- Champion: `tree_15`
- Challenger: the configuration recorded in `reports/offline_model_v5/candidate_manifest.json`
- Champion output remains authoritative.
- Challenger output must be stored in a separate research-only table or file.
- `productionAdoptionAllowed=false` remains mandatory.

## Preconditions for a future implementation task

1. Human approval for a new prospective parallel-shadow task.
2. Exact model, feature-schema, dataset, configuration, and code hashes match the candidate manifest.
3. The current Stage A package, public anchor payload, ledger schema, and scheduled task remain unchanged.
4. Challenger predictions are generated before results and are append-only.
5. Results are appended separately after the race; no prediction rewrite is allowed.

## Comparison contract

- Evaluate champion and challenger on exactly the same newly arriving eligible races.
- Do not tune blend weight, gate, features, or thresholds during the comparison window.
- Report race log loss, multiclass Brier, ECE, Top-1, coverage, and segment counts.
- Do not calculate ROI without timestamped pre-decision odds and verified payout provenance.
- Do not expose challenger probabilities through the existing public commitment payload.

## Stop conditions

- Any fixed-hash mismatch
- Prediction mutation or duplicate race prediction
- Result leakage into prediction features
- Prospective or production schema modification
- Ledger or anchor integrity failure

The current task only produces this design. It does not start the comparison.
