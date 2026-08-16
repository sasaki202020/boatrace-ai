# Course/Start Evaluation Gate Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the offline course/start challenger gate without changing tree_15, prospective records, feature collection, or production behavior.

**Architecture:** Keep the existing read-only runner and evaluator. Add strict prediction/cohort validation at the runner boundary, persist a local immutable evaluation-cohort manifest beside the existing research report, and make the existing evaluator report OOF sample and segment stability requirements before it can return a challenger status.

**Tech Stack:** Python 3.13, pytest, SQLite read-only access, existing NumPy/scikit-learn code, PowerShell scheduled runner.

## Global Constraints

- `tree_15` model, feature schema, dataset, as-of artifact, prospective predictions, settlements, and ledgers remain unchanged.
- No new external data, network request, production write, betting, payment, or external anchor write is allowed.
- Existing dirty files unrelated to this gate are preserved.
- All unknown, missing, late, or conflicting evidence fails closed.
- Existing 24 full-suite baseline failures remain separate and are not repaired by this task.

---

### Task 1: Lock the input contract with failing tests

**Files:**
- Modify: `tests/feature_forward_v1/test_course_start_challenger.py`
- Modify: `tests/feature_forward_v1/test_local_pipeline.py` only if a shared prediction fixture is needed
- Modify: `scripts/run_course_start_challenger_v1.py`

**Interfaces:**
- `_verify_prediction(path: Path) -> dict[str, Any]` must reject missing timing fields, timezone-naive timestamps, and `generatedAtJst >= deadlineJst`.
- The CLI parser must require `--model-artifact`.
- `build_course_start_race_rows` must require eligibility/provenance/schema flags to be exactly `True`.

- [ ] **Step 1: Write failing tests**

Add tests that create a valid prediction payload with a correct hash, then assert rejection for:

```python
payload.pop("generatedAtJst")
payload["generatedAtJst"] = "2026-07-21T13:25:00+09:00"
payload["deadlineJst"] = "2026-07-21T13:25:00+09:00"
```

Add a test that invokes the CLI argument parser without `--model-artifact` and expects argument failure. Add a race-row test with `researchEligible=None` and assert `ValueError("feature_provenance_invalid")`.

- [ ] **Step 2: Run the tests and confirm RED**

Run:

```powershell
py -3.13 -m pytest -q tests/feature_forward_v1/test_course_start_challenger.py -k "timing or model_artifact or eligibility"
```

Expected: the new tests fail because the current verifier ignores timing, the CLI option is optional, and missing flags are accepted.

- [ ] **Step 3: Implement the minimum validation**

Parse both timestamps with `datetime.fromisoformat`, reject a missing timezone, require `generated_at < deadline`, and require `--model-artifact`. Change the three provenance checks to `is not True`; require the exact feature group.

- [ ] **Step 4: Run the focused tests and confirm GREEN**

Run the same command and expect all focused tests to pass.

### Task 2: Make the coverage cohort complete and immutable

**Files:**
- Modify: `scripts/run_course_start_challenger_v1.py`
- Modify: `tests/feature_forward_v1/test_course_start_challenger.py`

**Interfaces:**
- Add a pure helper that derives the trailing consecutive assessment dates from verified feature keys.
- Extend `load_selected_scope_schedule` to receive the full assessment date set, including dates with no feature snapshot.
- Add `evaluation_cohort_manifest.json` under the existing allowlisted report root.

- [ ] **Step 1: Write failing tests**

Create a request ledger with selected venues for two consecutive dates and a B file only for the first date. Pass both dates as the assessment cohort and assert the schedule metadata is `UNAVAILABLE`, not a one-date denominator. Add a test that a locked cohort digest changes when a joined race is added.

- [ ] **Step 2: Run the tests and confirm RED**

Run:

```powershell
py -3.13 -m pytest -q tests/feature_forward_v1/test_course_start_challenger.py -k "cohort or denominator"
```

Expected: the current implementation ignores dates with no feature key and has no cohort lock.

- [ ] **Step 3: Implement cohort derivation and lock**

Use the trailing consecutive verified feature dates to define `cohortStart` and `cohortEnd`. Load the B file and selected-venue state for every date in that interval. Write a manifest containing the date interval, schedule source hashes, selected venues, coverage denominator, model/schema hashes, and a stable digest of joined rows. If an existing manifest has a different digest, set `CHALLENGER_EVALUATION_BLOCKED` with `evaluation_cohort_changed_requires_review` and do not evaluate. If it matches an existing evaluation, reuse the stored result and do not rerun OOF.

- [ ] **Step 4: Run focused tests and inspect the manifest behavior**

Run the cohort tests, then run the read-only gate runner against the current runtime. Confirm no evaluation is executed while coverage is unavailable and no production/prospective file changes occur.

### Task 3: Add OOF sample and segment stability gates with failing tests

**Files:**
- Modify: `src/feature_forward_v1/course_start_challenger.py`
- Modify: `tests/feature_forward_v1/test_course_start_challenger.py`

**Interfaces:**
- Add constants derived from the existing prerequisite: `MIN_OOF_RACES = 1250`, `MIN_OOF_DATES = 25`, `MIN_VALIDATION_RACES_PER_FOLD = 250`, `MIN_SEGMENT_RACES = 100`, and `MAX_SEGMENT_LOG_LOSS_DEGRADATION = 0.002`.
- The evaluation result must include OOF race/date counts and per-fold date/race counts.

- [ ] **Step 1: Write failing tests**

Add a test that constructs enough total races but an imbalanced validation fold and asserts `insufficient_oof_validation_sample`. Add a test where one venue has at least 100 races and candidate log loss degrades by more than 0.002; assert `segment_stability_failed`.

- [ ] **Step 2: Run the tests and confirm RED**

Run:

```powershell
py -3.13 -m pytest -q tests/feature_forward_v1/test_course_start_challenger.py -k "oof_sample or segment_stability"
```

Expected: the current evaluator returns a candidate status without these gates.

- [ ] **Step 3: Implement the minimum gates**

Compute validation counts from the actual OOF predictions, add them to the result, and append adoption reasons when the derived sample requirements or supported segment degradation limit fail. Keep segment results descriptive for groups below 100 races.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run the focused tests and the complete challenger test file. Verify that existing deterministic and probability-contract tests remain green.

### Task 4: Align reports and documentation

**Files:**
- Modify: `scripts/run_course_start_challenger_v1.py`
- Modify: `docs/feature_forward_v1/COURSE_START_CHALLENGER_GOAL.md`
- Modify: `reports/feature_forward/` only through the existing runner
- Test: `tests/feature_forward_v1/test_course_start_challenger.py`

**Interfaces:**
- Readiness output must expose `cohortStart`, `cohortEnd`, `cohortDigest`, `oofValidationRaceCount`, `oofValidationDateCount`, and `evaluationLocked`.
- Human-readable readiness must state that a passing offline screening result is not adoption evidence.

- [ ] **Step 1: Add a report contract test**

Assert that blocked readiness exposes the new fields with safe values and continues to report `productionAdoptionAllowed=false`.

- [ ] **Step 2: Update the runner/report text**

Write the fixed cohort and OOF counts into JSON/Markdown without exposing raw inputs, predictions, or secrets.

- [ ] **Step 3: Run the report test**

Run:

```powershell
py -3.13 -m pytest -q tests/feature_forward_v1/test_course_start_challenger.py
```

### Task 5: Full verification and handoff

**Files:**
- No new production/data files.
- Inspect: `git diff --check`, current frozen artifact, prospective ledger, and runtime status.

- [ ] **Step 1: Run the feature-forward suite**

```powershell
py -3.13 -m pytest -q tests/feature_forward_v1
```

- [ ] **Step 2: Compile and check the diff**

```powershell
py -3.13 -m py_compile src/feature_forward_v1/course_start_challenger.py scripts/run_course_start_challenger_v1.py
git diff --check
```

- [ ] **Step 3: Run the read-only gate**

```powershell
pwsh -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File .\scripts\run_course_start_challenger_gate_v1.ps1
```

Expected while current data remains below thresholds: `CHALLENGER_EVALUATION_BLOCKED`, `evaluationExecuted=false`, no production/prospective writes.

- [ ] **Step 4: Verify fixed artifacts and isolation**

Recompute the tree_15 SHA-256 and compare the before/after bytes of prospective prediction, settlement, and feature-store roots. Confirm no network request, production write, model change, or prospective ledger change.

- [ ] **Step 5: Report findings and remaining external gate**

Report the hardened gate status, test counts, existing baseline failures separately, changed files, and the remaining data-accrual counts. Do not claim a challenger result until the locked cohort is available.
