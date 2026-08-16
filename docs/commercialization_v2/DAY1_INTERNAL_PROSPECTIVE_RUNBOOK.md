# Day 1 Internal Prospective Shadow Runbook

## Scope

- One race date, one venue, at most 12 races.
- Manual execution, no retry, one prediction package, one external anchor.
- Only a human-placed local B file may be read.
- Prediction contents, racer data, and raw B data remain private.
- Synthetic transport verification uses one canonical `synthetic_anchor` JSON file in the allowlisted GitHub repository, branch, and path. GitHub Issues are not used.

## Preconditions

1. Candidate status is `CANDIDATE_FREEZE_INTEGRITY_PASS_WITH_COVERAGE_GAP` or stronger.
2. Model and feature-schema hashes match the frozen manifest.
3. Input schema is `PRE_RACE_SCHEMA_VERIFIED` and contains no result, winner, payout, final-odds, or settlement fields.
4. The approved dedicated GitHub repository is the sole allowlisted repository.
5. The fine-grained credential is configured through the approved environment variable.
6. Commitment dry-run and append-only ledger integrity checks pass.
7. `paymentEnabled`, `profitClaimsAllowed`, and `productionAdoptionAllowed` are all `false`.

Commercial source rights are not an internal-shadow precondition. They remain mandatory for publication, sale, redistribution, and commercial claims.

## Runtime artifact restoration

The Git baseline intentionally excludes model binaries, canonical datasets, SQLite ledgers, raw B files, receipts, and reveal material. Before Day 1, restore the frozen candidate, feature order, candidate manifest, canonical dataset, as-of artifact, isolated v1/v2 ledgers, production-model hash reference, and calibrator hash reference at the paths validated by `prepare_day1_readiness_v2.py`. Missing artifacts stop before inference with `missing_runtime_artifacts`; they are never synthesized or downloaded.

## Cutoff modes

### Daily package

The GitHub server-side `created_at` must be strictly earlier than race-date `00:00:00 JST`. If the local B file is unavailable in time, record `SKIPPED_INPUT_NOT_AVAILABLE`; do not block future dates.

### Race-level package

Use only an explicitly parsed, timezone-aware scheduled deadline from a verified B schema. Apply the configured safety margin and require GitHub `created_at` to be strictly earlier. If the deadline is absent or ambiguous, skip the race. Never infer a deadline.

## Day 1 sequence

1. Verify the date, venue count, race count, pre-race schema, and frozen hashes.
2. Generate one private package and verify that it has 1-12 races, six lanes per race, finite probabilities summing to one, and zero forbidden result fields.
3. Append the package to the isolated ledger and record its package hash.
4. Generate a random salt and the commitment. Keep package and salt private.
5. Display the minimal public anchor payload and run the publisher in dry-run mode. Confirm `networkRequests=0`.
6. Recheck repository allowlist, human approval, credential availability, and cutoff.
7. Only after explicit human approval, create exactly one file through the GitHub Contents API at `anchors/synthetic/<commitment>.json`.
8. Fetch that file through the Contents API and verify repository, branch, path, canonical content hash, object SHA, commit SHA, and commit timestamp.
9. Do not generate another prediction package for the same race date.
10. After results are available, append results to a separate ledger table without changing prediction rows.
11. Generate the reveal bundle, recompute the commitment, and verify ledger integrity.

## Success criteria

- Valid predictions greater than zero.
- One external anchor with `created_at < cutoff`.
- Model and schema hashes match.
- Forbidden result fields, prediction mutations, ledger tampering, production writes, betting actions, and payment actions are all zero.
- Reveal verification succeeds.

After success, set `Prospective Timing Status=EXTERNALLY_COMMITTED` and `Shadow Status=ACTIVE_INTERNAL_ONLY`. Do not publish predictions or enable payment.
