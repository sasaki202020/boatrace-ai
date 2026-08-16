# Final Product Specification

## 1. Status and Authority

This is the single product specification for the Git repository containing
this file.

It is the sole normative source of truth for the product specification.

It combines the durable rules needed to operate, evaluate, and improve the
system. It is not a generated status report and must not be used to claim that
the evidence gates have passed.

When documents disagree, use this precedence:

1. docs/FINAL_PRODUCT_SPEC.md as the sole product specification
2. AGENTS.md execution rules
3. docs/CODEX_TASKS.md task queue
4. docs/CODEX_CONTEXT.md and docs/CODEX_HANDOFF.md as historical context
5. Generated reports as current-state evidence only

In short: `FINAL_PRODUCT_SPEC > AGENTS execution rules > CODEX_TASKS > CONTEXT/HANDOFF historical context > reports evidence`.

AGENTS.md defines execution rules and does not override this specification.
Section 12 contracts, executable code, and tests are implementation evidence
and review inputs; they must remain consistent with the precedence above.

No generated report, backtest, paper result, or UI state can override a safety
restriction or authorize production adoption.

## 2. Product Objective

The product is a local, evidence-first BOATRACE prediction system. Its durable
outcome is a trustworthy record of:

pre-deadline inputs -> frozen prediction or candidate -> official result ->
settlement -> audit

The primary goal is not to add models or produce BUY decisions. It is to
demonstrate whether available pre-deadline information has reproducible
predictive value beyond defined baselines while preserving provenance,
time-order, and settlement traceability.

The product has three isolated modes:

| Mode | Purpose | Allowed output | Prohibited connection |
| --- | --- | --- | --- |
| Daily paper or shadow operation | Create and freeze same-day predictions, then settle and monitor them | prediction sheets, frozen records, reports, local Web display | betting, voting, automatic production changes |
| Prediction-edge research | Test a small set of challengers against fixed baselines | isolated research reports | replacement of the production predictor |
| Forward feature or OOF research | Collect approved timestamped evidence and evaluate a frozen challenger | append-only feature evidence and OOF diagnostics | production adoption, prospective writes, commercial use |

## 3. Non-Negotiable Safety Boundary

- Do not change BUY thresholds, EV formulas, baseline_score_model weights,
  hard_guard, or the production predictor without an explicit approved task.
- Do not use result, payout, or final-odds fields when generating a prediction.
- Treat frozen_bets as the prediction record. Results and settlement never
  overwrite it.
- Keep live and forward evidence separate from backfill and fixtures.
- Never create a sample, dummy, fallback prediction, inferred odds, or
  fabricated source value to make a pipeline appear successful.
- source_not_ready, future_date_not_ready, and result_data_missing are
  operational states, not successful predictions.
- No automated purchase, voting, external send, or production deployment is
  part of this product.

## 4. Canonical Data and Time Contract

### Identity and layers

- A race is identified by race_date, jcd, rno, and canonical race_id.
- Data layers are raw, staging or interim, processed, model output, strategy
  output, frozen record, settlement, and monitoring.
- Each snapshot has an explicit snapshot_time and data_phase.
- A race card is valid for relative features only when the expected six boats
  are complete. Key fields are never silently imputed.

### Availability and leakage

- Pre-race history and race-card fields may be used only when known at the
  prediction horizon.
- Just-before signals, including exhibition-related values, remain missing if
  their source was unavailable; they are not guessed.
- Results, payout, finishing position, in-race events, and post-race
  corrections are forbidden predictor inputs.
- Training and today inference use equivalent feature semantics. Feature
  preprocessing and calibration are fit from training data only.

### Missingness

- Key columns: reject an invalid record.
- Stable history-derived numeric fields: use only the documented historical
  imputation order.
- Derived and race-relative fields: recompute from source inputs; do not
  impute the derived value directly.
- Missingness that can change ranking or a policy decision remains visible in
  the record and can block promotion.

## 5. Prediction and Candidate Contract

### Probability model

- The model owns raw probability, model version, feature version, and
  prediction hash.
- Calibration owns calibrated probability and calibrator version.
- The market layer owns odds, odds capture time, deadline, and market
  probability.
- The policy layer owns estimated edge and paper BUY, WATCH, or SKIP decisions.
- These layers join on candidateId; model or calibration code must not import
  policy decisions.
- Race probabilities must be normalized to sum to one.

### Candidate generation and records

- Candidate generation derives combinations from first-place probabilities;
  it does not read outcome columns.
- candidateId, not predictionHash, is the canonical trace key.
- The trace is prediction_sheet -> frozen_bets -> settlement -> monitoring.
- predictionHash, modelVersion, policyVersion, timestamps, and source hashes
  are preserved where available. Legacy unknown values are never reconstructed.

### Current research boundary

The approved v1 challengers are limited to linear_boat_score,
lightgbm_boat_score, and market_augmented_lightgbm under the research design.
Random splits, arbitrary model expansion, and use of exposed locked periods as
confirmation are prohibited.

The current research conclusion is:

- current feature-set edge: NO_EDGE
- market and ROI edge: INCONCLUSIVE
- production adoption: prohibited

Therefore, no model or policy promotion follows from current historical
results.

## 6. Daily Paper and Shadow Operation

### Required flow

1. Preflight validates source readiness.
2. Morning processing creates entries, features, model output, and candidate
   records.
3. Odds refresh obtains and evaluates odds once.
4. The prediction sheet and frozen records are written before results.
5. Evening processing imports official results and performs settlement.
6. Monitoring writes daily, live-operation, trace, and tuning-gate reports.

The canonical operational wrappers are:

- scripts/run_paper_ops_preflight.bat YYYY-MM-DD
- scripts/run_paper_ops_morning.bat YYYY-MM-DD
- scripts/run_paper_ops_evening.bat YYYY-MM-DD
- scripts/run_paper_ops_monitor.bat YYYY-MM-DD

The morning wrapper calls pre-race processing with
--defer-odds-evaluation, then invokes run_daily_odds_refresh directly. This
prevents a duplicate odds request and prevents stale odds or EV artifacts from
being copied into the pre-race report. Direct standalone pre-race use retains
its documented behavior unless that flag is set.

### Canonical operational outputs

- reports/predictions/YYYY-MM-DD/prediction_sheet.json
- reports/predictions/YYYY-MM-DD/frozen_bets.json
- data/predictions/YYYYMMDD/frozen_bets_all.json
- reports/daily/YYYY-MM-DD/daily_report.json
- reports/monitoring/live_operation_summary.json
- reports/monitoring/tuning_gate.json
- reports/monitoring/candidate_trace_*

The local Web view is an inspection surface. It is not an alternative source
of truth and cannot create a result, settlement, or buy authorization.

## 7. Evidence and Promotion Gates

### A. Research validity gate

All challenger evaluation is chronological, race-grouped, and train-only for
preprocessing and calibration. The locked test is fixed before candidate
selection. Required metrics include Top-1, log loss, Brier, calibration,
paired confidence intervals, and baseline comparison.

Missing prediction-time timestamps, same-horizon market odds, active or scratch
state, or an unexposed confirmatory period produce INCONCLUSIVE, not an
optimistic result.

### B. Forward feature and OOF gate

The active forward contract is personal-research-only, source-policy-gated,
and append-only. It compares the frozen tree_15 baseline with the
course_start_residual_shadow_v1 challenger using chronological five-fold OOF
evaluation.

Diagnostic readiness requires at least:

- 30 forward days
- 80 percent coverage
- 500 settled feature races
- 75 validation races per fold
- 375 OOF races over 25 dates
- zero unknown, terminal conflict, leakage, and production-relevant failure
  counts
- a valid hash chain

Decision readiness raises the evidence requirement to 1,500 settled feature
races, 250 validation races per fold, and 1,250 OOF races. Promotion still
requires explicit approval and remains disabled by configuration.

No OOF readiness may be inferred when its generated result is absent.

### C. Live shadow tuning gate

Starting live-only tuning requires both:

- liveSettledBetCount >= 100
- liveSettlementCoverage >= 0.5

Backfill readiness alone does not satisfy this gate.

### D. Production evidence gate

Production adoption is a separate, stricter decision. It requires at least:

- 60 observation days
- 500 settled shadow candidates
- trace coverage >= 0.95
- pre-deadline odds coverage >= 0.95
- settlement coverage >= 0.98
- no severe drift, no candidate ID duplicates, and no unresolved candidates
- calculable ROI and confidence evidence without profit concentration above
  25 percent

Passing any earlier gate does not enable BUY, EV, voting, or production model
changes.

## 8. Source Policy and Runtime Provenance

Only approved sources and collection modes may run. For the current
feature-forward collector:

- usage is personal research only
- source policy is enforced at runtime
- low-frequency official beforeinfo access is bounded and fail-closed
- commercial use, redistribution, public release, paid service, and automatic
  adoption are prohibited

Every forward run records the policy and config hash, code commit, runtime
source hash, source-file manifest hash, lifecycle ledger integrity, and output
tree hash. A run with invalid integrity, time-order violation, terminal
conflict, or an unapproved source is blocked.

The runtime source is versioned in this repository.
src/feature_forward_v1/runtime_provenance.py resolves SOURCE_ROOTS from the
current Git root. Runtime identity is verified by the repository verifier
against config/runtime_lock.json; a missing lock, source-root mismatch, commit
mismatch, or hash mismatch blocks the run. This specification does not claim
that verification passed. No external recovery clone is a runtime source or a
migration prerequisite.

## 9. Definition of Done

The product is not complete merely because code exists. Completion has four
separate outcomes:

| Outcome | Required condition | Current decision |
| --- | --- | --- |
| Implementation complete | Contracts, audits, and reproducible evaluators exist | achieved for architecture v2 |
| Daily shadow operation complete | Morning freeze, odds refresh, evening settlement, and monitoring run reliably with traceable outputs | not yet demonstrated after the current scheduler repair |
| Evidence complete | All applicable OOF and live gates pass with provenance and settlement evidence | blocked |
| Production adoption | Explicit human approval after evidence completion | prohibited |

The primary near-term definition of success is a stable daily record, not a
claim of predictive or financial edge.

## 10. Current Snapshot (2026-08-16)

This section records the status at specification creation; generated reports
remain the live source for subsequent values.

- Architecture v2 reports implementationComplete=true and
  evidenceComplete=false.
- The forward collector reports 90 valid captures, 821 research-eligible
  snapshots, 23 collection days, and valid lifecycle and cumulative integrity.
- No generated OOF readiness result is currently present.
- Live tuning is blocked at zero live settled bets and zero live settlement
  coverage.
- The latest daily health report records a morning scheduler failure. The
  duplicate odds-fetch repair was committed afterward, so the next scheduled
  morning route must validate the repair before the daily flow is considered
  stable.

## 11. Change Control

Any proposed change must state:

1. Which mode and contract it affects.
2. Whether it can affect prediction, policy, freeze, settlement, or only
   monitoring.
3. The expected generated evidence and pass or fail gate.
4. The smallest relevant verification command.
5. Whether a new explicit approval is required.

Changes to a threshold, model, source, prediction-time field, policy, or
source permission require a new review before execution. Generated reports,
data, models, and raw official files are not ordinary Git targets.

## 12. Review Register

The following is the complete review set for a change to the finished product.
Read the files in the listed group before changing that group. A file not in
the relevant group is not automatically in scope.

### Governance and entry points

- AGENTS.md
- docs/FINAL_PRODUCT_SPEC.md
- docs/CODEX_CONTEXT.md
- docs/CODEX_HANDOFF.md
- docs/CODEX_TASKS.md
- docs/CURRENT_STATUS.md
- docs/operation_phase.md
- docs/00_MASTER_INDEX.md (navigation only)

### Data, feature, and inference contracts

- docs/data_contract.md
- docs/feature_contract.md
- docs/feature_inventory.md
- docs/feature_availability_matrix.md
- docs/feature_missing_policy.md
- docs/feature_tiering.md
- docs/leakage_rules_for_features.md
- docs/model_contract.md
- docs/inference_contract.md
- docs/time_split_policy.md
- docs/calibration_policy.md

### Prediction, policy, and trace contracts

- docs/candidate_generation_policy.md
- docs/ev_policy.md
- docs/MODEL_POLICY_SEPARATION_CONTRACT.md
- docs/CANDIDATE_TRACE_CONTRACT.md
- docs/ARCHITECTURE_V2_WALK_FORWARD.md
- docs/ARCHITECTURE_V2_GAP_AUDIT.md
- docs/ARCHITECTURE_V2_COMPLETION.md

### Operation, source, and evidence contracts

- docs/daily_runbook.md
- docs/PAPER_PREDICTION_WEB_V1_OPERATIONS.md
- docs/STRICT_EVIDENCE_DAILY_AUDIT.md
- docs/LIVE_EVIDENCE_OPERATION.md
- docs/LIVE_SHADOW_EVIDENCE_GATE.md
- docs/boatrace_official_pipeline.md
- docs/source_registry.md (historical source catalog)
- docs/commercialization_v2/SOURCE_RIGHTS_EVIDENCE_REGISTRATION.md
- docs/FEATURE_FORWARD_COLLECTION_V1.md
- docs/feature_forward_v1/OOF_DECISION_PROTOCOL.md
- docs/feature_forward_v1/OOF_PROTOCOL_FREEZE.json
- docs/feature_forward_v1/PARALLEL_SHADOW_RUNBOOK.md
- docs/LIVE_EVIDENCE_BURN_IN.md

### Executable implementation anchors

- src/pipeline/run_daily_pre_race.py
- src/pipeline/run_daily_odds_refresh.py
- src/pipeline/run_daily_post_race.py
- src/pipeline/health_check.py
- src/pipeline/ops_goal_board.py
- src/pipeline/candidate_metadata.py
- src/evaluation/run_day_evaluation_v2.py
- src/evaluation/run_batch_evaluation_v2.py
- src/evaluation/live_operation_summary.py
- src/evaluation/tuning_gate.py
- scripts/run_paper_ops_preflight.bat
- scripts/run_paper_ops_morning.bat
- scripts/run_paper_ops_evening.bat
- scripts/run_paper_ops_monitor.bat
- scripts/build_candidate_trace_audit.py
- scripts/build_live_evidence_gate.py

### Feature-forward runtime anchors

The runtime source is versioned in this repository. The repository verifier
checks the repo-local runtime source against config/runtime_lock.json; missing
or mismatched verification input is blocking. The review inputs are:

- config/runtime_lock.json (required runtime identity lock)
- config/feature_forward_v1/
- reports/feature_forward/feature_value_contract.json (runtime config input;
  never hand-edit)
- scripts/run_live_feature_capture_v1.py
- src/feature_forward_v1/runtime_provenance.py
- src/feature_forward_v1/
- src/commercialization_v2/

### Generated evidence to inspect, never to hand-edit

- reports/repo_audit/final_goal_progress.json
- reports/monitoring/*health_check.json
- reports/monitoring/tuning_gate.json
- reports/monitoring/architecture_v2_completion.json
- reports/monitoring/live_shadow_evidence.json
- reports/monitoring/live_evidence_gate.json
- reports/model_eval/architecture_v2_walk_forward_validation.json
- reports/prediction_edge_v1/final_report.md
- reports/feature_forward_v1/latest_status.json
- the run manifest and provenance sidecar referenced by latest_status.json

## 13. Minimum Verification

For a documentation-only change:

1. Confirm every path in the changed review register exists or is explicitly
   marked historical or runtime-external.
2. Confirm the document does not authorize a blocked source, model, policy,
   or deployment.
3. Confirm the documented command order matches the active batch wrapper.

For an operational change, add the focused unit test and compile check for the
modified implementation; do not run a live data task merely to validate
documentation.
