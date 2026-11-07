# AGENTS.md

新 `C:\Users\goo10\競艇\boatrace-ai-mvp` は今後の正本であり、Codex IDEで進める本命。
旧 `C:\Users\goo10\OneDrive\ドキュメント\New project\boat_race_ai` は別プロジェクト / legacy reference であり、直接作業しない。
`PROJECT_HANDOFF` / `NEXT_ACTIONS` / `PHASE_HISTORY` 相当は `docs/CODEX_HANDOFF.md` / `docs/CODEX_TASKS.md` / `docs/CURRENT_STATUS.md` / `docs/operation_phase.md` を読む。
次回は `AGENTS.md` を読んで `docs/CODEX_TASKS.md` の次タスクから進める。

## 基本ルール
- 変更前に対象ファイルを確認する
- 大きな設計変更は勝手にしない
- 関係ないファイルは触らない
- 自動送信・本番実行・外部送信はしない
- 変更後は最小限の確認だけを実行する

## 最初に読む順
1. `AGENTS.md`
2. `docs/CODEX_HANDOFF.md`
3. `docs/CODEX_TASKS.md`
4. `docs/CURRENT_STATUS.md`
5. `docs/operation_phase.md`

## 作業フォルダー
- `C:\Users\goo10\競艇\boatrace-ai-mvp`
- この配下を正本として扱う
- `node_modules/`, `build/`, `dist/`, `venv/`, `logs/`, `backups/` は原則触らない
- `data/` は原則触らないが、`data/frozen_bets/` は生成先 / 運用対象として扱う
- 必要な場合だけ最小限を確認する

## 現在フェーズ
- live shadow operation
- 本番 BUY / EV / 投票は禁止
- 本番チューニングではない
- `frozen_bets` を毎日保存し、result / settlement / daily_report / monitoring を継続運用する
- BUY ルール変更はまだしない
- `liveSettledBetCount >= 100` かつ `liveSettlementCoverage >= 0.5` が揃うまで tuning は保留

## source of truth
- 既存実体: `src/`, `scripts/`, `docs/CODEX_HANDOFF.md`, `docs/CODEX_TASKS.md`, `docs/CURRENT_STATUS.md`, `docs/operation_phase.md`
- 生成先 / 運用対象: `data/frozen_bets/YYYYMMDD/frozen_bets_all.json`（未存在または生成前なら既存実体ではなくここを運用対象として扱う）, `reports/daily/YYYY-MM-DD/`, `reports/monitoring/`, `data/ui/YYYYMMDD/`, `reports/predictions/YYYY-MM-DD/`

## 絶対禁止事項
- 予想ロジック、BUY 閾値、EV 計算、`baseline_score_model` 重み、`hard_guard` を勝手に変えない
- 結果データで予想を再生成しない
- `frozen_bets` を結果到着後に上書きしない
- `sample` / `dummy` / `fallback sample` / `固定買い目` / `固定選手データ` を production パスに入れない
- `source_not_ready` / `result_data_missing` / `future_date_not_ready` を成功扱いしない
- 実購入、代行投票、自動送信、本番送信をしない
- backfill と live を混ぜない
- `data/raw/official/**`, `models/**`, `config/**` を無断で変更しない

## daily 運用で見る数字
- `latestCompleteOpsDate`
- `completeOpsReady`
- `primaryBlocker`
- `nextAction`
- `paperEligibleCandidateCount`
- `remainingPaperEligibleCandidateCount`
- `liveSettledBetCount`
- `revenueValidationReady`
- `settledBetCount`
- `unresolvedBetCount`
- `resultMissingCount`
- `errorCount`
- `liveSettlementCoverage`
- `canTuneWithLiveOnly`
- `canTuneWithBackfill`

## 次にやる作業
1. `health_check` で `primaryBlocker` と `nextAction` を確認する
2. 朝は freeze、夜は settle と report を回す
3. K ファイルが来たら先に取り込み、backfill readiness を更新する
4. `daily_summary` / `live_operation_summary` / `tuning_gate` を見て、次の最小アクションを 1 つだけ残す

## Git対象OK/NG
- OK: この `AGENTS.md`, `docs/*.md`, `src/**`, `scripts/**`, 小さなテスト/補助スクリプト
- NG: `data/raw/**`, `data/**` の生成物, `reports/**` の日次生成物, `logs/**`, `models/**`, `config/**`, `node_modules/**`, `build/**`, `dist/**`, `venv/**`, `backups/**`
- 迷うものは Git 対象外。必要なら task 単位で明示する

## BUY / EV / 投票禁止
- BUY は shadow record まで。production ルール変更は禁止
- EV は表示・検証用。閾値変更や式の変更は禁止
- 投票・購入の自動実行、本番送信、代行送信は禁止
- 外部予想は BUY 判定に混ぜない
