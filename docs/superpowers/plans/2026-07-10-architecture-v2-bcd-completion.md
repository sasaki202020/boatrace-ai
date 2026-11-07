# Architecture V2 B-D Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Phase A の candidate trace を土台に、モデルとpolicyの境界監査、同一splitのwalk-forward再検証、live shadow証拠ゲート、A-D統合判定を再実行可能にする。

**Architecture:** 本番経路を変更せず、既存の source、model evaluation、monitoring artifact を読む sidecar scripts を追加する。各scriptは JSON/CSV/Markdown を出力し、欠損や期間不整合を合格に見せず warning/blocked として固定する。

**Tech Stack:** Python 3.13, standard library, pandas, pytest

## Global Constraints

- `src/strategy/evaluate_ev_and_skip.py`、BUY/EV、校正、hard guard、frozen_bets、settlement生成を変更しない。
- `daily_ops`、`predict_today`、`main.py`、投票処理へ接続しない。
- 推測で version、odds timestamp、settlement、ROI を補完しない。
- git add / commit / push を実行しない。
- テスト先行で各sidecarを実装する。

---

### Task 1: Model / Policy Separation Audit

**Files:**
- Create: `scripts/build_model_policy_separation_audit.py`
- Create: `tests/test_build_model_policy_separation_audit.py`
- Create: `docs/MODEL_POLICY_SEPARATION_CONTRACT.md`
- Generate: `reports/monitoring/model_policy_separation_audit.json`
- Generate: `reports/monitoring/model_policy_separation_audit.csv`
- Generate: `reports/monitoring/model_policy_separation_audit.md`

**Interfaces:**
- Consumes: `src/strategy/evaluate_ev_and_skip.py`, model/calibration modules, candidate trace audit.
- Produces: `build_model_policy_separation_audit() -> dict` and explicit model/calibration/market/policy output contracts.

- [ ] Write a failing test asserting layer ownership, forbidden reverse imports, legacy coupling detection, and separate artifact schemas.
- [ ] Run the focused test and confirm it fails because the audit module is absent.
- [ ] Implement the AST/source audit and JSON/CSV/Markdown writers without importing production policy code.
- [ ] Run the focused test and confirm it passes.

### Task 2: Same-Split Walk-Forward Validation Audit

**Files:**
- Create: `scripts/run_architecture_v2_walk_forward_validation.py`
- Create: `tests/test_architecture_v2_walk_forward_validation.py`
- Create: `docs/ARCHITECTURE_V2_WALK_FORWARD.md`
- Generate: `reports/model_eval/architecture_v2_walk_forward_validation.json`
- Generate: `reports/model_eval/architecture_v2_walk_forward_validation.csv`
- Generate: `reports/model_eval/architecture_v2_walk_forward_validation.md`

**Interfaces:**
- Consumes: existing `reports/model_eval/*.json`, candidate trace date range, model split metadata.
- Produces: `build_walk_forward_validation() -> dict` with split parity, model metric deltas, policy overlap, and readiness.

- [ ] Write a failing test using synthetic reports with matching and mismatching split periods.
- [ ] Run the focused test and confirm the missing module failure.
- [ ] Implement deterministic report parsing, split signature comparison, leakage checks, and cross-layer date overlap checks.
- [ ] Run the focused test and confirm it passes.
- [ ] Run against current artifacts; do not retrain or overwrite model bundles.

### Task 3: Live Shadow Evidence Gate

**Files:**
- Create: `scripts/build_live_shadow_evidence.py`
- Create: `tests/test_build_live_shadow_evidence.py`
- Create: `docs/LIVE_SHADOW_EVIDENCE_GATE.md`
- Generate: `reports/monitoring/live_shadow_evidence.json`
- Generate: `reports/monitoring/live_shadow_evidence.csv`
- Generate: `reports/monitoring/live_shadow_evidence.md`

**Interfaces:**
- Consumes: `live_operation_summary.json`, `tuning_gate.json`, candidate trace audit.
- Produces: `build_live_shadow_evidence() -> dict` with fixed 60-day/500-settlement gates, coverage, concentration availability, and blocker list.

- [ ] Write failing tests for ready and blocked evidence fixtures.
- [ ] Run the focused test and confirm the missing module failure.
- [ ] Implement fail-closed evidence aggregation; null metrics remain unavailable.
- [ ] Run the focused test and confirm it passes.
- [ ] Run against current monitoring artifacts and preserve the expected blocked status when live settlements are zero.

### Task 4: Architecture V2 Completion Report

**Files:**
- Create: `scripts/build_architecture_v2_completion_report.py`
- Create: `tests/test_architecture_v2_completion_report.py`
- Create: `docs/ARCHITECTURE_V2_COMPLETION.md`
- Generate: `reports/monitoring/architecture_v2_completion.json`
- Generate: `reports/monitoring/architecture_v2_completion.md`

**Interfaces:**
- Consumes: Phase A candidate trace plus Task 1-3 artifacts.
- Produces: `build_completion_report() -> dict` separating implementation completion from evidence-gate completion.

- [ ] Write a failing test asserting all four phase statuses and overall fail-closed classification.
- [ ] Run the focused test and confirm the missing module failure.
- [ ] Implement the aggregator and human-readable next-action report.
- [ ] Run all four focused test modules, py_compile all new scripts, regenerate artifacts, and verify production-path files are unchanged.

## Self-Review

- Spec coverage: B separation audit, C same-split validation, D live evidence, and A-D integration are all mapped.
- Placeholder scan: no implementation placeholder is used; unavailable source fields are explicitly represented as unavailable.
- Scope: all changes are sidecar-only and do not alter prediction, BUY/EV, calibration, settlement, or frozen ledgers.
- Git: commit steps are intentionally omitted because the user explicitly prohibited git operations.
