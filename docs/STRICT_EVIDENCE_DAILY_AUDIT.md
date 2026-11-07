# Strict Evidence Daily Audit

## 目的

Phase F.2 の読み取り専用監査です。指定日の新規候補が、候補生成、metadata、
締切前オッズ、freeze、公式結果 settlement のどこで止まったかを記録します。
この監査は予想ロジック、BUY、EV、投票、本番送信を実行しません。

## 実行

```powershell
py -3.13 scripts\build_strict_evidence_daily_audit.py --date 2026-07-10
py -3.13 scripts\build_strict_evidence_daily_audit.py --date 2026-07-10 --dry-run
```

出力先は次の3ファイルです。

- `reports/monitoring/strict_evidence_daily_audit_YYYY-MM-DD.json`
- `reports/monitoring/strict_evidence_daily_audit_YYYY-MM-DD.csv`
- `reports/monitoring/strict_evidence_daily_audit_YYYY-MM-DD.md`

## strict条件

strict eligible は、次を満たす候補です。

- `candidateId`、`modelVersion`、`policyVersion`、`predictionHash` が存在する
- `oddsCapturedAt < deadlineAt`
- policy と guard を通過している
- `frozenAt` または frozen ledger の存在が確認できる
- `candidateId` が対象日で重複していない

`settledCandidateCount` は strict eligible のうち、公式結果による settlement join が
成功した候補です。結果待ちと settlement join failure は strict eligible の分母に残し、
別々の lifecycle として示します。

## 主原因の優先順位

複数の原因が同時にある場合、次の順で `primaryBlockingReason` を決めます。

1. `scope_mismatch`
2. `missing_metadata`
3. `missing_odds`
4. `policy_filtered_all`
5. `guard_filtered_all`
6. `freeze_not_run`
7. `expected_no_candidate`

strict候補が存在した後の停止は、主原因または補助原因として
`result_waiting` / `settlement_join_failure` に分類します。

`freeze_not_run` は、metadata完全、締切前オッズあり、policy/guard通過、締切経過済み、
frozen ledgerなしの場合だけ付与します。

## Operator action

- `scope_mismatch`: 当日のprediction sheet / frozen ledgerが生成されたかを確認する。過去のlegacy集計で補完しない。
- `missing_metadata`: forward-only metadata writerの出力を確認する。legacy行を補完しない。
- `missing_odds`: 締切前odds取得時刻とdeadlineの証跡を確認する。締切後oddsを採用しない。
- `policy_filtered_all` / `guard_filtered_all`: 判定を変更せず、当日の除外理由を記録する。
- `freeze_not_run`: safe morning/freeze経路の実行記録を確認する。既存ledgerを上書きしない。
- `result_waiting`: Kファイルまたは公式結果の公開待ちとして扱う。
- `settlement_join_failure`: raceId / candidateId / predictionHashのjoin証跡を確認し、結果を推測補完しない。

## lifecycle

- `candidate_created`
- `metadata_complete`
- `pre_deadline_odds_confirmed`
- `frozen`
- `result_waiting`
- `settled`
- `settlement_join_failure`

## legacyの扱い

legacy行は `legacyReference` にだけ出します。strictの分母・分子、blocker、
`burnInReady`には混ぜません。legacy metadataを推測補完したり、既存の
`frozen_bets`を再生成・上書きしたりしません。

## burn-in連携

`build_live_evidence_burn_in.py` は、対象日の正常なdaily auditを `targetDate` で選び、
主原因・blocking stage・watchdogの証跡に使います。ファイルmtimeだけで新旧を決めません。
burn-in ready条件自体は変更しません。

移行条件は次のとおりです。

- strict settled 10件未満: 既存正本をcanonical schemaへ置換しない
- strict settled 10件: canonical projectionの設計レビュー
- strict settled 30件: 経路品質レビュー
- strict settled 100件: 校正・分布確認
- strict settled 500件かつ観測60日以上: 統計評価

生成レポートはGit対象外です。
