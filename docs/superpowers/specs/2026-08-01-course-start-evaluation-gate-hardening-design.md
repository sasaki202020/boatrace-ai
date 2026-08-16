# Course/Start Evaluation Gate Hardening Design

## Goal

Make the `course_and_start_exhibition` challenger evaluation fail closed and scientifically interpretable before the existing 30-day and 1,500-settled-race threshold is reached.

## Scope

This change is limited to the offline research gate in `feature-forward-v1`.

It may change:

- evaluation input validation;
- coverage cohort reconstruction;
- one-time evaluation locking;
- OOF sample-size reporting and gating;
- segment stability checks;
- tests and research documentation.

It must not change:

- `tree_15` or its artifact;
- prospective prediction files, settlement files, or their hashes;
- feature collection behavior;
- production prediction, BUY, EV, betting, payment, or scheduler behavior;
- external anchors or credentials.

## Current Gate

The current gate remains the minimum data prerequisite:

- 30 consecutive verified forward collection days;
- 1,500 settled races joined to complete verified feature snapshots;
- 80% coverage over the collector-selected scope;
- zero post-deadline records, result leakage, duplicate records, and schema/provenance/timestamp failures;
- fixed `tree_15` model hash;
- deterministic rerun.

These thresholds are not relaxed. They are collection/readiness thresholds, not proof of production superiority.

## Hardened Gate

### 1. Input eligibility

Every prediction used by evaluation must satisfy all of the following:

- the model artifact argument is present;
- the artifact SHA-256 equals the frozen `tree_15` SHA-256;
- `generatedAtJst` and `deadlineJst` are timezone-aware ISO timestamps;
- `generatedAtJst < deadlineJst`;
- the prediction hash covers the timing fields;
- model and feature-schema hashes match the frozen values;
- all feature provenance flags are explicitly `true`;
- the feature group is exactly `course_and_start_exhibition`.

Missing or unknown values fail closed.

### 2. Fixed coverage cohort

The assessment cohort is the trailing consecutive verified collection window ending at the latest verified feature date. The schedule denominator is reconstructed for every calendar date in that window from the append-only request state and the corresponding B files.

An entire date with no valid feature snapshot must remain in the denominator. Missing B files or missing selected-venue state block the denominator instead of silently shrinking it.

The report stores a cohort manifest containing the cohort start/end, schedule source hashes, selected scope, coverage denominator, and a digest of joined evaluation rows.

### 3. One-time OOF screening

When the prerequisite gate first becomes ready, the cohort manifest is written before evaluation. The OOF result is evaluated once for that manifest. Later scheduled runs reuse the locked result when the cohort digest is unchanged and block when data changes.

The five expanding date folds remain chronological and race-grouped. In addition to the existing 1,500 joined-race prerequisite, the result must expose:

- OOF validation race count;
- OOF validation date count;
- per-fold validation race counts;
- per-fold validation date counts.

For the current six-way date partition used to produce five validation folds, the derived minimum OOF sample is `ceil(1500 * 5 / 6) = 1250` races and 25 validation dates. Each validation fold must contain at least 250 races when the prerequisite is met. These are screening safeguards, not a claim of statistical independence.

### 4. Segment stability

Segment metrics remain descriptive, but adoption screening must not pass solely because segment labels exist. Segments with fewer than 100 races are reported but cannot support a stability claim. For supported segments, candidate log loss may not degrade by more than 0.002 versus `tree_15`; the result records the failing segment identifiers.

The evaluated segment set is fixed in the contract: venue, race number, month, top predicted boat, and feature availability/missingness where present.

### 5. Interpretation

`PERSONAL_OFFLINE_CHALLENGER` means only that the frozen offline screening contract passed. It does not permit replacement of `tree_15`. A separate, newly collected, parallel champion/challenger period is required before personal-use adoption.

## Error Handling

The gate fails closed for:

- missing or late prediction timing;
- missing model artifact or hash mismatch;
- unknown provenance flags;
- incomplete or changing cohort denominator;
- changed locked cohort;
- insufficient OOF validation sample;
- segment stability failure;
- any existing leakage, duplicate, parser, schema, timestamp, or ledger failure.

No prediction, settlement, feature, or production record is modified by this gate.

## Verification

The implementation must add failing tests before production changes for:

- late prediction rejection;
- missing model artifact rejection;
- missing eligibility flag rejection;
- denominator inclusion of a fully missed date;
- cohort lock and changed-cohort rejection;
- OOF sample-size reporting/gating;
- segment degradation rejection;
- unchanged tree/model/prospective inputs.

The relevant feature-forward suite, compilation, diff check, and deterministic rerun must pass. Existing unrelated baseline failures remain separate.
